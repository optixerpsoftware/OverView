"""Connexion d'un tenant Microsoft 365 par consentement administrateur (« Connecter Microsoft 365 »).

L'application OverView est inscrite UNE fois dans Azure (multi-tenant) ; son ID et son secret viennent de
l'environnement (M365_APPLICATION_ID / M365_TOKEN). Chaque client donne son consentement : Microsoft crée alors le
principal de service dans son tenant et nous renvoie l'ID du tenant ; on crée (ou réactive) le connecteur.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import timedelta
from typing import Optional
from urllib.parse import urlencode

from django.core import signing
from django.db import transaction
from django.utils import timezone

from module.microsoft_connector.application.services import ConnectorService, OrganizationNotFound
from module.microsoft_connector.domain.consent import admin_consent_url, parse_callback
from module.microsoft_connector.infrastructure.models import ConsentRequest, MicrosoftConnector
from shared_kernel.aio import db

log = logging.getLogger(__name__)
SALT = "overview.m365-admin-consent"
MAX_AGE = timedelta(minutes=15)
CALLBACK_PATH = "/api/connectors/consent/callback/"


class ConsentNotConfigured(Exception):
    pass


def _platform() -> tuple[str, str]:
    return os.environ.get("M365_APPLICATION_ID", ""), os.environ.get("M365_TOKEN", "")


def api_public_url() -> str:
    return os.environ.get("API_PUBLIC_URL", "http://localhost:8000").rstrip("/")


def app_public_url() -> str:
    return os.environ.get("APP_PUBLIC_URL", "http://localhost:3000").rstrip("/")


def redirect_uri() -> str:
    return api_public_url() + CALLBACK_PATH


class ConsentService:
    def __init__(self, connectors: Optional[ConnectorService] = None, test_delays=(0, 3, 6, 10)):
        self.connectors = connectors or ConnectorService()
        self._delays = test_delays   # le consentement met quelques secondes à se propager chez Microsoft

    @staticmethod
    def config() -> dict:
        client_id, secret = _platform()
        return {"available": bool(client_id and secret), "client_id": client_id, "redirect_uri": redirect_uri()}

    def start(self, *, org_id: uuid.UUID, name: str, by: str, tenant_hint: str = "") -> str:
        client_id, secret = _platform()
        if not (client_id and secret):
            raise ConsentNotConfigured("M365_APPLICATION_ID et M365_TOKEN doivent être définis côté serveur.")
        if not self.connectors.orgs.exists(org_id):
            raise OrganizationNotFound(str(org_id))
        req = ConsentRequest.objects.create(org_id=org_id, name=(name or "").strip()[:255], created_by=by[:150])
        state = signing.dumps({"n": str(req.id)}, salt=SALT, compress=True)
        return admin_consent_url(client_id, redirect_uri(), state, tenant_hint or "organizations")

    # --------------------------------------------------------------------------------------- retour de Microsoft
    async def complete_async(self, params) -> str:
        """Traite le retour et renvoie l'URL du frontend où rediriger le navigateur (jamais d'exception : le
        navigateur doit toujours aboutir sur une page lisible)."""
        result = parse_callback(params)
        req = await db(self._claim)(result.state)
        if req is None:
            return f"{app_public_url()}/organizations?" + urlencode(
                {"consent": "error", "message": "Lien de connexion expiré ou déjà utilisé. Recommence depuis l'organisation."})
        back = f"{app_public_url()}/organizations/{req.org_id}?"
        if not result.ok:
            return back + urlencode({"consent": "error", "message": result.error})
        try:
            connector_id = await db(self._upsert)(req, result.tenant_id)
        except Exception as e:  # noqa: BLE001
            log.exception("Création du connecteur après consentement impossible")
            return back + urlencode({"consent": "error", "message": f"Création du connecteur impossible : {e}"[:300]})
        error = await self._test(connector_id)
        if not error and not req.name:
            await self._name_from_tenant(connector_id)
        q = {"consent": "ok", "connector": str(connector_id)}
        if error:
            q = {"consent": "pending", "connector": str(connector_id), "message": error[:300]}
        return back + urlencode(q)

    @staticmethod
    def _claim(state: str) -> Optional[ConsentRequest]:
        """Vérifie la signature et la fraîcheur de `state`, puis consomme la demande (usage unique)."""
        try:
            nonce = signing.loads(state, salt=SALT, max_age=MAX_AGE)["n"]
        except (signing.BadSignature, KeyError, TypeError):
            return None
        with transaction.atomic():
            req = ConsentRequest.objects.select_for_update().filter(pk=nonce, used_at__isnull=True).first()
            if req is None or timezone.now() - req.create_at > MAX_AGE:
                return None
            req.used_at = timezone.now()
            req.save(update_fields=["used_at", "modified_at"])
            return req

    def _upsert(self, req: ConsentRequest, tenant_id: str) -> uuid.UUID:
        client_id, secret = _platform()
        existing = MicrosoftConnector.objects.filter(org_id=req.org_id, tenant_id=tenant_id, client_id=client_id).first()
        if existing:   # reconnexion : on réactive et on remet le secret courant de la plateforme
            self.connectors.update(existing.id, client_secret=secret, is_active=True,
                                   name=req.name or None)
            cid = existing.id
        else:
            cid = self.connectors.register(org_id=req.org_id, name=req.name or f"Tenant {tenant_id[:8]}",
                                           tenant_id=uuid.UUID(tenant_id), client_id=uuid.UUID(client_id),
                                           client_secret=secret).id
        ConsentRequest.objects.filter(pk=req.pk).update(connector_id=cid)
        return cid

    async def _name_from_tenant(self, connector_id: uuid.UUID) -> None:
        """Nom du connecteur = nom de l'organisation dans Microsoft 365 (si l'admin n'en a pas saisi)."""
        from module.microsoft_connector.application.graph_factory import build_graph
        try:
            c = await db(self.connectors.repo.get)(connector_id)
            res = await build_graph(c).client.organization.get()
            name = (res.value[0].display_name if res and res.value else "") or ""
            if name:
                await db(self.connectors.update)(connector_id, name=name)
        except Exception:  # noqa: BLE001 : le nom par défaut reste utilisable
            log.info("Nom du tenant indisponible pour %s", connector_id)

    async def _test(self, connector_id: uuid.UUID) -> str:
        error = ""
        for delay in self._delays:
            if delay:
                await asyncio.sleep(delay)
            c = await self.connectors.test_connection_async(connector_id)
            error = c.last_error
            if not error:
                break
        if error:
            return "Consentement accordé ; Microsoft le propage encore. Utilise « Tester la connexion » dans une minute."
        return ""
