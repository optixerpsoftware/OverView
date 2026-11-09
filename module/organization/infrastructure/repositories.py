from __future__ import annotations

import uuid
from typing import Optional

from module.organization.domain.entities import OrganizationEntity
from module.organization.domain.repositories import OrganizationRepository
from module.organization.infrastructure.models import Organization


class DjangoOrganizationRepository(OrganizationRepository):
    @staticmethod
    def _to_entity(m: Organization) -> OrganizationEntity:
        return OrganizationEntity(id=m.id, name=m.name, description=m.description)

    def add(self, org: OrganizationEntity) -> OrganizationEntity:
        m = Organization.objects.create(id=org.id, name=org.name, description=org.description)
        return self._to_entity(m)

    def get(self, org_id: uuid.UUID) -> Optional[OrganizationEntity]:
        m = Organization.objects.filter(pk=org_id).first()
        return self._to_entity(m) if m else None

    def exists(self, org_id: uuid.UUID) -> bool:
        return Organization.objects.filter(pk=org_id).exists()

    def delete(self, org_id: uuid.UUID) -> None:
        Organization.objects.filter(pk=org_id).delete()
