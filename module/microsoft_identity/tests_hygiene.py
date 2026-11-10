import uuid
from datetime import datetime, timedelta, timezone

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TransactionTestCase

from module.insights.domain.alerts import hygiene_alerts
from module.microsoft_connector.infrastructure.models import MicrosoftConnector
from module.microsoft_identity.application.hygiene_service import HygieneRefused, HygieneService
from module.microsoft_identity.domain import hygiene as hy
from module.microsoft_identity.infrastructure.audit_models import TenantSettingChange
from module.organization.infrastructure.models import Organization

NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)
OWN = "aaaaaaaa-0000-0000-0000-000000000000"
VENDOR = "bbbbbbbb-0000-0000-0000-000000000000"


def iso(days):
    return (NOW - timedelta(days=days)).isoformat()


def user(uid, upn, last=None, created=400, utype="Member", enabled=True, **kw):
    u = {"id": uid, "userPrincipalName": upn, "displayName": upn.split("@")[0], "userType": utype,
         "accountEnabled": enabled, "createdDateTime": iso(created), **kw}
    u["signInActivity"] = {"lastSignInDateTime": iso(last)} if last is not None else {}
    return u


USERS = [
    user("u1", "actif@corp.com", last=3), user("u2", "vieux@corp.com", last=200), user("u3", "jamais@corp.com"),
    user("u4", "recent@corp.com", created=10), user("u5", "off@corp.com", last=300, enabled=False),
    user("g1", "ext_gmail.com#EXT#@corp.com", utype="Guest", externalUserState="PendingAcceptance",
         externalUserStateChangeDateTime=iso(60)),
    user("g2", "ok_x.com#EXT#@corp.com", last=5, utype="Guest", externalUserState="Accepted"),
]
ROLES = [{"displayName": "Global Administrator", "members": [
    {"userPrincipalName": "admin@corp.com", "displayName": "Admin", "type": "#microsoft.graph.user"}]},
    {"displayName": "Exchange Administrator", "members": [
        {"userPrincipalName": "vieux@corp.com", "displayName": "Vieux", "type": "#microsoft.graph.user"}]},
    {"displayName": "Directory Readers", "members": [{"userPrincipalName": "x@corp.com"}]}]
REGS = [{"userPrincipalName": "admin@corp.com", "isMfaRegistered": True},
        {"userPrincipalName": "vieux@corp.com", "isMfaRegistered": False}]
SPS = [
    {"id": "sp-evil", "appId": "a1", "displayName": "MailSync Pro", "appOwnerOrganizationId": VENDOR, "accountEnabled": True},
    {"id": "sp-ok", "appId": "a2", "displayName": "Calendly", "appOwnerOrganizationId": VENDOR,
     "verifiedPublisher": {"displayName": "Calendly", "verifiedPublisherId": "1"}},
    {"id": "sp-ms", "appId": "a3", "displayName": "Teams", "appOwnerOrganizationId": "f8cdef31-a31e-4b4a-93e4-5f571e91255a"},
    {"id": "sp-own", "appId": "a4", "displayName": "OverView", "appOwnerOrganizationId": OWN},
    {"id": "graph", "appId": "00000003-0000-0000-c000-000000000000", "displayName": "Microsoft Graph",
     "appOwnerOrganizationId": "f8cdef31-a31e-4b4a-93e4-5f571e91255a"},
]
GRANTS = [{"id": "gr1", "clientId": "sp-ok", "consentType": "Principal", "principalId": "u1", "resourceId": "graph",
           "scope": "openid profile User.Read Calendars.ReadWrite"},
          {"id": "gr2", "clientId": "sp-ms", "consentType": "AllPrincipals", "resourceId": "graph", "scope": "Mail.ReadWrite"}]
ASSIGN = {"sp-evil": [{"id": "as1", "resourceId": "graph", "appRoleId": "r-mail"}]}


class DomainTests(SimpleTestCase):
    def test_inactive(self):
        rows = hy.inactive_accounts(USERS, NOW)
        self.assertEqual({r["upn"] for r in rows}, {"vieux@corp.com", "jamais@corp.com"})
        self.assertIsNone(hy.inactive_accounts([{**u, "signInActivity": None} for u in USERS if "signInActivity" in u]
                                               and [{k: v for k, v in u.items() if k != "signInActivity"} for u in USERS], NOW))

    def test_guests_and_admins(self):
        g = hy.guests(USERS, NOW)
        self.assertEqual(g[0]["id"], "g1")
        self.assertIn("jamais acceptée", g[0]["flags"][0])
        self.assertEqual(g[1]["flags"], [])
        a = hy.admins(ROLES, REGS)
        self.assertEqual([p["upn"] for p in a["people"]], ["admin@corp.com", "vieux@corp.com"])   # Directory Readers ignoré
        self.assertEqual(a["people"][1]["flags"], ["Sans MFA"])
        self.assertEqual(a["global_admins"], 1)
        self.assertIn("au moins 2", a["notes"][0]["text"])

    def test_app_consents(self):
        apps = hy.app_consents(SPS, GRANTS, ASSIGN, {("graph", "r-mail"): "Mail.Read"}, {"graph": "Microsoft Graph"}, OWN,
                               {"u1": "actif@corp.com"})
        self.assertEqual([(a["name"], a["risk"]) for a in apps], [("MailSync Pro", "high"), ("Calendly", "medium")])
        self.assertTrue(apps[0].get("unverified_warning"))
        self.assertEqual(apps[1]["delegated"][0]["who"], "actif@corp.com")
        self.assertEqual(apps[0]["application"][0]["permission"], "Mail.Read")

    def test_alert_rules(self):
        snap = {"apps": [{"id": "sp-evil", "name": "MailSync", "risk": "high", "enabled": True, "high_risk_scopes": ["Mail.Read"]},
                         {"id": "sp-off", "name": "Off", "risk": "high", "enabled": False}],
                "admins": hy.admins(ROLES, REGS)}
        out = hygiene_alerts("c", snap)
        self.assertEqual(sorted(a.severity for a in out), ["critical", "high"])


class FakeGw:
    def __init__(self):
        self.disabled, self.apps = [], []

    async def users_full(self): return USERS
    async def roles(self): return ROLES
    async def registrations(self): return REGS
    async def service_principals(self): return SPS
    async def grants(self): return GRANTS
    async def organization_id(self): return OWN
    async def assignments(self, ids): return {i: ASSIGN.get(i, []) for i in ids}
    async def resources(self, ids): return {("graph", "r-mail"): "Mail.Read"}, {"graph": "Microsoft Graph"}
    async def user_names(self, ids): return {}
    async def set_user_enabled(self, uid, enabled): self.disabled.append((uid, enabled))
    async def set_app_enabled(self, sp, enabled): self.apps.append((sp, enabled))


class ServiceTests(TransactionTestCase):
    def setUp(self):
        org = Organization.objects.create(name="O")
        self.conn = MicrosoftConnector.objects.create(org_id=org.id, name="c", tenant_id=uuid.uuid4(),
                                                      client_id=uuid.uuid4(), client_secret="s")
        self.gw = FakeGw()
        self.svc = HygieneService(gateway_factory=lambda c: self.gw, now=lambda: NOW)

    def test_scan_stores_and_summarizes(self):
        r = self.svc.scan(self.conn.id)
        self.assertEqual(r["summary"], {"inactive": 2, "guests": 2, "guests_flagged": 1, "admins": 2, "global_admins": 1,
                                        "admins_no_mfa": 1, "apps": 2, "apps_high": 1})
        self.assertEqual(HygieneService.latest(self.conn.id)["summary"]["apps_high"], 1)

    def test_disable_accounts_guarded(self):
        dis = async_to_sync(self.svc.set_accounts_enabled_async)
        with self.assertRaises(HygieneRefused):
            dis(self.conn.id, ["u3"], False, confirm="DESACTIVER", by="a")   # pas encore d'analyse
        self.svc.scan(self.conn.id)
        with self.assertRaises(HygieneRefused):
            dis(self.conn.id, ["u3"], False, confirm="oui", by="a")
        r = dis(self.conn.id, ["u3", "u1", "u2", "g1"], False, confirm="desactiver", by="admin")
        # u1 actif : non signalé ; u2 inactif MAIS administrateur Exchange : refusé ; u3 et g1 désactivés
        self.assertEqual([x for x, _ in self.gw.disabled], ["u3", "g1"])
        self.assertEqual((r["succeeded"], r["failed"]), (2, 2))
        self.assertEqual(TenantSettingChange.objects.get().setting, "account_disable")

    def test_app_disable_only_third_party_from_scan(self):
        self.svc.scan(self.conn.id)
        app = async_to_sync(self.svc.set_app_enabled_async)
        with self.assertRaises(HygieneRefused):
            app(self.conn.id, "sp-ms", False, confirm="DESACTIVER", by="a")   # appli Microsoft : refusée
        app(self.conn.id, "sp-evil", False, confirm="DESACTIVER", by="a")
        self.assertEqual(self.gw.apps, [("sp-evil", False)])
        self.assertFalse(next(a for a in HygieneService.latest(self.conn.id)["apps"] if a["id"] == "sp-evil")["enabled"])
