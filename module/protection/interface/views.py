"""Vues async : protection automatique (limites par organisation)."""
from module.protection.application.service import REENABLE_WORD, ProtectionRefused, ProtectionService
from shared_kernel.aio import db
from shared_kernel.async_api import AsyncAPIView, Response, graph_error_response, status


class _View(AsyncAPIView):
    def handle_exception(self, exc):
        if isinstance(exc, (ValueError, ProtectionRefused)):
            return Response({'detail': str(exc)}, status.HTTP_400_BAD_REQUEST)
        if isinstance(exc, AsyncAPIView._internal_errors()):
            return super().handle_exception(exc)
        return graph_error_response(exc)


class PolicyView(_View):
    """GET : réglages + journal. PUT : modifie les limites et le mode (off | monitor | enforce)."""

    async def get(self, request, org_id):
        return Response({'policy': await db(ProtectionService.get_policy)(org_id),
                         'events': await db(ProtectionService.events)(org_id), 'reenable_word': REENABLE_WORD})

    async def put(self, request, org_id):
        body = request.data if isinstance(request.data, dict) else {}
        return Response(await db(ProtectionService.set_policy)(org_id, body, request.user.get_username()))


class EvaluateView(_View):
    """POST : évalue tout de suite les tenants de l'organisation (sinon toutes les 10 minutes)."""

    async def post(self, request, org_id):
        from module.microsoft_connector.infrastructure.models import MicrosoftConnector
        ids = await db(lambda: list(MicrosoftConnector.objects.filter(org_id=org_id, is_active=True).values_list('id', flat=True)))()
        svc = ProtectionService()
        return Response([{'connector_id': i, **(await svc.evaluate_async(i))} for i in ids])


class ReenableView(_View):
    """POST {confirm: "REACTIVER"} : réactive un compte désactivé par la protection."""

    async def post(self, request, event_id):
        body = request.data if isinstance(request.data, dict) else {}
        return Response(await ProtectionService().reenable_async(event_id, confirm=str(body.get('confirm', '')),
                                                                 by=request.user.get_username()))
