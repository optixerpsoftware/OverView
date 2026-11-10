"""Adresses du schéma public (plateforme) : console des super-administrateurs, retour du consentement Microsoft."""
from django.urls import path

from module.microsoft_connector.interface.views import ConsentCallbackView
from module.platform.interface import views as platform

urlpatterns = [
    path('api/platform/info/', platform.PlatformInfoView.as_view()),
    # Console de la plateforme (super-administrateurs, MFA obligatoire)
    path('api/platform/auth/login/', platform.ConsoleLoginView.as_view()),
    path('api/platform/auth/verify/', platform.ConsoleVerifyView.as_view()),
    path('api/auth/token/refresh/', __import__('rest_framework_simplejwt.views', fromlist=['TokenRefreshView']).TokenRefreshView.as_view()),
    path('api/platform/me/', platform.ConsoleMeView.as_view()),
    path('api/platform/overview/', platform.ConsoleOverviewView.as_view()),
    path('api/platform/msps/', platform.ConsoleMspsView.as_view()),
    path('api/platform/msps/<int:msp_id>/', platform.ConsoleMspView.as_view()),
    path('api/platform/msps/<int:msp_id>/domains/', platform.ConsoleDomainsView.as_view()),
    path('api/platform/msps/<int:msp_id>/rescue-admin/', platform.ConsoleRescueAdminView.as_view()),
    path('api/platform/admins/', platform.ConsoleAdminsView.as_view()),
    path('api/platform/admins/<int:user_id>/', platform.ConsoleAdminView.as_view()),
    path('api/platform/admins/<int:user_id>/reset-mfa/', platform.ConsoleAdminMfaView.as_view()),
    path('api/platform/msps/<int:msp_id>/users/<int:user_id>/reset-mfa/', platform.ConsoleMspUserMfaView.as_view()),
    # Le consentement Microsoft revient sur UNE adresse fixe (déclarée dans Azure) : le MSP est dans `state` (signé).
    path('api/connectors/consent/callback/', ConsentCallbackView.as_view()),
]
