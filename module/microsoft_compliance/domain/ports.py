from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Optional

from shared_kernel.graph_rest import DataUnavailable  # noqa: F401  (ré-exporté)


class ComplianceGateway(ABC):
    """Port vers Microsoft Graph : données brutes du tenant (dict JSON)."""

    @abstractmethod
    async def licenses(self) -> list[dict]: ...

    @abstractmethod
    async def users(self) -> list[dict]: ...

    @abstractmethod
    async def registrations(self) -> list[dict]: ...

    @abstractmethod
    async def roles(self) -> list[dict]: ...

    @abstractmethod
    async def ca_policies(self) -> list[dict]: ...

    @abstractmethod
    async def security_defaults(self) -> dict: ...

    @abstractmethod
    async def secure_score(self) -> Optional[dict]: ...

    @abstractmethod
    async def score_profiles(self) -> list[dict]: ...

    @abstractmethod
    async def applications(self) -> list[dict]: ...


class SnapshotRepository(ABC):
    @abstractmethod
    def add(self, *, connector_id: uuid.UUID, org_id: Optional[uuid.UUID], score: Optional[float],
            findings: list[dict], licenses: dict) -> dict: ...

    @abstractmethod
    def latest(self, connector_id: uuid.UUID) -> Optional[dict]: ...

    @abstractmethod
    def history(self, connector_id: uuid.UUID, limit: int = 60) -> list[dict]: ...
