import os
import uuid
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import AsyncClient, SimpleTestCase, TransactionTestCase
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from module.microsoft_connector.application.consent_service import SALT, ConsentService
from module.microsoft_connector.domain.consent import admin_consent_url, parse_callback
from module.microsoft_connector.infrastructure.models import ConsentRequest, MicrosoftConnector
from module.organization.infrastructure.models import Organization

APP = "11111111-2222-3333-4444-555555555555"
TENANT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ENV = {"M365_APPLICATION_ID": APP, "M365_TOKEN": "secret-plateforme",
       "API_PUBLIC_URL": "http://localhost:8000", "APP_PUBLIC_URL": "http://localhost:3000"}


class DomainTests(SimpleTestCase):
    def test_url(self):
        u = urlparse(admin_consent_url(APP, "http://localhost:8000/cb/", "st"))
        self.assertEqual(u.path, "/organizations/v2.0/adminconsent")
        q = parse_qs(u.query)
        self.assertEqual((q["client_id"][0], q["redirect_uri"][0], q["state"][0]), (APP, "http://localhost:8000/cb/", "st"))
        self.assertEqual(q["scope"][0], "https://graph.microsoft.com/.default")
        self.assertIn(f"/{TENANT}/", admin_consent_url(APP, "x", "s", TENANT))
        self.assertIn("/organizations/", admin_consent_url(APP, "x", "s", "../evil"))
        self.assertIn("/client.onmicrosoft.com/", admin_consent_url(APP, "x", "s", "Client.onmicrosoft.com"))
        self.assertIn("/organizations/", admin_consent_url(APP, "x", "s", "evil.com/../x"))

    def test_parse(self):
        ok = parse_callback({"tenant": TENANT.upper(), "admin_consent": "True", "state": "s"})
        self.assertEqual((ok.ok, ok.tenant_id), (True, TENANT))
        self.assertFalse(parse_callback({"error": "access_denied", "error_description": "AADSTS65004: refus\r\nTrace"}).ok)
        self.assertEqual(parse_callback({"error": "access_denied", "error_description": "AADSTS65004: refus\r\nTrace"}).error,
                         "AADSTS65004: refus")
        self.assertFalse(parse_callback({"tenant": "pas-un-guid", "admin_consent": "True"}).ok)
        self.assertFalse(parse_callback({"tenant": TENANT, "admin_consent": "False"}).ok)


class FakeConnectors:
    """ConnectorService réel pour l'écriture, test de connexion simulé."""
    def __init__(self, errors=("",)):
        from module.microsoft_connector.application.services import ConnectorService
        self.real = ConnectorService()
        self.repo, self.orgs = self.real.repo, self.real.orgs
        self.errors = list(errors)
        self.tests = 0

    def register(self, **kw): return self.real.register(**kw)
    def update(self, *a, **kw): return self.real.update(*a, **kw)

    async def test_connection_async(self, cid):
        self.tests += 1
        err = self.errors.pop(0) if self.errors else ""
        class C: last_error = err
        return C()


@mock.patch.dict(os.environ, ENV)
class ServiceTests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Client")
        self.fake = FakeConnectors()
        self.svc = ConsentService(connectors=self.fake, test_delays=(0, 0))
        self.svc._name_from_tenant = mock.AsyncMock()

    def state_of(self, url):
        return parse_qs(urlparse(url).query)["state"][0]

    def finish(self, params):
        return async_to_sync(self.svc.complete_async)(params)

    def test_full_flow_creates_connector_once(self):
        state = self.state_of(self.svc.start(org_id=self.org.id, name="Client SA", by="admin"))
        dest = self.finish({"tenant": TENANT, "admin_consent": "True", "state": state})
        self.assertTrue(dest.startswith(f"http://localhost:3000/organizations/{self.org.id}?consent=ok"))
        c = MicrosoftConnector.objects.get()
        self.assertEqual((str(c.tenant_id), str(c.client_id), c.client_secret, c.name), (TENANT, APP, "secret-plateforme", "Client SA"))
        # rejouer le même retour : refusé (usage unique), aucun second connecteur
        again = self.finish({"tenant": TENANT, "admin_consent": "True", "state": state})
        self.assertIn("consent=error", again)
        self.assertEqual(MicrosoftConnector.objects.count(), 1)

    def test_reconnect_reactivates_existing(self):
        c = MicrosoftConnector.objects.create(org_id=self.org.id, name="old", tenant_id=TENANT, client_id=APP,
                                              client_secret="ancien", is_active=False)
        state = self.state_of(self.svc.start(org_id=self.org.id, name="", by="admin"))
        self.finish({"tenant": TENANT, "admin_consent": "True", "state": state})
        c.refresh_from_db()
        self.assertEqual((c.is_active, c.client_secret), (True, "secret-plateforme"))
        self.assertEqual(MicrosoftConnector.objects.count(), 1)

    def test_forged_expired_and_refused(self):
        self.assertIn("consent=error", self.finish({"tenant": TENANT, "admin_consent": "True", "state": "forge"}))
        forged = signing.dumps({"n": str(uuid.uuid4())}, salt="autre-sel")
        self.assertIn("consent=error", self.finish({"tenant": TENANT, "admin_consent": "True", "state": forged}))
        state = self.state_of(self.svc.start(org_id=self.org.id, name="x", by="admin"))
        ConsentRequest.objects.update(create_at=timezone.now() - timedelta(minutes=30))
        self.assertIn("expir", self.finish({"tenant": TENANT, "admin_consent": "True", "state": state}))
        state = self.state_of(self.svc.start(org_id=self.org.id, name="x", by="admin"))
        dest = self.finish({"error": "access_denied", "error_description": "refusé par l'admin", "state": state})
        self.assertIn(f"/organizations/{self.org.id}?consent=error", dest)
        self.assertEqual(MicrosoftConnector.objects.count(), 0)

    def test_propagation_delay_reports_pending(self):
        self.fake.errors = ["401", "401"]
        state = self.state_of(self.svc.start(org_id=self.org.id, name="x", by="admin"))
        dest = self.finish({"tenant": TENANT, "admin_consent": "True", "state": state})
        self.assertIn("consent=pending", dest)
        self.assertEqual(self.fake.tests, 2)

    def test_not_configured(self):
        from module.microsoft_connector.application.consent_service import ConsentNotConfigured
        with mock.patch.dict(os.environ, {"M365_TOKEN": ""}):
            with self.assertRaises(ConsentNotConfigured):
                self.svc.start(org_id=self.org.id, name="", by="a")
            self.assertFalse(ConsentService.config()["available"])


@mock.patch.dict(os.environ, ENV)
class ViewTests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Client")
        u = get_user_model().objects.create_superuser("adm", password="x")
        self.h = {"Authorization": f"Bearer {AccessToken.for_user(u)}"}
        self.c = AsyncClient()

    def test_start_requires_auth_and_callback_redirects(self):
        r = async_to_sync(self.c.post)("/api/connectors/consent/start/", {"org_id": str(self.org.id)}, content_type="application/json")
        self.assertEqual(r.status_code, 401)
        r = async_to_sync(self.c.post)("/api/connectors/consent/start/", {"org_id": str(self.org.id)},
                                       content_type="application/json", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["url"].startswith("https://login.microsoftonline.com/organizations/v2.0/adminconsent?"))
        r = async_to_sync(self.c.post)("/api/connectors/consent/start/", {"org_id": str(uuid.uuid4())},
                                       content_type="application/json", headers=self.h)
        self.assertEqual(r.status_code, 404)
        # le callback est public (navigateur) mais ne fait rien sans `state` valide
        r = async_to_sync(self.c.get)("/api/connectors/consent/callback/?tenant=" + TENANT + "&admin_consent=True&state=x")
        self.assertEqual(r.status_code, 302)
        self.assertIn("consent=error", r["Location"])
        r = async_to_sync(self.c.get)("/api/connectors/consent/config/", headers=self.h)
        self.assertEqual(r.json()["redirect_uri"], "http://localhost:8000/api/connectors/consent/callback/")
