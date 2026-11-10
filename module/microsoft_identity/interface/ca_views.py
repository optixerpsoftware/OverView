"""Vues async : accès conditionnel et Security Defaults."""
from module.microsoft_identity.application.conditional_access_service import (
    CaConfirmationRequired, CaConnectorUnavailable, CaRefused, ConditionalAccessService,
)
from shared_kernel.aio import db
from shared_kernel.async_api import AsyncAPIView, Response, graph_error_response, status


class _CaView(AsyncAPIView):
    def handle_exception(self, exc):
        if isinstance(exc, CaConnectorUnavailable):
            return Response({'detail': 'Connecteur introuvable ou inactif.'}, status.HTTP_404_NOT_FOUND)
        if isinstance(exc, (CaConfirmationRequired, ValueError)):
            return Response({'detail': str(exc)}, status.HTTP_400_BAD_REQUEST)
        if isinstance(exc, CaRefused):
            return Response({'detail': str(exc)}, status.HTTP_409_CONFLICT)
        if isinstance(exc, AsyncAPIView._internal_errors()):
            return super().handle_exception(exc)
        return graph_error_response(exc)


class ConditionalAccessView(_CaView):
    """GET : Security Defaults, licence, politiques lisibles, base recommandée, verdict."""

    async def get(self, request, connector_id):
        data = await ConditionalAccessService().overview_async(connector_id)
        data['history'] = await db(ConditionalAccessService.history)(connector_id)
        return Response(data)


class SecurityDefaultsView(_CaView):
    """PUT {"enabled": bool, "confirm": "ACTIVER"|"DESACTIVER"} (journalisé ; désactivation refusée sans MFA de remplacement)."""

    async def put(self, request, connector_id):
        body = request.data if isinstance(request.data, dict) else {}
        return Response(await ConditionalAccessService().set_security_defaults_async(
            connector_id, bool(body.get('enabled')), confirm=str(body.get('confirm', '')), by=request.user.get_username()))


class CaTemplateView(_CaView):
    """POST {"template": "mfa_all", "confirm": "CREER", "exclude_user_ids": [...]} : politique en mode rapport seulement."""

    async def post(self, request, connector_id):
        body = request.data if isinstance(request.data, dict) else {}
        return Response(await ConditionalAccessService().create_from_template_async(
            connector_id, str(body.get('template', '')), confirm=str(body.get('confirm', '')),
            by=request.user.get_username(), exclude_user_ids=list(body.get('exclude_user_ids') or [])),
            status.HTTP_201_CREATED)
