import uuid

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import AsyncClient, SimpleTestCase, TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from module.microsoft_connector.infrastructure.models import MicrosoftConnector
from module.microsoft_identity.application.conditional_access_service import (
    CaConfirmationRequired, CaRefused, ConditionalAccessService,
)
from module.microsoft_identity.domain import conditional_access as ca
from module.microsoft_identity.infrastructure.audit_models import TenantSettingChange
from module.organization.infrastructure.models import Organization

GA = "62e90394-69f5-4237-9190-012177145e10"
BREAK = "11111111-1111-1111-1111-111111111111"


def policy(name, state="enabled", users=None, apps=("All",), grant=("mfa",), clients=("all",), roles=(), excl=()):
    return {"id": name, "displayName": name, "state": state, "conditions": {
        "users": {"includeUsers": list(users or []), "includeRoles": list(roles), "excludeUsers": list(excl)},
        "applications": {"includeApplications": list(apps)}, "clientAppTypes": list(clients)},
        "grantControls": {"operator": "OR", "builtInControls": list(grant)}}


class DomainTests(SimpleTestCase):
    def test_license_tier(self):
        self.assertEqual(ca.license_tier([{"servicePlans": [{"servicePlanName": "EXCHANGE_S_STANDARD"}]}]), "none")
        self.assertEqual(ca.license_tier([{"servicePlans": [{"servicePlanName": "AAD_PREMIUM"}]}]), "p1")
        self.assertEqual(ca.license_tier([{"servicePlans": [{"servicePlanName": "AAD_PREMIUM"},
                                                            {"servicePlanName": "AAD_PREMIUM_P2"}]}]), "p2")
        self.assertEqual(ca.license_tier([{"servicePlans": [{"servicePlanName": "AAD_PREMIUM", "provisioningStatus": "Disabled"}]}]), "none")

    def test_baseline_with_security_defaults_only(self):
        items = {b.code: b for b in ca.baseline([], True, "none")}
        self.assertTrue(all(items[c].status == "covered" for c in ("mfa_admins", "mfa_all", "block_legacy", "mfa_azure")))
        self.assertEqual(items["risk"].status, "not_available")
        self.assertEqual(ca.verdict(True, "none", [])["level"], "ok")
        self.assertEqual(ca.verdict(False, "none", [])["level"], "danger")

    def test_baseline_policies(self):
        ps = [policy("MFA admins", roles=[GA]), policy("Legacy", users=["All"], grant=["block"], clients=["exchangeActiveSync", "other"]),
              policy("MFA tous", state="enabledForReportingButNotEnforced", users=["All"])]
        items = {b.code: b for b in ca.baseline(ps, False, "p1")}
        self.assertEqual(items["mfa_admins"].status, "covered")
        self.assertEqual(items["block_legacy"].policies, ("Legacy",))
        self.assertEqual((items["mfa_all"].status, items["mfa_all"].template), ("report_only", "mfa_all"))
        self.assertEqual(items["mfa_azure"].status, "covered")       # MFA admins couvre toutes les applis
        self.assertEqual(ca.verdict(True, "p1", ps)["level"], "warn")  # les deux coexistent

    def test_describe_resolves_names_and_flags(self):
        d = ca.describe(policy("MFA tous", users=["All"]), {})
        self.assertEqual(d["include"], ["tous les utilisateurs"])
        self.assertEqual(d["grant"], "Exiger le MFA")
        self.assertTrue(any("compte d'urgence" in f["text"] for f in d["flags"]))
        d = ca.describe(policy("x", users=[BREAK], apps=[ca.AZURE_MANAGEMENT_APP], grant=["block"], excl=[BREAK]), {BREAK: "Urgence"})
        self.assertEqual((d["include"], d["exclude"], d["apps"], d["grant"]), (["Urgence"], ["Urgence"], ["Gestion Microsoft Azure"], "Bloquer l'accès"))
        refs = ca.referenced_ids([policy("r", roles=[GA], users=[BREAK, "All"])])
        self.assertEqual((refs["objects"], refs["roles"]), ({BREAK}, {GA}))

    def test_templates_are_report_only(self):
        for code in ca.TEMPLATES:
            self.assertEqual(ca.template_body(code)["state"], "enabledForReportingButNotEnforced")
        self.assertEqual(ca.template_body("mfa_all", [BREAK])["conditions"]["users"]["excludeUsers"], [BREAK])
        self.assertNotIn("excludeUsers", ca.TEMPLATES["mfa_all"][1]["users"])   # le modèle partagé n'est pas modifié
        with self.assertRaises(ValueError):
            ca.template_body("inconnu")


class FakeGw:
    def __init__(self, sd=True, policies=None):
        self.sd, self._policies, self.created, self.set_calls = sd, policies, [], []

    async def security_defaults(self): return {"isEnabled": self.sd}
    async def set_security_defaults(self, enabled): self.set_calls.append(enabled); self.sd = enabled
    async def policies(self): return self._policies
    async def skus(self): return []
    async def resolve_names(self, *a): return {}
    async def create_policy(self, body): self.created.append(body); return {"id": "new"}


class ServiceTests(TransactionTestCase):
    def setUp(self):
        org = Organization.objects.create(name="O")
        self.conn = MicrosoftConnector.objects.create(org_id=org.id, name="c", tenant_id=uuid.uuid4(),
                                                      client_id=uuid.uuid4(), client_secret="s")
        self.gw = FakeGw(sd=True, policies=[])
        self.svc = ConditionalAccessService(gateway_factory=lambda c: self.gw)

    def run_(self, coro):
        return async_to_sync(lambda: coro)()

    def test_overview_without_p1(self):
        self.gw._policies = None   # accès conditionnel illisible
        o = async_to_sync(self.svc.overview_async)(self.conn.id)
        self.assertEqual((o["security_defaults"], o["license"], o["policies_readable"]), (True, "none", False))
        self.assertEqual(o["verdict"]["level"], "ok")

    def test_disable_security_defaults_is_guarded(self):
        with self.assertRaises(CaConfirmationRequired):
            async_to_sync(self.svc.set_security_defaults_async)(self.conn.id, False, confirm="oui", by="a")
        with self.assertRaises(CaRefused):
            async_to_sync(self.svc.set_security_defaults_async)(self.conn.id, False, confirm="DESACTIVER", by="a")
        self.assertEqual(self.gw.set_calls, [])
        self.gw._policies = [policy("MFA tous", users=["All"])]
        r = async_to_sync(self.svc.set_security_defaults_async)(self.conn.id, False, confirm="desactiver", by="admin")
        self.assertEqual(r, {"security_defaults": False, "changed": True})
        a = TenantSettingChange.objects.get()
        self.assertEqual((a.setting, a.ok, a.performed_by), ("security_defaults", True, "admin"))

    def test_enable_and_noop(self):
        self.gw.sd = False
        async_to_sync(self.svc.set_security_defaults_async)(self.conn.id, True, confirm="ACTIVER", by="a")
        r = async_to_sync(self.svc.set_security_defaults_async)(self.conn.id, True, confirm="ACTIVER", by="a")
        self.assertEqual((self.gw.set_calls, r["changed"]), ([True], False))

    def test_template_creation(self):
        with self.assertRaises(CaConfirmationRequired):
            async_to_sync(self.svc.create_from_template_async)(self.conn.id, "mfa_all", confirm="", by="a")
        with self.assertRaises(ValueError):
            async_to_sync(self.svc.create_from_template_async)(self.conn.id, "mfa_all", confirm="CREER", by="a",
                                                               exclude_user_ids=["pas-un-guid"])
        r = async_to_sync(self.svc.create_from_template_async)(self.conn.id, "mfa_all", confirm="CREER", by="a",
                                                               exclude_user_ids=[BREAK])
        self.assertEqual(r["state"], "enabledForReportingButNotEnforced")
        self.assertEqual(self.gw.created[0]["conditions"]["users"]["excludeUsers"], [BREAK])


class ViewTests(ServiceTests):
    def test_views(self):
        from unittest import mock
        u = get_user_model().objects.create_superuser("adm", password="x")
        h = {"Authorization": f"Bearer {AccessToken.for_user(u)}"}
        c = AsyncClient()
        base = f"/api/identity/connectors/{self.conn.id}/conditional-access/"
        with mock.patch("module.microsoft_identity.application.conditional_access_service.GraphConditionalAccessGateway",
                        lambda g: self.gw), \
             mock.patch("module.microsoft_identity.application.conditional_access_service.build_graph", lambda c: None):
            self.assertEqual(async_to_sync(c.get)(base).status_code, 401)
            r = async_to_sync(c.get)(base, headers=h)
            self.assertEqual(r.status_code, 200)
            self.assertIn("baseline", r.json())
            r = async_to_sync(c.put)(base + "security-defaults/", {"enabled": False, "confirm": "DESACTIVER"},
                                     content_type="application/json", headers=h)
            self.assertEqual(r.status_code, 409)
            r = async_to_sync(c.post)(base + "templates/", {"template": "block_legacy", "confirm": "CREER"},
                                      content_type="application/json", headers=h)
            self.assertEqual(r.status_code, 201)
            self.assertEqual(async_to_sync(c.get)(f"/api/identity/connectors/{uuid.uuid4()}/conditional-access/",
                                                  headers=h).status_code, 404)
