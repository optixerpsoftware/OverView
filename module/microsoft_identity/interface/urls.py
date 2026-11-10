from django.urls import path

from module.microsoft_identity.interface.views import (
    AddAuthMethodView, DeleteAuthMethodView, DeleteDeviceView, EmergencyView, QuarantineView, ResetPasswordView, RevokeSessionsView, SecurityActivityView,
    SecurityOverviewView, SecuritySyncView,
)

from module.microsoft_identity.interface.hygiene_views import (
    HygieneAccountsView, HygieneAppTrafficView, HygieneAppView, HygieneView,
)
from module.microsoft_identity.interface.ca_views import CaTemplateView, ConditionalAccessView, SecurityDefaultsView
from module.microsoft_identity.interface.device_policy_views import (
    DeviceRegistrationHistoryView, DeviceRegistrationView, DirectoryUsersView, SecurityGroupsView,
)

urlpatterns = [
    path('identity/connectors/<uuid:connector_id>/groups/', SecurityGroupsView.as_view()),
    path('identity/connectors/<uuid:connector_id>/directory-users/', DirectoryUsersView.as_view()),
    path('identity/connectors/<uuid:connector_id>/hygiene/', HygieneView.as_view()),
    path('identity/connectors/<uuid:connector_id>/hygiene/accounts/', HygieneAccountsView.as_view()),
    path('identity/connectors/<uuid:connector_id>/hygiene/apps/<str:sp_id>/', HygieneAppView.as_view()),
    path('identity/connectors/<uuid:connector_id>/hygiene/apps/<str:sp_id>/traffic/', HygieneAppTrafficView.as_view()),
    path('identity/connectors/<uuid:connector_id>/conditional-access/', ConditionalAccessView.as_view()),
    path('identity/connectors/<uuid:connector_id>/conditional-access/security-defaults/', SecurityDefaultsView.as_view()),
    path('identity/connectors/<uuid:connector_id>/conditional-access/templates/', CaTemplateView.as_view()),
    path('identity/connectors/<uuid:connector_id>/device-registration/', DeviceRegistrationView.as_view()),
    path('identity/connectors/<uuid:connector_id>/device-registration/history/',
         DeviceRegistrationHistoryView.as_view()),
    path('identity/mailboxes/<uuid:mailbox_id>/security/', SecurityOverviewView.as_view()),
    path('identity/mailboxes/<uuid:mailbox_id>/security/activity/', SecurityActivityView.as_view()),
    path('identity/mailboxes/<uuid:mailbox_id>/security/sync/', SecuritySyncView.as_view()),
    path('identity/mailboxes/<uuid:mailbox_id>/revoke-sessions/', RevokeSessionsView.as_view()),
    path('identity/mailboxes/<uuid:mailbox_id>/devices/<str:device_id>/', DeleteDeviceView.as_view()),
    path('identity/mailboxes/<uuid:mailbox_id>/auth-methods/', AddAuthMethodView.as_view()),
    path('identity/mailboxes/<uuid:mailbox_id>/auth-methods/<str:method_id>/', DeleteAuthMethodView.as_view()),
    path('identity/mailboxes/<uuid:mailbox_id>/reset-password/', ResetPasswordView.as_view()),
    path('identity/mailboxes/<uuid:mailbox_id>/emergency/', EmergencyView.as_view()),
    path('identity/mailboxes/<uuid:mailbox_id>/quarantine/', QuarantineView.as_view()),
]
