from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from module.microsoft_identity.domain.entities import (
    AuthMethodInfo, DeviceInfo, SignInInfo, UserSecurityProfile,
)


class IdentityGateway(ABC):
    """Port vers l'annuaire (Microsoft Entra ID via Graph)."""

    @abstractmethod
    async def get_profile(self, upn: str) -> UserSecurityProfile: ...

    @abstractmethod
    async def list_devices(self, upn: str) -> list[DeviceInfo]: ...

    @abstractmethod
    async def list_auth_methods(self, upn: str) -> list[AuthMethodInfo]: ...

    @abstractmethod
    async def list_sign_ins(self, user_id: str, top: int = 100, since: Optional[datetime] = None) -> list[SignInInfo]:
        """Connexions du compte ; `since` limite aux connexions créées à partir de cet instant (synchro incrémentale)."""

    @abstractmethod
    async def revoke_sessions(self, upn: str) -> None: ...

    @abstractmethod
    async def delete_auth_method(self, upn: str, kind: str, method_id: str) -> None: ...

    @abstractmethod
    async def add_phone_method(self, upn: str, phone_number: str, phone_type: str) -> None: ...

    @abstractmethod
    async def add_email_method(self, upn: str, email: str) -> None: ...

    @abstractmethod
    async def add_totp_method(self, upn: str, secret: str, serial: str, label: str) -> None:
        """Enregistre une application TOTP (jeton OATH) pour le compte, activée avec la clé `secret`."""

    @abstractmethod
    async def create_temporary_access_pass(self, upn: str, lifetime_minutes: int, once: bool) -> dict:
        """Renvoie {"pass": str, "lifetime_minutes": int, "once": bool}."""

    @abstractmethod
    async def delete_device(self, device_object_id: str) -> None: ...

    @abstractmethod
    async def set_password(self, upn: str, password: str, force_change: bool) -> None: ...

    # --- utilisées par la procédure d'urgence (facultatives) ---
    async def internal_domains(self) -> list[str]:
        raise NotImplementedError

    async def list_message_rules(self, upn: str) -> list[dict]:
        raise NotImplementedError

    async def delete_message_rule(self, upn: str, rule_id: str) -> None:
        raise NotImplementedError

    async def clear_mailbox_forwarding(self, upn: str) -> bool:
        raise NotImplementedError

    async def set_account_enabled(self, upn: str, enabled: bool) -> None:
        raise NotImplementedError


class SecuritySyncRepository(ABC):
    """Stockage local de l'état de sécurité d'un compte et de son historique de connexions."""

    @abstractmethod
    def get(self, mailbox_id) -> Optional[dict]: ...

    @abstractmethod
    def try_start(self, mailbox_id, org_id=None) -> bool:
        """Marque la synchro « en cours » si aucune ne tourne (ou si elle est périmée). Faux = déjà en cours."""

    @abstractmethod
    def save_snapshot(self, mailbox_id, *, profile: Optional[dict], devices: list, devices_error: str,
                      auth_methods: list, auth_methods_error: str, sign_ins_error: str) -> None: ...

    @abstractmethod
    def finish(self, mailbox_id, error: str = "") -> None: ...

    @abstractmethod
    def latest_sign_in_at(self, mailbox_id) -> Optional[datetime]: ...

    @abstractmethod
    def add_sign_ins(self, mailbox_id, org_id, sign_ins: list[SignInInfo]) -> int: ...

    @abstractmethod
    def list_sign_ins(self, mailbox_id, limit: int = 100) -> list[SignInInfo]: ...

    @abstractmethod
    def prune_sign_ins(self, mailbox_id, before: datetime) -> int: ...


class AuditLog(ABC):
    @abstractmethod
    def record(self, *, mailbox_id, action: str, ok: bool, detail: str, performed_by: str,
               org_id=None) -> None: ...

    @abstractmethod
    def recent(self, mailbox_id, limit: int = 20) -> list[dict]: ...
