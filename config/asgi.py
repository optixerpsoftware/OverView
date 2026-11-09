"""
ASGI config : HTTP (Django) et WebSocket (Channels) servis par Daphne.

    daphne -b 0.0.0.0 -p 8000 config.asgi:application
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# L'application HTTP doit être créée AVANT d'importer ce qui touche aux modèles (initialise le registre d'apps).
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from shared_kernel.realtime.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    # L'origine du navigateur doit appartenir à ALLOWED_HOSTS ; l'authentification se fait dans le consommateur (JWT).
    'websocket': AllowedHostsOriginValidator(URLRouter(websocket_urlpatterns)),
})
