"""Comptes OverView : rôles, droits par route, vérification TOTP. Fonctions pures."""
from __future__ import annotations

import re
from typing import Optional

from shared_kernel.totp import totp_code

ROLES = ("client", "viewer", "operator", "admin")
ROLE_LABEL = {"client": "Client (portail)", "viewer": "Lecture seule", "operator": "Technicien", "admin": "Administrateur"}
_RANK = {r: i for i, r in enumerate(ROLES)}

# Portail client : lecture seule d'un sous-ensemble de pages (sa propre organisation, filtrée par la portée).
CLIENT_READS = [re.compile(p) for p in (
    r"^/api/organizations/?$",
    r"^/api/organizations/[0-9a-f-]{36}/?$",
    r"^/api/insights/dashboard/$",
    r"^/api/insights/alerts/$",
    r"^/api/insights/alerts/counts/$",
    r"^/api/insights/organizations/[0-9a-f-]{36}/report/$",
)]
SAFE = {"GET", "HEAD", "OPTIONS"}

# Routes toujours permises à un compte connecté (son propre compte et son MFA), même en lecture seule
# et même avant d'avoir activé le MFA quand il est obligatoire.
SELF_SERVICE = re.compile(r"^/api/accounts/me(/|$)")

# Écritures réservées aux administrateurs : réglages de tenant/organisation et gestion des comptes OverView.
ADMIN_WRITES = [re.compile(p) for p in (
    r"^/api/organizations(/|$)",
    r"^/api/connectors/?$",
    r"^/api/connectors/consent/",
    r"^/api/connectors/[0-9a-f-]{36}/?$",
    r"^/api/identity/connectors/[^/]+/conditional-access/",
    r"^/api/identity/connectors/[^/]+/device-registration/",
    r"^/api/identity/connectors/[^/]+/security-groups/",
    r"^/api/protection/organizations/[^/]+/?$",
    r"^/api/insights/organizations/[^/]+/notifications/",
    r"^/api/accounts/users",
)]


def at_least(role: str, minimum: str) -> bool:
    return _RANK.get(role, -1) >= _RANK.get(minimum, 99)


def required_role(method: str, path: str) -> str:
    """Rôle minimal pour appeler `method path` sur l'API."""
    if SELF_SERVICE.match(path):
        return "client"
    if method.upper() in SAFE:
        if path.startswith("/api/accounts/users"):
            return "admin"
        return "client" if any(p.match(path) for p in CLIENT_READS) else "viewer"
    if any(p.match(path) for p in ADMIN_WRITES):
        return "admin"
    return "operator"


def allowed(role: str, method: str, path: str) -> bool:
    return at_least(role, required_role(method, path))


def verify_totp(secret: str, code: str, now: float, last_counter: Optional[int] = None, window: int = 1) -> Optional[int]:
    """Renvoie le compteur (pas de 30 s) du code s'il est valide (± `window` pas de tolérance d'horloge) et plus récent
    que le dernier code accepté (anti-rejeu) ; sinon None."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6) or not secret:
        return None
    base = int(now // 30)
    for counter in range(base - window, base + window + 1):
        if last_counter is not None and counter <= last_counter:
            continue
        if totp_code(secret, at=counter * 30) == code:
            return counter
    return None
