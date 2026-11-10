"""Accès conditionnel : état, politiques lisibles, base recommandée, Security Defaults et modèles (rapport seulement)."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict
from typing import Any, Callable, Optional

from asgiref.sync import async_to_sync

from module.microsoft_connector.application.graph_factory import build_graph
from module.microsoft_connector.infrastructure.repositories import DjangoConnectorRepository
from module.microsoft_identity.domain import conditional_access as ca
from module.microsoft_identity.infrastructure.audit_models import TenantSettingChange
from module.microsoft_identity.infrastructure.ca_gateway import GraphConditionalAccessGateway
from shared_kernel.aio import db

WORD_ENABLE = "ACTIVER"
WORD_DISABLE = "DESACTIVER"
WORD_CREATE = "CREER"


class CaConnectorUnavailable(Exception):
    pass


class CaConfirmationRequired(Exception):
    pass


class CaRefused(Exception):
    """Action refusée par un garde-fou (ex. désactiver Security Defaults sans protection de remplacement)."""


class ConditionalAccessService:
    def __init__(self, connectors=None, gateway_factory: Callable[[Any], Any] = None):
        self.connectors = connectors or DjangoConnectorRepository()
        self.gateway_factory = gateway_factory or (lambda c: GraphConditionalAccessGateway(build_graph(c)))

    def _ctx(self, connector_id: uuid.UUID):
        c = self.connectors.get(connector_id)
        if c is None or not c.is_active:
            raise CaConnectorUnavailable(str(connector_id))
        return c, self.gateway_factory(c)

    async def overview_async(self, connector_id: uuid.UUID) -> dict:
        _, gw = await db(self._ctx)(connector_id)
        sd_raw, policies, skus = await asyncio.gather(gw.security_defaults(), gw.policies(), gw.skus())
        sd = bool((sd_raw or {}).get("isEnabled"))
        tier = ca.license_tier(skus)
        readable = policies is not None
        policies = policies or []
        refs = ca.referenced_ids(policies)
        names = await gw.resolve_names(refs["objects"], refs["roles"], refs["apps"]) if policies else {}
        described = sorted((ca.describe(p, names) for p in policies),
                           key=lambda d: ({"enabled": 0, "enabledForReportingButNotEnforced": 1}.get(d["state"], 2), d["name"].lower()))
        return {
            "security_defaults": sd, "license": tier, "policies_readable": readable,
            "verdict": ca.verdict(sd, tier, policies),
            "counts": {s: sum(1 for p in policies if p.get("state") == s)
                       for s in ("enabled", "enabledForReportingButNotEnforced", "disabled")},
            "baseline": [asdict(b) for b in ca.baseline(policies, sd, tier)],
            "policies": described,
            "words": {"enable": WORD_ENABLE, "disable": WORD_DISABLE, "create": WORD_CREATE},
        }

    def overview(self, connector_id: uuid.UUID) -> dict:
        return async_to_sync(self.overview_async)(connector_id)

    async def set_security_defaults_async(self, connector_id: uuid.UUID, enabled: bool, *, confirm: str, by: str) -> dict:
        word = WORD_ENABLE if enabled else WORD_DISABLE
        if (confirm or "").strip().upper() != word:
            raise CaConfirmationRequired(f"Tapez {word} pour confirmer.")
        connector, gw = await db(self._ctx)(connector_id)
        current = bool((await gw.security_defaults()).get("isEnabled"))
        if current == enabled:
            return {"security_defaults": current, "changed": False}
        if not enabled:
            # Garde-fou : ne jamais laisser le tenant sans MFA de base.
            policies = await gw.policies() or []
            mfa_all = [b for b in ca.baseline(policies, False, "p1") if b.code == "mfa_all" and b.status == "covered"]
            if not mfa_all:
                raise CaRefused("Refusé : aucune politique d'accès conditionnel active n'exige le MFA pour tous. "
                                "Désactiver Security Defaults laisserait le tenant sans protection.")
        change = [f"Security Defaults : {'activé' if current else 'désactivé'} → {'activé' if enabled else 'désactivé'}"]
        try:
            await gw.set_security_defaults(enabled)
        except Exception as e:
            await db(self._audit)(connector, "security_defaults", change, False, f"{type(e).__name__}: {e}", by)
            raise
        await db(self._audit)(connector, "security_defaults", change, True, "", by)
        return {"security_defaults": enabled, "changed": True}

    async def create_from_template_async(self, connector_id: uuid.UUID, code: str, *, confirm: str, by: str,
                                         exclude_user_ids: Optional[list[str]] = None) -> dict:
        """Crée une politique recommandée en mode « rapport seulement » (aucun effet sur les connexions)."""
        if (confirm or "").strip().upper() != WORD_CREATE:
            raise CaConfirmationRequired(f"Tapez {WORD_CREATE} pour confirmer.")
        excl = [str(uuid.UUID(str(i))) for i in (exclude_user_ids or [])][:5]   # lève ValueError si invalide
        body = ca.template_body(code, excl)
        connector, gw = await db(self._ctx)(connector_id)
        change = [f"Politique créée (rapport seulement) : {body['displayName']}"]
        try:
            created = await gw.create_policy(body)
        except Exception as e:
            await db(self._audit)(connector, "conditional_access", change, False, f"{type(e).__name__}: {e}", by)
            raise
        await db(self._audit)(connector, "conditional_access", change, True, "", by)
        return {"id": created.get("id"), "name": body["displayName"], "state": body["state"]}

    @staticmethod
    def _audit(connector, setting, changes, ok, detail, by):
        TenantSettingChange.objects.create(connector_id=connector.id, org_id=connector.org_id, setting=setting,
                                           changes=changes, ok=ok, detail=(detail or "")[:2000], performed_by=by[:150])

    @staticmethod
    def history(connector_id: uuid.UUID) -> list[dict]:
        return list(TenantSettingChange.objects.filter(
            connector_id=connector_id, setting__in=("security_defaults", "conditional_access")).values(
            "id", "setting", "changes", "ok", "detail", "performed_by", "create_at")[:30])
