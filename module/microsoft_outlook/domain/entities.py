from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from shared_kernel.domain.base_entity import Entity


@dataclass(eq=False)
class MailboxEntity(Entity):
    """Aggregate root : boîte aux lettres surveillée via un connecteur."""
    org_id: uuid.UUID
    connector_id: uuid.UUID
    user_principal_name: str
    graph_user_id: str = ""
    is_active: bool = True
    last_synced_at: Optional[datetime] = None

    def __post_init__(self):
        self.user_principal_name = (self.user_principal_name or "").strip().lower()
        if "@" not in self.user_principal_name:
            raise ValueError("Adresse de boîte invalide.")

    def mark_synced(self) -> None:
        self.last_synced_at = datetime.now(timezone.utc)


@dataclass(frozen=True)
class RuleSnapshot:
    """Value object : une règle de boîte telle que vue par Graph."""
    rule_id: str
    display_name: str
    sequence: int
    is_enabled: bool
    has_error: bool
    is_read_only: bool
    conditions: Optional[dict]
    actions: Optional[dict]


@dataclass(frozen=True)
class RuleRef:
    """Value object : référence à une règle stockée localement."""
    id: uuid.UUID
    mailbox_id: uuid.UUID
    rule_id: str
    display_name: str
    is_read_only: bool
