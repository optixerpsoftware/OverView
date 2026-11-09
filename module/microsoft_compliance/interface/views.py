from module.microsoft_compliance.application.services import ComplianceService, ConnectorUnavailable
from shared_kernel.aio import db
from shared_kernel.async_api import AsyncAPIView, Response, graph_error_response, status


class _ComplianceView(AsyncAPIView):
    def handle_exception(self, exc):
        if isinstance(exc, ConnectorUnavailable):
            return Response({'detail': 'Connecteur introuvable ou inactif.'}, status.HTTP_404_NOT_FOUND)
        return super().handle_exception(exc)


class LatestView(_ComplianceView):
    """GET : dernière analyse du tenant (204 si aucune analyse n'a encore été faite)."""

    async def get(self, request, connector_id):
        snap = await db(ComplianceService().latest)(connector_id)
        return Response(snap) if snap else Response(status=status.HTTP_204_NO_CONTENT)


class HistoryView(_ComplianceView):
    """GET : évolution du score (instantanés quotidiens)."""

    async def get(self, request, connector_id):
        return Response(await db(ComplianceService().history)(connector_id))


class ScanView(_ComplianceView):
    """POST : lance une analyse maintenant et renvoie l'instantané. Les lectures Graph tournent en parallèle
    dans cette même coroutine (pas de thread bloqué pendant les ~5 s d'attente réseau)."""

    async def post(self, request, connector_id):
        try:
            snap = await ComplianceService().scan_async(connector_id)
        except ConnectorUnavailable:
            raise
        except Exception as e:  # authentification Graph échouée, réseau…
            return graph_error_response(e)
        return Response(snap, status.HTTP_201_CREATED)
