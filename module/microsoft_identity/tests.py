import uuid
from datetime import datetime, timedelta, timezone

from django.test import TestCase

from module.microsoft_connector.domain.entities import ConnectorEntity
from module.microsoft_identity.application.services import (
    ConfirmationMismatch, DeviceNotOwned, IdentityService,
)
from module.microsoft_identity.domain.entities import DeviceInfo, SignInInfo, UserSecurityProfile
from module.microsoft_identity.domain.sessions import estimate_active_sessions
from module.microsoft_identity.domain.password import generate_password
from module.microsoft_identity.domain.ports import IdentityGateway
from module.microsoft_identity.infrastructure.audit import DjangoAuditLog
from module.microsoft_identity.infrastructure.audit_models import SecurityAction
from module.microsoft_outlook.domain.entities import MailboxEntity


class FakeGateway(IdentityGateway):
    def __init__(self, fail_reset=False, fail_revoke=False):
        self.calls, self.fail_reset, self.fail_revoke = [], fail_reset, fail_revoke
        self.password = None

    async def get_profile(self, upn):
        return UserSecurityProfile("uid", "Test", True, None, None)

    async def list_devices(self, upn):
        return [DeviceInfo(id="obj-1", display_name="PC-TEST", operating_system="Windows",
                           operating_system_version="10", trust_type="AzureAd", is_compliant=None,
                           is_managed=None, last_sign_in=None, registered_at=None)]

    async def delete_device(self, device_object_id):
        self.calls.append(f"delete:{device_object_id}")

    methods = None  # None : lecture refusée (403) ; sinon liste de AuthMethodInfo

    async def list_auth_methods(self, upn):
        if self.methods is None:
            raise PermissionError("403 UserAuthenticationMethod.Read.All")
        return self.methods

    async def delete_auth_method(self, upn, kind, method_id):
        self.calls.append(f"delmethod:{kind}:{method_id}")

    async def add_phone_method(self, upn, phone_number, phone_type):
        self.calls.append(f"addphone:{phone_number}:{phone_type}")

    async def add_email_method(self, upn, email):
        self.calls.append(f"addemail:{email}")

    async def add_totp_method(self, upn, secret, serial, label):
        self.calls.append(f"totp-device:{serial}")
        self.totp_secret = secret

    async def create_temporary_access_pass(self, upn, lifetime_minutes, once):
        self.calls.append(f"tap:{lifetime_minutes}:{once}")
        return {"pass": "S3cret-TAP-xyz", "lifetime_minutes": lifetime_minutes, "once": once}

    sign_ins = ()
    sign_in_calls = None

    async def list_sign_ins(self, user_id, top=100, since=None):
        if self.sign_in_calls is not None:
            self.sign_in_calls.append(since)
        return list(self.sign_ins)

    async def revoke_sessions(self, upn):
        self.calls.append("revoke")
        if self.fail_revoke:
            raise RuntimeError("boom")

    async def set_password(self, upn, password, force_change):
        self.calls.append("reset")
        if self.fail_reset:
            raise RuntimeError("denied")
        self.password = password


class _Mailboxes:
    def __init__(self, mb): self.mb = mb
    def list_active(self): return [self.mb]
    def get(self, _id): return self.mb


class _Connectors:
    def get(self, _id):
        return ConnectorEntity(org_id=uuid.uuid4(), name="c", tenant_id=uuid.uuid4(),
                               client_id=uuid.uuid4(), client_secret="s")


class IdentityServiceTests(TestCase):
    def setUp(self):
        self.mb = MailboxEntity(org_id=uuid.uuid4(), connector_id=uuid.uuid4(),
                                user_principal_name="victim@example.com")

    def svc(self, gw):
        return IdentityService(mailboxes=_Mailboxes(self.mb), connectors=_Connectors(),
                               audit=DjangoAuditLog(), gateway_factory=lambda c: gw)

    def test_password_policy(self):
        for _ in range(50):
            p = generate_password()
            self.assertEqual(len(p), 20)
            self.assertTrue(any(c.islower() for c in p) and any(c.isupper() for c in p))
            self.assertTrue(any(c.isdigit() for c in p) and any(not c.isalnum() for c in p))

    def test_emergency_runs_full_pipeline_in_order(self):
        gw = FakeGateway()
        steps, pw = self.svc(gw).emergency(self.mb.id, "admin", "VICTIM@example.com")
        self.assertEqual(gw.calls, ["revoke", "reset", "revoke"])
        self.assertTrue(all(s.ok for s in steps))
        self.assertEqual(pw, gw.password)

    def test_emergency_requires_exact_confirmation_and_does_nothing_otherwise(self):
        gw = FakeGateway()
        with self.assertRaises(ConfirmationMismatch):
            self.svc(gw).emergency(self.mb.id, "admin", "someone@else.com")
        self.assertEqual(gw.calls, [])

    def test_failed_reset_returns_no_password_but_still_revokes(self):
        gw = FakeGateway(fail_reset=True)
        steps, pw = self.svc(gw).emergency(self.mb.id, "admin", self.mb.user_principal_name)
        self.assertIsNone(pw)
        self.assertEqual([s.ok for s in steps], [True, False, True])

    def test_password_is_never_written_to_audit_log(self):
        gw = FakeGateway()
        steps, pw = self.svc(gw).emergency(self.mb.id, "admin", self.mb.user_principal_name)
        self.assertEqual(SecurityAction.objects.count(), 3)
        for a in SecurityAction.objects.all():
            self.assertNotIn(pw, a.detail)

    def test_successful_action_triggers_a_background_sync_but_emergency_triggers_only_one(self):
        triggered = []
        svc = IdentityService(mailboxes=_Mailboxes(self.mb), connectors=_Connectors(), audit=DjangoAuditLog(),
                              gateway_factory=lambda c: FakeGateway(), sync_trigger=triggered.append)
        svc.revoke_sessions(self.mb.id, "admin")
        self.assertEqual(triggered, [self.mb.id])
        triggered.clear()
        svc.emergency(self.mb.id, "admin", self.mb.user_principal_name)
        self.assertEqual(triggered, [self.mb.id])          # 3 étapes, une seule synchro à la fin

    def test_a_failing_sync_trigger_never_breaks_the_action(self):
        def boom(_): raise RuntimeError("redis down")
        svc = IdentityService(mailboxes=_Mailboxes(self.mb), connectors=_Connectors(), audit=DjangoAuditLog(),
                              gateway_factory=lambda c: FakeGateway(), sync_trigger=boom)
        svc.revoke_sessions(self.mb.id, "admin")           # l'action est déjà appliquée chez Microsoft : pas d'erreur
        self.assertTrue(SecurityAction.objects.filter(action="revoke_sessions", ok=True).exists())

    def test_delete_device_needs_exact_name_and_ownership(self):
        gw = FakeGateway()
        svc = self.svc(gw)
        with self.assertRaises(ConfirmationMismatch):
            svc.delete_device(self.mb.id, "obj-1", "admin", "autre")
        with self.assertRaises(DeviceNotOwned):
            svc.delete_device(self.mb.id, "obj-inconnu", "admin", "PC-TEST")
        self.assertEqual(gw.calls, [])
        svc.delete_device(self.mb.id, "obj-1", "admin", "pc-test")
        self.assertEqual(gw.calls, ["delete:obj-1"])
        self.assertEqual(SecurityAction.objects.filter(action="delete_device", ok=True).count(), 1)


def _si(at, app="Outlook", ip="1.1.1.1", ok=True):
    return SignInInfo(at=at, app=app, ip=ip, city="", country="", client="", os="Windows",
                      browser="Edge", interactive=True, success=ok, error_code=0 if ok else 50126)


class ActiveSessionTests(TestCase):
    now = datetime(2026, 11, 9, tzinfo=timezone.utc)

    def d(self, n):
        return self.now - timedelta(days=n)

    def test_groups_and_ignores_failed_revoked_and_old(self):
        sign_ins = [_si(self.d(1)), _si(self.d(2)), _si(self.d(3), ip="2.2.2.2"),
                    _si(self.d(1), ok=False), _si(self.d(10)), _si(self.d(120))]
        sessions = estimate_active_sessions(sign_ins, self.d(5), self.now)
        self.assertEqual(len(sessions), 2)  # d(10) révoquée, d(120) trop vieille, échec ignoré
        self.assertEqual(sessions[0].count, 2)
        self.assertEqual(sessions[0].last_seen, self.d(1))

    def test_no_revocation_keeps_recent_sign_ins(self):
        self.assertEqual(len(estimate_active_sessions([_si(self.d(20))], None, self.now)), 1)


class AuthMethodTests(TestCase):
    def setUp(self):
        from module.microsoft_identity.domain.entities import AuthMethodInfo
        self.mb = MailboxEntity(org_id=uuid.uuid4(), connector_id=uuid.uuid4(),
                                user_principal_name="victim@example.com")
        self.gw = FakeGateway()
        self.gw.methods = [
            AuthMethodInfo(id="m-pw", kind="password", label="Mot de passe", is_mfa=False),
            AuthMethodInfo(id="m-ph", kind="phone", label="Téléphone", detail="+1 5145550000"),
        ]
        self.svc = IdentityService(mailboxes=_Mailboxes(self.mb), connectors=_Connectors(),
                                   audit=DjangoAuditLog(), gateway_factory=lambda c: self.gw)

    def test_delete_requires_exact_confirmation(self):
        with self.assertRaises(ConfirmationMismatch):
            self.svc.delete_auth_method(self.mb.id, "m-ph", "admin", "autre@x.com")
        self.assertEqual(self.gw.calls, [])

    def test_delete_checks_method_belongs_to_account_and_is_not_password(self):
        from module.microsoft_identity.application.services import MethodNotFound
        with self.assertRaises(MethodNotFound):
            self.svc.delete_auth_method(self.mb.id, "inconnue", "admin", "victim@example.com")
        with self.assertRaises(ValueError):
            self.svc.delete_auth_method(self.mb.id, "m-pw", "admin", "victim@example.com")
        self.assertEqual(self.gw.calls, [])

    def test_delete_calls_graph_and_logs(self):
        self.svc.delete_auth_method(self.mb.id, "m-ph", "admin", "VICTIM@example.com")
        self.assertEqual(self.gw.calls, ["delmethod:phone:m-ph"])
        self.assertTrue(SecurityAction.objects.filter(action="delete_auth_method", ok=True).exists())

    def test_add_phone_validates_format_and_masks_value_in_audit(self):
        with self.assertRaises(ValueError):
            self.svc.add_auth_method(self.mb.id, "admin", "victim@example.com", "phone", "5145551234")
        self.assertEqual(self.gw.calls, [])
        self.svc.add_auth_method(self.mb.id, "admin", "victim@example.com", "phone", "+1 5145551234")
        self.assertEqual(self.gw.calls, ["addphone:+1 5145551234:mobile"])
        detail = SecurityAction.objects.get(action="add_auth_method:phone").detail
        self.assertNotIn("5145551234", detail)
        self.assertEqual(detail, "…234")

    def test_add_email_validation(self):
        with self.assertRaises(ValueError):
            self.svc.add_auth_method(self.mb.id, "admin", "victim@example.com", "email", "pas-un-courriel")
        self.svc.add_auth_method(self.mb.id, "admin", "victim@example.com", "email", "perso@gmail.com")
        self.assertEqual(self.gw.calls, ["addemail:perso@gmail.com"])

    def test_tap_returns_code_but_never_logs_it(self):
        out = self.svc.add_auth_method(self.mb.id, "admin", "victim@example.com", "tap", lifetime_minutes=30)
        self.assertEqual(out["pass"], "S3cret-TAP-xyz")
        for a in SecurityAction.objects.all():
            self.assertNotIn("S3cret", a.detail)

    def test_only_phone_email_tap_can_be_added_and_confirmation_is_required(self):
        for kind in ("fido2", "authenticator", "password"):
            with self.assertRaises(ValueError):
                self.svc.add_auth_method(self.mb.id, "admin", "victim@example.com", kind, "x")
        with self.assertRaises(ConfirmationMismatch):
            self.svc.add_auth_method(self.mb.id, "admin", "nope@x.com", "tap")
        self.assertEqual(self.gw.calls, [])


class TotpMethodTests(TestCase):
    def setUp(self):
        self.mb = MailboxEntity(org_id=uuid.uuid4(), connector_id=uuid.uuid4(), user_principal_name="victim@example.com")
        self.gw = FakeGateway()
        self.svc = IdentityService(mailboxes=_Mailboxes(self.mb), connectors=_Connectors(),
                                   audit=DjangoAuditLog(), gateway_factory=lambda c: self.gw)

    def test_totp_returns_secret_and_uri_once_and_never_logs_the_secret(self):
        out = self.svc.add_auth_method(self.mb.id, "admin", "victim@example.com", "totp")
        self.assertEqual(out["secret"], self.gw.totp_secret)
        self.assertTrue(out["uri"].startswith("otpauth://totp/Microsoft%2065".replace("%2065", "%20365")))
        self.assertIn(out["secret"], out["uri"])
        log = SecurityAction.objects.get(action="add_auth_method:totp")
        self.assertTrue(log.ok)
        self.assertNotIn(out["secret"], log.detail)
        self.assertTrue(log.detail.startswith("jeton OATH OV"))

    def test_totp_requires_confirmation(self):
        with self.assertRaises(ConfirmationMismatch):
            self.svc.add_auth_method(self.mb.id, "admin", "autre@x.com", "totp")
        self.assertEqual(self.gw.calls, [])

    def test_each_totp_gets_a_fresh_secret(self):
        a = self.svc.add_auth_method(self.mb.id, "admin", "victim@example.com", "totp")
        b = self.svc.add_auth_method(self.mb.id, "admin", "victim@example.com", "totp")
        self.assertNotEqual(a["secret"], b["secret"])
