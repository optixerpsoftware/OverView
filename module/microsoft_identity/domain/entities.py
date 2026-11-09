from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    display_name: str
    operating_system: str
    operating_system_version: str
    trust_type: str
    is_compliant: Optional[bool]
    is_managed: Optional[bool]
    last_sign_in: Optional[datetime]
    registered_at: Optional[datetime]
    device_id: str = ""   # id.id = identifiant d'objet Entra (utilisé pour supprimer) ; ici deviceId


@dataclass(frozen=True)
class AuthMethodInfo:
    id: str
    kind: str          # authenticator, phone, email, fido2, hello, oath, tap, password
    label: str
    detail: str = ""   # ex. modèle du téléphone, numéro masqué
    is_mfa: bool = True


@dataclass(frozen=True)
class SignInInfo:
    at: Optional[datetime]
    app: str
    ip: str
    city: str
    country: str
    client: str
    os: str
    browser: str
    interactive: bool
    success: bool
    error_code: int
    id: str = ""          # identifiant Graph de la connexion (déduplication entre synchros)


@dataclass(frozen=True)
class ActiveSession:
    """Session probablement active, déduite des connexions réussies (estimation)."""
    app: str
    client: str
    ip: str
    city: str
    country: str
    os: str
    browser: str
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]
    count: int
    interactive: bool


@dataclass(frozen=True)
class UserSecurityProfile:
    user_id: str
    display_name: str
    account_enabled: Optional[bool]
    sessions_valid_from: Optional[datetime]
    last_password_change: Optional[datetime]


@dataclass
class Section:
    """Une section de la vue sécurité : données OU erreur (permission manquante, licence…)."""
    items: list = field(default_factory=list)
    error: str = ""


@dataclass(frozen=True)
class StepResult:
    step: str
    ok: bool
    detail: str = ""
