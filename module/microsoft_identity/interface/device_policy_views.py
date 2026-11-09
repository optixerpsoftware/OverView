from dataclasses import asdict

from module.microsoft_identity.application.device_policy_service import (
    CONFIRM_WORD, ConfirmationRequired, ConnectorUnavailable, DevicePolicyService,
)
from shared_kernel.aio import db
from shared_kernel.async_api import AsyncAPIView, Response, graph_error_response, status


class _PolicyView(AsyncAPIView):
    def handle_exception(self, exc):
        if isinstance(exc, ConnectorUnavailable):
            return Response({'detail': 'Connecteur introuvable ou inactif.'}, status.HTTP_404_NOT_FOUND)
        if isinstance(exc, ConfirmationRequired):
            return Response({'detail': f'Confirmation requise : saisis « {CONFIRM_WORD} ».'}, status.HTTP_400_BAD_REQUEST)
        if isinstance(exc, ValueError):
            return Response({'detail': str(exc)}, status.HTTP_400_BAD_REQUEST)
        if isinstance(exc, AsyncAPIView._internal_errors()):
            return super().handle_exception(exc)
        return graph_error_response(exc)


class DeviceRegistrationView(_PolicyView):
    """GET : réglages d'inscription des appareils du tenant.
    PUT {"changes": {...}, "confirm": "MODIFIER"} : modifie les réglages (journalisé)."""

    async def get(self, request, connector_id):
        settings, groups = await DevicePolicyService().get_with_groups_async(connector_id)
        return Response({**asdict(settings), 'join_groups_detail': groups})

    async def put(self, request, connector_id):
        settings, lines = await DevicePolicyService().update_async(
            connector_id, request.data.get('changes') or {}, by=request.user.get_username(),
            confirm=request.data.get('confirm'))
        return Response({'settings': asdict(settings), 'changes': lines})


class SecurityGroupsView(_PolicyView):
    """GET ?q= : groupes de sécurité du tenant (sélecteur). 502 + `requires` si Group.Read.All manque.
    POST {"name": "...", "member_ids": [...]} : crée un groupe de sécurité (Group.ReadWrite.All)."""

    async def get(self, request, connector_id):
        return Response(await DevicePolicyService().groups_async(connector_id, request.query_params.get('q', '')))

    async def post(self, request, connector_id):
        group = await DevicePolicyService().create_group_async(
            connector_id, request.data.get('name'), request.data.get('member_ids'), by=request.user.get_username())
        return Response(group, status.HTTP_201_CREATED)


class DirectoryUsersView(_PolicyView):
    """GET : comptes membres actifs du tenant (choix des membres d'un groupe)."""

    async def get(self, request, connector_id):
        return Response(await DevicePolicyService().directory_users_async(connector_id))


class DeviceRegistrationHistoryView(_PolicyView):
    async def get(self, request, connector_id):
        return Response(await db(DevicePolicyService().history)(connector_id))
