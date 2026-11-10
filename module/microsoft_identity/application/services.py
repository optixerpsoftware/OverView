from __future__ import annotations

import asyncio
import re
import secrets
import uuid
from typing import Callable, Optional

from asgiref.sync import async_to_sync

from shared_kernel.aio import db

from module.microsoft_connector.application.graph_factory import build_graph
from module.microsoft_connector.domain.repositories import ConnectorRepository
from module.microsoft_connector.infrastructure.repositories import DjangoConnectorRepository
from module.microsoft_identity.domain.entities import Section, StepResult
from module.microsoft_identity.domain.password import generate_password
from module.microsoft_identity.domain.sessions import estimate_active_sessions
from module.microsoft_identity.domain.totp import generate_secret, otpauth_uri
from module.microsoft_identity.domain.ports import AuditLog, IdentityGateway
from module.microsoft_identity.infrastructure.audit import DjangoAuditLog
from module.microsoft_identity.infrastructure.graph_gateway import GraphIdentityGateway
from module.microsoft_outlook.domain.entities import MailboxEntity
from module.microsoft_outlook.domain.repositories import MailboxRepository
from module.microsoft_outlook.infrastructure.repositories import DjangoMailboxRepository


class MailboxNotFound(Exception):
    pass


class ConnectorUnavailable(Exception):
    pass


class MethodNotFound(Exception):
    """La méthode d'authentification ciblée n'existe pas pour ce compte."""


class DeviceNotOwned(Exception):
    """L'appareil ciblé n'est pas enregistré pour ce compte."""


class ConfirmationMismatch(Exception):
    """L'adresse saisie pour confirmer ne correspond pas à la boîte ciblée."""


# Méthodes supprimables : tout sauf le mot de passe (non supprimable) et les types inconnus.
DELETABLE_KINDS = {"authenticator", "phone", "email", "fido2", "hello", "oath", "hardware_oath", "tap", "platform"}
# Méthodes qu'un administrateur peut AJOUTER. Authenticator, FIDO2, Hello et TOTP doivent être enregistrées
# par l'utilisateur lui-même ; le code d'accès temporaire (TAP) lui permet de le faire.
ADDABLE_KINDS = {"phone", "email", "tap", "totp"}
TOTP_ISSUER = "Microsoft 365"
_PHONE = re.compile(r"^\+\d{1,3} \d{4,14}$")        # format Graph : « +1 5145551234 »
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _mask(kind: str, value: str) -> str:
    """Valeur masquée pour le journal d'audit (jamais le numéro ni l'adresse complets)."""
    if kind == "phone":
        return f"…{value[-3:]}"
    if kind == "email":
        return f"…@{value.rsplit('@', 1)[-1]}"
    return ""


def _default_gateway(connector) -> IdentityGateway:
    return GraphIdentityGateway(build_graph(connector))


def _err(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:400]


class IdentityService:
    """Sécurité d'un compte : devices, MFA, sessions, déconnexion, mot de passe, urgence."""

    def __init__(
        self,
        mailboxes: Optional[MailboxRepository] = None,
        connectors: Optional[ConnectorRepository] = None,
        audit: Optional[AuditLog] = None,
        gateway_factory: Optional[Callable] = None,
        sync_trigger: Optional[Callable[[uuid.UUID], object]] = None,
    ):
        self.mailboxes = mailboxes or DjangoMailboxRepository()
        self.connectors = connectors or DjangoConnectorRepository()
        self.audit = audit or DjangoAuditLog()
        self.gateway_factory = gateway_factory or _default_gateway   # résolu à l'exécution (substituable en test)
        # Appelé après une action réussie pour rafraîchir l'instantané local du compte (synchro asynchrone).
        self.sync_trigger = sync_trigger

    # ---------- contexte ----------
    def _context(self, mailbox_id: uuid.UUID) -> tuple[MailboxEntity, IdentityGateway]:
        mailbox = self.mailboxes.get(mailbox_id)
        if mailbox is None:
            raise MailboxNotFound(str(mailbox_id))
        connector = self.connectors.get(mailbox.connector_id)
        if connector is None or not connector.is_active:
            raise ConnectorUnavailable(str(mailbox.connector_id))
        return mailbox, self.gateway_factory(connector)

    @staticmethod
    def _check_confirmation(mailbox: MailboxEntity, confirm: Optional[str]) -> None:
        if (confirm or "").strip().lower() != mailbox.user_principal_name:
            raise ConfirmationMismatch(mailbox.user_principal_name)

    def _log(self, mailbox: MailboxEntity, action: str, ok: bool, detail: str, by: str) -> None:
        self.audit.record(mailbox_id=mailbox.id, org_id=mailbox.org_id, action=action,
                          ok=ok, detail=detail, performed_by=by)
        if ok and not action.startswith("emergency:"):   # l'urgence déclenche UNE synchro, à la fin du pipeline
            self._changed(mailbox.id)

    def _changed(self, mailbox_id: uuid.UUID) -> None:
        """Une action a modifié le compte : on planifie une synchro pour rafraîchir l'instantané local.
        Ne fait jamais échouer l'action (déjà appliquée chez Microsoft) si la planification échoue."""
        if self.sync_trigger is None:
            return
        try:
            self.sync_trigger(mailbox_id)
        except Exception:
            pass

    # La lecture (profil, appareils, MFA, connexions, sessions) est dans SecuritySyncService : elle lit
    # l'instantané local mis à jour par la synchro, au lieu d'interroger Microsoft à chaque page.

    # ---------- actions ----------
    # Convention : chaque action est écrite en async (`<nom>_async`, appelée par les vues Daphne) et exposée aussi
    # en version synchrone (`<nom>`, pour Celery et les tests) qui se contente de l'exécuter.
    async def _acontext(self, mailbox_id: uuid.UUID) -> tuple[MailboxEntity, IdentityGateway]:
        return await db(self._context)(mailbox_id)

    async def _alog(self, mailbox: MailboxEntity, action: str, ok: bool, detail: str, by: str) -> None:
        await db(self._log)(mailbox, action, ok, detail, by)

    async def revoke_sessions_async(self, mailbox_id: uuid.UUID, by: str) -> None:
        """Invalide tous les jetons d'actualisation : l'utilisateur est déconnecté partout
        (les jetons d'accès déjà émis expirent d'ici ~1 h)."""
        mailbox, gw = await self._acontext(mailbox_id)
        try:
            await gw.revoke_sessions(mailbox.user_principal_name)
        except Exception as e:
            await self._alog(mailbox, "revoke_sessions", False, _err(e), by)
            raise
        await self._alog(mailbox, "revoke_sessions", True, "", by)

    def revoke_sessions(self, mailbox_id: uuid.UUID, by: str) -> None:
        return async_to_sync(self.revoke_sessions_async)(mailbox_id, by)

    async def delete_device_async(self, mailbox_id: uuid.UUID, device_object_id: str, by: str,
                                  confirm: Optional[str]) -> None:
        """Supprime un appareil de l'annuaire Entra ID (il sort de la liste des PC joints).

        Garde-fous : l'appareil doit être enregistré pour CE compte (on ne supprime pas un
        objet arbitraire du tenant) et `confirm` doit être le nom exact de l'appareil."""
        mailbox, gw = await self._acontext(mailbox_id)
        devices = await gw.list_devices(mailbox.user_principal_name)
        device = next((d for d in devices if d.id == device_object_id), None)
        if device is None:
            raise DeviceNotOwned(device_object_id)
        if (confirm or "").strip().lower() != device.display_name.lower():
            raise ConfirmationMismatch(device.display_name)
        label = f"{device.display_name} ({device.id})"
        try:
            await gw.delete_device(device.id)
        except Exception as e:
            await self._alog(mailbox, "delete_device", False, f"{label} — {_err(e)}", by)
            raise
        await self._alog(mailbox, "delete_device", True, label, by)

    def delete_device(self, mailbox_id: uuid.UUID, device_object_id: str, by: str, confirm: Optional[str]) -> None:
        return async_to_sync(self.delete_device_async)(mailbox_id, device_object_id, by, confirm)

    async def delete_auth_method_async(self, mailbox_id: uuid.UUID, method_id: str, by: str,
                                       confirm: Optional[str]) -> None:
        """Supprime une méthode d'authentification (téléphone, Authenticator, FIDO2…).

        Garde-fous : confirmation par l'adresse exacte de la boîte, méthode relue en direct
        sur CE compte, mot de passe non supprimable, action journalisée."""
        mailbox, gw = await self._acontext(mailbox_id)
        self._check_confirmation(mailbox, confirm)
        methods = await gw.list_auth_methods(mailbox.user_principal_name)
        method = next((m for m in methods if m.id == method_id), None)
        if method is None:
            raise MethodNotFound(method_id)
        if method.kind not in DELETABLE_KINDS:
            raise ValueError("Cette méthode ne peut pas être supprimée (le mot de passe n'est pas supprimable).")
        label = f"{method.label}{' — ' + method.detail if method.detail else ''}"
        try:
            await gw.delete_auth_method(mailbox.user_principal_name, method.kind, method.id)
        except Exception as e:
            await self._alog(mailbox, "delete_auth_method", False, f"{label} — {_err(e)}", by)
            raise
        await self._alog(mailbox, "delete_auth_method", True, label, by)

    def delete_auth_method(self, mailbox_id: uuid.UUID, method_id: str, by: str, confirm: Optional[str]) -> None:
        return async_to_sync(self.delete_auth_method_async)(mailbox_id, method_id, by, confirm)

    def add_auth_method(self, mailbox_id: uuid.UUID, by: str, confirm: Optional[str], kind: str,
                        value: Optional[str] = None, phone_type: str = "mobile",
                        lifetime_minutes: int = 60, once: bool = True) -> Optional[dict]:
        return async_to_sync(self.add_auth_method_async)(mailbox_id, by, confirm, kind, value, phone_type,
                                                         lifetime_minutes, once)

    async def add_auth_method_async(self, mailbox_id: uuid.UUID, by: str, confirm: Optional[str], kind: str,
                                    value: Optional[str] = None, phone_type: str = "mobile",
                                    lifetime_minutes: int = 60, once: bool = True) -> Optional[dict]:
        """Ajoute une méthode d'authentification : téléphone, courriel de récupération ou code d'accès
        temporaire (TAP). Pour le TAP, renvoie le code (affiché une seule fois, jamais journalisé)."""
        if kind not in ADDABLE_KINDS:
            raise ValueError("Seuls le téléphone, le courriel de récupération, une application TOTP et le code "
                             "d'accès temporaire peuvent être ajoutés par un administrateur.")
        value = (value or "").strip()
        if kind == "phone":
            if not _PHONE.match(value):
                raise ValueError("Numéro invalide : format international avec un espace, ex. +1 5145551234.")
            if phone_type not in ("mobile", "alternateMobile", "office"):
                raise ValueError("Type de téléphone invalide.")
        elif kind == "email":
            if not _EMAIL.match(value):
                raise ValueError("Adresse courriel invalide.")
        elif kind == "tap" and not 10 <= int(lifetime_minutes) <= 43200:
            raise ValueError("Durée du code invalide (10 à 43 200 minutes).")
        mailbox, gw = await self._acontext(mailbox_id)
        self._check_confirmation(mailbox, confirm)
        upn, result = mailbox.user_principal_name, None
        try:
            if kind == "phone":
                await gw.add_phone_method(upn, value, phone_type)
            elif kind == "email":
                await gw.add_email_method(upn, value)
            elif kind == "totp":
                secret, serial = generate_secret(), f"OV{secrets.token_hex(5).upper()}"
                await gw.add_totp_method(upn, secret, serial, "Application TOTP")
                # La clé n'est renvoyée qu'ici (jamais stockée ni journalisée) ; l'URI alimente le QR code.
                result = {"secret": secret, "uri": otpauth_uri(TOTP_ISSUER, upn, secret), "account": upn,
                          "serial": serial}
            else:
                result = await gw.create_temporary_access_pass(upn, int(lifetime_minutes), bool(once))
        except Exception as e:
            await self._alog(mailbox, f"add_auth_method:{kind}", False, f"{_mask(kind, value)} — {_err(e)}", by)
            raise
        if kind == "totp":
            detail = f"jeton OATH {result['serial']}"
        else:
            detail = _mask(kind, value) or f"durée {lifetime_minutes} min, usage unique : {bool(once)}"
        await self._alog(mailbox, f"add_auth_method:{kind}", True, detail, by)
        return result

    async def reset_password_async(self, mailbox_id: uuid.UUID, by: str, confirm: Optional[str],
                                   force_change: bool = True) -> str:
        """Définit un nouveau mot de passe généré. Il est renvoyé UNE fois et jamais stocké."""
        mailbox, gw = await self._acontext(mailbox_id)
        self._check_confirmation(mailbox, confirm)
        password = generate_password()
        try:
            await gw.set_password(mailbox.user_principal_name, password, force_change)
        except Exception as e:
            await self._alog(mailbox, "reset_password", False, _err(e), by)
            raise
        await self._alog(mailbox, "reset_password", True, f"changement forcé à la connexion : {force_change}", by)
        return password

    def reset_password(self, mailbox_id: uuid.UUID, by: str, confirm: Optional[str], force_change: bool = True) -> str:
        return async_to_sync(self.reset_password_async)(mailbox_id, by, confirm, force_change)

    async def emergency_async(self, mailbox_id: uuid.UUID, by: str, confirm: Optional[str],
                              options: Optional[dict] = None) -> tuple[list[StepResult], Optional[str]]:
        """Pipeline d'urgence (compte compromis), au mieux de ses possibilités :
        1. déconnexion de toutes les sessions
        2. purge de TOUTES les méthodes MFA (l'attaquant a pu ajouter son téléphone ou son Authenticator)
        3. suppression de TOUS les appareils enregistrés ou joints au nom du compte
        4. (par défaut) suppression des règles Outlook qui transfèrent vers l'extérieur + retrait du transfert SMTP de boîte
        5. nouveau mot de passe généré (changement forcé à la prochaine connexion)
        6. nouvelle déconnexion (invalide les jetons émis pendant l'opération)
        7. (option) désactivation du compte ; (option) passe d'accès temporaire pour la réinscription MFA
        Options : {"remove_forwarding": True, "disable_account": False, "create_tap": False}.
        Une étape en échec n'interrompt pas les suivantes ; le mot de passe n'est
        renvoyé que si l'étape 4 a réussi. Le mot de passe (méthode « password ») n'est jamais supprimé."""
        mailbox, gw = await self._acontext(mailbox_id)
        self._check_confirmation(mailbox, confirm)
        opts = {"remove_forwarding": True, "disable_account": False, "create_tap": False, **(options or {})}
        upn = mailbox.user_principal_name
        steps: list[StepResult] = []
        password: Optional[str] = None
        self.last_tap = None

        async def step(name: str, label: str, action) -> bool:
            try:
                detail = await action() or ""
                steps.append(StepResult(label, True, detail))
                await self._alog(mailbox, f"emergency:{name}", True, detail, by)
                return True
            except Exception as e:
                steps.append(StepResult(label, False, _err(e)))
                await self._alog(mailbox, f"emergency:{name}", False, _err(e), by)
                return False

        async def purge_mfa() -> str:
            """Supprime toutes les méthodes sauf le mot de passe. Microsoft refuse parfois de supprimer la méthode
            par défaut tant qu'il en reste d'autres : un second passage reprend les échecs du premier."""
            methods = [m for m in await gw.list_auth_methods(upn) if m.kind in DELETABLE_KINDS]
            if not methods:
                return "Aucune méthode MFA à supprimer."
            removed, pending, errors = [], list(methods), {}
            for _ in range(2):
                retry = []
                for m in pending:
                    try:
                        await gw.delete_auth_method(upn, m.kind, m.id)
                        removed.append(m.label + (f" ({m.detail})" if m.detail else ""))
                        errors.pop(m.id, None)
                    except Exception as e:  # noqa: BLE001
                        errors[m.id] = f"{m.label} : {_err(e)}"
                        retry.append(m)
                pending = retry
                if not pending:
                    break
            if pending:
                raise RuntimeError(f"{len(removed)} supprimée(s), {len(pending)} en échec — " + " ; ".join(errors.values()))
            return f"{len(removed)} méthode(s) supprimée(s) : " + ", ".join(removed)

        async def delete_devices() -> str:
            devices = await gw.list_devices(upn)
            if not devices:
                return "Aucun appareil enregistré."
            removed, failed = [], []
            for d in devices:
                try:
                    await gw.delete_device(d.id)
                    removed.append(d.display_name or d.id)
                except Exception as e:  # noqa: BLE001
                    failed.append(f"{d.display_name or d.id} : {_err(e)}")
            if failed:
                raise RuntimeError(f"{len(removed)} supprimé(s), {len(failed)} en échec — " + " ; ".join(failed))
            return f"{len(removed)} appareil(s) supprimé(s) : " + ", ".join(removed)

        async def reset():
            nonlocal password
            pw = generate_password()
            await gw.set_password(upn, pw, True)
            password = pw  # affecté seulement après succès

        await step("revoke_1", "Déconnexion de toutes les sessions", lambda: gw.revoke_sessions(upn))
        await step("purge_mfa", "Suppression de toutes les méthodes MFA", purge_mfa)
        await step("delete_devices", "Suppression de tous les appareils", delete_devices)
        async def remove_forwarding() -> str:
            domains = {d.lower() for d in await gw.internal_domains()} or {upn.rsplit("@", 1)[-1].lower()}
            removed = []
            for r in await gw.list_message_rules(upn):
                acts = r.get("actions") or {}
                targets = [((a or {}).get("emailAddress") or {}).get("address", "")
                           for k in ("forwardTo", "forwardAsAttachmentTo", "redirectTo") for a in acts.get(k) or []]
                ext = [t for t in targets if t and t.rsplit("@", 1)[-1].lower() not in domains]
                if ext:
                    await gw.delete_message_rule(upn, r["id"])
                    removed.append(f"« {r.get('displayName', '?')} » → {', '.join(ext)}")
            parts = [f"{len(removed)} règle(s) de transfert supprimée(s)" + (f" : {'; '.join(removed)}" if removed else "")]
            try:
                parts.append("transfert SMTP de boîte retiré" if await gw.clear_mailbox_forwarding(upn) else "aucun transfert SMTP de boîte")
            except Exception as e:  # noqa: BLE001 : Exchange non accessible → les règles restent traitées
                parts.append(f"transfert SMTP non vérifié ({_err(e)})")
            return " · ".join(parts)

        async def create_tap() -> str:
            self.last_tap = await gw.create_temporary_access_pass(upn, 480, True)
            return "Passe d'accès temporaire créé (8 h, usage unique), affiché une seule fois."

        if opts["remove_forwarding"]:
            await step("remove_forwarding", "Suppression des transferts vers l'extérieur", remove_forwarding)
        await step("reset_password", "Nouveau mot de passe (changement forcé à la connexion)", reset)
        await step("revoke_2", "Seconde déconnexion des sessions", lambda: gw.revoke_sessions(upn))
        if opts["disable_account"]:
            await step("disable_account", "Désactivation du compte", lambda: gw.set_account_enabled(upn, False))
        if opts["create_tap"]:
            await step("create_tap", "Passe d'accès temporaire pour la réinscription MFA", create_tap)
        await db(self._changed)(mailbox.id)
        return steps, password

    def emergency(self, mailbox_id: uuid.UUID, by: str, confirm: Optional[str],
                  options: Optional[dict] = None) -> tuple[list[StepResult], Optional[str]]:
        return async_to_sync(self.emergency_async)(mailbox_id, by, confirm, options)
