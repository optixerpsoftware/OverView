import time
import uuid
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from module.accounts.application.service import AccountError, AccountService
from module.microsoft_connector.application.services import ConnectorService
from module.organization.application.services import OrganizationService
from module.organization.infrastructure.models import Organization
from module.platform import console
from module.platform.limits import QuotaExceeded, check, is_expired
from module.platform.models import Msp
from module.platform.tests import ensure_other_msp
from shared_kernel.tenancy import PUBLIC, clear_domain_cache, tenant_schemas, use_schema
from shared_kernel.totp import totp_code

PW = "Un-mot-de-passe-solide-42"


def set_limits(schema="test", **kw):
    with use_schema(PUBLIC):
        Msp.objects.filter(schema_name=schema).update(**kw)
    clear_domain_cache()


class LimitTests(TransactionTestCase):
    def tearDown(self):
        from django.db import connection
        set_limits(max_tenants=None, max_organizations=None, max_users=None, expires_at=None, plan="personnalise")
        set_limits("autre", is_active=True, expires_at=None)
        connection.set_schema("test")

    def test_tenant_quota_on_service_and_api(self):
        org = Organization.objects.create(name="O")
        set_limits(max_tenants=1)
        svc = ConnectorService()
        svc.register(org_id=org.id, name="t1", tenant_id=uuid.uuid4(), client_id=uuid.uuid4(), client_secret="s")
        with self.assertRaises(QuotaExceeded) as cm:
            svc.register(org_id=org.id, name="t2", tenant_id=uuid.uuid4(), client_id=uuid.uuid4(), client_secret="s")
        self.assertIn("1/1 tenants", str(cm.exception))
        admin = get_user_model().objects.create_superuser("adm", password=PW)
        r = Client().post("/api/connectors/", {"org_id": str(org.id), "name": "t3", "tenant_id": str(uuid.uuid4()),
                                               "client_id": str(uuid.uuid4()), "client_secret": "s"},
                          content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(admin)}")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Limite de votre abonnement", r.json()["detail"])

    def test_org_and_user_quotas(self):
        set_limits(max_organizations=0, max_users=0)
        with self.assertRaises(QuotaExceeded):
            OrganizationService().create("Client")
        with self.assertRaises(AccountError):
            AccountService.create_user("bob", PW, "viewer")
        set_limits(max_organizations=None, max_users=None)
        check("organizations")                                   # illimité : aucune erreur

    def test_expiry_blocks_access_and_background_tasks(self):
        ensure_other_msp()
        self.assertFalse(is_expired(date.today()))
        self.assertTrue(is_expired(date.today() - timedelta(days=1)))
        set_limits("autre", expires_at=date.today() - timedelta(days=1))
        self.assertEqual(Client().get("/api/organizations/", HTTP_HOST="autre.localhost").status_code, 404)
        self.assertNotIn("autre", tenant_schemas())
        set_limits("autre", expires_at=date.today() + timedelta(days=10))
        self.assertEqual(Client().get("/api/organizations/", HTTP_HOST="autre.localhost").status_code, 401)


class ConsoleSubscriptionTests(TransactionTestCase):
    def tearDown(self):
        from django.db import connection
        set_limits(max_tenants=None, max_organizations=None, max_users=None, expires_at=None, plan="personnalise",
                   contact_name="", notes="")
        connection.set_schema("test")

    def token(self):
        name = f"root-{uuid.uuid4().hex[:6]}"
        r = console.create_platform_admin(name, PW)
        tok = console.login(name, PW)["mfa_token"]
        return console.verify(tok, totp_code(r["secret"], at=time.time()))["access"], name

    def call(self, method, path, token, body=None):
        return getattr(Client(), method)(path, body or {}, content_type="application/json", HTTP_HOST="platform.localhost",
                                         HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_plans_custom_limits_detail_and_admin_guards(self):
        token, me = self.token()
        with use_schema(PUBLIC):
            test_id = Msp.objects.get(schema_name="test").pk
        r = self.call("patch", f"/api/platform/msps/{test_id}/", token, {"subscription": {"plan": "essentiel"}})
        self.assertEqual(r.json()["limits"], {"tenants": 3, "organizations": 3, "users": 3})
        r = self.call("patch", f"/api/platform/msps/{test_id}/", token, {"subscription": {
            "plan": "personnalise", "limits": {"tenants": 7, "organizations": "", "users": 2},
            "expires_at": (date.today() + timedelta(days=5)).isoformat(), "contact_name": "Jean", "notes": "VIP"}})
        body = r.json()
        self.assertEqual(body["limits"], {"tenants": 7, "organizations": None, "users": 2})
        self.assertTrue(any("échu dans 5 j" in a["text"] for a in body["alerts"]))
        self.assertEqual(self.call("patch", f"/api/platform/msps/{test_id}/", token,
                                   {"subscription": {"plan": "personnalise", "limits": {"tenants": -1}}}).status_code, 400)
        d = self.call("get", f"/api/platform/msps/{test_id}/", token).json()
        self.assertIn("users", d)
        self.assertTrue(any(h["action"] == "subscription" for h in d["history"]))
        # Super-administrateurs : pas de désactivation de soi-même ; nouvelle clé MFA.
        my_id = next(a["id"] for a in console.admins() if a["username"] == me)
        self.assertEqual(self.call("patch", f"/api/platform/admins/{my_id}/", token, {"active": False}).status_code, 400)
        r = self.call("post", f"/api/platform/admins/{my_id}/reset-mfa/", token)
        self.assertEqual(len(r.json()["secret"]) >= 16, True)
