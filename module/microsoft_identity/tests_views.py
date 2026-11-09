"""Bout en bout des vues async d'identity : URL -> vue async (Daphne) -> service async -> faux Graph, avec JWT."""
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from module.microsoft_connector.infrastructure.models import MicrosoftConnector
from module.microsoft_identity.infrastructure.audit_models import SecurityAction
from module.microsoft_identity.infrastructure.sync_models import MailboxSecuritySnapshot
from module.microsoft_outlook.infrastructure.models import Mailbox
from module.organization.infrastructure.models import Organization

from .tests import FakeGateway

GW = "module.microsoft_identity.application.services._default_gateway"
SYNC_GW = "module.microsoft_identity.application.security_sync._default_gateway"
DELAY = "module.microsoft_identity.interface.tasks.sync_mailbox_security.delay"
UPN = "victim@example.com"


class IdentityViewsTests(TransactionTestCase):
    async def asetUp(self):
        self.org = await Organization.objects.acreate(name="Org")
        self.conn = await MicrosoftConnector.objects.acreate(
            org_id=self.org.id, name="c", tenant_id=uuid.uuid4(), client_id=uuid.uuid4(), client_secret="s")
        self.mb = await Mailbox.objects.acreate(org_id=self.org.id, connector_id=self.conn.id, user_principal_name=UPN)
        u = await get_user_model().objects.acreate(username="op")
        self.h = {"Authorization": f"Bearer {AccessToken.for_user(u)}"}
        self.base = f"/api/identity/mailboxes/{self.mb.id}"
        self.gw = FakeGateway()
        self.gw.methods = []

    def patches(self):
        # défauts de passerelle remplacés ; la mise en file Celery (Redis) est simulée
        return (mock.patch(GW, return_value=self.gw), mock.patch(SYNC_GW, return_value=self.gw), mock.patch(DELAY))

    async def post(self, path, body=None, **kw):
        return await self.async_client.post(f"{self.base}{path}", data=body if body is not None else "",
                                            content_type="application/json", headers=self.h, **kw)

    async def test_every_identity_endpoint_requires_a_token(self):
        await self.asetUp()
        for method, path in (("get", "/security/"), ("get", "/security/activity/"), ("post", "/security/sync/"),
                             ("post", "/revoke-sessions/"), ("post", "/emergency/"), ("post", "/reset-password/"),
                             ("post", "/auth-methods/"), ("delete", "/devices/x/"), ("delete", "/auth-methods/x/")):
            r = await getattr(self.async_client, method)(f"{self.base}{path}")
            self.assertEqual(r.status_code, 401, msg=f"{method} {path}")

    async def test_revoke_sessions_calls_graph_logs_and_triggers_a_sync(self):
        await self.asetUp()
        p1, p2, delay = self.patches()
        with p1, p2, delay as d:
            r = await self.post("/revoke-sessions/")
        self.assertEqual((r.status_code, r.json()), (200, {"ok": True}))
        self.assertEqual(self.gw.calls, ["revoke"])
        self.assertTrue(await SecurityAction.objects.filter(action="revoke_sessions", ok=True, performed_by="op").aexists())
        d.assert_called_once_with(str(self.mb.id))                     # l'instantané local sera rafraîchi

    async def test_emergency_requires_exact_confirmation_then_runs_the_pipeline_and_hides_the_password(self):
        await self.asetUp()
        p1, p2, delay = self.patches()
        with p1, p2, delay:
            bad = await self.post("/emergency/", '{"confirm": "autre@x.com"}')
            self.assertEqual(bad.status_code, 400)
            self.assertEqual(self.gw.calls, [])                         # rien d'exécuté sans bonne confirmation
            ok = await self.post("/emergency/", f'{{"confirm": "{UPN}"}}')
        body = ok.json()
        self.assertEqual((ok.status_code, body["ok"], len(body["steps"])), (200, True, 3))
        self.assertEqual(self.gw.calls, ["revoke", "reset", "revoke"])
        self.assertEqual(body["password"], self.gw.password)
        self.assertEqual(ok["Cache-Control"], "no-store")
        for a in [a async for a in SecurityAction.objects.all()]:
            self.assertNotIn(body["password"], a.detail)

    async def test_reset_password_returns_it_once_with_no_store(self):
        await self.asetUp()
        p1, p2, delay = self.patches()
        with p1, p2, delay:
            r = await self.post("/reset-password/", f'{{"confirm": "{UPN}"}}')
        self.assertEqual((r.status_code, r["Cache-Control"]), (200, "no-store"))
        self.assertEqual(r.json()["password"], self.gw.password)

    async def test_add_phone_validation_and_graph_conflict_message(self):
        await self.asetUp()
        p1, p2, delay = self.patches()
        with p1, p2, delay:
            bad = await self.post("/auth-methods/", f'{{"kind": "phone", "value": "5145551234", "confirm": "{UPN}"}}')
            self.assertEqual(bad.status_code, 400)
            ok = await self.post("/auth-methods/", f'{{"kind": "phone", "value": "+1 5145551234", "confirm": "{UPN}"}}')
            self.assertEqual((ok.status_code, ok.json()["ok"]), (201, True))
            from shared_kernel.graph_rest import GraphError

            async def conflict(*a): raise GraphError(409, "Un numéro de ce type existe déjà.")
            self.gw.add_phone_method = conflict
            dup = await self.post("/auth-methods/", f'{{"kind": "phone", "value": "+1 5145551234", "confirm": "{UPN}"}}')
        self.assertEqual((dup.status_code, dup.json()["detail"]), (409, "Graph 409: Un numéro de ce type existe déjà."))

    async def test_graph_permission_error_is_502_with_the_missing_permission(self):
        await self.asetUp()
        from shared_kernel.graph_rest import DataUnavailable

        async def denied(upn): raise DataUnavailable("Accès refusé.", "User.ReadWrite.All")
        self.gw.revoke_sessions = denied
        p1, p2, delay = self.patches()
        with p1, p2, delay:
            r = await self.post("/revoke-sessions/")
        self.assertEqual((r.status_code, r.json()["requires"]), (502, "User.ReadWrite.All"))
        self.assertTrue(await SecurityAction.objects.filter(action="revoke_sessions", ok=False).aexists())

    async def test_delete_device_over_http_checks_ownership_and_name(self):
        await self.asetUp()
        p1, p2, delay = self.patches()
        with p1, p2, delay:
            unknown = await self.async_client.delete(f"{self.base}/devices/zzz/", data='{"confirm": "PC-TEST"}',
                                                    content_type="application/json", headers=self.h)
            wrong = await self.async_client.delete(f"{self.base}/devices/obj-1/", data='{"confirm": "autre"}',
                                                  content_type="application/json", headers=self.h)
            ok = await self.async_client.delete(f"{self.base}/devices/obj-1/", data='{"confirm": "pc-test"}',
                                               content_type="application/json", headers=self.h)
        self.assertEqual((unknown.status_code, wrong.status_code, ok.status_code), (404, 400, 204))
        self.assertEqual(self.gw.calls, ["delete:obj-1"])

    async def test_unknown_mailbox_is_404(self):
        await self.asetUp()
        r = await self.async_client.post(f"/api/identity/mailboxes/{uuid.uuid4()}/revoke-sessions/", headers=self.h)
        self.assertEqual(r.status_code, 404)

    async def test_overview_is_served_from_the_local_snapshot_and_first_call_starts_a_sync(self):
        await self.asetUp()
        p1, p2, delay = self.patches()
        with p1, p2, delay as d:
            first = await self.async_client.get(f"{self.base}/security/", headers=self.h)
            self.assertEqual((first.status_code, first.json()["profile"], first.json()["sync"]["status"]),
                             (200, None, "running"))
            d.assert_called_once()
            # le worker (simulé) termine la synchro
            from module.microsoft_identity.application.security_sync import SecuritySyncService
            from asgiref.sync import sync_to_async
            await sync_to_async(SecuritySyncService().run_sync)(self.mb.id)
            second = await self.async_client.get(f"{self.base}/security/", headers=self.h)
            act = await self.async_client.get(f"{self.base}/security/activity/", headers=self.h)
        data = second.json()
        self.assertEqual((data["profile"]["display_name"], data["sync"]["status"]), ("Test", "idle"))
        self.assertEqual([x["display_name"] for x in data["devices"]["items"]], ["PC-TEST"])
        self.assertEqual((act.status_code, set(act.json())), (200, {"sign_ins", "active_sessions", "sync"}))
        self.assertEqual(d.call_count, 1)                              # pas de 2e synchro lancée

    async def test_manual_sync_returns_202_and_does_not_start_twice(self):
        await self.asetUp()
        p1, p2, delay = self.patches()
        with p1, p2, delay as d:
            a = await self.post("/security/sync/")
            b = await self.post("/security/sync/")
        self.assertEqual((a.status_code, a.json()), (202, {"started": True}))
        self.assertEqual((b.status_code, b.json()), (202, {"started": False}))
        self.assertEqual(d.call_count, 1)
        self.assertEqual((await MailboxSecuritySnapshot.objects.aget(mailbox_id=self.mb.id)).status, "running")
