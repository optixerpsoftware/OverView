import time

from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from shared_kernel.realtime import apublish, publish
from shared_kernel.realtime.consumers import AUTH_FAILED
from shared_kernel.realtime.routing import websocket_urlpatterns

app = URLRouter(websocket_urlpatterns)


async def connect(token: str):
    comm = WebsocketCommunicator(app, f"/ws/events/?token={token}")
    connected, _ = await comm.connect()
    return comm, connected


class EventsConsumerTests(TransactionTestCase):
    def make_user(self, **kw):
        return get_user_model().objects.create_user("ws-user", password="x", **kw)

    async def test_valid_token_connects_and_receives_published_events(self):
        user = await get_user_model().objects.acreate(username="ws-a")
        comm, ok = await connect(str(AccessToken.for_user(user)))
        self.assertTrue(ok)
        self.assertEqual((await comm.receive_json_from())["topic"], "ready")
        await apublish("security.sync", {"mailbox_id": "m1", "status": "running"})
        self.assertEqual(await comm.receive_json_from(), {"topic": "security.sync", "data": {"mailbox_id": "m1", "status": "running"}})
        await comm.disconnect()

    async def test_invalid_or_missing_token_is_closed_with_4401(self):
        for token in ("", "pas-un-jeton"):
            comm = WebsocketCommunicator(app, f"/ws/events/?token={token}")
            await comm.connect()
            out = await comm.receive_output()
            self.assertEqual((out["type"], out.get("code")), ("websocket.close", AUTH_FAILED))

    async def test_refresh_token_is_not_accepted_as_access_token(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        user = await get_user_model().objects.acreate(username="ws-r")
        comm = WebsocketCommunicator(app, f"/ws/events/?token={RefreshToken.for_user(user)}")
        await comm.connect()
        self.assertEqual((await comm.receive_output())["code"], AUTH_FAILED)

    async def test_inactive_user_is_refused(self):
        user = await get_user_model().objects.acreate(username="ws-off", is_active=False)
        comm = WebsocketCommunicator(app, f"/ws/events/?token={AccessToken.for_user(user)}")
        await comm.connect()
        self.assertEqual((await comm.receive_output())["code"], AUTH_FAILED)

    async def test_deleted_user_is_refused(self):
        user = await get_user_model().objects.acreate(username="ws-gone")
        token = str(AccessToken.for_user(user))
        await user.adelete()
        comm = WebsocketCommunicator(app, f"/ws/events/?token={token}")
        await comm.connect()
        self.assertEqual((await comm.receive_output())["code"], AUTH_FAILED)

    async def test_ping_pong(self):
        user = await get_user_model().objects.acreate(username="ws-p")
        comm, _ = await connect(str(AccessToken.for_user(user)))
        await comm.receive_json_from()   # ready
        await comm.send_json_to({"type": "ping"})
        self.assertEqual((await comm.receive_json_from())["topic"], "pong")
        await comm.disconnect()

    async def test_connection_is_closed_when_the_token_expires(self):
        user = await get_user_model().objects.acreate(username="ws-e")
        token = AccessToken.for_user(user)
        token.set_exp(from_time=None, lifetime=__import__("datetime").timedelta(seconds=1))
        comm, ok = await connect(str(token))
        self.assertTrue(ok)
        await comm.receive_json_from()   # ready
        self.assertEqual((await comm.receive_json_from(timeout=3))["topic"], "auth.expired")
        self.assertEqual((await comm.receive_output(timeout=3))["code"], AUTH_FAILED)

    async def test_every_connected_client_gets_the_event(self):
        a = await get_user_model().objects.acreate(username="ws-1")
        b = await get_user_model().objects.acreate(username="ws-2")
        c1, _ = await connect(str(AccessToken.for_user(a)))
        c2, _ = await connect(str(AccessToken.for_user(b)))
        await c1.receive_json_from(); await c2.receive_json_from()
        await apublish("sharing.scan", {"connector_id": "c", "status": "done"})
        self.assertEqual((await c1.receive_json_from())["topic"], "sharing.scan")
        self.assertEqual((await c2.receive_json_from())["topic"], "sharing.scan")
        await c1.disconnect(); await c2.disconnect()

    def test_publish_never_raises_even_without_a_working_layer(self):
        from unittest import mock
        with mock.patch("shared_kernel.realtime.get_channel_layer", side_effect=RuntimeError("redis down")):
            publish("x", {})      # doit simplement journaliser
