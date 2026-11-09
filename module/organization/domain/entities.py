from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from shared_kernel.domain.base_entity import Entity


@dataclass(eq=False)
class OrganizationEntity(Entity):
    """Aggregate root : une organisation (tenant applicatif)."""
    name: str
    description: Optional[str] = None

    def __post_init__(self):
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValueError("Le nom de l'organisation est obligatoire.")
