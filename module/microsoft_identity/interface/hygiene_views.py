"""Vues async : hygiène des identités."""
from module.microsoft_identity.application.hygiene_service import (
    AppNotManaged, HygieneRefused, HygieneService, HygieneUnavailable, app_traffic_async, delete_app_async,
)
from shared_kernel.aio import db
from shared_kernel.async_api import AsyncAPIView, Response, graph_error_response, status


class _View(AsyncAPIView):
    def handle_exception(self, exc):
        if isinstance(exc, HygieneUnavailable):
            msg = str(exc)
            if msg.startswith("Lecture"):
                return Response({'detail': msg}, status.HTTP_502_BAD_GATEWAY)
            return Response({'detail': 'Connecteur introuvable ou inactif.'}, status.HTTP_404_NOT_FOUND)
        if isinstance(exc, AppNotManaged):
            return Response({'detail': str(exc)}, status.HTTP_404_NOT_FOUND)
        if isinstance(exc, HygieneRefused):
            return Response({'detail': str(exc)}, status.HTTP_400_BAD_REQUEST)
        if isinstance(exc, AsyncAPIView._internal_errors()):
            return super().handle_exception(exc)
        return graph_error_response(exc)


class HygieneView(_View):
    """GET : dernière analyse enregistrée (null si aucune) + historique. POST : analyse maintenant."""

    async def get(self, request, connector_id):
        snap = await db(HygieneService.latest)(connector_id)
        hist = await db(HygieneService.history)(connector_id)
        return Response({'snapshot': snap, 'history': hist})

    async def post(self, request, connector_id):
        return Response(await HygieneService().scan_async(connector_id))


class HygieneAccountsView(_View):
    """POST {user_ids: [...], enabled: false, confirm: "DESACTIVER"} : désactive (ou réactive) des comptes signalés."""

    async def post(self, request, connector_id):
        b = request.data if isinstance(request.data, dict) else {}
        return Response(await HygieneService().set_accounts_enabled_async(
            connector_id, [str(i) for i in (b.get('user_ids') or [])], bool(b.get('enabled')),
            confirm=str(b.get('confirm', '')), by=request.user.get_username()))


class HygieneAppView(_View):
    """POST {enabled: false, confirm: "DESACTIVER"} : bloque (ou rétablit) la connexion d'une application tierce."""

    async def delete(self, request, connector_id, sp_id):
        """DELETE {confirm: "SUPPRIMER"} : supprime l'application du tenant (permissions et consentements compris)."""
        b = request.data if isinstance(request.data, dict) else {}
        return Response(await delete_app_async(HygieneService(), connector_id, str(sp_id), confirm=str(b.get('confirm', '')),
                                               by=request.user.get_username()))

    async def post(self, request, connector_id, sp_id):
        b = request.data if isinstance(request.data, dict) else {}
        return Response(await HygieneService().set_app_enabled_async(
            connector_id, str(sp_id), bool(b.get('enabled')), confirm=str(b.get('confirm', '')), by=request.user.get_username()))



class HygieneAppTrafficView(_View):
    """GET ?days=7|30 : trafic d'une application tierce (connexions, utilisateurs, pays, échecs)."""

    async def get(self, request, connector_id, sp_id):
        days = request.query_params.get('days', '30')
        return Response(await app_traffic_async(HygieneService(), connector_id, str(sp_id), int(days) if days.isdigit() else 30))
