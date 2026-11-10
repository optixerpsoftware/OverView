import time

from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase

from module.platform import console
from module.platform.models import Domain, Msp, PlatformAudit
from module.platform.tests import ensure_other_msp
from shared_kernel.tenancy import PUBLIC, use_schema
from shared_kernel.totp import totp_code

PW = "Un-mot-de-passe-solide-42"
HOST = "platform.localhost"


class ConsoleTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_other_msp()

    def tearDown(self):
        from django.db import connection
        connection.set_schema("test")      # une requête vers la plateforme laisse la connexion sur le schéma public
        # Pas de suppression de comptes publics (le journal d'admin Django n'existe que dans les schémas des MSP) :
        # chaque test utilise ses propres noms.
        with use_schema(PUBLIC):
            Msp.objects.filter(schema_name="autre").update(is_active=True)
            Domain.objects.filter(domain="support.autre.localhost").delete()
        console.clear_domain_cache()

    def login(self) -> str:
        import uuid
        name = f"root-{uuid.uuid4().hex[:6]}"
        r = console.create_platform_admin(name, PW)
        c = Client()
        tok = c.post("/api/platform/auth/login/", {"username": name, "password": PW}, content_type="application/json",
                     HTTP_HOST=HOST).json()
        self.assertTrue(tok["mfa_required"])
        bad = c.post("/api/platform/auth/verify/", {"mfa_token": tok["mfa_token"], "code": "000000"},
                     content_type="application/json", HTTP_HOST=HOST)
        self.assertEqual(bad.status_code, 401)
        ok = c.post("/api/platform/auth/verify/", {"mfa_token": tok["mfa_token"], "code": totp_code(r["secret"], at=time.time())},
                    content_type="application/json", HTTP_HOST=HOST)
        self.assertEqual(ok.status_code, 200)
        return ok.json()["access"]

    def call(self, method, path, token, body=None):
        return getattr(Client(), method)(path, body or {}, content_type="application/json", HTTP_HOST=HOST,
                                         HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_console_flow(self):
        token = self.login()
        ov = self.call("get", "/api/platform/overview/", token).json()
        autre = next(m for m in ov["msps"] if m["schema"] == "autre")
        self.assertIn("organizations", autre["stats"])
        # Suspendre un MSP coupe l'accès à son espace.
        self.call("patch", f"/api/platform/msps/{autre['id']}/", token, {"active": False})
        self.assertEqual(Client().get("/api/organizations/", HTTP_HOST="autre.localhost").status_code, 404)
        self.call("patch", f"/api/platform/msps/{autre['id']}/", token, {"active": True})
        self.assertEqual(Client().get("/api/organizations/", HTTP_HOST="autre.localhost").status_code, 401)
        # Adresses
        r = self.call("post", f"/api/platform/msps/{autre['id']}/domains/", token, {"domain": "support.autre.localhost"})
        self.assertIn("support.autre.localhost", [d["domain"] for d in r.json()["domains"]])
        self.assertEqual(self.call("post", f"/api/platform/msps/{autre['id']}/domains/", token, {"domain": "bad domain"}).status_code, 400)
        primary = next(d for d in r.json()["domains"] if d["primary"])
        self.assertEqual(self.call("delete", f"/api/platform/msps/{autre['id']}/domains/", token, {"domain_id": primary["id"]}).status_code, 400)
        # Administrateur de secours dans l'espace du MSP
        r = self.call("post", f"/api/platform/msps/{autre['id']}/rescue-admin/", token, {"username": "secours", "password": PW})
        self.assertEqual(r.status_code, 201)
        with use_schema("autre"):
            self.assertTrue(get_user_model().objects.filter(username="secours", is_superuser=True).exists())
            get_user_model().objects.filter(username="secours").delete()
        # Création d'un MSP depuis la console (seul moyen avec la commande create_msp)
        r = self.call("post", "/api/platform/msps/", token, {"name": "Neuf", "slug": "neuf", "admin_username": "admin",
                                                             "admin_password": PW})
        self.assertEqual(r.status_code, 201)
        with use_schema(PUBLIC):
            self.assertTrue(PlatformAudit.objects.filter(action="msp_update").exists())

    def test_non_platform_users_are_refused(self):
        with use_schema(PUBLIC):
            get_user_model().objects.get_or_create(username="intrus")[0].set_password(PW)
            u = get_user_model().objects.get(username="intrus")
            u.set_password(PW)
            u.save()                                                              # compte public SANS rôle plateforme
        r = Client().post("/api/platform/auth/login/", {"username": "intrus", "password": PW}, content_type="application/json",
                          HTTP_HOST=HOST)
        self.assertEqual(r.status_code, 401)
        # Un jeton d'un MSP n'ouvre pas la console.
        from rest_framework_simplejwt.tokens import AccessToken
        with use_schema("test"):
            u = get_user_model().objects.create_superuser("msp-admin", password=PW)
            t = AccessToken.for_user(u)
        self.assertEqual(Client().get("/api/platform/overview/", HTTP_HOST=HOST, HTTP_AUTHORIZATION=f"Bearer {t}").status_code, 401)
