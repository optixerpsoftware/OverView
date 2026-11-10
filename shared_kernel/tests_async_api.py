import asyncio
import uuid
from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.urls import path
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from shared_kernel.async_api import AsyncAPIView, Response, no_store


class Boom(Exception):
    pass


class EchoView(AsyncAPIView):
    async def get(self, request):
        await asyncio.sleep(0)                       # vraie coroutine : pas de thread bloqué
        return Response({"user": request.user.username, "q": request.query_params.get("q"), "id": uuid.UUID(int=1),
                         "at": datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)})

    async def post(self, request):
        return Response({"got": request.data}, 201)

    async def delete(self, request):
        return Response(status=204)

    async def put(self, request):
        raise Boom("détail interne secret")


class MappedView(AsyncAPIView):
    def handle_exception(self, exc):
        if isinstance(exc, KeyError):
            return Response({"detail": "clé manquante"}, 404)
        return super().handle_exception(exc)

    async def get(self, request):
        raise KeyError("x")


class SecretView(AsyncAPIView):
    async def get(self, request):
        return no_store(Response({"password": "p"}))


class OpenView(AsyncAPIView):
    authentication_required = False

    async def get(self, request):
        return Response({"open": True})


urlpatterns = [
    path("echo/", EchoView.as_view()),
    path("mapped/", MappedView.as_view()),
    path("secret/", SecretView.as_view()),
    path("open/", OpenView.as_view()),
]


@override_settings(ROOT_URLCONF="shared_kernel.tests_async_api")
class AsyncAPIViewTests(TransactionTestCase):
    async def asetUp(self):
        self.user = await get_user_model().objects.acreate(username="alice", is_superuser=True)
        self.auth = {"headers": {"Authorization": f"Bearer {AccessToken.for_user(self.user)}"}}

    async def test_views_are_real_coroutines(self):
        self.assertTrue(asyncio.iscoroutinefunction(EchoView.as_view()))

    async def test_authenticated_request_serializes_like_drf(self):
        await self.asetUp()
        r = await self.async_client.get("/echo/?q=1", **self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"user": "alice", "q": "1", "id": "00000000-0000-0000-0000-000000000001",
                                    "at": "2026-01-02T03:04:00Z"})

    async def test_missing_invalid_expired_and_refresh_tokens_give_401_json(self):
        await self.asetUp()
        refresh = str(RefreshToken.for_user(self.user))
        for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": f"Bearer {refresh}"},
                        {"Authorization": "Basic abc"}):
            r = await self.async_client.get("/echo/", headers=headers)
            self.assertEqual(r.status_code, 401, msg=str(headers))
            self.assertIn("detail", r.json())
            self.assertEqual(r["WWW-Authenticate"], "Bearer")

    async def test_inactive_or_deleted_user_is_refused(self):
        await self.asetUp()
        await get_user_model().objects.filter(pk=self.user.pk).aupdate(is_active=False)
        self.assertEqual((await self.async_client.get("/echo/", **self.auth)).status_code, 401)
        await self.user.adelete()
        self.assertEqual((await self.async_client.get("/echo/", **self.auth)).status_code, 401)

    async def test_json_body_is_parsed_and_invalid_json_is_a_400(self):
        await self.asetUp()
        r = await self.async_client.post("/echo/", data='{"a": [1, 2]}', content_type="application/json", **self.auth)
        self.assertEqual((r.status_code, r.json()), (201, {"got": {"a": [1, 2]}}))
        bad = await self.async_client.post("/echo/", data="{pas du json", content_type="application/json", **self.auth)
        self.assertEqual((bad.status_code, bad.json()["detail"]), (400, "Corps JSON invalide."))

    async def test_delete_with_body_and_204(self):
        await self.asetUp()
        r = await self.async_client.delete("/echo/", data='{"confirm": "x"}', content_type="application/json", **self.auth)
        self.assertEqual((r.status_code, r.content), (204, b""))

    async def test_unhandled_exception_is_a_generic_500_without_leaking_details(self):
        await self.asetUp()
        with self.assertLogs("shared_kernel.async_api", "ERROR"):
            r = await self.async_client.put("/echo/", **self.auth)
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("secret", r.content.decode())

    async def test_subclass_maps_domain_exceptions(self):
        await self.asetUp()
        r = await self.async_client.get("/mapped/", **self.auth)
        self.assertEqual((r.status_code, r.json()), (404, {"detail": "clé manquante"}))

    async def test_unsupported_method_is_405_with_allow_header(self):
        await self.asetUp()
        r = await self.async_client.patch("/secret/", **self.auth)
        self.assertEqual(r.status_code, 405)
        self.assertIn("GET", r["Allow"])

    async def test_no_store_headers_and_optional_authentication(self):
        await self.asetUp()
        r = await self.async_client.get("/secret/", **self.auth)
        self.assertEqual((r["Cache-Control"], r["Pragma"]), ("no-store", "no-cache"))
        self.assertEqual((await self.async_client.get("/open/")).status_code, 200)

    async def test_no_csrf_required_for_bearer_authenticated_posts(self):
        await self.asetUp()
        from django.test import AsyncClient
        client = AsyncClient(enforce_csrf_checks=True)
        r = await client.post("/echo/", data="{}", content_type="application/json", **self.auth)
        self.assertEqual(r.status_code, 201)
