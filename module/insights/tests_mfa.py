from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

from django.test import SimpleTestCase, TransactionTestCase

from module.insights.domain.alerts import mfa_change_alerts
from module.microsoft_identity.application.security_sync import SecuritySyncService
from module.microsoft_identity.infrastructure.sync_models import AuthMethodChange

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
MB = {"id": "m1", "upn": "bob@corp.com"}


def si(hours_ago, country="CA", success=True):
    return {"at": NOW - timedelta(hours=hours_ago), "country": country, "success": success, "ip": "1.1.1.1"}


class RuleTests(SimpleTestCase):
    def test_plain_addition_is_medium(self):
        added = [{"method_id": "p1", "kind": "phone", "label": "Téléphone", "detail": "+1 555", "at": NOW - timedelta(hours=1)}]
        a = mfa_change_alerts(MB, added, [si(72), si(48), si(3)], NOW)[0]
        self.assertEqual((a.severity, a.key), ("medium", "mfa-added:m1:p1"))

    def test_addition_after_new_country_is_critical(self):
        added = [{"method_id": "a1", "kind": "authenticator", "label": "Authenticator", "detail": "", "at": NOW - timedelta(hours=1)}]
        a = mfa_change_alerts(MB, added, [si(72), si(48), si(2, "NG")], NOW)[0]
        self.assertEqual(a.severity, "critical")
        self.assertIn("NG", a.detail)
        a = mfa_change_alerts(MB, added, [si(72)] + [si(5, success=False)] * 6, NOW)[0]
        self.assertEqual(a.severity, "critical")


class RecordTests(TransactionTestCase):
    def test_changes_recorded_only_with_a_reference(self):
        mb = SimpleNamespace(id=uuid.uuid4(), org_id=uuid.uuid4())
        pw = {"id": "pw", "kind": "password", "label": "Mot de passe"}
        phone = {"id": "p1", "kind": "phone", "label": "Téléphone", "detail": "+1"}
        auth = {"id": "a1", "kind": "authenticator", "label": "Authenticator", "detail": "iPhone"}
        SecuritySyncService._record_method_changes(mb, {}, [pw, phone])                       # première synchro
        self.assertEqual(AuthMethodChange.objects.count(), 0)
        old = {"auth_methods": [pw, phone], "auth_methods_error": "", "last_synced_at": NOW}
        SecuritySyncService._record_method_changes(mb, old, [pw, auth])
        self.assertEqual(sorted((c.change, c.method_id) for c in AuthMethodChange.objects.all()), [("added", "a1"), ("removed", "p1")])
        SecuritySyncService._record_method_changes(mb, {**old, "auth_methods_error": "403"}, [pw])   # lecture précédente en erreur
        self.assertEqual(AuthMethodChange.objects.count(), 2)
