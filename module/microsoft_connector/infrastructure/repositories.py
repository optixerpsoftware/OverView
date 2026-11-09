from __future__ import annotations

import uuid
from typing import Optional

from module.microsoft_connector.domain.entities import ConnectorEntity
from module.microsoft_connector.domain.repositories import ConnectorRepository
from module.microsoft_connector.infrastructure.models import MicrosoftConnector


class DjangoConnectorRepository(ConnectorRepository):
    @staticmethod
    def _to_entity(m: MicrosoftConnector) -> ConnectorEntity:
        return ConnectorEntity(
            id=m.id, org_id=m.org_id, name=m.name, tenant_id=m.tenant_id,
            client_id=m.client_id, client_secret=m.client_secret,
            is_active=m.is_active, last_checked_at=m.last_checked_at,
            last_error=m.last_error,
        )

    def add(self, c: ConnectorEntity) -> ConnectorEntity:
        m = MicrosoftConnector.objects.create(
            id=c.id, org_id=c.org_id, name=c.name, tenant_id=c.tenant_id,
            client_id=c.client_id, client_secret=c.client_secret, is_active=c.is_active,
        )
        return self._to_entity(m)

    def get(self, connector_id: uuid.UUID) -> Optional[ConnectorEntity]:
        m = MicrosoftConnector.objects.filter(pk=connector_id).first()
        return self._to_entity(m) if m else None

    def save(self, c: ConnectorEntity) -> None:
        MicrosoftConnector.objects.filter(pk=c.id).update(
            name=c.name, is_active=c.is_active, client_secret=c.client_secret,
            last_checked_at=c.last_checked_at, last_error=c.last_error,
        )

    def list_active(self) -> list[ConnectorEntity]:
        return [self._to_entity(m) for m in MicrosoftConnector.objects.filter(is_active=True)]

    def delete_by_org(self, org_id: uuid.UUID) -> int:
        return MicrosoftConnector.objects.filter(org_id=org_id).delete()[0]

    def delete(self, connector_id: uuid.UUID) -> None:
        MicrosoftConnector.objects.filter(pk=connector_id).delete()
