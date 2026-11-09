from __future__ import annotations

import uuid
from typing import Any, Callable, Optional

from asgiref.sync import async_to_sync

from shared_kernel.aio import db

from module.microsoft_connector.application.graph_factory import build_graph
from module.microsoft_connector.domain.repositories import ConnectorRepository
from module.microsoft_connector.infrastructure.repositories import DjangoConnectorRepository
from module.microsoft_identity.domain import device_policy as dp
from module.microsoft_identity.infrastructure.audit_models import TenantSettingChange
from module.microsoft_identity.infrastructure.policy_gateway import GraphDevicePolicyGateway

CONFIRM_WORD = "MODIFIER"


class ConnectorUnavailable(Exception):
    pass


class ConfirmationRequired(Exception):
    pass


class DevicePolicyService:
    """Réglages d'inscription des appareils du tenant (Entra ID) : lecture et modification encadrée."""

    def __init__(
        self,
        connectors: Optional[ConnectorRepository] = None,
        gateway_factory: Callable[[Any], Any] = lambda c: GraphDevicePolicyGateway(build_graph(c)),
    ):
        self.connectors = connectors or DjangoConnectorRepository()
        self.gateway_factory = gateway_factory

    def _ctx(self, connector_id: uuid.UUID):
        c = self.connectors.get(connector_id)
        if c is None or not c.is_active:
            raise ConnectorUnavailable(str(connector_id))
        return c, self.gateway_factory(c)

    # Async d'abord (`<nom>_async`, appelé par les vues Daphne) + version synchrone (`<nom>`) pour Celery et les tests.
    async def _actx(self, connector_id: uuid.UUID):
        return await db(self._ctx)(connector_id)

    async def get_async(self, connector_id: uuid.UUID) -> dp.DeviceRegistrationSettings:
        _, gw = await self._actx(connector_id)
        return dp.parse(await gw.get())

    def get(self, connector_id: uuid.UUID) -> dp.DeviceRegistrationSettings:
        return async_to_sync(self.get_async)(connector_id)

    async def get_with_groups_async(self, connector_id: uuid.UUID) -> tuple[dp.DeviceRegistrationSettings, list[dict]]:
        """Réglages + groupes de la sélection « joindre » avec leur nom (None si illisible)."""
        _, gw = await self._actx(connector_id)
        settings = dp.parse(await gw.get())
        names = await gw.group_names(list(settings.join_group_ids))
        return settings, [{"id": i, "name": names.get(i)} for i in settings.join_group_ids]

    def get_with_groups(self, connector_id: uuid.UUID) -> tuple[dp.DeviceRegistrationSettings, list[dict]]:
        return async_to_sync(self.get_with_groups_async)(connector_id)

    async def groups_async(self, connector_id: uuid.UUID, query: str = "") -> list[dict]:
        """Groupes de sécurité du tenant pour le sélecteur (DataUnavailable si Group.Read.All manque)."""
        _, gw = await self._actx(connector_id)
        return await gw.groups(query)

    def groups(self, connector_id: uuid.UUID, query: str = "") -> list[dict]:
        return async_to_sync(self.groups_async)(connector_id, query)

    async def directory_users_async(self, connector_id: uuid.UUID) -> list[dict]:
        _, gw = await self._actx(connector_id)
        return sorted(await gw.directory_users(), key=lambda u: u["name"].lower())

    def directory_users(self, connector_id: uuid.UUID) -> list[dict]:
        return async_to_sync(self.directory_users_async)(connector_id)

    def create_group(self, connector_id: uuid.UUID, name: str, member_ids: list[str], by: str) -> dict:
        return async_to_sync(self.create_group_async)(connector_id, name, member_ids, by)

    async def create_group_async(self, connector_id: uuid.UUID, name: str, member_ids: list[str], by: str) -> dict:
        """Crée un groupe de sécurité (non lié à la messagerie) avec ses premiers membres.

        Validé côté serveur : nom 3–64 caractères, 1 à 20 membres (GUID). Le nickname technique est dérivé
        du nom + suffixe aléatoire (unicité). Journalisé."""
        import re
        import secrets
        name = (name or "").strip()
        if not 3 <= len(name) <= 64 or any(ord(c) < 32 for c in name):
            raise ValueError("Le nom du groupe doit faire 3 à 64 caractères.")
        if not isinstance(member_ids, list) or not 1 <= len(member_ids) <= 20:
            raise ValueError("Choisis de 1 à 20 membres.")
        ids = dp._ids(member_ids, "Membres")
        connector, gw = await self._actx(connector_id)
        base = re.sub(r"[^a-z0-9]", "", name.lower())[:40] or "groupe"
        nickname = f"{base}-{secrets.token_hex(3)}"
        try:
            group = await gw.create_security_group(
                name, nickname, "Créé depuis OverView (exception de jonction d'appareils)", ids)
        except Exception as e:
            await db(self._log)(connector, [f"Création du groupe « {name} » ({len(ids)} membre(s))"], False,
                                f"{type(e).__name__}: {e}"[:500], by, setting="security_group")
            raise
        await db(self._log)(connector, [f"Groupe de sécurité « {name} » créé avec {len(ids)} membre(s)"], True,
                            group["id"], by, setting="security_group")
        return group

    def update(self, connector_id: uuid.UUID, changes: dict, by: str,
               confirm: Optional[str]) -> tuple[dp.DeviceRegistrationSettings, list[str]]:
        return async_to_sync(self.update_async)(connector_id, changes, by, confirm)

    async def update_async(self, connector_id: uuid.UUID, changes: dict, by: str,
                           confirm: Optional[str]) -> tuple[dp.DeviceRegistrationSettings, list[str]]:
        """Applique les changements au tenant. Garde-fous : mot de confirmation, valeurs validées,
        relecture avant écriture (pas d'écrasement à l'aveugle), journalisation avant/après.
        Renvoie (réglages à jour, liste des changements effectués) ; rien n'est écrit s'il n'y a pas de différence."""
        if (confirm or "").strip() != CONFIRM_WORD:
            raise ConfirmationRequired(CONFIRM_WORD)
        connector, gw = await self._actx(connector_id)
        raw = await gw.get()
        before = dp.parse(raw)
        new_raw = dp.apply_changes(raw, changes)   # ValueError si invalide
        lines = dp.diff(before, dp.parse(new_raw))
        if not lines:
            return before, []
        try:
            await gw.put(new_raw)
        except Exception as e:
            await db(self._log)(connector, lines, False, f"{type(e).__name__}: {e}"[:500], by)
            raise
        await db(self._log)(connector, lines, True, "", by)
        return dp.parse(await gw.get()), lines

    def history(self, connector_id: uuid.UUID, limit: int = 20) -> list[dict]:
        self._ctx(connector_id)
        return list(TenantSettingChange.objects.filter(
            connector_id=connector_id, setting__in=["device_registration", "security_group"]).values(
            "id", "changes", "ok", "detail", "performed_by", "create_at")[:limit])

    @staticmethod
    def _log(connector, lines, ok, detail, by, setting: str = "device_registration") -> None:
        TenantSettingChange.objects.create(
            connector_id=connector.id, org_id=connector.org_id, setting=setting,
            changes=lines, ok=ok, detail=detail, performed_by=by)
