"""Vues OneDrive / SharePoint : async natives (Daphne). Les appels Microsoft Graph sont « awaités » directement."""
from module.microsoft_onedrive.application.services import (
    ConnectorUnavailable, NotRevocable, PermissionNotFound, SharingService,
)
from shared_kernel.aio import db
from shared_kernel.async_api import AsyncAPIView, Response, status
from shared_kernel.graph_rest import DataUnavailable, GraphError


class _SharingView(AsyncAPIView):
    def handle_exception(self, exc):
        if isinstance(exc, ConnectorUnavailable):
            return Response({'detail': 'Connecteur introuvable ou inactif.'}, status.HTTP_404_NOT_FOUND)
        if isinstance(exc, PermissionNotFound):
            return Response({'detail': 'Partage introuvable (déjà supprimé ?).'}, status.HTTP_404_NOT_FOUND)
        if isinstance(exc, NotRevocable):
            return Response({'detail': str(exc)}, status.HTTP_409_CONFLICT)
        if isinstance(exc, ValueError):  # identifiant Graph invalide
            return Response({'detail': str(exc)}, status.HTTP_400_BAD_REQUEST)
        if isinstance(exc, DataUnavailable):
            return Response({'detail': exc.reason, 'requires': exc.requires}, status.HTTP_502_BAD_GATEWAY)
        if isinstance(exc, GraphError):
            return Response({'detail': str(exc)}, status.HTTP_404_NOT_FOUND if exc.status == 404 else status.HTTP_502_BAD_GATEWAY)
        return super().handle_exception(exc)


class DrivesView(_SharingView):
    """GET ?refresh=1 : OneDrive des utilisateurs + bibliothèques SharePoint ({drives, fetched_at, cached})."""

    async def get(self, request, connector_id):
        refresh = request.query_params.get("refresh") == "1"
        return Response(await SharingService().drives_async(connector_id, refresh=refresh))


class ItemsView(_SharingView):
    """GET ?parent=<id> : contenu d'un dossier (racine si `parent` est omis)."""

    async def get(self, request, connector_id, drive_id):
        return Response(await SharingService().children_async(
            connector_id, drive_id, request.query_params.get('parent') or None))


class PermissionsView(_SharingView):
    """GET : partages (liens / accès directs) d'un élément, relus en direct."""

    async def get(self, request, connector_id, drive_id, item_id):
        return Response(await SharingService().permissions_async(connector_id, drive_id, item_id))


class RevokeView(_SharingView):
    """DELETE : supprime un lien ou un partage direct dans Microsoft 365."""

    async def delete(self, request, connector_id, drive_id, item_id, permission_id):
        await SharingService().revoke_async(connector_id, drive_id, item_id, permission_id,
                                            by=request.user.get_username())
        return Response(status=status.HTTP_204_NO_CONTENT)


class LatestScanView(_SharingView):
    """GET : dernier audit (en cours, terminé ou échoué) ; 204 s'il n'y en a jamais eu."""

    async def get(self, request, connector_id):
        scan = await db(SharingService().latest)(connector_id)
        return Response(scan) if scan else Response(status=status.HTTP_204_NO_CONTENT)


class StartScanView(_SharingView):
    """POST : lance l'audit en tâche de fond (202). Si un audit tourne déjà, il est renvoyé tel quel."""

    async def post(self, request, connector_id):
        from module.microsoft_onedrive.interface.tasks import run_sharing_scan

        svc = SharingService()
        scan, created = await db(svc.start_scan)(connector_id)
        if created:
            try:
                await db(run_sharing_scan.delay)(str(scan['id']), str(connector_id))   # mise en file (Redis)
            except Exception as e:  # broker (Redis) indisponible
                await db(svc.scans.fail)(scan['id'], f'File de tâches indisponible : {e}')
                return Response({'detail': 'File de tâches indisponible (Redis).'}, status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(scan, status.HTTP_202_ACCEPTED)


class ScanHistoryView(_SharingView):
    async def get(self, request, connector_id):
        return Response(await db(SharingService().history)(connector_id))
