import uuid
from datetime import date, datetime, timedelta, timezone

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import AsyncClient, SimpleTestCase, TransactionTestCase
from django.utils import timezone as djtz
from rest_framework_simplejwt.tokens import AccessToken

from module.insights.application.services import DashboardService, ReportService
from module.insights.domain import report as rp
from module.insights.infrastructure.models import Alert
from module.microsoft_compliance.infrastructure.snapshot_models import ComplianceSnapshot
from module.microsoft_connector.infrastructure.models import MicrosoftConnector
from module.microsoft_onedrive.infrastructure.scan_models import SharingAction
from module.organization.infrastructure.models import Organization

T = date(2026, 11, 9)


def at(d, h=12):
    return datetime(d.year, d.month, d.day, h, tzinfo=timezone.utc)


class DomainTests(SimpleTestCase):
    def test_trend_counts_active_per_day(self):
        alerts = [
            {"severity": "high", "first_seen": at(T - timedelta(days=2)), "resolved_at": at(T - timedelta(days=1))},
            {"severity": "critical", "first_seen": at(T - timedelta(days=1)), "resolved_at": None},
            {"severity": "high", "first_seen": at(T - timedelta(days=40)), "resolved_at": None},   # ancienne, toujours ouverte
        ]
        tr = rp.alert_trend(alerts, T, 3)
        self.assertEqual([d["day"] for d in tr], ["2026-11-07", "2026-11-08", "2026-11-09"])
        self.assertEqual([d["active"] for d in tr], [2, 2, 2])
        self.assertEqual((tr[0]["high"], tr[1]["critical"], tr[1]["resolved"]), (1, 1, 1))

    def test_breakdown_mttr_delta_highlights(self):
        a = [{"severity": "high", "category": "sharing", "first_seen": at(T), "resolved_at": at(T, 18)},
             {"severity": "low", "category": "rule", "first_seen": at(T), "resolved_at": None}]
        self.assertEqual(rp.breakdown(a)["by_severity"]["high"], 1)
        self.assertEqual(rp.mean_time_to_resolve(a), 6.0)
        self.assertIsNone(rp.mean_time_to_resolve([a[1]]))
        self.assertEqual(rp.score_delta([70, 72.5, 80]), 10.0)
        self.assertIsNone(rp.score_delta([80]))
        hl = rp.highlights({"alerts": {"open_by_severity": {"critical": 2}, "resolved": 0}, "score": 80, "score_delta": 5.0,
                            "sharing": {"external": 3}, "hygiene": {"apps_high": 1}, "actions": []})
        self.assertTrue(hl[0].startswith("2 alerte(s) critique(s)"))
        self.assertIn("+5.0 pts", hl[1])
        self.assertEqual(rp.highlights({"alerts": {"open_by_severity": {}, "resolved": 0}}), ["Aucun point d'attention sur la période."])


class ReportTests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Client")
        self.conn = MicrosoftConnector.objects.create(org_id=self.org.id, name="client.ca", tenant_id=uuid.uuid4(),
                                                      client_id=uuid.uuid4(), client_secret="s")
        now = djtz.now()
        Alert.objects.create(connector_id=self.conn.id, org_id=self.org.id, key="k1", category="sharing", severity="critical",
                             title="Lien anonyme", status="open")
        r = Alert.objects.create(connector_id=self.conn.id, org_id=self.org.id, key="k2", category="rule", severity="high",
                                 title="Transfert", status="resolved", resolved_at=now)
        Alert.objects.filter(pk=r.pk).update(first_seen=now - timedelta(hours=10))
        for s in (70, 85):
            ComplianceSnapshot.objects.create(connector_id=self.conn.id, org_id=self.org.id, score=s,
                                              findings=[{"title": "MFA", "status": "fail", "severity": "high"}])
        SharingAction.objects.create(connector_id=self.conn.id, org_id=self.org.id, drive_id="d", item_id="i",
                                     permission_id="p", description="lien anonyme", ok=True, detail="", performed_by="admin")

    def test_report(self):
        r = ReportService.build(self.org.id, 30)
        t = r["tenants"][0]
        self.assertEqual((t["alerts"]["opened"], t["alerts"]["resolved"]), (2, 1))
        self.assertEqual(t["alerts"]["open_by_severity"]["critical"], 1)
        self.assertEqual(t["alerts"]["mttr_hours"], 10.0)
        self.assertEqual((t["score_delta"], len(t["trend"])), (15.0, 30))
        self.assertEqual(t["actions"][0]["kind"], "Partage révoqué")
        self.assertEqual(t["findings"][0]["title"], "MFA")
        self.assertIsNone(ReportService.build(uuid.uuid4()))
        with self.assertRaises(ValueError):
            ReportService.build(self.org.id, 12)

    def test_dashboard_charts_and_view(self):
        d = DashboardService.build()
        self.assertEqual(len(d["charts"]["trend"]), 30)
        self.assertEqual(d["charts"]["by_severity"]["critical"], 1)
        u = get_user_model().objects.create_superuser("adm", password="x")
        h = {"Authorization": f"Bearer {AccessToken.for_user(u)}"}
        c = AsyncClient()
        url = f"/api/insights/organizations/{self.org.id}/report/?days=7"
        self.assertEqual(async_to_sync(c.get)(url).status_code, 401)
        r = async_to_sync(c.get)(url, headers=h)
        self.assertEqual((r.status_code, r.json()["period_days"]), (200, 7))
        self.assertEqual(async_to_sync(c.get)(f"/api/insights/organizations/{uuid.uuid4()}/report/", headers=h).status_code, 404)
        bad = f"/api/insights/organizations/{self.org.id}/report/?days=12"
        self.assertEqual(async_to_sync(c.get)(bad, headers=h).status_code, 400)
