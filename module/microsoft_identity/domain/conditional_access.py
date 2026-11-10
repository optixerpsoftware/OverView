"""Accès conditionnel : lecture lisible des politiques, comparaison avec une base recommandée, modèles.

Fonctions pures sur le JSON de Microsoft Graph (`/identity/conditionalAccess/policies`) : aucun appel réseau.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

# Rôles d'administration à protéger en priorité (ID de modèle de rôle, identiques dans tous les tenants).
PRIVILEGED_ROLES = {
    "62e90394-69f5-4237-9190-012177145e10": "Administrateur général",
    "e8611ab8-c189-46e8-94e1-60213ab1f814": "Administrateur de rôle privilégié",
    "194ae4cb-b126-40b2-bd5b-6091b380977d": "Administrateur de la sécurité",
    "29232cdf-9323-42fd-ade2-1d097af3e4de": "Administrateur Exchange",
    "f28a1f50-f6e7-4571-818b-6a12f2af6b6c": "Administrateur SharePoint",
    "fe930be7-5e62-47db-91af-98c3a49a38b1": "Administrateur des utilisateurs",
    "729827e3-9c14-49f7-bb1b-9608f156bbb8": "Administrateur du support technique",
    "b1be1c3e-b65d-4f19-8427-f6fa0d97feb9": "Administrateur de l'accès conditionnel",
    "9b895d92-2cd3-44c7-9d02-a6ac2d5ea5c3": "Administrateur d'applications",
    "158c047a-c907-4556-b7ef-446551a6b5f7": "Administrateur d'applications cloud",
    "c4e39bd9-1100-46d3-8c65-fb160da0071f": "Administrateur de l'authentification",
    "7be44c8a-adaf-4e2a-84d6-ab2649e08a13": "Administrateur de l'authentification privilégiée",
    "b0f54661-2d74-4c50-afa3-1ec803f12efe": "Administrateur de facturation",
    "17315797-102d-40b4-93e0-432062caca18": "Administrateur de la conformité",
}
AZURE_MANAGEMENT_APP = "797f4846-ba00-4fd7-ba43-dac1f8f63013"
WELL_KNOWN_APPS = {
    "All": "Toutes les applications cloud", "None": "Aucune application", "Office365": "Office 365",
    "MicrosoftAdminPortals": "Portails d'administration Microsoft", AZURE_MANAGEMENT_APP: "Gestion Microsoft Azure",
    "00000002-0000-0ff1-ce00-000000000000": "Exchange Online", "00000003-0000-0ff1-ce00-000000000000": "SharePoint Online",
}
STATE_LABEL = {"enabled": "Activée", "disabled": "Désactivée", "enabledForReportingButNotEnforced": "Rapport seulement"}
GRANT_LABEL = {"mfa": "exiger le MFA", "block": "bloquer l'accès", "compliantDevice": "exiger un appareil conforme",
               "domainJoinedDevice": "exiger un appareil joint (hybride)", "approvedApplication": "exiger une app approuvée",
               "compliantApplication": "exiger une app protégée", "passwordChange": "exiger un changement de mot de passe"}
CLIENT_APP_LABEL = {"all": "tous les clients", "browser": "navigateur", "mobileAppsAndDesktopClients": "applis mobiles et bureau",
                    "exchangeActiveSync": "Exchange ActiveSync", "other": "autres clients (hérités : IMAP, POP, SMTP…)"}
LEGACY_CLIENTS = {"exchangeActiveSync", "other"}


def license_tier(skus: Iterable[dict]) -> str:
    """'p2', 'p1' ou 'none' selon les plans de service Entra ID présents dans les abonnements."""
    plans = {p.get("servicePlanName", "") for s in skus for p in (s.get("servicePlans") or [])
             if p.get("provisioningStatus", "Success") != "Disabled"}
    if "AAD_PREMIUM_P2" in plans:
        return "p2"
    if "AAD_PREMIUM" in plans:
        return "p1"
    return "none"


# ---------------------------------------------------------------- lecture d'une politique
def _c(p: dict) -> dict:
    return p.get("conditions") or {}


def _list(d: Optional[dict], key: str) -> list[str]:
    return list((d or {}).get(key) or [])


def referenced_ids(policies: Iterable[dict]) -> dict[str, set[str]]:
    """Identifiants à traduire en noms : utilisateurs/groupes (getByIds), rôles, applications."""
    out = {"objects": set(), "roles": set(), "apps": set()}
    for p in policies:
        u = _c(p).get("users") or {}
        for k in ("includeUsers", "excludeUsers", "includeGroups", "excludeGroups"):
            out["objects"] |= {i for i in _list(u, k) if i not in ("All", "None", "GuestsOrExternalUsers")}
        for k in ("includeRoles", "excludeRoles"):
            out["roles"] |= set(_list(u, k))
        a = _c(p).get("applications") or {}
        for k in ("includeApplications", "excludeApplications"):
            out["apps"] |= {i for i in _list(a, k) if i not in WELL_KNOWN_APPS}
    return out


def _names(ids: Iterable[str], names: dict[str, str]) -> list[str]:
    special = {"All": "tous les utilisateurs", "None": "personne", "GuestsOrExternalUsers": "invités et externes"}
    return [special.get(i) or names.get(i) or PRIVILEGED_ROLES.get(i) or WELL_KNOWN_APPS.get(i) or i for i in ids]


def describe(p: dict, names: dict[str, str]) -> dict:
    c = _c(p)
    u = c.get("users") or {}
    a = c.get("applications") or {}
    grant = p.get("grantControls") or {}
    session = p.get("sessionControls") or {}
    include = _names(_list(u, "includeUsers") + _list(u, "includeGroups") + _list(u, "includeRoles"), names)
    exclude = _names(_list(u, "excludeUsers") + _list(u, "excludeGroups") + _list(u, "excludeRoles"), names)
    conditions = []
    if _list(c, "clientAppTypes") and _list(c, "clientAppTypes") != ["all"]:
        conditions.append("Clients : " + ", ".join(CLIENT_APP_LABEL.get(x, x) for x in _list(c, "clientAppTypes")))
    plat = c.get("platforms") or {}
    if _list(plat, "includePlatforms"):
        conditions.append("Plateformes : " + ", ".join(_list(plat, "includePlatforms"))
                          + (f" (sauf {', '.join(_list(plat, 'excludePlatforms'))})" if _list(plat, "excludePlatforms") else ""))
    loc = c.get("locations") or {}
    if _list(loc, "includeLocations"):
        conditions.append("Emplacements : " + ", ".join(_names(_list(loc, "includeLocations"), names))
                          + (f" (sauf {', '.join(_names(_list(loc, 'excludeLocations'), names))})" if _list(loc, "excludeLocations") else ""))
    if _list(c, "signInRiskLevels"):
        conditions.append("Risque de connexion : " + ", ".join(_list(c, "signInRiskLevels")))
    if _list(c, "userRiskLevels"):
        conditions.append("Risque utilisateur : " + ", ".join(_list(c, "userRiskLevels")))
    controls = [GRANT_LABEL.get(g, g) for g in _list(grant, "builtInControls")]
    if grant.get("authenticationStrength"):
        controls.append(f"force d'authentification « {(grant['authenticationStrength'] or {}).get('displayName', '?')} »")
    op = " ou " if grant.get("operator") == "OR" else " et "
    sess = []
    if (session.get("signInFrequency") or {}).get("isEnabled"):
        f = session["signInFrequency"]
        sess.append(f"reconnexion toutes les {f.get('value')} {'h' if f.get('type') == 'hours' else 'j'}")
    if (session.get("persistentBrowser") or {}).get("isEnabled"):
        sess.append("session de navigateur " + ("persistante" if session["persistentBrowser"].get("mode") == "always" else "non persistante"))
    flags = []
    if p.get("state") == "enabledForReportingButNotEnforced":
        flags.append(("info", "Mode rapport seulement : la politique n'est pas appliquée."))
    blocks = "block" in _list(grant, "builtInControls")
    if p.get("state") == "enabled" and "All" in _list(u, "includeUsers") and not exclude:
        flags.append(("warn", "Aucune exclusion : prévois un compte d'urgence exclu, sinon risque de verrouiller tous les admins."))
    if len(exclude) >= 5:
        flags.append(("warn", f"{len(exclude)} exclusions : vérifie qu'elles sont toutes justifiées."))
    return {
        "id": p.get("id"), "name": p.get("displayName", "?"), "state": p.get("state"),
        "state_label": STATE_LABEL.get(p.get("state"), p.get("state")),
        "modified": p.get("modifiedDateTime") or p.get("createdDateTime"),
        "include": include, "exclude": exclude,
        "apps": _names(_list(a, "includeApplications"), names) or ([f"actions : {', '.join(_list(a, 'includeUserActions'))}"] if _list(a, "includeUserActions") else []),
        "apps_excluded": _names(_list(a, "excludeApplications"), names),
        "conditions": conditions,
        "grant": ("Bloquer l'accès" if blocks else (_upper_first(op.join(controls)) if controls else "Accorder l'accès")),
        "session": sess, "flags": [{"level": lv, "text": t} for lv, t in flags],
    }


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:]


# ---------------------------------------------------------------- base recommandée
@dataclass(frozen=True)
class BaselineItem:
    code: str
    title: str
    why: str
    status: str                     # covered | report_only | missing | not_available
    detail: str
    policies: tuple[str, ...] = ()
    template: Optional[str] = None  # modèle proposé pour combler le manque
    requires: str = "p1"


def _enabled(ps):
    return [p for p in ps if p.get("state") == "enabled"]


def _report(ps):
    return [p for p in ps if p.get("state") == "enabledForReportingButNotEnforced"]


def _requires_mfa(p: dict) -> bool:
    g = p.get("grantControls") or {}
    return "mfa" in _list(g, "builtInControls") or bool(g.get("authenticationStrength"))


def _all_users(p: dict) -> bool:
    return "All" in _list(_c(p).get("users"), "includeUsers")


def _all_apps(p: dict) -> bool:
    return "All" in _list(_c(p).get("applications"), "includeApplications")


def _match(ps, pred):
    return [p for p in ps if pred(p)]


def baseline(policies: list[dict], security_defaults: bool, tier: str) -> list[BaselineItem]:
    checks = [
        ("mfa_admins", "MFA pour les administrateurs",
         "Un compte admin compromis donne les clés du tenant.",
         lambda p: _requires_mfa(p) and _all_apps(p) and (_all_users(p) or bool(set(_list(_c(p).get("users"), "includeRoles")) & {"62e90394-69f5-4237-9190-012177145e10"}))),
        ("mfa_all", "MFA pour tous les utilisateurs",
         "Le MFA bloque l'immense majorité des prises de contrôle de compte par mot de passe.",
         lambda p: _requires_mfa(p) and _all_users(p) and _all_apps(p)),
        ("block_legacy", "Bloquer l'authentification héritée",
         "IMAP, POP, SMTP authentifié et ActiveSync ne savent pas faire de MFA : ils le contournent.",
         lambda p: "block" in _list(p.get("grantControls"), "builtInControls") and LEGACY_CLIENTS <= set(_list(_c(p), "clientAppTypes"))),
        ("mfa_azure", "MFA pour la gestion Azure",
         "Portail Azure, PowerShell et CLI permettent de modifier toute l'infrastructure.",
         lambda p: _requires_mfa(p) and (_all_apps(p) or AZURE_MANAGEMENT_APP in _list(_c(p).get("applications"), "includeApplications"))),
    ]
    out: list[BaselineItem] = []
    for code, title, why, pred in checks:
        on, rep = _match(_enabled(policies), pred), _match(_report(policies), pred)
        if on:
            out.append(BaselineItem(code, title, why, "covered", "Couvert par une politique active.",
                                    tuple(p.get("displayName", "?") for p in on)))
        elif security_defaults:
            out.append(BaselineItem(code, title, why, "covered", "Couvert par Security Defaults."))
        elif rep:
            out.append(BaselineItem(code, title, why, "report_only", "Politique présente mais en mode rapport seulement.",
                                    tuple(p.get("displayName", "?") for p in rep), code))
        elif tier == "none":
            out.append(BaselineItem(code, title, why, "missing",
                                    "Non couvert. Sans licence Entra ID P1, active Security Defaults.", (), None, "none"))
        else:
            out.append(BaselineItem(code, title, why, "missing", "Aucune politique ne couvre ce point.", (), code))
    risk = _match(_enabled(policies), lambda p: bool(_list(_c(p), "signInRiskLevels") or _list(_c(p), "userRiskLevels")))
    if tier != "p2":
        out.append(BaselineItem("risk", "Politiques basées sur le risque", "Exige le MFA ou bloque quand Microsoft détecte une connexion à risque.",
                                "not_available", "Nécessite Entra ID P2 (Identity Protection).", (), None, "p2"))
    else:
        out.append(BaselineItem("risk", "Politiques basées sur le risque", "Exige le MFA ou bloque quand Microsoft détecte une connexion à risque.",
                                "covered" if risk else "missing", "Couvert." if risk else "Aucune politique de risque.",
                                tuple(p.get("displayName", "?") for p in risk), None if risk else "risk_signin", "p2"))
    return out


def verdict(security_defaults: bool, tier: str, policies: list[dict]) -> dict:
    """Recommandation globale lisible."""
    active = _enabled(policies)
    if security_defaults and active:
        return {"level": "warn", "text": "Security Defaults et accès conditionnel coexistent : Microsoft ignore l'un des deux. "
                                         "Garde l'accès conditionnel et désactive Security Defaults."}
    if security_defaults:
        if tier == "none":
            return {"level": "ok", "text": "Protection de base assurée par Security Defaults. Sans licence Entra ID P1, c'est la "
                                           "bonne configuration : garde-le activé."}
        return {"level": "info", "text": "Security Defaults protège le tenant. Avec ta licence Entra ID P1, l'accès conditionnel "
                                         "permet des règles plus fines (exclusions, emplacements, appareils)."}
    if active:
        return {"level": "ok", "text": f"{len(active)} politique(s) d'accès conditionnel active(s). Vérifie la base recommandée ci-dessous."}
    if tier == "none":
        return {"level": "danger", "text": "Aucune protection : ni Security Defaults ni accès conditionnel. Active Security Defaults."}
    return {"level": "danger", "text": "Aucune politique active et Security Defaults désactivé : crée les politiques recommandées."}


# ---------------------------------------------------------------- modèles (créés en « rapport seulement »)
TEMPLATES = {
    "mfa_admins": ("OverView — MFA pour les administrateurs", {
        "users": {"includeRoles": list(PRIVILEGED_ROLES)}, "applications": {"includeApplications": ["All"]},
        "clientAppTypes": ["all"]}, {"operator": "OR", "builtInControls": ["mfa"]}),
    "mfa_all": ("OverView — MFA pour tous les utilisateurs", {
        "users": {"includeUsers": ["All"]}, "applications": {"includeApplications": ["All"]},
        "clientAppTypes": ["all"]}, {"operator": "OR", "builtInControls": ["mfa"]}),
    "block_legacy": ("OverView — Bloquer l'authentification héritée", {
        "users": {"includeUsers": ["All"]}, "applications": {"includeApplications": ["All"]},
        "clientAppTypes": ["exchangeActiveSync", "other"]}, {"operator": "OR", "builtInControls": ["block"]}),
    "mfa_azure": ("OverView — MFA pour la gestion Azure", {
        "users": {"includeUsers": ["All"]}, "applications": {"includeApplications": [AZURE_MANAGEMENT_APP]},
        "clientAppTypes": ["all"]}, {"operator": "OR", "builtInControls": ["mfa"]}),
}


def template_body(code: str, exclude_user_ids: Iterable[str] = ()) -> dict:
    """Corps Graph d'une politique recommandée, TOUJOURS en mode « rapport seulement » : elle n'a aucun effet tant
    qu'un administrateur ne l'a pas activée après avoir vérifié son impact. Le compte d'urgence fourni est exclu."""
    if code not in TEMPLATES:
        raise ValueError("Modèle inconnu.")
    name, conditions, grant = TEMPLATES[code]
    cond = {k: (dict(v) if isinstance(v, dict) else v) for k, v in conditions.items()}
    excl = [i for i in exclude_user_ids if i]
    if excl:
        cond["users"] = {**cond["users"], "excludeUsers": excl}
    return {"displayName": name, "state": "enabledForReportingButNotEnforced", "conditions": cond, "grantControls": grant}


__all__ = ["license_tier", "referenced_ids", "describe", "baseline", "verdict", "template_body", "TEMPLATES",
           "BaselineItem", "PRIVILEGED_ROLES", "field"]
