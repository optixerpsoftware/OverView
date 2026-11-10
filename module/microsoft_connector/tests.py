import uuid
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from module.microsoft_connector.application.services import ConnectorNotFound, ConnectorService
from module.microsoft_connector.infrastructure.models import MicrosoftConnector
from module.organization.infrastructure.models import Organization

BUILD = "module.microsoft_connector.application.services.build_graph"


def graph(error=None):
    async def get():
        if error:
            raise error
        return SimpleNamespace(value=[])
    return SimpleNamespace(client=SimpleNamespace(organization=SimpleNamespace(get=get)))


class ConnectorTestConnectionTests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.conn = MicrosoftConnector.objects.create(
            org_id=self.org.id, name="c", tenant_id=uuid.uuid4(), client_id=uuid.uuid4(), client_secret="s")

    def test_success_is_recorded(self):
        with mock.patch(BUILD, return_value=graph()):
            c = ConnectorService().test_connection(self.conn.id)
        self.assertTrue(c.last_check_ok)
        self.conn.refresh_from_db()
        self.assertEqual((self.conn.last_error, self.conn.last_checked_at is not None), ("", True))

    def test_failure_is_recorded_not_raised(self):
        with mock.patch(BUILD, return_value=graph(PermissionError("AADSTS7000215 secret invalide"))):
            c = ConnectorService().test_connection(self.conn.id)
        self.assertFalse(c.last_check_ok)
        self.conn.refresh_from_db()
        self.assertIn("AADSTS7000215", self.conn.last_error)

    def test_unknown_connector(self):
        with self.assertRaises(ConnectorNotFound):
            ConnectorService().test_connection(uuid.uuid4())

    def test_a_new_secret_resets_the_previous_check(self):
        with mock.patch(BUILD, return_value=graph(PermissionError("KO"))):
            ConnectorService().test_connection(self.conn.id)
        ConnectorService().update(self.conn.id, client_secret="nouveau")
        self.conn.refresh_from_db()
        self.assertEqual((self.conn.last_error, self.conn.last_checked_at), ("", None))


class ConnectorAsyncViewTests(TransactionTestCase):
    async def asetUp(self):
        self.org = await Organization.objects.acreate(name="Org")
        self.conn = await MicrosoftConnector.objects.acreate(
            org_id=self.org.id, name="c", tenant_id=uuid.uuid4(), client_id=uuid.uuid4(), client_secret="s")
        u = await get_user_model().objects.acreate(username="op", is_superuser=True)
        self.h = {"Authorization": f"Bearer {AccessToken.for_user(u)}"}

    async def test_test_endpoint_over_http(self):
        await self.asetUp()
        url = f"/api/connectors/{self.conn.id}/test/"
        self.assertEqual((await self.async_client.post(url)).status_code, 401)
        with mock.patch(BUILD, return_value=graph()):
            r = await self.async_client.post(url, headers=self.h)
        self.assertEqual((r.status_code, r.json()), (200, {"ok": True, "error": ""}))
        with mock.patch(BUILD, return_value=graph(RuntimeError("réseau"))):
            r = await self.async_client.post(url, headers=self.h)
        self.assertEqual((r.status_code, r.json()["ok"]), (200, False))

    async def test_unknown_connector_is_404(self):
        await self.asetUp()
        r = await self.async_client.post(f"/api/connectors/{uuid.uuid4()}/test/", headers=self.h)
        self.assertEqual(r.status_code, 404)

    async def test_connector_list_never_exposes_the_secret(self):
        await self.asetUp()
        r = await self.async_client.get(f"/api/connectors/?org_id={self.org.id}", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("client_secret", r.content.decode())
