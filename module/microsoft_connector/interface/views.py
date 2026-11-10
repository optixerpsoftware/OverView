from rest_framework import status, viewsets
from shared_kernel.async_api import AsyncAPIView, Response as AsyncResponse, status as http
from rest_framework.response import Response

from module.microsoft_connector.application.services import (
    ConnectorNotFound, ConnectorService, OrganizationNotFound,
)
from module.microsoft_outlook.application.services import OutlookService
from module.microsoft_connector.infrastructure.models import MicrosoftConnector
from module.microsoft_connector.interface.serializers import MicrosoftConnectorSerializer


class MicrosoftConnectorViewSet(viewsets.ModelViewSet):
    """Lectures via le queryset (CQRS léger) ; écritures métier via ConnectorService."""
    queryset = MicrosoftConnector.objects.all().order_by('name')
    serializer_class = MicrosoftConnectorSerializer
    filterset_fields = ['org_id', 'is_active']

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            entity = ConnectorService().register(**ser.validated_data)
        except OrganizationNotFound:
            return Response({'org_id': ['Organisation introuvable.']}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        out = self.get_serializer(MicrosoftConnector.objects.get(pk=entity.id))
        return Response(out.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        """Modifie nom / secret / activation via le service (le secret n'est jamais renvoyé)."""
        data = request.data
        try:
            ConnectorService().update(
                kwargs['pk'],
                name=data.get('name'),
                client_secret=data.get('client_secret'),
                is_active=data.get('is_active'),
            )
        except ConnectorNotFound:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(self.get_object()).data)

    def update(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Supprime le connecteur et ses boîtes/règles locales (rien dans Microsoft 365)."""
        try:
            ConnectorService().delete(kwargs['pk'], purgers=[OutlookService().purge_connector])
        except ConnectorNotFound:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TestConnectorView(AsyncAPIView):
    """POST : vérifie les identifiants via un appel Graph (/organization). Vue async native (Daphne)."""

    async def post(self, request, connector_id):
        c = await ConnectorService().test_connection_async(connector_id)
        return AsyncResponse({'ok': c.last_check_ok, 'error': c.last_error})

    def handle_exception(self, exc):
        if isinstance(exc, ConnectorNotFound):
            return AsyncResponse(status=http.HTTP_404_NOT_FOUND)
        return super().handle_exception(exc)


# ---------------------------------------------------------------- connexion par consentement administrateur
from django.http import HttpResponseRedirect  # noqa: E402

from module.microsoft_connector.application.consent_service import ConsentNotConfigured, ConsentService  # noqa: E402
from shared_kernel.aio import db  # noqa: E402


class ConsentConfigView(AsyncAPIView):
    """GET : la connexion « en un clic » est-elle configurée ? (et l'URI de redirection à déclarer dans Azure)."""

    async def get(self, request):
        return AsyncResponse(ConsentService.config())


class ConsentStartView(AsyncAPIView):
    """POST {org_id, name?, tenant_hint?} → {url} : adresse Microsoft où envoyer l'administrateur du client."""

    def handle_exception(self, exc):
        if isinstance(exc, ConsentNotConfigured):
            return AsyncResponse({"detail": str(exc)}, http.HTTP_503_SERVICE_UNAVAILABLE)
        if isinstance(exc, OrganizationNotFound):
            return AsyncResponse({"detail": "Organisation introuvable."}, http.HTTP_404_NOT_FOUND)
        if isinstance(exc, ValueError):
            return AsyncResponse({"detail": "Identifiant d'organisation invalide."}, http.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)

    async def post(self, request):
        import uuid as _uuid
        body = request.data if isinstance(request.data, dict) else {}
        url = await db(ConsentService().start)(
            org_id=_uuid.UUID(str(body.get("org_id", ""))), name=str(body.get("name", "")),
            by=request.user.get_username(), tenant_hint=str(body.get("tenant_hint", "")).strip())
        return AsyncResponse({"url": url})


class ConsentCallbackView(AsyncAPIView):
    """GET : retour de Microsoft après le consentement (navigateur, sans jeton JWT : la sécurité repose sur `state`,
    signé, limité à 15 min et à usage unique). Redirige toujours vers le frontend avec le résultat."""
    authentication_required = False

    async def get(self, request):
        return HttpResponseRedirect(await ConsentService().complete_async(request.GET))
