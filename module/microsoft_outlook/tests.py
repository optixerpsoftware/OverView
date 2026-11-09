import uuid
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from module.microsoft_connector.infrastructure.models import MicrosoftConnector
from module.microsoft_outlook.application.services import (
    ConnectorUnavailable, OutlookService, RuleReadOnly,
)
from module.microsoft_outlook.infrastructure.models import Mailbox, OutlookRules
from module.organization.infrastructure.models import Organization

BUILD = "module.microsoft_outlook.application.services.build_graph"


def user(mail, name="X", utype="Member", enabled=True):
    return SimpleNamespace(id=f"id-{mail}", mail=mail, display_name=name, user_type=utype, account_enabled=enabled)


class FakeRules:
    def __init__(self, rules): self.rules, self.deleted = rules, []
    async def get(self): return SimpleNamespace(value=self.rules)
    def by_message_rule_id(self, rid):
        outer = self
        class _R:
            async def delete(self_inner): outer.deleted.append(rid)
        return _R()


class FakeGraph:
    """Imite juste la partie du SDK msgraph utilisée par OutlookService."""
    def __init__(self, users=(), rules=()):
        self.pages = list(users)
        self.rules = FakeRules(list(rules))
        graph = self

        class Users:
            async def get(self, request_configuration=None):
                return SimpleNamespace(value=graph.pages[:2], odata_next_link="next" if len(graph.pages) > 2 else None)
            def with_url(self, url):
                class _P:
                    async def get(_s): return SimpleNamespace(value=graph.pages[2:], odata_next_link=None)
                return _P()
            def by_user_id(self, upn):
                return SimpleNamespace(mail_folders=SimpleNamespace(
                    by_mail_folder_id=lambda f: SimpleNamespace(message_rules=graph.rules)))
        self.client = SimpleNamespace(users=Users())


def rule(rid, name, seq=1, ro=False):
    return SimpleNamespace(id=rid, display_name=name, sequence=seq, is_enabled=True, has_error=False,
                           is_read_only=ro, conditions=None, actions=None)


class OutlookServiceTests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.conn = MicrosoftConnector.objects.create(
            org_id=self.org.id, name="c", tenant_id=uuid.uuid4(), client_id=uuid.uuid4(), client_secret="s")
        self.svc = OutlookService()

    def test_discover_filters_guests_disabled_and_mailless_users_and_follows_pages(self):
        graph = FakeGraph(users=[user("b@x.com"), user("a@x.com"), user("g@x.com", utype="Guest"),
                                 user("off@x.com", enabled=False), user(None)])
        with mock.patch(BUILD, return_value=graph):
            found = self.svc.discover_mailboxes(self.conn.id)
        self.assertEqual([d.user_principal_name for d in found], ["a@x.com", "b@x.com"])   # triées, filtrées

    def test_import_is_idempotent_and_only_imports_the_selection(self):
        graph = FakeGraph(users=[user("a@x.com"), user("b@x.com")])
        with mock.patch(BUILD, return_value=graph):
            first = self.svc.import_mailboxes(self.conn.id, ["A@x.com"])
            again = self.svc.import_mailboxes(self.conn.id, ["a@x.com", "b@x.com"])
        self.assertEqual(first, {"imported": ["a@x.com"], "already_present": 0})
        self.assertEqual(again, {"imported": ["b@x.com"], "already_present": 1})
        self.assertEqual(Mailbox.objects.count(), 2)
        self.assertEqual(Mailbox.objects.get(user_principal_name="a@x.com").org_id, self.org.id)

    def test_sync_rules_replaces_local_rules_and_marks_the_mailbox_synced(self):
        mb = Mailbox.objects.create(org_id=self.org.id, connector_id=self.conn.id, user_principal_name="a@x.com")
        with mock.patch(BUILD, return_value=FakeGraph(rules=[rule("r1", "Un", 1), rule("r2", "Deux", 2)])):
            self.assertEqual(self.svc.sync_rules(mb.id), 2)
        with mock.patch(BUILD, return_value=FakeGraph(rules=[rule("r3", "Trois", 1)])):
            self.assertEqual(self.svc.sync_rules(mb.id), 1)      # remplace, n'ajoute pas
        self.assertEqual(list(OutlookRules.objects.values_list("display_name", flat=True)), ["Trois"])
        mb.refresh_from_db()
        self.assertIsNotNone(mb.last_synced_at)

    def test_delete_rule_deletes_in_outlook_first_then_locally(self):
        mb = Mailbox.objects.create(org_id=self.org.id, connector_id=self.conn.id, user_principal_name="a@x.com")
        local = OutlookRules.objects.create(org_id=self.org.id, mailbox=mb, rule_id="r1", display_name="Un", sequence=1,
                                            is_enabled=True, has_error=False, is_read_only=False)
        graph = FakeGraph()
        with mock.patch(BUILD, return_value=graph):
            self.svc.delete_rule(local.id)
        self.assertEqual(graph.rules.deleted, ["r1"])
        self.assertFalse(OutlookRules.objects.exists())

    def test_graph_failure_keeps_the_local_copy(self):
        mb = Mailbox.objects.create(org_id=self.org.id, connector_id=self.conn.id, user_principal_name="a@x.com")
        local = OutlookRules.objects.create(org_id=self.org.id, mailbox=mb, rule_id="r1", display_name="Un", sequence=1,
                                            is_enabled=True, has_error=False, is_read_only=False)
        graph = FakeGraph()

        async def boom(): raise PermissionError("403 MailboxSettings.ReadWrite")
        graph.rules.by_message_rule_id = lambda rid: SimpleNamespace(delete=boom)
        with mock.patch(BUILD, return_value=graph), self.assertRaises(PermissionError):
            self.svc.delete_rule(local.id)
        self.assertTrue(OutlookRules.objects.filter(pk=local.id).exists())

    def test_read_only_rule_and_inactive_connector_are_refused(self):
        mb = Mailbox.objects.create(org_id=self.org.id, connector_id=self.conn.id, user_principal_name="a@x.com")
        ro = OutlookRules.objects.create(org_id=self.org.id, mailbox=mb, rule_id="r1", display_name="RO", sequence=1,
                                         is_enabled=True, has_error=False, is_read_only=True)
        with self.assertRaises(RuleReadOnly):
            self.svc.delete_rule(ro.id)
        MicrosoftConnector.objects.filter(pk=self.conn.id).update(is_active=False)
        with self.assertRaises(ConnectorUnavailable):
            self.svc.sync_rules(mb.id)


class OutlookAsyncViewsTests(TransactionTestCase):
    """Bout en bout : URL -> vue async -> service async -> faux Graph, avec authentification JWT."""

    async def asetUp(self):
        self.org = await Organization.objects.acreate(name="Org")
        self.conn = await MicrosoftConnector.objects.acreate(
            org_id=self.org.id, name="c", tenant_id=uuid.uuid4(), client_id=uuid.uuid4(), client_secret="s")
        u = await get_user_model().objects.acreate(username="op")
        self.h = {"Authorization": f"Bearer {AccessToken.for_user(u)}"}

    async def test_available_then_import_over_http(self):
        await self.asetUp()
        graph = FakeGraph(users=[user("a@x.com", "Alice"), user("b@x.com", "Bob")])
        with mock.patch(BUILD, return_value=graph):
            r = await self.async_client.get(f"/api/outlook/connectors/{self.conn.id}/available-mailboxes/", headers=self.h)
            self.assertEqual((r.status_code, [d["display_name"] for d in r.json()]), (200, ["Alice", "Bob"]))
            r = await self.async_client.post(f"/api/outlook/connectors/{self.conn.id}/import-mailboxes/",
                                             data='{"user_principal_names": ["a@x.com"]}', content_type="application/json",
                                             headers=self.h)
        self.assertEqual((r.status_code, r.json()["imported"]), (201, ["a@x.com"]))

    async def test_requests_without_a_token_are_401_and_bad_payload_is_400(self):
        await self.asetUp()
        url = f"/api/outlook/connectors/{self.conn.id}/import-mailboxes/"
        self.assertEqual((await self.async_client.post(url, data="{}", content_type="application/json")).status_code, 401)
        r = await self.async_client.post(url, data='{"user_principal_names": "pas-une-liste"}',
                                         content_type="application/json", headers=self.h)
        self.assertEqual(r.status_code, 400)

    async def test_unknown_connector_is_404_and_graph_permission_error_is_502_with_requires(self):
        await self.asetUp()
        r = await self.async_client.get(f"/api/outlook/connectors/{uuid.uuid4()}/available-mailboxes/", headers=self.h)
        self.assertEqual(r.status_code, 404)
        from shared_kernel.graph_rest import DataUnavailable

        class Denied:
            class client:
                class users:
                    @staticmethod
                    async def get(request_configuration=None): raise DataUnavailable("refusé", "User.Read.All")

        with mock.patch(BUILD, return_value=Denied):
            r = await self.async_client.get(f"/api/outlook/connectors/{self.conn.id}/available-mailboxes/", headers=self.h)
        self.assertEqual((r.status_code, r.json()["requires"]), (502, "User.Read.All"))

    async def test_rule_delete_and_sync_endpoints(self):
        await self.asetUp()
        mb = await Mailbox.objects.acreate(org_id=self.org.id, connector_id=self.conn.id, user_principal_name="a@x.com")
        graph = FakeGraph(rules=[rule("r1", "Un")])
        with mock.patch(BUILD, return_value=graph):
            r = await self.async_client.post(f"/api/outlook/mailboxes/{mb.id}/sync/", headers=self.h)
            self.assertEqual((r.status_code, r.json()), (200, {"rules": 1}))
            local = await OutlookRules.objects.aget(rule_id="r1")
            self.assertEqual((await self.async_client.get(f"/api/outlook/rules/{local.id}/", headers=self.h)).status_code, 200)
            self.assertEqual((await self.async_client.delete(f"/api/outlook/rules/{local.id}/", headers=self.h)).status_code, 204)
        self.assertEqual(graph.rules.deleted, ["r1"])
        self.assertEqual((await self.async_client.delete(f"/api/outlook/rules/{local.id}/", headers=self.h)).status_code, 404)

    async def test_drf_crud_routes_still_work_next_to_the_async_ones(self):
        await self.asetUp()
        r = await self.async_client.get(f"/api/outlook/mailboxes/?org_id={self.org.id}", headers=self.h)
        self.assertEqual((r.status_code, r.json()), (200, []))
