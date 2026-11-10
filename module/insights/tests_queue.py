import uuid
from datetime import timedelta

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import AsyncClient, SimpleTestCase, TransactionTestCase
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from module.insights.application.queue_service import QueueService
from module.insights.domain.queue import quick_actions
from module.insights.infrastructure.models import Alert
from module.microsoft_connector.infrastructure.models import MicrosoftConnector
from module.organization.infrastructure.models import Organization

MB = "11111111-2222-3333-4444-555555555555"


def al(category, **kw):
    return {"connector_id": "c1", "category": category, "severity": kw.pop("severity", "high"), "title": kw.pop("title", "x"),
            "data": kw.pop("data", {}), "link": kw.pop("link", ""), **kw}


class ActionTests(SimpleTestCase):
    def kinds(self, a):
        return [x["kind"] for x in quick_actions(a)]

    def test_actions_per_category(self):
        share = al("sharing", data={"drive_id": "d", "item_id": "i", "permission_id": "p", "web_url": "https://x"}, link="/sharing/c1")
        acts = quick_actions(share)
        self.assertEqual([a["kind"] for a in acts], ["revoke_share", "open_external", "open"])
        self.assertEqual(acts[0]["path"], "/onedrive/connectors/c1/drives/d/items/i/permissions/p/")
        self.assertEqual(self.kinds(al("rule", data={"rule_id": "r9"}, link=f"/mailboxes/{MB}"))[0], "delete_rule")
        app = quick_actions(al("app", data={"sp_id": "sp1"}))[0]
        self.assertEqual((app["kind"], app["body"]["confirm"]), ("block_app", "DESACTIVER"))

    def test_identity_actions_need_mailbox_and_upn(self):
        a = al("identity", severity="critical", title="MFA ajouté de façon suspecte : bob@corp.com", link=f"/mailboxes/{MB}")
        acts = quick_actions(a)
        self.assertEqual([x["kind"] for x in acts], ["quarantine", "revoke_sessions", "open"])
        self.assertEqual((acts[0]["require_text"], acts[0]["body"]["confirm"]), ("bob@corp.com", "bob@corp.com"))
        low = al("identity", severity="medium", title="Nouvelle méthode MFA : bob@corp.com", link=f"/mailboxes/{MB}")
        self.assertEqual(self.kinds(low), ["revoke_sessions", "open"])            # pas de quarantaine pour une alerte moyenne
        self.assertEqual(self.kinds(al("governance", link="/compliance/c1")), ["open"])
        self.assertEqual(self.kinds(al("identity", title="sans adresse", link=f"/mailboxes/{MB}")), ["open"])


class QueueTests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Client A")
        self.conn = MicrosoftConnector.objects.create(org_id=self.org.id, name="a.ca", tenant_id=uuid.uuid4(),
                                                      client_id=uuid.uuid4(), client_secret="s")
        mk = lambda k, sev, st="open": Alert.objects.create(connector_id=self.conn.id, org_id=self.org.id, key=k, category="governance",
                                                           severity=sev, title=k, status=st, link="/compliance/x")
        self.a1, self.a2, self.a3 = mk("k1", "medium"), mk("k2", "critical"), mk("k3", "high", "acknowledged")

    def test_order_snooze_assign_note_and_stats(self):
        self.assertEqual([r["key"] for r in QueueService.items("bob")], ["k2", "k1", "k3"])   # nouvelles, gravité, puis prises en compte
        self.assertEqual(QueueService.items("bob")[0]["org_name"], "Client A")
        QueueService.snooze([self.a2.id], 4)
        self.assertNotIn("k2", [r["key"] for r in QueueService.items("bob")])
        with self.assertRaises(ValueError):
            QueueService.snooze([self.a2.id], 5)
        QueueService.assign([self.a1.id], "bob")
        self.assertEqual([r["key"] for r in QueueService.items("bob", scope="mine")], ["k1"])
        QueueService.set_note(self.a1.id, "Client prévenu", "bob")
        self.assertIn("bob] Client prévenu", Alert.objects.get(pk=self.a1.id).note)
        Alert.objects.filter(pk=self.a3.id).update(acknowledged_at=timezone.now())
        s = QueueService.stats("bob")
        self.assertEqual((s["todo"], s["snoozed"], s["mine"], s["handled_today"]), (2, 1, 1, 1))
        Alert.objects.filter(pk=self.a2.id).update(snoozed_until=timezone.now() - timedelta(minutes=1))   # report échu
        self.assertIn("k2", [r["key"] for r in QueueService.items("bob")])

    def test_views_and_roles(self):
        admin = get_user_model().objects.create_superuser("adm", password="x")
        viewer = get_user_model().objects.create_user("lect", password="x")
        c = AsyncClient()
        h = lambda u: {"Authorization": f"Bearer {AccessToken.for_user(u)}"}
        r = async_to_sync(c.get)("/api/insights/queue/", headers=h(viewer))
        self.assertEqual((r.status_code, len(r.json()["items"])), (200, 3))
        r = async_to_sync(c.post)("/api/insights/queue/assign/", {"ids": [str(self.a1.id)], "to": "me"},
                                  content_type="application/json", headers=h(viewer))
        self.assertEqual(r.status_code, 403)                                    # lecture seule : pas d'assignation
        r = async_to_sync(c.post)("/api/insights/queue/assign/", {"ids": [str(self.a1.id)], "to": "me"},
                                  content_type="application/json", headers=h(admin))
        self.assertEqual(r.json()["updated"], 1)
        self.assertEqual(Alert.objects.get(pk=self.a1.id).assigned_to, "adm")
        r = async_to_sync(c.post)("/api/insights/queue/snooze/", {"ids": [str(self.a1.id)], "hours": 24},
                                  content_type="application/json", headers=h(admin))
        self.assertEqual(r.json()["updated"], 1)
        r = async_to_sync(c.post)(f"/api/insights/alerts/{self.a1.id}/note/", {"text": "Appel client"},
                                  content_type="application/json", headers=h(admin))
        self.assertIn("Appel client", r.json()["note"])
