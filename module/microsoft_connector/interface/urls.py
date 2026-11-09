from django.urls import path
from rest_framework.routers import DefaultRouter

from module.microsoft_connector.interface.views import MicrosoftConnectorViewSet, TestConnectorView

router = DefaultRouter()
router.register('connectors', MicrosoftConnectorViewSet, basename='connector')

# Vue async (appel Graph) avant le routeur DRF : même URL qu'avant (POST connectors/<id>/test/).
urlpatterns = [path('connectors/<uuid:connector_id>/test/', TestConnectorView.as_view())] + router.urls
