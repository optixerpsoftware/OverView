from __future__ import annotations

import uuid
from typing import Optional

from asgiref.sync import async_to_sync

from shared_kernel.aio import db
from django.db import transaction

from module.microsoft_connector.application.graph_factory import build_graph
from module.microsoft_connector.domain.entities import ConnectorEntity
from module.microsoft_connector.domain.repositories import ConnectorRepository
from module.microsoft_connector.infrastructure.repositories import DjangoConnectorRepository
from module.organization.domain.repositories import OrganizationRepository
from module.organization.infrastructure.repositories import DjangoOrganizationRepository


class ConnectorNotFound(Exception):
    pass


class OrganizationNotFound(Exception):
    pass


class ConnectorService:
    def __init__(
        self,
        repo: Optional[ConnectorRepository] = None,
        orgs: Optional[OrganizationRepository] = None,
    ):
        self.repo = repo or DjangoConnectorRepository()
        self.orgs = orgs or DjangoOrganizationRepository()

    def register(self, *, org_id: uuid.UUID, name: str, tenant_id, client_id,
                 client_secret: str, is_active: bool = True) -> ConnectorEntity:
        if not self.orgs.exists(org_id):
            raise OrganizationNotFound(str(org_id))
        return self.repo.add(ConnectorEntity(
            org_id=org_id, name=name, tenant_id=tenant_id, client_id=client_id,
            client_secret=client_secret, is_active=is_active,
        ))

    def update(self, connector_id: uuid.UUID, *, name: Optional[str] = None,
               client_secret: Optional[str] = None,
               is_active: Optional[bool] = None) -> ConnectorEntity:
        connector = self.repo.get(connector_id)
        if connector is None:
            raise ConnectorNotFound(str(connector_id))
        if name is not None:
            connector.rename(name)
        if client_secret:
            connector.rotate_secret(client_secret)
        if is_active is not None:
            connector.is_active = is_active
        self.repo.save(connector)
        return connector

    @transaction.atomic
    def delete(self, connector_id: uuid.UUID, purgers=()) -> None:
        """Supprime le connecteur ; `purgers` nettoie les données qui en dépendent
        (boîtes, règles). Rien n'est supprimé dans Microsoft 365."""
        if self.repo.get(connector_id) is None:
            raise ConnectorNotFound(str(connector_id))
        for purge in purgers:
            purge(connector_id)
        self.repo.delete(connector_id)

    def test_connection(self, connector_id: uuid.UUID) -> ConnectorEntity:
        return async_to_sync(self.test_connection_async)(connector_id)

    async def test_connection_async(self, connector_id: uuid.UUID) -> ConnectorEntity:
        """Vérifie les identifiants par un appel Graph (/organization), en async natif (sans thread bloqué)."""
        connector = await db(self.repo.get)(connector_id)
        if connector is None:
            raise ConnectorNotFound(str(connector_id))
        try:
            graph = build_graph(connector)
            await graph.client.organization.get()
            connector.record_check()
        except Exception as e:  # erreur d'authentification / permission / réseau
            connector.record_check(f"{type(e).__name__}: {e}")
        await db(self.repo.save)(connector)
        return connector

    def purge_org(self, org_id: uuid.UUID) -> int:
        """Supprime les connecteurs d'une organisation (appelé à la suppression de l'org)."""
        return self.repo.delete_by_org(org_id)
