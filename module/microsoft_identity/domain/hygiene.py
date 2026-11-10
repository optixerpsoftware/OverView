"""Hygiène des identités : comptes inactifs, invités, administrateurs, applications tierces autorisées.

Fonctions pures sur le JSON de Microsoft Graph : aucun appel réseau.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

INACTIVE_DAYS = 90
PENDING_GUEST_DAYS = 30
MICROSOFT_TENANTS = {"f8cdef31-a31e-4b4a-93e4-5f571e91255a", "72f988bf-86f1-41af-91ab-2d7cd011db47"}
GLOBAL_ADMIN = "Global Administrator"
PRIVILEGED = {
    "Global Administrator", "Privileged Role Administrator", "Security Administrator", "Exchange Administrator",
    "SharePoint Administrator", "User Administrator", "Helpdesk Administrator", "Conditional Access Administrator",
    "Application Administrator", "Cloud Application Administrator", "Authentication Administrator",
    "Privileged Authentication Administrator", "Billing Administrator", "Compliance Administrator",
    "Intune Administrator", "Teams Administrator",
}
ROLE_FR = {
    "Global Administrator": "Administrateur général", "Privileged Role Administrator": "Admin. des rôles privilégiés",
    "Security Administrator": "Admin. de la sécurité", "Exchange Administrator": "Admin. Exchange",
    "SharePoint Administrator": "Admin. SharePoint", "User Administrator": "Admin. des utilisateurs",
    "Helpdesk Administrator": "Admin. du support", "Conditional Access Administrator": "Admin. de l'accès conditionnel",
    "Application Administrator": "Admin. d'applications", "Cloud Application Administrator": "Admin. d'applications cloud",
    "Authentication Administrator": "Admin. de l'authentification", "Billing Administrator": "Admin. de facturation",
    "Privileged Authentication Administrator": "Admin. de l'authentification privilégiée",
    "Compliance Administrator": "Admin. de la conformité", "Intune Administrator": "Admin. Intune",
    "Teams Administrator": "Admin. Teams",
}
# Permissions qui donnent accès au courrier, aux fichiers ou à l'annuaire de toute l'organisation.
HIGH_RISK = {
    "Mail.Read", "Mail.ReadWrite", "Mail.Send", "MailboxSettings.ReadWrite", "full_access_as_app", "EWS.AccessAsUser.All",
    "Files.Read.All", "Files.ReadWrite.All", "Sites.Read.All", "Sites.ReadWrite.All", "Sites.FullControl.All",
    "Directory.ReadWrite.All", "Directory.AccessAsUser.All", "User.ReadWrite.All", "Group.ReadWrite.All",
    "RoleManagement.ReadWrite.Directory", "AppRoleAssignment.ReadWrite.All", "Application.ReadWrite.All",
    "Policy.ReadWrite.ConditionalAccess", "Calendars.ReadWrite", "Contacts.ReadWrite", "Notes.ReadWrite.All",
    "Chat.Read.All", "ChannelMessage.Read.All", "IMAP.AccessAsUser.All", "POP.AccessAsUser.All", "SMTP.Send",
}
LOW_RISK = {"openid", "profile", "email", "offline_access", "User.Read"}


def parse_dt(v) -> Optional[datetime]:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def last_activity(u: dict) -> Optional[datetime]:
    act = u.get("signInActivity") or {}
    return max(filter(None, (parse_dt(act.get("lastSignInDateTime")),
                             parse_dt(act.get("lastNonInteractiveSignInDateTime")),
                             parse_dt(act.get("lastSuccessfulSignInDateTime")))), default=None)


def _row(u: dict, last: Optional[datetime]) -> dict:
    return {"id": u.get("id"), "upn": u.get("userPrincipalName") or "", "name": u.get("displayName") or "",
            "type": u.get("userType") or "Member", "enabled": u.get("accountEnabled", True),
            "created": u.get("createdDateTime"), "last_activity": last.isoformat() if last else None,
            "licensed": bool(u.get("assignedLicenses"))}


def inactive_accounts(users: Iterable[dict], now: datetime, days: int = INACTIVE_DAYS) -> Optional[list[dict]]:
    """Comptes membres activés sans activité depuis `days` jours. None si l'activité est inconnue pour tous."""
    users = [u for u in users if u.get("userType", "Member") == "Member"]
    if not any("signInActivity" in u for u in users):
        return None
    out = []
    for u in users:
        if not u.get("accountEnabled", True):
            continue
        last = last_activity(u)
        created = parse_dt(u.get("createdDateTime"))
        if last is None and created and now - created > timedelta(days=days):
            out.append({**_row(u, None), "reason": "Jamais utilisé"})
        elif last and now - last > timedelta(days=days):
            out.append({**_row(u, last), "reason": f"Inactif depuis {(now - last).days} j"})
    return sorted(out, key=lambda r: r["last_activity"] or "")


def guests(users: Iterable[dict], now: datetime) -> list[dict]:
    out = []
    for u in users:
        if u.get("userType") != "Guest":
            continue
        last = last_activity(u)
        state = u.get("externalUserState") or ""
        changed = parse_dt(u.get("externalUserStateChangeDateTime")) or parse_dt(u.get("createdDateTime"))
        flags = []
        if state == "PendingAcceptance" and changed and now - changed > timedelta(days=PENDING_GUEST_DAYS):
            flags.append(f"Invitation jamais acceptée ({(now - changed).days} j)")
        if last and now - last > timedelta(days=INACTIVE_DAYS):
            flags.append(f"Inactif depuis {(now - last).days} j")
        out.append({**_row(u, last), "state": state, "mail": u.get("mail") or "", "flags": flags})
    return sorted(out, key=lambda g: (not g["flags"], g["upn"].lower()))


def admins(roles: Iterable[dict], registrations: Iterable[dict]) -> dict:
    """Membres des rôles privilégiés, avec leur MFA. `roles` : [{displayName, members:[{userPrincipalName, type}]}]."""
    mfa = {(r.get("userPrincipalName") or "").lower(): bool(r.get("isMfaRegistered")) for r in registrations}
    people: dict[str, dict] = {}
    for role in roles:
        name = role.get("displayName") or ""
        if name not in PRIVILEGED:
            continue
        for m in role.get("members") or []:
            upn = (m.get("userPrincipalName") or m.get("displayName") or "?")
            key = upn.lower()
            p = people.setdefault(key, {"upn": upn, "name": m.get("displayName") or "", "roles": [],
                                        "is_user": (m.get("type") or "").endswith("user"),
                                        "guest": "#EXT#" in upn, "mfa": mfa.get(key)})
            p["roles"].append(ROLE_FR.get(name, name))
    rows = sorted(people.values(), key=lambda p: (ROLE_FR[GLOBAL_ADMIN] not in p["roles"], p["upn"].lower()))
    for p in rows:
        p["flags"] = ([f"Sans MFA"] if p["is_user"] and p["mfa"] is False else []) + (["Compte invité"] if p["guest"] else [])
    n_global = sum(1 for p in rows if ROLE_FR[GLOBAL_ADMIN] in p["roles"])
    notes = []
    if n_global < 2:
        notes.append(("warn", f"{n_global} administrateur général : prévois au moins 2 comptes (dont un compte d'urgence)."))
    elif n_global > 4:
        notes.append(("warn", f"{n_global} administrateurs généraux : Microsoft recommande d'en garder moins de 5."))
    return {"people": rows, "global_admins": n_global, "notes": [{"level": lv, "text": t} for lv, t in notes]}


# ---------------------------------------------------------------- applications tierces
def is_third_party(sp: dict, own_tenant: str) -> bool:
    owner = (sp.get("appOwnerOrganizationId") or "").lower()
    return bool(owner) and owner not in MICROSOFT_TENANTS and owner != own_tenant.lower() \
        and sp.get("servicePrincipalType", "Application") == "Application"


def app_consents(service_principals: list[dict], grants: list[dict], assignments: dict[str, list[dict]],
                 role_names: dict[tuple[str, str], str], resource_names: dict[str, str], own_tenant: str,
                 user_names: Optional[dict[str, str]] = None) -> list[dict]:
    """Applications tierces et ce qu'elles peuvent faire.

    `grants` : oauth2PermissionGrants (permissions déléguées) ; `assignments` : appRoleAssignments par ID de principal
    de service (permissions d'application) ; `role_names` : (resourceId, appRoleId) → valeur (ex. Mail.Read)."""
    user_names = user_names or {}
    by_id = {sp["id"]: sp for sp in service_principals}
    apps: dict[str, dict] = {}

    def entry(sp):
        return apps.setdefault(sp["id"], {
            "id": sp["id"], "app_id": sp.get("appId"), "name": sp.get("displayName") or sp.get("appId"),
            "publisher": (sp.get("verifiedPublisher") or {}).get("displayName") or sp.get("publisherName") or "",
            "verified": bool((sp.get("verifiedPublisher") or {}).get("verifiedPublisherId")),
            "enabled": sp.get("accountEnabled", True), "created": sp.get("createdDateTime"),
            "delegated": [], "application": [], "risk": "low", "high_risk_scopes": []})

    for g in grants:
        sp = by_id.get(g.get("clientId"))
        if not sp or not is_third_party(sp, own_tenant):
            continue
        scopes = [s for s in (g.get("scope") or "").split() if s]
        who = "tous les utilisateurs (consentement admin)" if g.get("consentType") == "AllPrincipals" \
            else user_names.get(g.get("principalId"), "un utilisateur")
        entry(sp)["delegated"].append({"grant_id": g.get("id"), "resource": resource_names.get(g.get("resourceId"), "?"),
                                       "scopes": scopes, "who": who, "all_users": g.get("consentType") == "AllPrincipals"})
    for sp_id, rows in assignments.items():
        sp = by_id.get(sp_id)
        if not sp or not is_third_party(sp, own_tenant):
            continue
        for a in rows:
            perm = role_names.get((a.get("resourceId"), a.get("appRoleId")), a.get("appRoleId"))
            entry(sp)["application"].append({"assignment_id": a.get("id"), "resource": resource_names.get(a.get("resourceId"), a.get("resourceDisplayName") or "?"),
                                             "permission": perm})
    for app in apps.values():
        app_perms = {x["permission"] for x in app["application"]}
        deleg = {s for d in app["delegated"] for s in d["scopes"]}
        risky = sorted((app_perms | deleg) & HIGH_RISK)
        app["high_risk_scopes"] = risky
        if app_perms & HIGH_RISK:
            app["risk"] = "high"            # sans utilisateur : accès à TOUTE l'organisation
        elif risky and any(d["all_users"] for d in app["delegated"]):
            app["risk"] = "high"
        elif risky:
            app["risk"] = "medium"
        elif (deleg - LOW_RISK) or app_perms:
            app["risk"] = "low"
        else:
            app["risk"] = "info"
        if not app["verified"] and app["risk"] in ("medium", "high"):
            app["unverified_warning"] = True
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    return sorted(apps.values(), key=lambda a: (order[a["risk"]], a["name"].lower()))


def summary(inactive: Optional[list], guest_rows: list, admin_info: dict, apps: list) -> dict:
    return {
        "inactive": None if inactive is None else len(inactive),
        "guests": len(guest_rows), "guests_flagged": sum(1 for g in guest_rows if g["flags"]),
        "admins": len(admin_info["people"]), "global_admins": admin_info["global_admins"],
        "admins_no_mfa": sum(1 for p in admin_info["people"] if "Sans MFA" in p["flags"]),
        "apps": len(apps), "apps_high": sum(1 for a in apps if a["risk"] == "high"),
    }
