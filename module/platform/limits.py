"""Abonnement d'un MSP : forfaits, limites (tenants Microsoft 365, organisations clientes, comptes OverView), expiration.

Les limites sont vérifiées au moment de chaque création, dans le schéma du MSP courant. `None` = illimité.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

PLANS = {
    "essentiel": {"label": "Essentiel", "max_tenants": 3, "max_organizations": 3, "max_users": 3},
    "pro": {"label": "Pro", "max_tenants": 15, "max_organizations": 15, "max_users": 10},
    "entreprise": {"label": "Entreprise", "max_tenants": None, "max_organizations": None, "max_users": None},
    "personnalise": {"label": "Personnalisé"},
}
KINDS = {
    "tenants": ("max_tenants", "tenants Microsoft 365"),
    "organizations": ("max_organizations", "organisations clientes"),
    "users": ("max_users", "comptes OverView"),
}


class QuotaExceeded(ValueError):
    """Limite de l'abonnement atteinte (sous-classe de ValueError : les vues existantes renvoient déjà 400 + message)."""


def is_expired(expires_at: Optional[date], today: Optional[date] = None) -> bool:
    return bool(expires_at) and (today or date.today()) > expires_at


def usage(schema: str) -> dict:
    """Consommation actuelle d'un MSP (lue dans son schéma)."""
    from django.contrib.auth import get_user_model
    from module.microsoft_connector.infrastructure.models import MicrosoftConnector
    from module.organization.infrastructure.models import Organization
    from shared_kernel.tenancy import use_schema
    with use_schema(schema):
        return {"tenants": MicrosoftConnector.objects.count(), "organizations": Organization.objects.count(),
                "users": get_user_model().objects.filter(is_active=True).count()}


def _current_msp():
    from module.platform.models import Msp
    from shared_kernel.tenancy import PUBLIC, current_schema, use_schema
    schema = current_schema()
    if schema == PUBLIC:
        return None
    with use_schema(PUBLIC):
        return Msp.objects.filter(schema_name=schema).first()


def check(kind: str, adding: int = 1) -> None:
    """Lève QuotaExceeded si ajouter `adding` élément(s) dépasse la limite du MSP courant."""
    field, label = KINDS[kind]
    msp = _current_msp()
    if msp is None:
        return
    limit = getattr(msp, field)
    if limit is None:
        return
    used = usage(msp.schema_name)[kind]
    if used + adding > limit:
        raise QuotaExceeded(f"Limite de votre abonnement atteinte : {used}/{limit} {label}. "
                            f"Contactez l'éditeur d'OverView pour augmenter votre forfait.")


def subscription() -> dict | None:
    """Abonnement du MSP courant, vu par le MSP lui-même : forfait, limites, consommation, échéance et alertes.
    Les notes internes de l'éditeur ne sont pas exposées."""
    from module.platform.console import _subscription_alerts
    msp = _current_msp()
    if msp is None:
        return None
    out = {"name": msp.name, "plan": msp.plan, "plan_label": PLANS.get(msp.plan, {}).get("label", msp.plan),
           "limits": {k: getattr(msp, f) for k, (f, _) in KINDS.items()},
           "usage": usage(msp.schema_name), "expires_at": msp.expires_at, "expired": is_expired(msp.expires_at)}
    out["remaining"] = {k: (None if out["limits"][k] is None else max(0, out["limits"][k] - out["usage"][k])) for k in KINDS}
    out["alerts"] = _subscription_alerts(out)
    return out
