from django.contrib import admin
from django.urls import include, path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/token/', TokenObtainPairView.as_view()),
    path('api/auth/token/refresh/', TokenRefreshView.as_view()),
    path('api/', include('module.organization.interface.urls')),
    path('api/', include('module.microsoft_connector.interface.urls')),
    path('api/', include('module.microsoft_outlook.interface.urls')),
    path('api/', include('module.microsoft_identity.interface.urls')),
    path('api/', include('module.microsoft_compliance.interface.urls')),
    path('api/', include('module.microsoft_onedrive.interface.urls')),
]
