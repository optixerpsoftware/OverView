from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(eq=False)
class Entity:
    """
    Basis for all entities.
    Equality is based on identity (ID), not attributes.
    """
    id: uuid.UUID = field(default_factory=uuid.uuid4, kw_only=True)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Entity):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.id}>"

    def __str__(self) -> str:
        return f"{self.__class__.__name__}({self.id})"
