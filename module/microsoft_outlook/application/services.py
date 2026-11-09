from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Optional

from asgiref.sync import async_to_sync

from shared_kernel.aio import db

from module.microsoft_connector.application.graph_factory import build_graph
from module.microsoft_connector.domain.repositories import ConnectorRepository
from module.microsoft_connector.infrastructure.repositories import DjangoConnectorRepository
from module.microsoft_outlook.domain.entities import MailboxEntity, RuleSnapshot
from module.microsoft_outlook.domain.repositories import MailboxRepository
from module.microsoft_outlook.infrastructure.repositories import DjangoMailboxRepository


class ConnectorUnavailable(Exception):
    pass


class MailboxNotFound(Exception):
    pass


class RuleNotFound(Exception):
    pass


class RuleReadOnly(Exception):
    pass


@dataclass(frozen=True)
class DiscoveredMailbox:
    """Boîte trouvée dans le tenant M365 (pas encore importée)."""
    user_principal_name: str
    display_name: str
    graph_user_id: str
    already_imported: bool = False


def _to_json(obj) -> Optional[dict]:
    """Sérialise un objet Kiota (conditions/actions de règle) en dict JSON."""
    if obj is None:
        return None
    from kiota_serialization_json.json_serialization_writer import JsonSerializationWriter
    w = JsonSerializationWriter()
    w.write_object_value(None, obj)
    return json.loads(w.get_serialized_content().decode() or "null")


_FOLDER_ACTIONS = {"moveToFolder": "moveToFolderName", "copyToFolder": "copyToFolderName"}


def _with_folder_names(actions: Optional[dict], names: dict) -> Optional[dict]:
    """Ajoute le nom lisible des dossiers de destination à côté de leur identifiant."""
    if not actions:
        return actions
    for key, name_key in _FOLDER_ACTIONS.items():
        fid = actions.get(key)
        if fid and names.get(fid):
            actions[name_key] = names[fid]
    return actions


class OutlookService:
    def __init__(
        self,
        mailboxes: Optional[MailboxRepository] = None,
        connectors: Optional[ConnectorRepository] = None,
    ):
        self.mailboxes = mailboxes or DjangoMailboxRepository()
        self.connectors = connectors or DjangoConnectorRepository()

    def register_mailbox(self, *, org_id: uuid.UUID, connector_id: uuid.UUID,
                         user_principal_name: str, is_active: bool = True) -> MailboxEntity:
        connector = self.connectors.get(connector_id)
        # Invariant : le connecteur doit exister et appartenir à la même organisation.
        if connector is None or connector.org_id != org_id:
            raise ConnectorUnavailable(str(connector_id))
        return self.mailboxes.add(MailboxEntity(
            org_id=org_id, connector_id=connector_id,
            user_principal_name=user_principal_name, is_active=is_active,
        ))

    def _connector_or_raise(self, connector_id: uuid.UUID):
        connector = self.connectors.get(connector_id)
        if connector is None or not connector.is_active:
            raise ConnectorUnavailable(str(connector_id))
        return connector

    # Async d'abord (`<nom>_async`, appelé par les vues Daphne) + version synchrone (`<nom>`) pour Celery et les tests.
    def discover_mailboxes(self, connector_id: uuid.UUID) -> list[DiscoveredMailbox]:
        return async_to_sync(self.discover_mailboxes_async)(connector_id)

    async def discover_mailboxes_async(self, connector_id: uuid.UUID) -> list[DiscoveredMailbox]:
        """Liste les boîtes (membres avec courriel) du tenant du connecteur."""
        from msgraph.generated.users.users_request_builder import UsersRequestBuilder
        from kiota_abstractions.base_request_configuration import RequestConfiguration

        connector = await db(self._connector_or_raise)(connector_id)
        graph = build_graph(connector)
        params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
            select=["id", "displayName", "mail", "userPrincipalName", "userType",
                    "accountEnabled"],
            top=100,
        )

        out, page = [], await graph.client.users.get(
            request_configuration=RequestConfiguration(query_parameters=params))
        while page:
            out.extend(page.value or [])
            if not page.odata_next_link:
                break
            page = await graph.client.users.with_url(page.odata_next_link).get()

        existing = await db(self.mailboxes.existing_upns)(connector_id)
        found = []
        for u in out:
            mail = (u.mail or "").strip().lower()
            if not mail or u.user_type == "Guest" or u.account_enabled is False:
                continue
            found.append(DiscoveredMailbox(
                user_principal_name=mail, display_name=u.display_name or mail,
                graph_user_id=u.id or "", already_imported=mail in existing,
            ))
        return sorted(found, key=lambda d: d.user_principal_name)

    def import_mailboxes(self, connector_id: uuid.UUID,
                         user_principal_names: Optional[list[str]] = None) -> dict:
        return async_to_sync(self.import_mailboxes_async)(connector_id, user_principal_names)

    async def import_mailboxes_async(self, connector_id: uuid.UUID,
                                     user_principal_names: Optional[list[str]] = None) -> dict:
        """Importe les boîtes sélectionnées (toutes si la liste est omise). Idempotent."""
        connector = await db(self._connector_or_raise)(connector_id)
        wanted = None if user_principal_names is None else {u.strip().lower() for u in user_principal_names}
        created, skipped = [], 0
        for d in await self.discover_mailboxes_async(connector_id):
            if wanted is not None and d.user_principal_name not in wanted:
                continue
            if d.already_imported:
                skipped += 1
                continue
            await db(self.mailboxes.add)(MailboxEntity(
                org_id=connector.org_id, connector_id=connector_id,
                user_principal_name=d.user_principal_name, graph_user_id=d.graph_user_id,
            ))
            created.append(d.user_principal_name)
        return {"imported": created, "already_present": skipped}

    def sync_rules(self, mailbox_id: uuid.UUID) -> int:
        return async_to_sync(self.sync_rules_async)(mailbox_id)

    def _mailbox_and_connector(self, mailbox_id: uuid.UUID):
        mailbox = self.mailboxes.get(mailbox_id)
        if mailbox is None:
            raise MailboxNotFound(str(mailbox_id))
        connector = self.connectors.get(mailbox.connector_id)
        if connector is None or not connector.is_active:
            raise ConnectorUnavailable(str(mailbox.connector_id))
        return mailbox, connector

    def _store_rules(self, mailbox, rules) -> int:
        count = self.mailboxes.replace_rules(mailbox, rules)
        mailbox.mark_synced()
        self.mailboxes.save(mailbox)
        return count

    @staticmethod
    async def _folder_names(graph, upn: str, raw_rules) -> dict:
        """Résout les identifiants de dossiers cités par les règles (déplacer/copier vers) en noms. Meilleur effort :
        un dossier introuvable garde simplement son identifiant."""
        ids = set()
        for r in raw_rules:
            a = r.actions
            for attr in ("move_to_folder", "copy_to_folder"):
                if a is not None and getattr(a, attr, None):
                    ids.add(getattr(a, attr))
        names: dict = {}
        for fid in ids:
            try:
                f = await graph.client.users.by_user_id(upn).mail_folders.by_mail_folder_id(fid).get()
                if f and f.display_name:
                    names[fid] = f.display_name
            except Exception:  # noqa: BLE001 : dossier supprimé ou accès refusé
                continue
        return names

    async def sync_rules_async(self, mailbox_id: uuid.UUID) -> int:
        """Récupère les règles de boîte via Graph et remplace l'état local. Appelé par le worker."""
        mailbox, connector = await db(self._mailbox_and_connector)(mailbox_id)
        graph = build_graph(connector)
        res = await (graph.client.users.by_user_id(mailbox.user_principal_name)
                     .mail_folders.by_mail_folder_id("inbox").message_rules.get())
        raw = res.value or []
        folder_names = await self._folder_names(graph, mailbox.user_principal_name, raw)
        rules = [
            RuleSnapshot(
                rule_id=r.id, display_name=r.display_name or "", sequence=r.sequence or 0,
                is_enabled=bool(r.is_enabled), has_error=bool(r.has_error),
                is_read_only=bool(r.is_read_only),
                conditions=_to_json(r.conditions), actions=_with_folder_names(_to_json(r.actions), folder_names),
            )
            for r in raw
        ]
        return await db(self._store_rules)(mailbox, rules)

    def delete_rule(self, rule_pk: uuid.UUID) -> None:
        return async_to_sync(self.delete_rule_async)(rule_pk)

    def _rule_context(self, rule_pk: uuid.UUID):
        rule = self.mailboxes.get_rule(rule_pk)
        if rule is None:
            raise RuleNotFound(str(rule_pk))
        if rule.is_read_only:
            raise RuleReadOnly(rule.display_name)
        mailbox = self.mailboxes.get(rule.mailbox_id)
        connector = self.connectors.get(mailbox.connector_id) if mailbox else None
        if mailbox is None or connector is None or not connector.is_active:
            raise ConnectorUnavailable(str(rule.mailbox_id))
        return rule, mailbox, connector

    async def delete_rule_async(self, rule_pk: uuid.UUID) -> None:
        """Supprime la règle dans Outlook (Graph) PUIS la copie locale.

        Nécessite la permission applicative MailboxSettings.ReadWrite.
        Si Graph échoue, la copie locale est conservée.
        """
        rule, mailbox, connector = await db(self._rule_context)(rule_pk)
        graph = build_graph(connector)
        await (graph.client.users.by_user_id(mailbox.user_principal_name)
               .mail_folders.by_mail_folder_id("inbox")
               .message_rules.by_message_rule_id(rule.rule_id).delete())
        await db(self.mailboxes.delete_rule)(rule_pk)

    def purge_connector(self, connector_id: uuid.UUID) -> int:
        """Supprime boîtes et règles liées à un connecteur (copie locale)."""
        return self.mailboxes.delete_by_connector(connector_id)

    def purge_org(self, org_id: uuid.UUID) -> int:
        """Supprime boîtes, règles et file d'e-mails d'une organisation (copie locale)."""
        return self.mailboxes.delete_by_org(org_id)

    def sync_all(self) -> dict:
        ok, failed = 0, 0
        for mb in self.mailboxes.list_active():
            try:
                self.sync_rules(mb.id)
                ok += 1
            except Exception:
                failed += 1
        return {"synced": ok, "failed": failed}
