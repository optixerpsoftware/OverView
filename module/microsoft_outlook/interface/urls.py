from django.urls import path
from rest_framework.routers import DefaultRouter

from module.microsoft_outlook.interface.views import (
    AvailableMailboxesView, EmailQueueViewSet, ImportMailboxesView, MailboxViewSet, OutlookRuleViewSet,
    MailTraceView, QuarantineActionsView, QuarantineListView, QuarantineReleaseView, RuleDetailView, SyncRulesView,
)

router = DefaultRouter()
router.register('outlook/mailboxes', MailboxViewSet, basename='outlook-mailbox')
router.register('outlook/rules', OutlookRuleViewSet, basename='outlook-rule')
router.register('outlook/email-queue', EmailQueueViewSet, basename='outlook-email-queue')

# Les vues async (appels Graph) passent AVANT le routeur DRF : même URL que le frontend utilise déjà.
urlpatterns = [
    path('outlook/connectors/<uuid:connector_id>/available-mailboxes/', AvailableMailboxesView.as_view()),
    path('outlook/connectors/<uuid:connector_id>/import-mailboxes/', ImportMailboxesView.as_view()),
    path('outlook/mailboxes/<uuid:mailbox_id>/sync/', SyncRulesView.as_view()),
    path('outlook/rules/<uuid:rule_id>/', RuleDetailView.as_view()),
    path('outlook/connectors/<uuid:connector_id>/mailflow/trace/', MailTraceView.as_view()),
    path('outlook/connectors/<uuid:connector_id>/quarantine/', QuarantineListView.as_view()),
    path('outlook/connectors/<uuid:connector_id>/quarantine/release/', QuarantineReleaseView.as_view()),
    path('outlook/connectors/<uuid:connector_id>/quarantine/actions/', QuarantineActionsView.as_view()),
] + router.urls
