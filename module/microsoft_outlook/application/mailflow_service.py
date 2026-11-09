"""Flux de messagerie et quarantaine : lecture (suivi des messages, quarantaine) et libération encadrée.

Async d'abord ; la libération exige une confirmation, journalisée. Les messages à risque (logiciel malveillant,
hameçonnage à haute confiance) exigent en plus un mot de confirmation tapé (`RELEASE_WORD`).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from asgiref.sync import async_to_sync

from module.microsoft_connector.application.graph_factory import build_graph
from module.microsoft_connector.infrastructure.repositories import DjangoConnectorRepository
from module.microsoft_outlook.domain.mailflow import (
    QUARANTINE_TYPES, RELEASE_WORD, TRACE_STATUSES, parse_quarantine, parse_trace, summarize_quarantine,
    summarize_trace, valid_email, valid_identity,
)
from module.microsoft_outlook.infrastructure.exchange_gateway import ExchangeGateway
from module.microsoft_outlook.infrastructure.models import QuarantineAction
from shared_kernel.aio import db
from shared_kernel.realtime import apublish

MAX_TRACE_HOURS = 24 * 10      # Exchange conserve 10 jours de suivi détaillé
MAX_QUARANTINE_DAYS = 30


class MailflowConnectorUnavailable(Exception):
    pass


class InvalidRequest(Exception):
    pass


class ReleaseNotConfirmed(Exception):
    def __init__(self, message: str, needs_word: bool = False):
        super().__init__(message)
        self.needs_word = needs_word


class QuarantineMessageNotFound(Exception):
    pass


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class MailflowService:
    def __init__(self, connectors=None, gateway_factory: Optional[Callable[[object], ExchangeGateway]] = None,
                 now: Optional[Callable[[], datetime]] = None):
        self.connectors = connectors or DjangoConnectorRepository()
        self._gateway_factory = gateway_factory or (lambda c: ExchangeGateway(build_graph(c), str(c.tenant_id)))
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _connector(self, connector_id: uuid.UUID):
        c = self.connectors.get(connector_id)
        if c is None or not c.is_active:
            raise MailflowConnectorUnavailable(str(connector_id))
        return c

    # --- suivi des messages -------------------------------------------------------------------------------
    async def trace_async(self, connector_id: uuid.UUID, *, hours: int = 24, sender: str = "", recipient: str = "",
                          status: str = "") -> dict:
        if not 1 <= hours <= MAX_TRACE_HOURS:
            raise InvalidRequest(f"La période doit être comprise entre 1 h et {MAX_TRACE_HOURS} h.")
        sender, recipient = (sender or "").strip().lower(), (recipient or "").strip().lower()
        for label, v in (("expéditeur", sender), ("destinataire", recipient)):
            if v and not valid_email(v):
                raise InvalidRequest(f"Adresse {label} invalide.")
        if status and status not in TRACE_STATUSES:
            raise InvalidRequest("Statut inconnu.")
        connector = await db(self._connector)(connector_id)
        end = self._now()
        start = end - timedelta(hours=hours)
        rows = await self._gateway_factory(connector).message_trace(
            start=_iso(start), end=_iso(end), sender=sender, recipient=recipient, status=status)
        msgs = sorted((parse_trace(r) for r in rows), key=lambda m: m.received or start, reverse=True)
        return {"summary": summarize_trace(msgs, start, end), "messages": msgs[:500], "truncated": len(msgs) > 500,
                "from": start, "to": end}

    def trace(self, connector_id, **kw) -> dict:
        return async_to_sync(self.trace_async)(connector_id, **kw)

    # --- quarantaine --------------------------------------------------------------------------------------
    async def quarantine_async(self, connector_id: uuid.UUID, *, days: int = 7, type_: str = "") -> dict:
        if not 1 <= days <= MAX_QUARANTINE_DAYS:
            raise InvalidRequest(f"La période doit être comprise entre 1 et {MAX_QUARANTINE_DAYS} jours.")
        if type_ and type_ not in QUARANTINE_TYPES:
            raise InvalidRequest("Type de quarantaine inconnu.")
        connector = await db(self._connector)(connector_id)
        end = self._now()
        rows = await self._gateway_factory(connector).quarantine(
            start=_iso(end - timedelta(days=days)), end=_iso(end), type_=type_)
        msgs = sorted((parse_quarantine(r) for r in rows), key=lambda m: m.received or end, reverse=True)
        return {"summary": summarize_quarantine(msgs), "messages": msgs[:500], "truncated": len(msgs) > 500}

    def quarantine(self, connector_id, **kw) -> dict:
        return async_to_sync(self.quarantine_async)(connector_id, **kw)

    # --- libération ---------------------------------------------------------------------------------------
    async def release_async(self, connector_id: uuid.UUID, identity: str, *, confirm: str, performed_by: str,
                            release_to_all: bool = True, allow_sender: bool = False) -> dict:
        """Libère UN message (jamais en masse). `confirm` : « OUI » pour un message ordinaire ; le mot
        `RELEASE_WORD` est exigé pour un message reconnu comme malveillant ou hameçonnage à haute confiance."""
        if not valid_identity(identity):
            raise InvalidRequest("Identifiant de message invalide.")
        connector = await db(self._connector)(connector_id)
        gw = self._gateway_factory(connector)
        row = await gw.quarantine_one(identity)
        if row is None:
            raise QuarantineMessageNotFound(identity)
        msg = parse_quarantine(row)
        if msg.released:
            raise InvalidRequest("Ce message a déjà été libéré.")
        confirm = (confirm or "").strip()
        if msg.risky and confirm != RELEASE_WORD:
            raise ReleaseNotConfirmed(
                f"Message de type « {msg.type} » : tapez {RELEASE_WORD} pour confirmer la libération.", needs_word=True)
        if not msg.risky and confirm.upper() not in ("OUI", RELEASE_WORD):
            raise ReleaseNotConfirmed("Confirmation requise pour libérer ce message.")
        ok, detail = True, f"Libéré vers {'tous les destinataires' if release_to_all else 'le destinataire demandé'}."
        try:
            await gw.release(identity, release_to_all=release_to_all, allow_sender=allow_sender)
        except Exception as e:  # noqa: BLE001 : on journalise l'échec puis on relance
            ok, detail = False, f"{type(e).__name__}: {e}"[:500]
            await db(self._audit)(connector, msg, "release", ok, detail, performed_by)
            raise
        await db(self._audit)(connector, msg, "release", ok, detail, performed_by)
        await apublish("mailflow.quarantine", {"connector_id": str(connector_id)})
        return {"identity": identity, "released": True, "subject": msg.subject, "type": msg.type}

    def release(self, connector_id, identity, **kw) -> dict:
        return async_to_sync(self.release_async)(connector_id, identity, **kw)

    @staticmethod
    def _audit(connector, msg, action, ok, detail, performed_by) -> None:
        QuarantineAction.objects.create(
            connector_id=connector.id, org_id=connector.org_id, identity=msg.identity, subject=msg.subject[:300],
            quarantine_type=msg.type, action=action, ok=ok, detail=detail, performed_by=performed_by[:150])

    async def actions_async(self, connector_id: uuid.UUID, limit: int = 30) -> list[dict]:
        await db(self._connector)(connector_id)

        def load():
            return list(QuarantineAction.objects.filter(connector_id=connector_id).values(
                "id", "identity", "subject", "quarantine_type", "action", "ok", "detail", "performed_by", "create_at")[:limit])
        return await db(load)()
