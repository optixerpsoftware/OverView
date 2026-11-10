"""Vues Outlook.

- CRUD sur la base locale : ViewSets DRF (aucun appel réseau, DRF suffit).
- Endpoints qui appellent Microsoft Graph : vues async natives (Daphne), voir plus bas.
"""
from rest_framework import status, viewsets
from shared_kernel.scope import OrgScopedQuerysetMixin
from rest_framework.response import Response as DrfResponse

from module.microsoft_outlook.application.services import (
    ConnectorUnavailable, MailboxNotFound, OutlookService, RuleNotFound, RuleReadOnly,
)
from module.microsoft_outlook.infrastructure.models import EmailQueue, Mailbox, OutlookRules
from module.microsoft_outlook.interface.serializers import (
    EmailQueueSerializer, MailboxSerializer, OutlookRuleSerializer,
)
from shared_kernel.aio import db
from shared_kernel.async_api import AsyncAPIView, Response, graph_error_response
from shared_kernel.async_api import status as http


class MailboxViewSet(OrgScopedQuerysetMixin, viewsets.ModelViewSet):
    """Lectures via queryset ; écritures métier via OutlookService. (La synchro des règles est une vue async.)"""
    queryset = Mailbox.objects.all().order_by('user_principal_name')
    serializer_class = MailboxSerializer
    filterset_fields = ['org_id', 'connector_id', 'is_active']

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            entity = OutlookService().register_mailbox(**ser.validated_data)
        except ConnectorUnavailable:
            return DrfResponse({'connector_id': ['Connecteur introuvable pour cette organisation.']},
                               status=status.HTTP_400_BAD_REQUEST)
        except ValueError as e:
            return DrfResponse({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        out = self.get_serializer(Mailbox.objects.get(pk=entity.id))
        return DrfResponse(out.data, status=status.HTTP_201_CREATED)


class OutlookRuleViewSet(OrgScopedQuerysetMixin, viewsets.ModelViewSet):
    scope_field = 'mailbox__org_id'
    """Liste/création/modification locales. (La suppression, qui appelle Graph, est une vue async.)"""
    queryset = OutlookRules.objects.all().order_by('mailbox', 'sequence')
    serializer_class = OutlookRuleSerializer
    filterset_fields = ['mailbox', 'is_enabled', 'has_error']


class EmailQueueViewSet(OrgScopedQuerysetMixin, viewsets.ModelViewSet):
    queryset = EmailQueue.objects.all().order_by('-create_at')
    serializer_class = EmailQueueSerializer
    filterset_fields = ['org_id']


# ---------------------------------------------------------------- vues async (appels Graph)
class _OutlookAsyncView(AsyncAPIView):
    def handle_exception(self, exc):
        if isinstance(exc, MailboxNotFound):
            return Response(status=http.HTTP_404_NOT_FOUND)
        if isinstance(exc, RuleNotFound):
            return Response(status=http.HTTP_404_NOT_FOUND)
        if isinstance(exc, RuleReadOnly):
            return Response({'detail': 'Règle en lecture seule : suppression impossible.'}, http.HTTP_409_CONFLICT)
        if isinstance(exc, ConnectorUnavailable):
            return Response({'detail': 'Connecteur introuvable ou inactif.'}, http.HTTP_404_NOT_FOUND)
        if isinstance(exc, AsyncAPIView._internal_errors()):
            return super().handle_exception(exc)
        return graph_error_response(exc)   # permission manquante (ex. MailboxSettings.ReadWrite), réseau…


class SyncRulesView(_OutlookAsyncView):
    """POST : synchronise les règles Outlook de la boîte depuis Microsoft (le worker le fait aussi en tâche de fond)."""

    async def post(self, request, mailbox_id):
        return Response({'rules': await OutlookService().sync_rules_async(mailbox_id)})


class RuleDetailView(_OutlookAsyncView):
    """GET : une règle locale. DELETE : supprime la règle dans Outlook (Graph) puis localement."""

    async def get(self, request, rule_id):
        def load():
            r = OutlookRules.objects.filter(pk=rule_id).first()
            return OutlookRuleSerializer(r).data if r else None

        data = await db(load)()
        return Response(data) if data is not None else Response(status=http.HTTP_404_NOT_FOUND)

    async def delete(self, request, rule_id):
        await OutlookService().delete_rule_async(rule_id)
        return Response(status=http.HTTP_204_NO_CONTENT)


class AvailableMailboxesView(_OutlookAsyncView):
    """GET : boîtes du tenant M365 du connecteur (via Graph), avec « déjà importée »."""

    async def get(self, request, connector_id):
        found = await OutlookService().discover_mailboxes_async(connector_id)
        return Response([
            {'user_principal_name': d.user_principal_name, 'display_name': d.display_name,
             'already_imported': d.already_imported}
            for d in found
        ])


class ImportMailboxesView(_OutlookAsyncView):
    """POST {"user_principal_names": [...]} : importe les boîtes choisies (toutes si omis)."""

    async def post(self, request, connector_id):
        names = request.data.get('user_principal_names')
        if names is not None and not isinstance(names, list):
            return Response({'user_principal_names': ['Liste attendue.']}, http.HTTP_400_BAD_REQUEST)
        res = await OutlookService().import_mailboxes_async(connector_id, names)
        return Response(res, http.HTTP_201_CREATED if res['imported'] else http.HTTP_200_OK)


# ---------------------------------------------------------------- flux de messagerie et quarantaine (Exchange Online)
from module.microsoft_outlook.application.mailflow_service import (  # noqa: E402
    InvalidRequest, MailflowConnectorUnavailable, MailflowService, QuarantineMessageNotFound, ReleaseNotConfirmed,
)
from shared_kernel.async_api import plain  # noqa: E402


class _MailflowView(AsyncAPIView):
    def handle_exception(self, exc):
        if isinstance(exc, MailflowConnectorUnavailable):
            return Response({'detail': 'Connecteur introuvable ou inactif.'}, http.HTTP_404_NOT_FOUND)
        if isinstance(exc, InvalidRequest):
            return Response({'detail': str(exc)}, http.HTTP_400_BAD_REQUEST)
        if isinstance(exc, QuarantineMessageNotFound):
            return Response({'detail': 'Message introuvable en quarantaine.'}, http.HTTP_404_NOT_FOUND)
        if isinstance(exc, ReleaseNotConfirmed):
            return Response({'detail': str(exc), 'needs_word': exc.needs_word}, http.HTTP_409_CONFLICT)
        if isinstance(exc, AsyncAPIView._internal_errors()):
            return super().handle_exception(exc)
        return graph_error_response(exc)


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class MailTraceView(_MailflowView):
    """GET ?hours=24&sender=&recipient=&status= : suivi des messages (Exchange Online) + synthèse."""

    async def get(self, request, connector_id):
        q = request.query_params
        data = await MailflowService().trace_async(
            connector_id, hours=_int(q.get('hours'), 24), sender=q.get('sender', ''),
            recipient=q.get('recipient', ''), status=q.get('status', ''))
        return Response(plain(data))


class QuarantineListView(_MailflowView):
    """GET ?days=7&type= : messages en quarantaine."""

    async def get(self, request, connector_id):
        q = request.query_params
        data = await MailflowService().quarantine_async(connector_id, days=_int(q.get('days'), 7), type_=q.get('type', ''))
        return Response(plain(data))


class QuarantineReleaseView(_MailflowView):
    """POST {identity, confirm, release_to_all?, allow_sender?} : libère UN message (journalisé)."""

    async def post(self, request, connector_id):
        body = request.data if isinstance(request.data, dict) else {}
        data = await MailflowService().release_async(
            connector_id, str(body.get('identity', '')), confirm=str(body.get('confirm', '')),
            performed_by=request.user.get_username(), release_to_all=bool(body.get('release_to_all', True)),
            allow_sender=bool(body.get('allow_sender', False)))
        return Response(data)


class QuarantineActionsView(_MailflowView):
    """GET : journal des libérations."""

    async def get(self, request, connector_id):
        return Response(await MailflowService().actions_async(connector_id))
