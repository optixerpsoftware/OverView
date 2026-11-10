from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Optional


class DriveGateway(ABC):
    """Port vers OneDrive / SharePoint (Microsoft Graph). Données brutes (dict JSON)."""

    @abstractmethod
    async def internal_domains(self) -> list[str]: ...

    @abstractmethod
    async def list_drives(self) -> list[dict]:
        """[{id, label, owner, kind: 'onedrive'|'sharepoint', web_url}]"""

    @abstractmethod
    async def children(self, drive_id: str, parent_id: Optional[str]) -> list[dict]: ...

    @abstractmethod
    async def permissions(self, drive_id: str, item_id: str) -> list[dict]: ...

    @abstractmethod
    async def scan_items(self, drive_id: str, max_items: int) -> tuple[list[dict], bool]:
        """Parcourt tout le drive : (éléments, tronqué?)."""

    @abstractmethod
    async def permissions_batch(self, drive_id: str, item_ids: list[str]) -> list[Optional[list[dict]]]: ...

    @abstractmethod
    async def shared_scopes(self, drive_id: str, item_ids: list[str]) -> dict[str, Optional[str]]:
        """Périmètre de partage réel de chaque élément (facette shared.scope : anonymous | organization | users)."""

    @abstractmethod
    async def delete_permission(self, drive_id: str, item_id: str, permission_id: str) -> None: ...


class SharingScanRepository(ABC):
    @abstractmethod
    def start(self, *, connector_id: uuid.UUID, org_id: Optional[uuid.UUID]) -> dict: ...

    @abstractmethod
    def finish(self, scan_id: uuid.UUID, *, records: list[dict], summary: dict, truncated: list[str],
               errors: list[str]) -> dict: ...

    def progress(self, scan_id: uuid.UUID, progress: dict) -> None:  # facultatif : avancement affiché pendant l'analyse
        return None

    @abstractmethod
    def fail(self, scan_id: uuid.UUID, error: str) -> None: ...

    @abstractmethod
    def latest(self, connector_id: uuid.UUID) -> Optional[dict]: ...

    @abstractmethod
    def history(self, connector_id: uuid.UUID, limit: int = 60) -> list[dict]: ...

    @abstractmethod
    def mark_revoked(self, connector_id: uuid.UUID, drive_id: str, item_id: str, permission_id: str) -> None: ...

    @abstractmethod
    def running(self, connector_id: uuid.UUID) -> Optional[dict]: ...
