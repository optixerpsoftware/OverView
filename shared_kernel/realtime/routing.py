from django.urls import path

from shared_kernel.realtime.consumers import EventsConsumer

websocket_urlpatterns = [
    path("ws/events/", EventsConsumer.as_asgi()),
]
