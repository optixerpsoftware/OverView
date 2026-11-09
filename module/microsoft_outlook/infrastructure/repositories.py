from __future__ import annotations

import uuid
from typing import Iterable, Optional

from django.db import transaction

from module.microsoft_outlook.domain.entities import MailboxEntity, RuleRef, RuleSnapshot
from module.microsoft_outlook.domain.repositories import MailboxRepository
from module.microsoft_outlook.infrastructure.models import EmailQueue, Mailbox, OutlookRules


class DjangoMailboxRepository(MailboxRepository):
    @staticmethod
    def _to_entity(m: Mailbox) -> MailboxEntity:
        return MailboxEntity(
            id=m.id, org_id=m.org_id, connector_id=m.connector_id,
            user_principal_name=m.user_principal_name, graph_user_id=m.graph_user_id,
            is_active=m.is_active, last_synced_at=m.last_synced_at,
        )

    def add(self, mb: MailboxEntity) -> MailboxEntity:
        m = Mailbox.objects.create(
            id=mb.id, org_id=mb.org_id, connector_id=mb.connector_id,
            user_principal_name=mb.user_principal_name, graph_user_id=mb.graph_user_id,
            is_active=mb.is_active,
        )
        return self._to_entity(m)

    def existing_upns(self, connector_id: uuid.UUID) -> set[str]:
        return set(Mailbox.objects.filter(connector_id=connector_id)
                   .values_list('user_principal_name', flat=True))

    def get(self, mailbox_id: uuid.UUID) -> Optional[MailboxEntity]:
        m = Mailbox.objects.filter(pk=mailbox_id).first()
        return self._to_entity(m) if m else None

    def list_active(self) -> list[MailboxEntity]:
        return [self._to_entity(m) for m in Mailbox.objects.filter(is_active=True)]

    def save(self, mb: MailboxEntity) -> None:
        Mailbox.objects.filter(pk=mb.id).update(
            graph_user_id=mb.graph_user_id, is_active=mb.is_active,
            last_synced_at=mb.last_synced_at,
        )

    @transaction.atomic
    def replace_rules(self, mb: MailboxEntity, rules: Iterable[RuleSnapshot]) -> int:
        OutlookRules.objects.filter(mailbox_id=mb.id).delete()
        objs = [
            OutlookRules(
                org_id=mb.org_id, mailbox_id=mb.id, rule_id=r.rule_id,
                display_name=r.display_name, sequence=r.sequence, is_enabled=r.is_enabled,
                has_error=r.has_error, is_read_only=r.is_read_only,
                conditions=r.conditions, actions=r.actions,
            )
            for r in rules
        ]
        OutlookRules.objects.bulk_create(objs)
        return len(objs)

    def get_rule(self, rule_pk: uuid.UUID) -> Optional[RuleRef]:
        r = OutlookRules.objects.filter(pk=rule_pk).first()
        if r is None:
            return None
        return RuleRef(id=r.id, mailbox_id=r.mailbox_id, rule_id=r.rule_id,
                       display_name=r.display_name, is_read_only=r.is_read_only)

    def delete_rule(self, rule_pk: uuid.UUID) -> None:
        OutlookRules.objects.filter(pk=rule_pk).delete()

    @transaction.atomic
    def delete_by_org(self, org_id: uuid.UUID) -> int:
        n = OutlookRules.objects.filter(org_id=org_id).delete()[0]
        n += EmailQueue.objects.filter(org_id=org_id).delete()[0]
        n += Mailbox.objects.filter(org_id=org_id).delete()[0]
        return n

    @transaction.atomic
    def delete_by_connector(self, connector_id: uuid.UUID) -> int:
        ids = list(Mailbox.objects.filter(connector_id=connector_id).values_list('pk', flat=True))
        n = OutlookRules.objects.filter(mailbox_id__in=ids).delete()[0]
        n += Mailbox.objects.filter(pk__in=ids).delete()[0]
        return n
