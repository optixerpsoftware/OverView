from django.contrib import admin
from django.urls import include, path
from rest_framework_simplejwt.views import TokenRefreshView

urlpatterns = [
    path('admin/', admin.site.urls),
    # Connexion : mot de passe puis code TOTP si le MFA est activé (l'émission directe de jetons SimpleJWT est retirée).
    path('api/', include('module.accounts.interface.urls')),
    path('api/auth/token/refresh/', TokenRefreshView.as_view()),
    path('api/', include('module.organization.interface.urls')),
    path('api/', include('module.microsoft_connector.interface.urls')),
    path('api/', include('module.microsoft_outlook.interface.urls')),
    path('api/', include('module.microsoft_identity.interface.urls')),
    path('api/', include('module.microsoft_compliance.interface.urls')),
    path('api/', include('module.microsoft_onedrive.interface.urls')),
    path('api/', include('module.insights.interface.urls')),
    path('api/', include('module.protection.interface.urls')),
]
