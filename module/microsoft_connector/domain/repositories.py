from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Optional

from module.microsoft_connector.domain.entities import ConnectorEntity


class ConnectorRepository(ABC):
    @abstractmethod
    def add(self, connector: ConnectorEntity) -> ConnectorEntity: ...

    @abstractmethod
    def get(self, connector_id: uuid.UUID) -> Optional[ConnectorEntity]: ...

    @abstractmethod
    def save(self, connector: ConnectorEntity) -> None: ...

    @abstractmethod
    def list_active(self) -> list[ConnectorEntity]: ...

    @abstractmethod
    def delete_by_org(self, org_id: uuid.UUID) -> int: ...

    @abstractmethod
    def delete(self, connector_id: uuid.UUID) -> None: ...
