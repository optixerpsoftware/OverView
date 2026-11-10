import uuid

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import AsyncClient, Client, TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from module.accounts.application.service import AccountError, AccountService
from module.insights.infrastructure.models import Alert
from module.microsoft_connector.infrastructure.models import MicrosoftConnector
from module.microsoft_outlook.infrastructure.models import Mailbox, OutlookRules
from module.organization.infrastructure.models import Organization

PW = "Un-mot-de-passe-solide-42"


class ScopeTests(TransactionTestCase):
    def setUp(self):
        self.a = Organization.objects.create(name="Client A")
        self.b = Organization.objects.create(name="Client B")
        self.ca = MicrosoftConnector.objects.create(org_id=self.a.id, name="a.ca", tenant_id=uuid.uuid4(), client_id=uuid.uuid4(), client_secret="s")
        self.cb = MicrosoftConnector.objects.create(org_id=self.b.id, name="b.ca", tenant_id=uuid.uuid4(), client_id=uuid.uuid4(), client_secret="s")
        self.mb = Mailbox.objects.create(org_id=self.b.id, connector_id=self.cb.id, user_principal_name="boss@b.ca")
        self.rule_b = OutlookRules.objects.create(mailbox=self.mb, rule_id="r", display_name="fwd", sequence=1,
                                                  is_enabled=True, has_error=False, is_read_only=False)
        for org, conn in ((self.a, self.ca), (self.b, self.cb)):
            Alert.objects.create(connector_id=conn.id, org_id=org.id, key=f"k-{org.name}", category="governance",
                                 severity="high", title=f"Alerte {org.name}", link="/compliance/x")
        self.admin = get_user_model().objects.create_superuser("root", password=PW)
        self.tech = AccountService.create_user("tech", PW, "operator", all_orgs=False, org_ids=[str(self.a.id)])
        self.client_user = AccountService.create_user("client", PW, "client", org_ids=[str(self.a.id)])
        self.ac = AsyncClient()

    def h(self, username):
        u = get_user_model().objects.get(username=username)
        return {"Authorization": f"Bearer {AccessToken.for_user(u)}"}

    def get(self, path, who):
        return async_to_sync(self.ac.get)(path, headers=self.h(who))

    def test_technician_sees_only_assigned_clients(self):
        names = [o["name"] for o in Client().get("/api/organizations/", HTTP_AUTHORIZATION=self.h("tech")["Authorization"]).json()]
        self.assertEqual(names, ["Client A"])
        self.assertEqual(Client().get(f"/api/organizations/{self.b.id}/", HTTP_AUTHORIZATION=self.h("tech")["Authorization"]).status_code, 404)
        self.assertEqual([a["title"] for a in self.get("/api/insights/alerts/", "tech").json()], ["Alerte Client A"])
        self.assertEqual(self.get("/api/insights/alerts/counts/", "tech").json()["total"], 1)
        self.assertEqual(len(self.get("/api/insights/queue/", "tech").json()["items"]), 1)
        d = self.get("/api/insights/dashboard/", "tech").json()
        self.assertEqual([t["name"] for t in d["tenants"]], ["a.ca"])
        # Adresses visant le client B : 404 avant même d'interroger Microsoft.
        self.assertEqual(self.get(f"/api/outlook/connectors/{self.cb.id}/quarantine/", "tech").status_code, 404)
        self.assertEqual(self.get(f"/api/identity/mailboxes/{self.mb.id}/security/", "tech").status_code, 404)
        self.assertEqual(self.get(f"/api/insights/organizations/{self.b.id}/report/", "tech").status_code, 404)
        self.assertEqual(self.get("/api/insights/search/?q=boss", "tech").json(), [])
        r = async_to_sync(self.ac.post)("/api/insights/bulk/rules/delete/", {"rule_ids": [str(self.rule_b.id)], "confirm": "SUPPRIMER"},
                                        content_type="application/json", headers=self.h("tech"))
        self.assertEqual(r.status_code, 404)
        alert_b = Alert.objects.get(org_id=self.b.id)
        r = async_to_sync(self.ac.post)("/api/insights/alerts/status/", {"ids": [str(alert_b.id)], "status": "acknowledged"},
                                        content_type="application/json", headers=self.h("tech"))
        self.assertEqual(r.json()["updated"], 0)                      # alerte du client B : non modifiée

    def test_client_portal_is_read_only_on_its_own_org(self):
        self.assertEqual(self.get(f"/api/insights/organizations/{self.a.id}/report/", "client").status_code, 200)
        self.assertEqual(self.get(f"/api/insights/organizations/{self.b.id}/report/", "client").status_code, 404)
        self.assertEqual([a["title"] for a in self.get("/api/insights/alerts/", "client").json()], ["Alerte Client A"])
        self.assertEqual(self.get("/api/insights/queue/", "client").status_code, 403)     # hors portail
        self.assertEqual(self.get(f"/api/outlook/connectors/{self.ca.id}/quarantine/", "client").status_code, 403)
        r = async_to_sync(self.ac.post)("/api/insights/alerts/status/", {"ids": [], "status": "acknowledged"},
                                        content_type="application/json", headers=self.h("client"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.get("/api/accounts/me/", "client").json()["role"], "client")

    def test_admin_sees_everything_and_validation(self):
        self.assertEqual(len(self.get("/api/insights/alerts/", "root").json()), 2)
        with self.assertRaises(AccountError):
            AccountService.create_user("c2", PW, "client")                              # client sans organisation
        with self.assertRaises(AccountError):
            AccountService.create_user("c3", PW, "operator", all_orgs=False, org_ids=[str(uuid.uuid4())])
        u = AccountService.update_user(self.admin, self.tech["id"], all_orgs=True)
        self.assertTrue(u["all_orgs"])
        self.assertEqual(len(self.get("/api/insights/alerts/", "tech").json()), 2)
