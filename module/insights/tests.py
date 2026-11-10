import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import AsyncClient, SimpleTestCase, TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from module.insights.application.alert_service import AlertService
from module.insights.application.services import BulkService, DashboardService, InvalidBulk, SearchService
from module.insights.domain import alerts as A
from module.insights.infrastructure.models import Alert, BulkActionLog, NotificationSetting
from module.insights.infrastructure.notifier import valid_teams_webhook
from module.microsoft_compliance.infrastructure.snapshot_models import ComplianceSnapshot
from module.microsoft_connector.infrastructure.models import MicrosoftConnector
from module.microsoft_identity.infrastructure.sync_models import MailboxSecuritySnapshot, SignInRecord
from module.microsoft_onedrive.infrastructure.scan_models import SharingScan
from module.microsoft_outlook.infrastructure.models import Mailbox, OutlookRules
from module.organization.infrastructure.models import Organization

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def share(item, scope="anonymous", link_type="edit", external=False, grantees=()):
    return {"drive_id": "d1", "item_id": item, "permission_id": "p" + item, "name": item, "path": "/" + item,
            "drive_label": "OneDrive — Bob", "scope": scope, "link_type": link_type, "roles": [],
            "external": external, "grantees": list(grantees), "risk": "critical"}


def addr(a):
    return {"emailAddress": {"address": a}}


class RuleTests(SimpleTestCase):
    def test_sharing_only_anonymous_and_external(self):
        out = A.sharing_alerts("c", [share("a"), share("b", "anonymous", "view"),
                                     share("c", "direct", "", True, ["x@gmail.com"]), share("d", "organization", "view")])
        self.assertEqual([(a.severity, a.data["item_id"]) for a in out], [("critical", "a"), ("high", "b"), ("medium", "c")])
        self.assertEqual(len({a.key for a in out}), 3)

    def test_rule_forward_outside_and_hiding(self):
        rules = [
            {"id": 1, "mailbox_id": "m", "mailbox_upn": "bob@corp.com", "display_name": "fwd", "is_enabled": True,
             "actions": {"forwardTo": [addr("evil@gmail.com"), addr("ann@corp.com")]}},
            {"id": 2, "mailbox_id": "m", "mailbox_upn": "bob@corp.com", "display_name": "int", "is_enabled": True,
             "actions": {"redirectTo": [addr("ann@corp.com")]}},
            {"id": 3, "mailbox_id": "m", "mailbox_upn": "bob@corp.com", "display_name": "hide", "is_enabled": True,
             "actions": {"delete": True, "markAsRead": True}},
            {"id": 4, "mailbox_id": "m", "mailbox_upn": "bob@corp.com", "display_name": "off", "is_enabled": False,
             "actions": {"forwardTo": [addr("evil@gmail.com")]}},
        ]
        out = A.rule_alerts(rules, ["corp.com"])
        self.assertEqual([(a.key, a.severity) for a in out], [("rule:1:forward", "high"), ("rule:3:hide", "medium")])
        self.assertIn("evil@gmail.com", out[0].detail)
        self.assertNotIn("ann@corp.com", out[0].detail)

    def test_identity_no_mfa_only_when_known(self):
        m = {"id": "m1", "upn": "a@corp.com"}
        pw = [{"kind": "password", "is_mfa": False}]
        self.assertEqual(len(A.identity_alerts(m, pw, {"account_enabled": True})), 1)
        self.assertEqual(A.identity_alerts(m, pw + [{"is_mfa": True}], {}), [])
        self.assertEqual(A.identity_alerts(m, [], {}), [])                       # pas encore synchronisé
        self.assertEqual(A.identity_alerts(m, pw, {"account_enabled": False}), [])  # compte désactivé

    def test_signin_new_country_and_failures(self):
        m = {"id": "m1", "upn": "a@corp.com"}
        old = [{"at": NOW - timedelta(days=d), "success": True, "country": "CA", "ip": "1"} for d in (3, 5)]
        recent = [{"at": NOW - timedelta(hours=2), "success": True, "country": "RU", "ip": "9.9.9.9"}]
        fails = [{"at": NOW - timedelta(hours=1), "success": False, "country": "CA", "ip": "6.6.6.6"}] * 10
        out = A.signin_alerts(m, old + recent + fails, NOW)
        self.assertEqual([a.severity for a in out], ["high", "medium"])
        self.assertIn("RU", out[0].title)
        self.assertEqual(A.signin_alerts(m, recent, NOW), [])   # aucun historique : pas de « nouveau pays »

    def test_governance_and_quarantine(self):
        g = A.governance_alerts("c", [{"code": "mfa", "status": "fail", "severity": "high", "title": "MFA"},
                                      {"code": "x", "status": "pass", "severity": "high"},
                                      {"code": "y", "status": "fail", "severity": "medium"}])
        self.assertEqual([a.key for a in g], ["governance:c:mfa"])
        q = A.quarantine_alerts("c", [{"identity": "1", "type": "Malware"}, {"identity": "2", "type": "Spam"},
                                      {"identity": "3", "type": "HighConfPhish", "released": True}])
        self.assertEqual([a.key for a in q], ["quarantine:1"])

    def test_reconcile_and_severity(self):
        c = A.AlertCandidate("k2", "rule", "high", "t", "d")
        new, gone = A.reconcile({"k1", "k2"}, [c, A.AlertCandidate("k3", "rule", "low", "t", "d")])
        self.assertEqual(([x.key for x in new], gone), (["k3"], {"k1"}))
        self.assertTrue(A.at_least("critical", "high"))
        self.assertFalse(A.at_least("medium", "high"))

    def test_teams_webhook_validation(self):
        self.assertTrue(valid_teams_webhook("https://corp.webhook.office.com/webhookb2/x"))
        self.assertTrue(valid_teams_webhook("https://prod-1.westus.logic.azure.com/workflows/x"))
        for bad in ("http://corp.webhook.office.com/x", "https://evil.com/x", "https://office.com.evil.com/x",
                    "https://169.254.169.254/latest"):
            self.assertFalse(valid_teams_webhook(bad))


class Base(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme")
        self.conn = MicrosoftConnector.objects.create(org_id=self.org.id, name="acme", tenant_id=uuid.uuid4(),
                                                      client_id=uuid.uuid4(), client_secret="s")
        self.box = Mailbox.objects.create(org_id=self.org.id, connector_id=self.conn.id, user_principal_name="bob@acme.com")
        self.rule = OutlookRules.objects.create(
            mailbox=self.box, rule_id="r1", display_name="fwd", sequence=1, is_enabled=True, has_error=False, is_read_only=False,
            actions={"forwardTo": [addr("evil@gmail.com")]})
        self.scan = SharingScan.objects.create(connector_id=self.conn.id, org_id=self.org.id, status="done",
                                               records=[share("a")], summary={"external": 0, "anonymous": 1, "anonymous_edit": 1})
        MailboxSecuritySnapshot.objects.create(mailbox_id=self.box.id, last_synced_at=NOW, profile={"account_enabled": True},
                                               auth_methods=[{"kind": "password", "is_mfa": False}])
        ComplianceSnapshot.objects.create(connector_id=self.conn.id, org_id=self.org.id, score=70,
                                          findings=[{"code": "mfa", "status": "fail", "severity": "high", "title": "MFA"}])
        self.q = [{"identity": "q1", "type": "Malware", "subject": "x", "sender": "a@b.c", "recipients": [], "released": False}]

        def reader(connector):
            async def read():
                return self.q
            return read
        self.svc = AlertService(quarantine_reader=reader, now=lambda: NOW)


class AlertServiceTests(Base):
    def test_evaluate_creates_once_then_updates_and_resolves(self):
        r = self.svc.evaluate(self.conn.id)
        self.assertEqual(r["created"], 5)   # partage, règle, MFA, gouvernance, quarantaine
        self.assertEqual(self.svc.evaluate(self.conn.id)["created"], 0)   # pas de doublon
        # le lien est révoqué, la MFA ajoutée : les alertes « état » se ferment ; la quarantaine (événement) reste
        self.scan.records = []
        self.scan.save()
        MailboxSecuritySnapshot.objects.filter(mailbox_id=self.box.id).update(
            auth_methods=[{"kind": "password", "is_mfa": False}, {"kind": "phone", "is_mfa": True}])
        self.q = []
        r = self.svc.evaluate(self.conn.id)
        self.assertEqual(r["resolved"], 2)
        self.assertTrue(Alert.objects.filter(category="quarantine", status="open").exists())

    def test_acknowledged_is_not_recreated_and_counts(self):
        self.svc.evaluate(self.conn.id)
        a = Alert.objects.get(category="rule")
        AlertService.set_status([a.id], "acknowledged", "admin")
        self.assertEqual(self.svc.evaluate(self.conn.id)["created"], 0)
        self.assertEqual(Alert.objects.get(pk=a.id).status, "acknowledged")
        c = AlertService.counts()
        self.assertEqual(c["total"], 4)
        with self.assertRaises(ValueError):
            AlertService.set_status([a.id], "resolved", "admin")

    def test_quarantine_failure_does_not_resolve_existing(self):
        self.svc.evaluate(self.conn.id)

        def broken(connector):
            async def read():
                raise RuntimeError("401")
            return read
        AlertService(quarantine_reader=broken, now=lambda: NOW).evaluate(self.conn.id)
        self.assertEqual(Alert.objects.get(category="quarantine").status, "open")

    def test_notifications_by_severity(self):
        NotificationSetting.objects.create(org_id=self.org.id, emails=["soc@acme.com"], min_severity="critical")
        with mock.patch("module.insights.infrastructure.notifier.send_mail") as sm:
            self.svc.evaluate(self.conn.id)
        sm.assert_called_once()
        self.assertIn("Lien anonyme", sm.call_args.args[1])
        self.assertNotIn("Compte sans MFA", sm.call_args.args[1])   # « élevée » < seuil « critique »
        self.assertEqual(Alert.objects.filter(notified=True).count(), 1)

    def test_dashboard_and_search(self):
        self.svc.evaluate(self.conn.id)
        d = DashboardService.build()
        t = d["tenants"][0]
        self.assertEqual((t["no_mfa"], t["suspicious_rules"], t["sharing"]["anonymous_edit"]), (1, 1, 1))
        self.assertEqual(d["alerts"]["critical"], 1)
        kinds = {r["type"] for r in SearchService.search("bob")}
        self.assertIn("mailbox", kinds)
        self.assertEqual(SearchService.search("a"), [])   # trop court
        self.assertEqual(SearchService.search("acme")[0]["type"], "org")


class BulkTests(Base):
    def test_requires_word_cap_and_reports_per_item(self):
        class Outlook:
            def __init__(self): self.deleted = []
            async def delete_rule_async(self, pk):
                if str(pk) == "00000000-0000-0000-0000-000000000000":
                    raise RuntimeError("introuvable")
                self.deleted.append(pk)
        o = Outlook()
        svc = BulkService(outlook=o)
        with self.assertRaises(InvalidBulk):
            async_to_sync(svc.delete_rules_async)([self.rule.id], confirm="oui", by="a")
        with self.assertRaises(InvalidBulk):
            async_to_sync(svc.delete_rules_async)([uuid.uuid4()] * 101, confirm="SUPPRIMER", by="a")
        r = async_to_sync(svc.delete_rules_async)([self.rule.id, "00000000-0000-0000-0000-000000000000"],
                                                  confirm="supprimer", by="admin")
        self.assertEqual((r["succeeded"], r["failed"]), (1, 1))
        self.assertEqual(o.deleted, [self.rule.id])
        self.assertEqual(BulkActionLog.objects.get().succeeded, 1)

    def test_revoke_goes_through_sharing_service(self):
        class Sharing:
            def __init__(self): self.calls = []
            async def revoke_async(self, *a): self.calls.append(a)
        s = Sharing()
        r = async_to_sync(BulkService(sharing=s).revoke_shares_async)(
            self.conn.id, [{"drive_id": "d1", "item_id": "a", "permission_id": "pa"}], confirm="REVOQUER", by="admin")
        self.assertEqual(r["succeeded"], 1)
        self.assertEqual(s.calls[0][1:], ("d1", "a", "pa", "admin"))


class ViewTests(Base):
    def setUp(self):
        super().setUp()
        u = get_user_model().objects.create_superuser("adm", password="x")
        self.h = {"Authorization": f"Bearer {AccessToken.for_user(u)}"}
        self.c = AsyncClient()

    def get(self, path):
        return async_to_sync(self.c.get)(path, headers=self.h)

    def send(self, method, path, body):
        return async_to_sync(getattr(self.c, method))(path, body, content_type="application/json", headers=self.h)

    def test_endpoints(self):
        self.assertEqual(async_to_sync(self.c.get)("/api/insights/dashboard/").status_code, 401)
        self.svc.evaluate(self.conn.id)
        self.assertEqual(self.get("/api/insights/dashboard/").status_code, 200)
        rows = self.get("/api/insights/alerts/").json()
        self.assertEqual(rows[0]["severity"], "critical")
        r = self.send("post", "/api/insights/alerts/status/", {"ids": [rows[0]["id"]], "status": "acknowledged"})
        self.assertEqual(r.json()["updated"], 1)
        self.assertEqual(self.get("/api/insights/search/?q=bob").status_code, 200)
        r = self.send("post", "/api/insights/bulk/rules/delete/", {"rule_ids": [str(self.rule.id)], "confirm": "non"})
        self.assertEqual(r.status_code, 400)

    def test_notification_settings_hide_webhook(self):
        url = f"/api/insights/organizations/{self.org.id}/notifications/"
        r = self.send("put", url, {"emails": ["soc@acme.com"], "min_severity": "high",
                                   "teams_webhook": "https://acme.webhook.office.com/x"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["teams_configured"], True)
        self.assertNotIn("teams_webhook", r.json())
        self.assertEqual(self.send("put", url, {"teams_webhook": "https://evil.com/x"}).status_code, 400)
        self.assertEqual(self.send("put", url, {"emails": ["pas une adresse"]}).status_code, 400)
