import uuid
from datetime import datetime, timedelta, timezone

from django.test import TestCase

from module.microsoft_connector.domain.entities import ConnectorEntity
from module.microsoft_identity.application.security_sync import SecuritySyncService
from module.microsoft_identity.application.services import ConnectorUnavailable
from module.microsoft_identity.domain.entities import SignInInfo
from module.microsoft_identity.infrastructure.sync_models import MailboxSecuritySnapshot, SignInRecord
from module.microsoft_outlook.domain.entities import MailboxEntity

from .tests import FakeGateway


class _Mailboxes:
    def __init__(self, mb): self.mb = mb
    def get(self, _id): return self.mb
    def list_active(self): return [self.mb]


class _Connectors:
    def __init__(self, active=True): self.active = active
    def get(self, _id):
        c = ConnectorEntity(org_id=uuid.uuid4(), name="c", tenant_id=uuid.uuid4(), client_id=uuid.uuid4(), client_secret="s")
        c.is_active = self.active
        return c


def si(gid, at, ok=True, app="Outlook", ip="1.1.1.1"):
    return SignInInfo(id=gid, at=at, app=app, ip=ip, city="", country="", client="", os="Windows", browser="Edge",
                      interactive=True, success=ok, error_code=0 if ok else 50126)


NOW = datetime.now(timezone.utc)


class SecuritySyncTests(TestCase):
    def setUp(self):
        self.mb = MailboxEntity(org_id=uuid.uuid4(), connector_id=uuid.uuid4(), user_principal_name="victim@example.com")
        self.gw = FakeGateway()
        self.gw.methods = []
        self.gw.sign_in_calls = []
        self.queue = []
        self.svc = SecuritySyncService(mailboxes=_Mailboxes(self.mb), connectors=_Connectors(),
                                       gateway_factory=lambda c: self.gw, enqueue=self.queue.append)

    # ---- collecte incrémentale ----
    def test_first_sync_fetches_30_days_then_only_what_is_new(self):
        self.gw.sign_ins = [si("a", NOW - timedelta(days=2)), si("b", NOW - timedelta(hours=1))]
        self.svc.sync_mailbox(self.mb.id)
        first_since = self.gw.sign_in_calls[0]
        self.assertAlmostEqual((NOW - first_since).total_seconds(), 30 * 86400, delta=60)
        self.assertEqual(SignInRecord.objects.count(), 2)

        self.gw.sign_ins = [si("b", NOW - timedelta(hours=1)), si("c", NOW - timedelta(minutes=2))]  # b déjà connu
        out = self.svc.sync_mailbox(self.mb.id)
        second_since = self.gw.sign_in_calls[1]
        # curseur = connexion la plus récente stockée (b) moins un petit chevauchement : plus de 30 jours en arrière
        self.assertLess(NOW - second_since, timedelta(hours=2))
        self.assertGreater(NOW - second_since, timedelta(minutes=50))
        self.assertEqual(out["new_sign_ins"], 1)                       # seule « c » est nouvelle
        self.assertEqual(SignInRecord.objects.count(), 3)              # pas de doublon de « b »

    def test_old_records_are_pruned_after_90_days(self):
        self.gw.sign_ins = [si("old", NOW - timedelta(days=120)), si("new", NOW - timedelta(days=1))]
        self.svc.sync_mailbox(self.mb.id)
        self.assertEqual(list(SignInRecord.objects.values_list("graph_id", flat=True)), ["new"])

    # ---- lecture locale ----
    def test_overview_reads_the_local_snapshot_without_calling_graph(self):
        self.svc.sync_mailbox(self.mb.id)
        self.gw.calls.clear()
        self.gw.sign_in_calls.clear()
        data = self.svc.overview(self.mb.id)
        self.assertEqual(self.gw.sign_in_calls, [])                    # aucune requête sortante à la lecture
        self.assertEqual(data["profile"]["display_name"], "Test")
        self.assertEqual([d["display_name"] for d in data["devices"].items], ["PC-TEST"])
        self.assertEqual(data["sync"]["status"], "idle")
        self.assertIsNotNone(data["sync"]["last_synced_at"])

    def test_first_overview_starts_a_background_sync_and_returns_no_profile_yet(self):
        data = self.svc.overview(self.mb.id)
        self.assertIsNone(data["profile"])
        self.assertEqual(data["sync"]["status"], "running")
        self.assertEqual(self.queue, [str(self.mb.id)])
        self.svc.overview(self.mb.id)                                  # 2e ouverture : pas de 2e file d'attente
        self.assertEqual(len(self.queue), 1)

    def test_activity_estimates_sessions_from_stored_sign_ins(self):
        self.gw.sign_ins = [si("a", NOW - timedelta(days=1)), si("b", NOW - timedelta(days=2)),
                            si("c", NOW - timedelta(days=3), ok=False)]
        self.svc.sync_mailbox(self.mb.id)
        act = self.svc.activity(self.mb.id)
        self.assertEqual(len(act["sign_ins"].items), 3)
        self.assertEqual(len(act["active_sessions"].items), 1)         # a et b regroupées ; l'échec est ignoré
        self.assertEqual(act["active_sessions"].items[0].count, 2)

    # ---- erreurs et verrou ----
    def test_a_failing_section_keeps_its_last_known_data_and_shows_the_error(self):
        self.svc.sync_mailbox(self.mb.id)

        async def boom(upn): raise PermissionError("403 devices")
        self.gw.list_devices = boom
        self.svc.sync_mailbox(self.mb.id)
        data = self.svc.overview(self.mb.id)
        self.assertEqual([d["display_name"] for d in data["devices"].items], ["PC-TEST"])   # dernières données conservées
        self.assertIn("403", data["devices"].error)
        self.assertEqual(data["sync"]["status"], "idle")

    def test_sign_in_failure_is_reported_but_does_not_fail_the_sync(self):
        async def boom(user_id, top=100, since=None): raise PermissionError("403 AuditLog")
        self.gw.list_sign_ins = boom
        self.svc.sync_mailbox(self.mb.id)
        act = self.svc.activity(self.mb.id)
        self.assertIn("403", act["sign_ins"].error)
        self.assertEqual(self.svc.overview(self.mb.id)["sync"]["status"], "idle")

    def test_profile_failure_marks_the_sync_failed_with_the_message(self):
        async def boom(upn): raise RuntimeError("auth KO")
        self.gw.get_profile = boom
        with self.assertRaises(RuntimeError):
            self.svc.sync_mailbox(self.mb.id)
        snap = MailboxSecuritySnapshot.objects.get(mailbox_id=self.mb.id)
        self.assertEqual(snap.status, "failed")
        self.assertIn("auth KO", snap.last_error)

    def test_only_one_sync_at_a_time(self):
        self.assertTrue(self.svc.request_sync(self.mb.id))
        self.assertFalse(self.svc.request_sync(self.mb.id))
        self.assertEqual(self.svc.sync_mailbox(self.mb.id), {"skipped": "synchronisation déjà en cours"})

    def test_stale_running_lock_is_released_after_15_minutes(self):
        self.svc.request_sync(self.mb.id)
        MailboxSecuritySnapshot.objects.update(started_at=datetime.now(timezone.utc) - timedelta(minutes=20))
        self.assertTrue(self.svc.request_sync(self.mb.id))             # worker mort : on peut relancer

    def test_broker_down_releases_the_lock(self):
        def boom(_): raise ConnectionError("redis")
        svc = SecuritySyncService(mailboxes=_Mailboxes(self.mb), connectors=_Connectors(),
                                  gateway_factory=lambda c: self.gw, enqueue=boom)
        with self.assertRaises(ConnectionError):
            svc.request_sync(self.mb.id)
        self.assertEqual(MailboxSecuritySnapshot.objects.get(mailbox_id=self.mb.id).status, "failed")
        self.assertTrue(self.svc.request_sync(self.mb.id))             # le verrou n'est pas resté bloqué

    # ---- planification ----
    def test_sync_all_queues_each_active_mailbox_once(self):
        self.assertEqual(self.svc.sync_all(), {"queued": 1, "skipped": 0})
        self.assertEqual(self.svc.sync_all(), {"queued": 0, "skipped": 1})   # déjà en cours
        self.assertEqual(len(self.queue), 1)

    def test_inactive_connector_is_refused(self):
        svc = SecuritySyncService(mailboxes=_Mailboxes(self.mb), connectors=_Connectors(active=False),
                                  gateway_factory=lambda c: self.gw, enqueue=self.queue.append)
        with self.assertRaises(ConnectorUnavailable):
            svc.request_sync(self.mb.id)
        self.assertEqual(svc.sync_all(), {"queued": 0, "skipped": 1})


class RealtimeEventsTests(TestCase):
    """Les changements d'état de synchro sont publiés (WebSocket) pour que les pages se mettent à jour sans sondage."""

    def setUp(self):
        from unittest import mock
        self.mb = MailboxEntity(org_id=uuid.uuid4(), connector_id=uuid.uuid4(), user_principal_name="victim@example.com")
        self.gw = FakeGateway()
        self.gw.methods = []
        self.svc = SecuritySyncService(mailboxes=_Mailboxes(self.mb), connectors=_Connectors(),
                                       gateway_factory=lambda c: self.gw, enqueue=lambda _id: None)
        patcher = mock.patch("module.microsoft_identity.application.security_sync.publish")
        self.publish = patcher.start()
        self.addCleanup(patcher.stop)

    def statuses(self):
        return [c.args[1]["status"] for c in self.publish.call_args_list if c.args[0] == "security.sync"]

    def test_request_then_run_publishes_running_then_idle(self):
        self.svc.request_sync(self.mb.id)
        self.svc.run_sync(self.mb.id)
        self.assertEqual(self.statuses(), ["running", "idle"])
        last = self.publish.call_args_list[-1].args[1]
        self.assertEqual(last["mailbox_id"], str(self.mb.id))
        self.assertIsNotNone(last["last_synced_at"])

    def test_failed_sync_publishes_failed(self):
        async def boom(upn): raise RuntimeError("auth KO")
        self.gw.get_profile = boom
        self.svc.request_sync(self.mb.id)
        with self.assertRaises(RuntimeError):
            self.svc.run_sync(self.mb.id)
        self.assertEqual(self.statuses(), ["running", "failed"])
