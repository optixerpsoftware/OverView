from __future__ import annotations

import asyncio
import time
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from shared_kernel.realtime import group_name
from shared_kernel.tenancy import CURRENT_SCHEMA, TENANT_CLAIM, schema_for_host

AUTH_FAILED = 4401   # code de fermeture applicatif : le client doit se reconnecter avec un jeton valide


@database_sync_to_async
def _user_for(token: AccessToken):
    User = get_user_model()
    try:
        user = User.objects.get(pk=token["user_id"])
    except (User.DoesNotExist, KeyError, ValueError):
        return None
    return user if user.is_active else None


class EventsConsumer(AsyncJsonWebsocketConsumer):
    """WebSocket `/ws/events/?token=<JWT d'accès>`.

    Le navigateur ne peut pas envoyer d'en-tête Authorization sur un WebSocket : le jeton passe en paramètre.
    Il est vérifié à la connexion, et la connexion est fermée (4401) quand il expire : le client en obtient un
    nouveau (rafraîchissement) et se reconnecte."""

    async def connect(self):
        self._expiry_task = None
        raw = parse_qs(self.scope.get("query_string", b"").decode()).get("token", [""])[0]
        await self.accept()                      # on accepte d'abord pour pouvoir renvoyer un code de fermeture précis
        host = dict(self.scope.get("headers") or []).get(b"host", b"").decode()
        self._schema = await database_sync_to_async(schema_for_host)(host)
        if not self._schema:
            await self.close(code=AUTH_FAILED)
            return
        CURRENT_SCHEMA.set(self._schema)        # pour toute la durée de la connexion (même tâche asyncio)
        try:
            token = AccessToken(raw)
        except TokenError:
            await self.close(code=AUTH_FAILED)
            return
        if token.get(TENANT_CLAIM) != self._schema:   # jeton d'un autre MSP
            await self.close(code=AUTH_FAILED)
            return
        user = await _user_for(token)
        if user is None:
            await self.close(code=AUTH_FAILED)
            return
        await self.channel_layer.group_add(group_name(self._schema), self.channel_name)
        self._joined = True
        delay = max(0.0, float(token["exp"]) - time.time())
        self._expiry_task = asyncio.create_task(self._close_at_expiry(delay))
        await self.send_json({"topic": "ready", "data": {}})

    async def _close_at_expiry(self, delay: float):
        await asyncio.sleep(delay)
        await self.send_json({"topic": "auth.expired", "data": {}})
        await self.close(code=AUTH_FAILED)

    async def disconnect(self, code):
        if getattr(self, "_expiry_task", None):
            self._expiry_task.cancel()
        if getattr(self, "_joined", False):
            await self.channel_layer.group_discard(group_name(self._schema), self.channel_name)

    async def receive_json(self, content, **kwargs):
        if isinstance(content, dict) and content.get("type") == "ping":     # battement de cœur du client
            await self.send_json({"topic": "pong", "data": {}})

    async def event_message(self, event):
        """Diffuse au navigateur un événement publié par `shared_kernel.realtime.publish`."""
        await self.send_json(event["payload"])
