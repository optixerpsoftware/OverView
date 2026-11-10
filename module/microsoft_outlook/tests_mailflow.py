import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import AsyncClient, SimpleTestCase, TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from module.microsoft_connector.infrastructure.models import MicrosoftConnector
from module.microsoft_outlook.application.mailflow_service import (
    InvalidRequest, MailflowService, QuarantineMessageNotFound, ReleaseNotConfirmed,
)
from module.microsoft_outlook.domain import mailflow as mf
from module.microsoft_outlook.infrastructure.models import QuarantineAction
from module.organization.infrastructure.models import Organization
from shared_kernel.graph_rest import DataUnavailable

NOW = datetime(2026, 11, 9, 12, 30, tzinfo=timezone.utc)
ID1 = "11111111-1111-1111-1111-111111111111\\22222222-2222-2222-2222-222222222222"
ID2 = "33333333-3333-3333-3333-333333333333\\44444444-4444-4444-4444-444444444444"
ID3 = "55555555-5555-5555-5555-555555555555\\66666666-6666-6666-6666-666666666666"


class DomainTests(SimpleTestCase):
    def test_parse_trace_and_states(self):
        m = mf.parse_trace({"MessageTraceId": "t", "Received": "2026-11-09T11:10:00Z", "SenderAddress": "A@x.com",
                            "RecipientAddress": "b@y.com", "Status": "FilteredAsSpam", "Size": "120"})
        self.assertEqual((m.sender, m.state, m.size), ("a@x.com", "spam", 120))
        self.assertEqual(mf.normalize_status("???"), "other")

    def test_summary_counts_hours_and_failure_rate(self):
        rows = [mf.parse_trace({"Received": "2026-11-09T11:10:00Z", "SenderAddress": "a@x.com",
                                "RecipientAddress": "b@y.com", "Status": s}) for s in ("Delivered", "Delivered", "Failed")]
        s = mf.summarize_trace(rows, NOW - timedelta(hours=3), NOW)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["by_state"], {"delivered": 2, "failed": 1})
        self.assertEqual(s["failure_rate"], 33.3)
        self.assertEqual(sum(h.get("delivered", 0) for h in s["per_hour"]), 2)
        self.assertEqual(mf.summarize_trace([], NOW - timedelta(hours=1), NOW)["failure_rate"], 0.0)

    def test_quarantine_parse_and_risk(self):
        q = mf.parse_quarantine({"Identity": ID1, "Type": "Malware", "RecipientAddress": ["A@x.com"],
                                 "ReleaseStatus": "NOTRELEASED"})
        self.assertTrue(q.risky)
        self.assertFalse(q.released)
        self.assertEqual(q.recipients, ("a@x.com",))
        self.assertTrue(mf.parse_quarantine({"Type": "Spam", "ReleaseStatus": "RELEASED"}).released)
        s = mf.summarize_quarantine([q, mf.parse_quarantine({"Type": "Spam"})])
        self.assertEqual((s["total"], s["pending_release"], s["risky"]), (2, 2, 1))

    def test_validators(self):
        self.assertTrue(mf.valid_email("a@b.co"))
        self.assertFalse(mf.valid_email("a b@c"))
        self.assertTrue(mf.valid_identity(ID1))
        for bad in ("", "x", ID1 + "; drop", "../" + ID1, None):
            self.assertFalse(mf.valid_identity(bad))


class FakeGateway:
    def __init__(self, rows=None, fail=False):
        self.rows = rows or {}
        self.released, self.calls, self.fail = [], [], fail

    async def message_trace(self, **kw):
        self.calls.append(("trace", kw))
        return self.rows.get("trace", [])

    async def quarantine(self, **kw):
        self.calls.append(("quarantine", kw))
        return list(self.rows.get("quarantine", {}).values())

    async def quarantine_one(self, identity):
        return self.rows.get("quarantine", {}).get(identity)

    async def release(self, identity, **kw):
        if self.fail:
            raise RuntimeError("boom")
        self.released.append((identity, kw))


class ServiceBase(TransactionTestCase):
    def setUp(self):
        org = Organization.objects.create(name="O")
        self.conn = MicrosoftConnector.objects.create(org_id=org.id, name="c", tenant_id=uuid.uuid4(),
                                                      client_id=uuid.uuid4(), client_secret="s")
        self.gw = FakeGateway({"quarantine": {
            ID1: {"Identity": ID1, "Type": "Spam", "Subject": "Promo", "ReleaseStatus": "NOTRELEASED"},
            ID2: {"Identity": ID2, "Type": "Malware", "Subject": "Facture", "ReleaseStatus": "NOTRELEASED"},
        }, "trace": [{"MessageTraceId": "t", "Received": "2026-11-09T12:00:00Z", "SenderAddress": "a@x.com",
                      "RecipientAddress": "b@y.com", "Status": "Delivered"}]})
        self.svc = MailflowService(gateway_factory=lambda c: self.gw, now=lambda: NOW)


class ServiceTests(ServiceBase):
    def test_trace_validates_and_summarizes(self):
        out = self.svc.trace(self.conn.id, hours=6)
        self.assertEqual(out["summary"]["total"], 1)
        self.assertEqual(self.gw.calls[0][1]["start"], "2026-11-09T06:30:00Z")
        for kw in ({"hours": 0}, {"hours": 999}, {"sender": "bad"}, {"status": "Nope"}):
            with self.assertRaises(InvalidRequest):
                self.svc.trace(self.conn.id, **kw)

    def test_release_ordinary_requires_confirmation_and_is_audited(self):
        with self.assertRaises(ReleaseNotConfirmed):
            self.svc.release(self.conn.id, ID1, confirm="", performed_by="admin")
        self.assertEqual(self.gw.released, [])
        out = self.svc.release(self.conn.id, ID1, confirm="OUI", performed_by="admin")
        self.assertTrue(out["released"])
        self.assertEqual(self.gw.released[0][0], ID1)
        a = QuarantineAction.objects.get()
        self.assertEqual((a.ok, a.performed_by, a.subject), (True, "admin", "Promo"))

    def test_release_risky_needs_typed_word(self):
        with self.assertRaises(ReleaseNotConfirmed) as cm:
            self.svc.release(self.conn.id, ID2, confirm="OUI", performed_by="admin")
        self.assertTrue(cm.exception.needs_word)
        self.assertEqual(self.gw.released, [])
        self.svc.release(self.conn.id, ID2, confirm="LIBERER", performed_by="admin")
        self.assertEqual(len(self.gw.released), 1)

    def test_release_rejects_bad_identity_unknown_and_already_released(self):
        with self.assertRaises(InvalidRequest):
            self.svc.release(self.conn.id, "x; Remove-Mailbox", confirm="OUI", performed_by="a")
        with self.assertRaises(QuarantineMessageNotFound):
            self.svc.release(self.conn.id, ID3, confirm="OUI", performed_by="a")
        self.gw.rows["quarantine"][ID1]["ReleaseStatus"] = "RELEASED"
        with self.assertRaises(InvalidRequest):
            self.svc.release(self.conn.id, ID1, confirm="OUI", performed_by="a")

    def test_failed_release_is_audited_and_reraised(self):
        self.gw.fail = True
        with self.assertRaises(RuntimeError):
            self.svc.release(self.conn.id, ID1, confirm="OUI", performed_by="admin")
        self.assertFalse(QuarantineAction.objects.get().ok)


class ViewTests(ServiceBase):
    def setUp(self):
        super().setUp()
        u = get_user_model().objects.create_superuser("adm", password="x")
        self.h = {"Authorization": f"Bearer {AccessToken.for_user(u)}"}
        self.c = AsyncClient()
        for target, repl in (("ExchangeGateway", lambda *a, **k: self.gw), ("build_graph", lambda c: None)):
            p = mock.patch(f"module.microsoft_outlook.application.mailflow_service.{target}", repl)
            p.start()
            self.addCleanup(p.stop)

    def get(self, path):
        return async_to_sync(self.c.get)(path, headers=self.h)

    def post(self, path, body):
        return async_to_sync(self.c.post)(path, body, content_type="application/json", headers=self.h)

    def test_requires_auth(self):
        r = async_to_sync(self.c.get)(f"/api/outlook/connectors/{self.conn.id}/quarantine/")
        self.assertEqual(r.status_code, 401)

    def test_trace_and_quarantine_endpoints(self):
        r = self.get(f"/api/outlook/connectors/{self.conn.id}/mailflow/trace/?hours=6")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["summary"]["total"], 1)
        r = self.get(f"/api/outlook/connectors/{self.conn.id}/quarantine/")
        self.assertEqual(r.json()["summary"]["total"], 2)
        self.assertEqual(self.get(f"/api/outlook/connectors/{self.conn.id}/mailflow/trace/?hours=6&sender=zz").status_code, 400)
        self.assertEqual(self.get(f"/api/outlook/connectors/{uuid.uuid4()}/quarantine/").status_code, 404)

    def test_release_endpoint_codes(self):
        url = f"/api/outlook/connectors/{self.conn.id}/quarantine/release/"
        r = self.post(url, {"identity": ID2, "confirm": "OUI"})
        self.assertEqual((r.status_code, r.json()["needs_word"]), (409, True))
        self.assertEqual(self.post(url, {"identity": ID2, "confirm": "LIBERER"}).status_code, 200)
        self.assertEqual(len(self.gw.released), 1)
        r = self.get(f"/api/outlook/connectors/{self.conn.id}/quarantine/actions/")
        self.assertEqual(len(r.json()), 1)

    def test_missing_exchange_permission_is_502_with_requires(self):
        async def deny(**kw):
            raise DataUnavailable("Accès refusé", "Exchange.ManageAsApp")
        self.gw.quarantine = deny
        r = self.get(f"/api/outlook/connectors/{self.conn.id}/quarantine/")
        self.assertEqual((r.status_code, r.json()["requires"]), (502, "Exchange.ManageAsApp"))
