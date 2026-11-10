"""Mise en quarantaine d'un compte : réversible, sans rien supprimer.

Le compte ne peut plus se connecter (connexion bloquée + sessions révoquées) et les protocoles d'accès à la boîte sont
coupés (Outlook web, ActiveSync, IMAP, POP, MAPI, EWS). La boîte continue de RECEVOIR le courrier : rien n'est perdu.
Les réglages d'origine sont mémorisés et restaurés à la levée.
"""
from __future__ import annotations

import uuid
from typing import Optional

from django.utils import timezone

from module.microsoft_connector.application.graph_factory import build_graph
from module.microsoft_identity.infrastructure.sync_models import AccountQuarantine
from shared_kernel.aio import db

PROTOCOLS = ("OWAEnabled", "ActiveSyncEnabled", "ImapEnabled", "PopEnabled", "MAPIEnabled", "EwsEnabled")


class QuarantineRefused(Exception):
    pass


class AccountQuarantineService:
    def __init__(self, identity=None, exchange_factory=None):
        from module.microsoft_identity.application.services import IdentityService
        self.identity = identity or IdentityService()
        self._exchange_factory = exchange_factory or self._default_exchange

    @staticmethod
    def _default_exchange(connector):
        from module.microsoft_outlook.infrastructure.exchange_gateway import ExchangeGateway
        return ExchangeGateway(build_graph(connector), str(connector.tenant_id))

    async def _ctx(self, mailbox_id):
        mailbox, gw = await self.identity._acontext(mailbox_id)
        connector = await db(self.identity.connectors.get)(mailbox.connector_id)
        return mailbox, gw, connector

    @staticmethod
    def status(mailbox_id: uuid.UUID) -> Optional[dict]:
        q = AccountQuarantine.objects.filter(mailbox_id=mailbox_id, released_at__isnull=True).first()
        return None if q is None else {"since": q.create_at, "by": q.started_by, "reason": q.reason,
                                       "protocols_saved": bool(q.previous_cas), "detail": q.detail}

    async def start_async(self, mailbox_id: uuid.UUID, *, confirm: str, by: str, reason: str = "") -> dict:
        mailbox, gw, connector = await self._ctx(mailbox_id)
        self.identity._check_confirmation(mailbox, confirm)
        if await db(self.status)(mailbox_id):
            raise QuarantineRefused("Ce compte est déjà en quarantaine.")
        upn, notes = mailbox.user_principal_name, []
        await gw.set_account_enabled(upn, False)
        await gw.revoke_sessions(upn)
        notes.append("connexion bloquée et sessions révoquées")
        previous = None
        try:
            exo = self._exchange_factory(connector)
            rows = await exo.invoke("Get-CASMailbox", {"Identity": upn})
            previous = {k: bool(rows[0].get(k)) for k in PROTOCOLS} if rows else None
            await exo.invoke("Set-CASMailbox", {"Identity": upn, **{k: False for k in PROTOCOLS}})
            notes.append("protocoles de messagerie coupés (Outlook, mobile, IMAP, POP, EWS)")
        except Exception as e:  # noqa: BLE001 : Exchange inaccessible → la connexion reste bloquée, on le signale
            notes.append(f"protocoles NON coupés ({type(e).__name__}) : la connexion bloquée suffit à empêcher l'accès")

        def save():
            AccountQuarantine.objects.create(mailbox_id=mailbox.id, org_id=mailbox.org_id, started_by=by[:150],
                                             reason=(reason or "")[:500], previous_cas=previous, detail=" · ".join(notes))
        await db(save)()
        await db(self.identity._log)(mailbox, "quarantine:start", True, " · ".join(notes), by)
        return await db(self.status)(mailbox_id)

    async def release_async(self, mailbox_id: uuid.UUID, *, confirm: str, by: str) -> dict:
        mailbox, gw, connector = await self._ctx(mailbox_id)
        self.identity._check_confirmation(mailbox, confirm)
        q = await db(lambda: AccountQuarantine.objects.filter(mailbox_id=mailbox_id, released_at__isnull=True).first())()
        if q is None:
            raise QuarantineRefused("Ce compte n'est pas en quarantaine.")
        upn, notes = mailbox.user_principal_name, []
        if q.previous_cas:
            try:
                await self._exchange_factory(connector).invoke("Set-CASMailbox", {"Identity": upn, **q.previous_cas})
                notes.append("protocoles de messagerie restaurés")
            except Exception as e:  # noqa: BLE001
                notes.append(f"protocoles NON restaurés ({type(e).__name__}) : à rétablir dans Exchange")
        await gw.set_account_enabled(upn, True)
        notes.append("connexion rétablie")

        def save():
            q.released_at, q.released_by = timezone.now(), by[:150]
            q.save(update_fields=["released_at", "released_by", "modified_at"])
        await db(save)()
        await db(self.identity._log)(mailbox, "quarantine:release", True, " · ".join(notes), by)
        return {"released": True, "detail": " · ".join(notes)}
