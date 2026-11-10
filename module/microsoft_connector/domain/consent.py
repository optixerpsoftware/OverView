"""Connexion d'un tenant par consentement administrateur Microsoft (application multi-tenant).

Au lieu de créer une inscription d'application dans chaque tenant et de recopier ID + secret, l'administrateur
du client clique sur « Connecter Microsoft 365 », se connecte chez Microsoft et accepte les permissions de
l'application OverView. Microsoft redirige ensuite vers notre callback avec l'ID du tenant qui a consenti.
Fonctions pures : construction de l'URL et lecture du retour, sans réseau ni base.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.parse import urlencode

LOGIN = "https://login.microsoftonline.com"
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def admin_consent_url(client_id: str, redirect_uri: str, state: str, tenant_hint: str = "organizations") -> str:
    """URL du consentement administrateur (point de terminaison v2.0) : accorde en une fois toutes les permissions
    d'application déclarées dans l'inscription de l'application (scope `.default`)."""
    hint = (tenant_hint or "").strip().lower()
    authority = hint if (_GUID.match(hint) or _DOMAIN.match(hint)) else "organizations"
    q = urlencode({"client_id": client_id, "scope": "https://graph.microsoft.com/.default",
                   "redirect_uri": redirect_uri, "state": state})
    return f"{LOGIN}/{authority}/v2.0/adminconsent?{q}"


@dataclass(frozen=True)
class ConsentResult:
    ok: bool
    tenant_id: Optional[str]
    state: str
    error: str = ""


def parse_callback(params: Mapping[str, str]) -> ConsentResult:
    state = params.get("state", "") or ""
    if params.get("error"):
        desc = (params.get("error_description") or params["error"]).split("\r\n")[0].split("\n")[0]
        return ConsentResult(False, None, state, desc[:300])
    tenant = params.get("tenant", "") or ""
    if not _GUID.match(tenant):
        return ConsentResult(False, None, state, "Réponse de Microsoft sans identifiant de tenant valide.")
    if (params.get("admin_consent") or "").lower() != "true":
        return ConsentResult(False, tenant, state, "Le consentement administrateur n'a pas été accordé.")
    return ConsentResult(True, tenant.lower(), state)
