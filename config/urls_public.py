"""Adresses du schéma public (plateforme) : inscription d'un MSP, retour du consentement Microsoft."""
from django.urls import path

from module.microsoft_connector.interface.views import ConsentCallbackView
from module.platform.interface import views as platform

urlpatterns = [
    path('api/platform/info/', platform.PlatformInfoView.as_view()),
    path('api/platform/signup/', platform.SignupView.as_view()),
    # Le consentement Microsoft revient sur UNE adresse fixe (déclarée dans Azure) : le MSP est dans `state` (signé).
    path('api/connectors/consent/callback/', ConsentCallbackView.as_view()),
]
