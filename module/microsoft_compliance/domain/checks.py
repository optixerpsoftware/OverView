"""Contrôles de gouvernance : fonctions pures sur des données Graph brutes (dict JSON).

Aucun appel réseau ici : chaque contrôle reçoit les données déjà récupérées et renvoie un
`Finding`. C'est ce qui les rend testables sans Microsoft 365.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from module.microsoft_compliance.domain.entities import Category, Finding, Severity, Status

STALE_DAYS = 90
SECRET_WARN_DAYS = 30
MAX_DETAILS = 25
WEAK_METHODS = {"mobilePhone", "alternateMobilePhone", "officePhone", "email"}
GLOBAL_ADMIN = "Global Administrator"
PRIVILEGED_ROLES = {
    "Global Administrator", "Privileged Role Administrator", "Privileged Authentication Administrator",
    "Security Administrator", "Exchange Administrator", "SharePoint Administrator",
    "User Administrator", "Application Administrator", "Cloud Application Administrator",
    "Authentication Administrator", "Conditional Access Administrator", "Intune Administrator",
}


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _cap(lines: list[str]) -> tuple[str, ...]:
    extra = len(lines) - MAX_DETAILS
    out = lines[:MAX_DETAILS]
    if extra > 0:
        out.append(f"… et {extra} autre(s)")
    return tuple(out)


def _label(u: dict) -> str:
    return u.get("userPrincipalName") or u.get("displayName") or u.get("id", "?")


def _active_members(users: Iterable[dict]) -> list[dict]:
    return [u for u in users if u.get("userType", "Member") == "Member" and u.get("accountEnabled", True)]


# ---------------------------------------------------------------- MFA
def mfa_coverage(users: list[dict], regs: list[dict]) -> Finding:
    registered = {r.get("userPrincipalName", "").lower() for r in regs if r.get("isMfaRegistered")}
    members = _active_members(users)
    if not members:
        return Finding("mfa.coverage", Category.MFA, "Couverture MFA", Severity.HIGH, Status.UNAVAILABLE,
                       "Aucun utilisateur à évaluer.")
    missing = sorted(_label(u) for u in members if _label(u).lower() not in registered)
    score = 1 - len(missing) / len(members)
    ok = not missing
    return Finding(
        "mfa.coverage", Category.MFA, "Couverture MFA", Severity.HIGH,
        Status.PASS if ok else Status.FAIL,
        f"{len(members) - len(missing)} / {len(members)} utilisateurs actifs ont une méthode MFA.",
        _cap(missing), "" if ok else "Exiger l'inscription MFA (accès conditionnel ou Security Defaults).", score,
    )


def mfa_admins(regs: list[dict]) -> Finding:
    admins = [r for r in regs if r.get("isAdmin")]
    if not admins:
        return Finding("mfa.admins", Category.MFA, "MFA des comptes administrateurs", Severity.CRITICAL,
                       Status.PASS, "Aucun compte administrateur détecté dans le rapport d'inscription.")
    bad = sorted(r.get("userPrincipalName", "?") for r in admins if not r.get("isMfaRegistered"))
    ok = not bad
    return Finding(
        "mfa.admins", Category.MFA, "MFA des comptes administrateurs", Severity.CRITICAL,
        Status.PASS if ok else Status.FAIL,
        f"{len(admins) - len(bad)} / {len(admins)} administrateurs ont le MFA.",
        _cap(bad), "" if ok else "Un administrateur sans MFA expose tout le tenant : l'inscrire immédiatement.",
        1 - len(bad) / len(admins),
    )


def mfa_weak_methods(regs: list[dict]) -> Finding:
    reg = [r for r in regs if r.get("isMfaRegistered")]
    if not reg:
        return Finding("mfa.weak", Category.MFA, "Méthodes MFA faibles", Severity.MEDIUM, Status.UNAVAILABLE,
                       "Aucun utilisateur avec MFA à évaluer.")
    weak = sorted(
        r.get("userPrincipalName", "?") for r in reg
        if r.get("methodsRegistered") and set(r["methodsRegistered"]) <= WEAK_METHODS
    )
    ok = not weak
    return Finding(
        "mfa.weak", Category.MFA, "Méthodes MFA faibles (SMS / appel)", Severity.MEDIUM,
        Status.PASS if ok else Status.FAIL,
        f"{len(weak)} utilisateur(s) n'ont que le SMS, l'appel vocal ou le courriel comme second facteur.",
        _cap(weak), "" if ok else "Passer à Microsoft Authenticator, une clé FIDO2 ou Windows Hello.",
        1 - len(weak) / len(reg),
    )


# ---------------------------------------------------------------- Accès / rôles
def global_admin_count(roles: list[dict]) -> Finding:
    ga = {m.get("userPrincipalName") or m.get("displayName", "?")
          for r in roles if r.get("displayName") == GLOBAL_ADMIN for m in r.get("members", [])}
    n = len(ga)
    title = "Nombre d'administrateurs généraux"
    if n < 2:
        return Finding("access.global_admins", Category.ACCESS, title, Severity.MEDIUM, Status.FAIL,
                       f"{n} administrateur général : aucun compte de secours.", tuple(sorted(ga)),
                       "Prévoir au moins 2 administrateurs généraux (dont un compte « brise-glace »).", 0.5 if n else 0.0)
    if n > 4:
        return Finding("access.global_admins", Category.ACCESS, title, Severity.HIGH, Status.FAIL,
                       f"{n} administrateurs généraux (recommandé : 2 à 4).", _cap(sorted(ga)),
                       "Réduire les privilèges : utiliser des rôles plus restreints.", 4 / n)
    return Finding("access.global_admins", Category.ACCESS, title, Severity.MEDIUM, Status.PASS,
                   f"{n} administrateurs généraux (recommandé : 2 à 4).", tuple(sorted(ga)))


def admin_guests(roles: list[dict]) -> Finding:
    bad = sorted({
        f"{m.get('userPrincipalName') or m.get('displayName', '?')} — {r.get('displayName')}"
        for r in roles if r.get("displayName") in PRIVILEGED_ROLES
        for m in r.get("members", []) if "#EXT#" in (m.get("userPrincipalName") or "")
    })
    ok = not bad
    return Finding("access.admin_guests", Category.ACCESS, "Invités avec un rôle privilégié", Severity.HIGH,
                   Status.PASS if ok else Status.FAIL,
                   "Aucun invité n'a de rôle privilégié." if ok else f"{len(bad)} invité(s) avec un rôle privilégié.",
                   _cap(bad), "" if ok else "Retirer les rôles privilégiés des comptes invités.",
                   1.0 if ok else 0.0)


def guests_overview(users: list[dict]) -> Finding:
    guests = [u for u in users if u.get("userType") == "Guest"]
    return Finding("access.guests", Category.ACCESS, "Comptes invités", Severity.INFO, Status.PASS,
                   f"{len(guests)} compte(s) invité(s).", _cap(sorted(_label(g) for g in guests)))


def stale_accounts(users: list[dict], now: Optional[datetime] = None) -> Finding:
    now = now or datetime.now(timezone.utc)
    title = f"Comptes inactifs depuis {STALE_DAYS} jours"
    if not any("signInActivity" in u for u in users):
        return Finding("access.stale", Category.ACCESS, title, Severity.MEDIUM, Status.UNAVAILABLE,
                       "Dernière connexion indisponible.",
                       requires="AuditLog.Read.All + licence Entra ID P1")
    stale, total = [], 0
    for u in users:
        if not u.get("accountEnabled", True):
            continue
        total += 1
        act = u.get("signInActivity") or {}
        last = max(filter(None, (parse_dt(act.get("lastSignInDateTime")),
                                 parse_dt(act.get("lastNonInteractiveSignInDateTime")))), default=None)
        if last is None:
            created = parse_dt(u.get("createdDateTime"))
            if created and now - created > timedelta(days=STALE_DAYS):
                stale.append(f"{_label(u)} — jamais connecté")
        elif now - last > timedelta(days=STALE_DAYS):
            stale.append(f"{_label(u)} — dernière connexion {last.date()}")
    ok = not stale
    return Finding("access.stale", Category.ACCESS, title, Severity.MEDIUM,
                   Status.PASS if ok else Status.FAIL,
                   f"{len(stale)} compte(s) actif(s) sans connexion récente sur {total}.", _cap(sorted(stale)),
                   "" if ok else "Désactiver ou supprimer les comptes inutilisés.",
                   1 - len(stale) / total if total else 1.0)


# ---------------------------------------------------------------- Politiques
def _enabled(policies: Iterable[dict]) -> list[dict]:
    return [p for p in policies if p.get("state") == "enabled"]


def _grants(p: dict) -> set[str]:
    return set((p.get("grantControls") or {}).get("builtInControls") or [])


def _covers_all(p: dict) -> bool:
    c = p.get("conditions") or {}
    return ("All" in ((c.get("users") or {}).get("includeUsers") or [])
            and "All" in ((c.get("applications") or {}).get("includeApplications") or []))


def _unknown(code: str, title: str, severity: Severity) -> Finding:
    return Finding(code, Category.POLICIES, title, severity, Status.UNAVAILABLE,
                   "Accès conditionnel illisible (licence ou permission) et Security Defaults désactivé : "
                   "impossible de conclure.", requires="Policy.Read.All + licence Entra ID P1")


def baseline_protection(security_defaults: Optional[dict], policies: Optional[list[dict]]) -> Finding:
    title = "Protection de base de l'identité"
    sd = bool((security_defaults or {}).get("isEnabled"))
    if not sd and policies is None:
        return _unknown("policies.baseline", title, Severity.CRITICAL)
    active = _enabled(policies or [])
    if sd:
        return Finding("policies.baseline", Category.POLICIES, title, Severity.CRITICAL, Status.PASS,
                       "Security Defaults est activé.")
    if active:
        return Finding("policies.baseline", Category.POLICIES, title, Severity.CRITICAL, Status.PASS,
                       f"{len(active)} politique(s) d'accès conditionnel active(s).")
    return Finding("policies.baseline", Category.POLICIES, title, Severity.CRITICAL, Status.FAIL,
                   "Ni Security Defaults ni accès conditionnel : aucune protection de base.",
                   recommendation="Activer Security Defaults ou créer des politiques d'accès conditionnel.", score=0.0)


def ca_mfa_for_all(security_defaults: Optional[dict], policies: Optional[list[dict]]) -> Finding:
    title = "MFA exigé pour tous les utilisateurs"
    if (security_defaults or {}).get("isEnabled"):
        return Finding("policies.mfa_all", Category.POLICIES, title, Severity.HIGH, Status.PASS,
                       "Couvert par Security Defaults.")
    if policies is None:
        return _unknown("policies.mfa_all", title, Severity.HIGH)
    full = [p for p in _enabled(policies) if _covers_all(p) and ("mfa" in _grants(p) or
            (p.get("grantControls") or {}).get("authenticationStrength"))]
    if full:
        return Finding("policies.mfa_all", Category.POLICIES, title, Severity.HIGH, Status.PASS,
                       "Une politique exige le MFA pour tous les utilisateurs et toutes les applications.",
                       tuple(p.get("displayName", "?") for p in full))
    report = [p for p in policies if p.get("state") == "enabledForReportingButNotEnforced"
              and _covers_all(p) and "mfa" in _grants(p)]
    if report:
        return Finding("policies.mfa_all", Category.POLICIES, title, Severity.HIGH, Status.FAIL,
                       "La politique MFA existe mais est en mode « rapport seulement » (non appliquée).",
                       tuple(p.get("displayName", "?") for p in report),
                       "Passer la politique en mode activé après vérification des journaux.", 0.3)
    return Finding("policies.mfa_all", Category.POLICIES, title, Severity.HIGH, Status.FAIL,
                   "Aucune politique n'exige le MFA pour tous les utilisateurs.",
                   recommendation="Créer une politique d'accès conditionnel « MFA pour tous ».", score=0.0)


def ca_block_legacy(security_defaults: Optional[dict], policies: Optional[list[dict]]) -> Finding:
    title = "Authentification héritée bloquée"
    if (security_defaults or {}).get("isEnabled"):
        return Finding("policies.legacy_auth", Category.POLICIES, title, Severity.HIGH, Status.PASS,
                       "Couvert par Security Defaults.")
    if policies is None:
        return _unknown("policies.legacy_auth", title, Severity.HIGH)
    blocking = [
        p for p in _enabled(policies)
        if "block" in _grants(p)
        and {"exchangeActiveSync", "other"} <= set((p.get("conditions") or {}).get("clientAppTypes") or [])
    ]
    if blocking:
        return Finding("policies.legacy_auth", Category.POLICIES, title, Severity.HIGH, Status.PASS,
                       "Une politique bloque l'authentification héritée (IMAP, POP, SMTP, EAS…).",
                       tuple(p.get("displayName", "?") for p in blocking))
    return Finding("policies.legacy_auth", Category.POLICIES, title, Severity.HIGH, Status.FAIL,
                   "L'authentification héritée n'est pas bloquée : elle contourne le MFA.",
                   recommendation="Créer une politique bloquant les clients d'authentification héritée.", score=0.0)


# ---------------------------------------------------------------- Posture
def secure_score(score: Optional[dict], profiles: Optional[list[dict]] = None) -> Finding:
    title = "Secure Score Microsoft"
    if not score or not score.get("maxScore"):
        return Finding("posture.secure_score", Category.POSTURE, title, Severity.MEDIUM, Status.UNAVAILABLE,
                       "Aucun score disponible.", requires="SecurityEvents.Read.All")
    cur, mx = float(score.get("currentScore", 0)), float(score["maxScore"])
    pct = cur / mx
    meta = {p.get("id"): p for p in (profiles or [])}
    gaps = []
    for c in score.get("controlScores", []):
        p = meta.get(c.get("controlName"), {})
        gap = float(p.get("maxScore", 0) or 0) - float(c.get("score", 0) or 0)
        if gap > 0:
            gaps.append((gap, p.get("title") or c.get("controlName", "?")))
    gaps.sort(reverse=True)
    details = tuple(f"{t} (+{g:g} pts possibles)" for g, t in gaps[:8])
    ok = pct >= 0.7
    return Finding("posture.secure_score", Category.POSTURE, title, Severity.MEDIUM,
                   Status.PASS if ok else Status.FAIL,
                   f"{cur:g} / {mx:g} points ({pct * 100:.0f} %).", details,
                   "" if ok else "Appliquer les actions recommandées ci-dessus, par gain décroissant.", pct)


def app_secrets(apps: list[dict], now: Optional[datetime] = None) -> Finding:
    now = now or datetime.now(timezone.utc)
    title = "Secrets et certificats d'applications"
    bad, total = [], 0
    for a in apps:
        for kind, creds in (("secret", a.get("passwordCredentials") or []), ("certificat", a.get("keyCredentials") or [])):
            for c in creds:
                end = parse_dt(c.get("endDateTime"))
                if end is None:
                    continue
                total += 1
                days = (end - now).days
                name = c.get("displayName") or kind
                if days < 0:
                    bad.append((0, f"{a.get('displayName', '?')} — {name} : expiré depuis {-days} j"))
                elif days <= SECRET_WARN_DAYS:
                    bad.append((1, f"{a.get('displayName', '?')} — {name} : expire dans {days} j"))
    bad.sort()
    expired = sum(1 for k, _ in bad if k == 0)
    ok = not bad
    return Finding("posture.app_secrets", Category.POSTURE, title,
                   Severity.HIGH if expired else Severity.MEDIUM, Status.PASS if ok else Status.FAIL,
                   f"{len(bad)} identifiant(s) expiré(s) ou expirant sous {SECRET_WARN_DAYS} j sur {total}.",
                   _cap([m for _, m in bad]), "" if ok else "Renouveler les secrets avant leur expiration (sinon coupure de service).",
                   1 - len(bad) / total if total else 1.0)
