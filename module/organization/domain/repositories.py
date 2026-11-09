from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Optional

from module.organization.domain.entities import OrganizationEntity


class OrganizationRepository(ABC):
    @abstractmethod
    def add(self, org: OrganizationEntity) -> OrganizationEntity: ...

    @abstractmethod
    def get(self, org_id: uuid.UUID) -> Optional[OrganizationEntity]: ...

    @abstractmethod
    def exists(self, org_id: uuid.UUID) -> bool: ...

    @abstractmethod
    def delete(self, org_id: uuid.UUID) -> None: ...
