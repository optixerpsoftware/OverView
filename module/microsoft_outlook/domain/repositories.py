from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Iterable, Optional

from module.microsoft_outlook.domain.entities import MailboxEntity, RuleRef, RuleSnapshot


class MailboxRepository(ABC):
    @abstractmethod
    def add(self, mailbox: MailboxEntity) -> MailboxEntity: ...

    @abstractmethod
    def existing_upns(self, connector_id: uuid.UUID) -> set[str]: ...

    @abstractmethod
    def get(self, mailbox_id: uuid.UUID) -> Optional[MailboxEntity]: ...

    @abstractmethod
    def list_active(self) -> list[MailboxEntity]: ...

    @abstractmethod
    def save(self, mailbox: MailboxEntity) -> None: ...

    @abstractmethod
    def replace_rules(self, mailbox: MailboxEntity, rules: Iterable[RuleSnapshot]) -> int: ...

    @abstractmethod
    def get_rule(self, rule_pk: uuid.UUID) -> Optional[RuleRef]: ...

    @abstractmethod
    def delete_rule(self, rule_pk: uuid.UUID) -> None: ...

    @abstractmethod
    def delete_by_org(self, org_id: uuid.UUID) -> int: ...

    @abstractmethod
    def delete_by_connector(self, connector_id: uuid.UUID) -> int: ...
