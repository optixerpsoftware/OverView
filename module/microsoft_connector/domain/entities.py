from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from shared_kernel.domain.base_entity import Entity


@dataclass(eq=False)
class ConnectorEntity(Entity):
    """Aggregate root : connexion M365 d'une organisation."""
    org_id: uuid.UUID
    name: str
    tenant_id: uuid.UUID
    client_id: uuid.UUID
    client_secret: str
    is_active: bool = True
    last_checked_at: Optional[datetime] = None
    last_error: str = ""

    def __post_init__(self):
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValueError("Le nom du connecteur est obligatoire.")
        if not self.client_secret:
            raise ValueError("Le client secret est obligatoire.")

    def rename(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            raise ValueError("Le nom du connecteur est obligatoire.")
        self.name = name

    def rotate_secret(self, client_secret: str) -> None:
        """Remplace le secret ; l'ancien résultat de test n'est plus valable."""
        if not client_secret:
            raise ValueError("Le client secret est obligatoire.")
        self.client_secret = client_secret
        self.last_checked_at = None
        self.last_error = ""

    def record_check(self, error: Optional[str] = None) -> None:
        self.last_checked_at = datetime.now(timezone.utc)
        self.last_error = (error or "")[:1000]

    @property
    def last_check_ok(self) -> bool:
        return self.last_checked_at is not None and not self.last_error
