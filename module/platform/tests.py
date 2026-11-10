import os
from unittest import mock

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import AsyncClient, Client, TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from module.organization.infrastructure.models import Organization
from module.platform.models import Domain, Msp
from module.platform.service import SignupError, create_msp
from shared_kernel.tenancy import (
    CURRENT_SCHEMA, PUBLIC, TENANT_CLAIM, clear_domain_cache, current_schema, for_each_tenant, use_schema,
)

PW = "Un-mot-de-passe-solide-42"
OTHER = "autre"


def ensure_other_msp():
    with use_schema(PUBLIC):
        if not Msp.objects.filter(schema_name=OTHER).exists():
            m = Msp(schema_name=OTHER, name="Autre MSP", slug=OTHER)
            m.save()                                           # crée et migre le schéma
            Domain.objects.create(domain="autre.localhost", tenant=m, is_primary=True)
    clear_domain_cache()


class IsolationTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_other_msp()

    def setUp(self):
        self.c = AsyncClient()

    def test_data_and_tokens_do_not_cross_msps(self):
        Organization.objects.create(name="Client du MSP test")
        admin = get_user_model().objects.create_superuser("adm", password=PW)
        token = AccessToken.for_user(admin)
        self.assertEqual(token[TENANT_CLAIM], "test")
        h = {"Authorization": f"Bearer {token}"}
        r = async_to_sync(self.c.get)("/api/organizations/", headers=h)
        self.assertEqual([o["name"] for o in r.json()], ["Client du MSP test"])
        # Même jeton, autre MSP : refusé (les identifiants d'utilisateurs se recoupent entre schémas).
        r = Client().get("/api/organizations/", HTTP_HOST="autre.localhost", HTTP_AUTHORIZATION=h["Authorization"])
        self.assertEqual(r.status_code, 401)
        with use_schema(OTHER):
            self.assertEqual(Organization.objects.count(), 0)        # rien n'a fui
            other_admin = get_user_model().objects.create_superuser("adm", password=PW)
            other_token = AccessToken.for_user(other_admin)
            Organization.objects.create(name="Client de l'autre MSP")
        r = Client().get("/api/organizations/", HTTP_HOST="autre.localhost", HTTP_AUTHORIZATION=f"Bearer {other_token}")
        self.assertEqual([o["name"] for o in r.json()], ["Client de l'autre MSP"])
        r = async_to_sync(self.c.get)("/api/insights/dashboard/", headers={"Authorization": f"Bearer {other_token}"})
        self.assertEqual(r.status_code, 401)                          # jeton de l'autre MSP sur le MSP test
        with use_schema(OTHER):
            Organization.objects.all().delete()
            get_user_model().objects.all().delete()

    def test_unknown_host_is_404_and_login_is_per_msp(self):
        get_user_model().objects.create_user("bob", password=PW)
        r = async_to_sync(self.c.post)("/api/auth/login/", {"username": "bob", "password": PW}, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        r = Client().post("/api/auth/login/", {"username": "bob", "password": PW},
                          content_type="application/json", HTTP_HOST="autre.localhost")
        self.assertEqual(r.status_code, 401)                          # bob n'existe pas dans l'autre MSP

    def test_for_each_tenant_and_context(self):
        seen = for_each_tenant(lambda: current_schema())
        self.assertIn(OTHER, seen)
        self.assertEqual(seen[OTHER], OTHER)
        tok = CURRENT_SCHEMA.set(OTHER)                               # tâche lancée depuis un MSP précis
        try:
            self.assertEqual(for_each_tenant(lambda: current_schema()), {OTHER: OTHER})
        finally:
            CURRENT_SCHEMA.reset(tok)

    def test_consent_state_carries_the_msp(self):
        from module.microsoft_connector.application.consent_service import SALT, ConsentService
        org = Organization.objects.create(name="O")
        with mock.patch.dict(os.environ, {"M365_APPLICATION_ID": "11111111-2222-3333-4444-555555555555", "M365_TOKEN": "s"}):
            url = ConsentService().start(org_id=org.id, name="", by="a")
        state = url.split("state=")[1]
        from urllib.parse import unquote
        self.assertEqual(signing.loads(unquote(state), salt=SALT)["s"], "test")


class SignupTests(TransactionTestCase):
    def test_validation_and_closed_signup(self):
        for kw in ({"slug": "Bad Slug"}, {"slug": "public"}, {"slug": "okslug", "admin_password": "123"}):
            with self.assertRaises(SignupError):
                create_msp(**{"name": "X", "slug": "okslug", "admin_username": "a", "admin_password": PW, **kw})
        r = Client().post("/api/platform/signup/", {"name": "X", "slug": "nouveau"},
                          content_type="application/json", HTTP_HOST="platform.localhost")
        self.assertEqual(r.status_code, 403)                          # inscriptions fermées par défaut
        r = Client().get("/api/platform/info/", HTTP_HOST="platform.localhost")
        self.assertEqual(r.json()["signup_open"], False)
