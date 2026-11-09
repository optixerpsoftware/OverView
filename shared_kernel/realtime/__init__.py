"""Temps réel : publication d'événements vers les navigateurs connectés (WebSocket, via Channels).

Un seul groupe (« events ») : l'application est un outil d'administration, tout utilisateur authentifié reçoit
les mêmes notifications d'état (synchro d'un compte, audit de partage…). Les événements ne contiennent que des
identifiants et des états : le navigateur recharge ensuite les données par l'API (soumise aux droits)."""
from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

GROUP = "events"
log = logging.getLogger(__name__)


async def apublish(topic: str, data: dict[str, Any]) -> None:
    """Publie un événement (depuis du code async). Ne lève jamais : le temps réel est un confort, pas une dépendance."""
    try:
        layer = get_channel_layer()
        if layer is not None:
            await layer.group_send(GROUP, {"type": "event.message", "payload": {"topic": topic, "data": data}})
    except Exception:  # Redis indisponible, etc.
        log.warning("Publication temps réel impossible (%s)", topic, exc_info=True)


def publish(topic: str, data: dict[str, Any]) -> None:
    """Publie un événement (depuis du code synchrone : tâches Celery, services)."""
    async_to_sync(apublish)(topic, data)
