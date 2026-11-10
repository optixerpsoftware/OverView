import uuid
from datetime import datetime, timedelta, timezone

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import AsyncClient, SimpleTestCase, TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from module.insights.infrastructure.models import Alert
from module.microsoft_connector.infrastructure.models import MicrosoftConnector
from module.organization.infrastructure.models import Organization
from module.protection.application.service import ProtectionRefused, ProtectionService
from module.protection.domain.rules import Limits, consent_breaches, evaluate, forwarding_breaches, rule_breaches
from module.protection.infrastructure.models import ProtectionEvent, ProtectionPolicy

NOW = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)


def dev(i, upn, hours_ago=1, uid=None):
    return {"id": f"d{i}", "displayName": f"PC-{i}", "registrationDateTime": (NOW - timedelta(hours=hours_ago)).isoformat(),
            "owners": [{"id": uid or f"id-{upn}", "userPrincipalName": upn}]}


class RuleTests(SimpleTestCase):
    def test_devices_per_user_and_org(self):
        devices = [dev(i, "bob@corp.com") for i in range(4)] + [dev(9, "ann@corp.com"), dev(10, "old@corp.com", hours_ago=30)]
        b = evaluate(Limits(mode="enforce", devices_per_user_per_day=3, devices_per_org_per_day=4), devices, [], NOW)
        self.assertEqual([x.rule for x in b], ["devices_user", "devices_org"])
        self.assertEqual((b[0].upn, b[0].count, b[0].disable), ("bob@corp.com", 4, True))
        self.assertFalse(b[1].disable)                     # la règle globale alerte seulement
        self.assertEqual(b[1].count, 5)                    # l'appareil de plus de 24 h n'est pas compté

    def test_monitor_mode_exclusions_and_protected(self):
        devices = [dev(i, "bob@corp.com") for i in range(5)]
        b = evaluate(Limits(mode="monitor"), devices, [], NOW)[0]
        self.assertEqual((b.disable, b.blocked_reason), (False, "Mode surveillance : alerte seulement."))
        b = evaluate(Limits(mode="enforce", exclusions=("bob@corp.com",)), devices, [], NOW)[0]
        self.assertFalse(b.disable)
        b = evaluate(Limits(mode="enforce"), devices, [], NOW, protected_upns=["BOB@corp.com"])[0]
        self.assertFalse(b.disable)
        self.assertEqual(evaluate(Limits(mode="off"), devices, [], NOW), [])

    def test_signin_failures_off_by_default_for_disable(self):
        rows = [{"at": NOW - timedelta(minutes=5), "success": False, "upn": "bob@corp.com", "ip": "6.6.6.6"}] * 21
        b = evaluate(Limits(mode="enforce"), [], rows, NOW)[0]
        self.assertEqual((b.rule, b.disable), ("signin_failures", False))
        b = evaluate(Limits(mode="enforce", disable_on_signin_limit=True), [], rows, NOW)[0]
        self.assertTrue(b.disable)

    def test_emails_per_sender_in_window(self):
        def mail(sender, minutes_ago, rcpt="x@gmail.com", mid=None):
            return {"sender": sender, "recipient": rcpt, "at": NOW - timedelta(minutes=minutes_ago),
                    "message_id": mid or f"m{minutes_ago}", "subject": "Facture en attente"}
        sent = [mail("bob@corp.com", i % 50, mid=f"m{i // 10}") for i in range(30)]          # 30 envois en 50 min
        sent += [mail("ann@corp.com", 5) for _ in range(5)]
        sent += [mail("bob@corp.com", 90) for _ in range(50)]                                 # hors fenêtre
        sent += [mail("spam@evil.com", 1) for _ in range(500)]                                # expéditeur externe : ignoré
        lim = Limits(mode="enforce", emails_per_user_window=20, email_window_minutes=60)
        b = evaluate(lim, [], [], NOW, sent=sent, internal_domains=["corp.com"])
        self.assertEqual([(x.rule, x.upn, x.count, x.disable) for x in b], [("emails_user", "bob@corp.com", 30, True)])
        self.assertIn("30 destinataire(s) externe(s)", b[0].detail)
        self.assertIn("3 message(s)", b[0].detail)
        self.assertEqual(evaluate(lim, [], [], NOW, sent=None), [])        # suivi indisponible : règle ignorée
        b = evaluate(Limits(mode="enforce", emails_per_user_window=20, disable_on_email_limit=False), [], [], NOW,
                     sent=sent, internal_domains=["corp.com"])
        self.assertFalse(b[0].disable)

    def test_rule_forwarding_and_consent_breaches(self):
        lim = Limits(mode="enforce")
        rules = [{"id": "1", "rule_id": "r1", "mailbox_upn": "bob@corp.com", "display_name": "x", "is_enabled": True,
                  "actions": {"forwardTo": [{"emailAddress": {"address": "evil@gmail.com"}}]}},
                 {"id": "2", "rule_id": "r2", "mailbox_upn": "bob@corp.com", "display_name": "int", "is_enabled": True,
                  "actions": {"redirectTo": [{"emailAddress": {"address": "ann@corp.com"}}]}}]
        b = rule_breaches(lim, rules, ["corp.com"])
        self.assertEqual([(x.rule, x.target, x.disable) for x in b], [("malicious_rule", "bob@corp.com|r1", True)])
        self.assertFalse(rule_breaches(Limits(mode="monitor"), rules, ["corp.com"])[0].disable)
        mbx = [{"UserPrincipalName": "boss@corp.com", "ForwardingSmtpAddress": "smtp:copie@gmail.com", "DeliverToMailboxAndForward": False},
               {"UserPrincipalName": "ann@corp.com", "ForwardingSmtpAddress": "smtp:bob@corp.com"}]
        f = forwarding_breaches(lim, mbx, ["corp.com"])
        self.assertEqual([(x.upn, x.disable) for x in f], [("boss@corp.com", True)])     # admin non exempté : on ne le verrouille pas
        self.assertIn("SANS copie", f[0].detail)
        apps = [{"id": "sp1", "name": "Evil", "risk": "high", "enabled": True, "verified": False, "high_risk_scopes": ["Mail.Read"], "delegated": []},
                {"id": "sp2", "name": "Ok", "risk": "low", "enabled": True}]
        self.assertEqual(consent_breaches(lim, apps, None), [])                            # première lecture : mémorisation seule
        c = consent_breaches(lim, apps, {"sp0"})
        self.assertEqual([(x.target, x.disable) for x in c], [("sp1", True)])
        self.assertEqual(consent_breaches(lim, apps, {"sp1"}), [])

    def test_validation(self):
        for bad in (Limits(mode="x"), Limits(devices_per_user_per_day=0), Limits(exclusions=("pas-une-adresse",)),
                    Limits(email_window_minutes=45), Limits(emails_per_user_window=1)):
            with self.assertRaises(ValueError):
                bad.validate()


class FakeGw:
    def __init__(self, devices, admins=(), roles_fail=False, disable_fail=False):
        self.devices, self.admins, self.roles_fail, self.disable_fail = devices, set(admins), roles_fail, disable_fail
        self.disabled, self.enabled = [], []

    async def recent_devices(self): return self.devices

    async def admin_upns(self):
        if self.roles_fail:
            raise RuntimeError("403")
        return self.admins

    async def disable_and_revoke(self, uid):
        if self.disable_fail:
            raise RuntimeError("403 User.ReadWrite.All")
        self.disabled.append(uid)

    async def enable(self, uid): self.enabled.append(uid)
    async def user_id(self, upn): return f"id-{upn}"

    sent = None

    async def internal_domains(self): return ["corp.com"]

    async def sent_messages(self, tenant_id, minutes):
        if self.sent is None:
            raise RuntimeError("Exchange 401")
        return self.sent


class FakeAlerts:
    def __init__(self): self.sent = []
    async def _send(self, connector, created): self.sent.extend(created)


class ServiceTests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Client")
        self.conn = MicrosoftConnector.objects.create(org_id=self.org.id, name="c", tenant_id=uuid.uuid4(),
                                                      client_id=uuid.uuid4(), client_secret="s")
        self.alerts = FakeAlerts()

    def svc(self, gw):
        return ProtectionService(gateway_factory=lambda c: gw, now=lambda: NOW, alerts=self.alerts)

    def test_enforce_disables_once_and_alerts(self):
        ProtectionService.set_policy(self.org.id, {"mode": "enforce", "devices_per_user_per_day": 2}, "admin")
        gw = FakeGw([dev(i, "bob@corp.com") for i in range(3)])
        r = self.svc(gw).evaluate(self.conn.id)
        self.assertEqual((r["new_events"], gw.disabled), (1, ["id-bob@corp.com"]))
        ev = ProtectionEvent.objects.get()
        self.assertEqual((ev.action, ev.upn, ev.count), ("disabled", "bob@corp.com", 3))
        a = Alert.objects.get(category="protection")
        self.assertEqual(a.severity, "critical")
        self.assertEqual(len(self.alerts.sent), 1)
        self.svc(gw).evaluate(self.conn.id)                # même jour : pas de second déclenchement
        self.assertEqual((ProtectionEvent.objects.count(), len(gw.disabled)), (1, 1))

    def test_default_is_monitor_only(self):
        gw = FakeGw([dev(i, "bob@corp.com") for i in range(5)])
        self.svc(gw).evaluate(self.conn.id)
        self.assertEqual((gw.disabled, ProtectionEvent.objects.get().action), ([], "alerted"))

    def test_admin_and_unreadable_roles_are_never_disabled(self):
        ProtectionService.set_policy(self.org.id, {"mode": "enforce"}, "admin")
        gw = FakeGw([dev(i, "boss@corp.com") for i in range(5)], admins=["boss@corp.com"])
        self.svc(gw).evaluate(self.conn.id)
        self.assertEqual((gw.disabled, ProtectionEvent.objects.get().action), ([], "blocked"))
        ProtectionEvent.objects.all().delete()
        gw = FakeGw([dev(i, "bob@corp.com") for i in range(5)], roles_fail=True)
        self.svc(gw).evaluate(self.conn.id)
        self.assertEqual((gw.disabled, ProtectionEvent.objects.get().action), ([], "blocked"))

    def test_failed_disable_is_recorded(self):
        ProtectionService.set_policy(self.org.id, {"mode": "enforce"}, "admin")
        gw = FakeGw([dev(i, "bob@corp.com") for i in range(5)], disable_fail=True)
        self.svc(gw).evaluate(self.conn.id)
        ev = ProtectionEvent.objects.get()
        self.assertEqual(ev.action, "disable_failed")
        self.assertIn("User.ReadWrite.All", ev.action_detail)

    def test_email_limit_disables_sender(self):
        ProtectionService.set_policy(self.org.id, {"mode": "enforce", "emails_per_user_window": 10}, "admin")
        gw = FakeGw([])
        gw.sent = [{"sender": "bob@corp.com", "recipient": f"r{i}@gmail.com", "at": NOW - timedelta(minutes=3),
                    "message_id": "m1", "subject": "Promo"} for i in range(12)]
        r = self.svc(gw).evaluate(self.conn.id)
        self.assertEqual((r["email_check"], gw.disabled), ("ok", ["id-bob@corp.com"]))
        self.assertEqual(ProtectionEvent.objects.get().rule, "emails_user")
        gw2 = FakeGw([])   # Exchange inaccessible : pas d'erreur, la règle est simplement ignorée
        self.assertTrue(self.svc(gw2).evaluate(self.conn.id)["email_check"].startswith("indisponible"))

    def test_neutralizes_forwarding_and_new_app(self):
        ProtectionService.set_policy(self.org.id, {"mode": "enforce"}, "admin")
        gw = FakeGw([])
        calls = []

        async def fwd(tenant): return [{"UserPrincipalName": "bob@corp.com", "ForwardingSmtpAddress": "smtp:x@gmail.com"}]
        async def clear(tenant, upn): calls.append(("clear", upn))
        apps = [[{"id": "old", "name": "Old", "risk": "high", "enabled": True, "high_risk_scopes": ["Mail.Read"]}]]
        async def third(tenant): return apps[0]
        async def disable_app(sp): calls.append(("app", sp))
        gw.mailbox_forwarding, gw.clear_forwarding, gw.third_party_apps, gw.disable_app = fwd, clear, third, disable_app
        self.svc(gw).evaluate(self.conn.id)                       # 1re fois : transfert neutralisé, applis mémorisées
        self.assertEqual(calls, [("clear", "bob@corp.com")])
        apps[0] = apps[0] + [{"id": "new", "name": "MailSync", "risk": "high", "enabled": True, "high_risk_scopes": ["Mail.ReadWrite"]}]
        self.svc(gw).evaluate(self.conn.id)
        self.assertEqual(calls[-1], ("app", "new"))
        self.assertEqual(sorted(e.action for e in ProtectionEvent.objects.all()), ["neutralized", "neutralized"])

    def test_reenable(self):
        ProtectionService.set_policy(self.org.id, {"mode": "enforce"}, "admin")
        gw = FakeGw([dev(i, "bob@corp.com") for i in range(5)])
        svc = self.svc(gw)
        svc.evaluate(self.conn.id)
        ev = ProtectionEvent.objects.get()
        with self.assertRaises(ProtectionRefused):
            async_to_sync(svc.reenable_async)(ev.id, confirm="oui", by="a")
        r = async_to_sync(svc.reenable_async)(ev.id, confirm="reactiver", by="admin")
        self.assertEqual((gw.enabled, r["reenabled_by"]), (["id-bob@corp.com"], "admin"))
        with self.assertRaises(ProtectionRefused):
            async_to_sync(svc.reenable_async)(ev.id, confirm="REACTIVER", by="admin")

    def test_policy_validation_and_view(self):
        with self.assertRaises(ValueError):
            ProtectionService.set_policy(self.org.id, {"devices_per_user_per_day": "abc"}, "a")
        p = ProtectionService.set_policy(self.org.id, {"exclusions": ["Urgence@Corp.com", " "]}, "a")
        self.assertEqual(p["exclusions"], ["urgence@corp.com"])
        u = get_user_model().objects.create_superuser("adm", password="x")
        h = {"Authorization": f"Bearer {AccessToken.for_user(u)}"}
        c = AsyncClient()
        url = f"/api/protection/organizations/{self.org.id}/"
        self.assertEqual(async_to_sync(c.get)(url).status_code, 401)
        r = async_to_sync(c.get)(url, headers=h)
        self.assertEqual(r.json()["policy"]["mode"], "monitor")
        r = async_to_sync(c.put)(url, {"mode": "enforce", "devices_per_user_per_day": 2}, content_type="application/json", headers=h)
        self.assertEqual((r.status_code, r.json()["mode"], r.json()["updated_by"]), (200, "enforce", "adm"))
        self.assertEqual(async_to_sync(c.put)(url, {"mode": "nimporte"}, content_type="application/json", headers=h).status_code, 400)
        self.assertEqual(ProtectionPolicy.objects.count(), 1)
