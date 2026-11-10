from django.urls import path

from module.accounts.interface import views as v

urlpatterns = [
    path('auth/login/', v.LoginView.as_view()),
    path('auth/token/', v.LoginView.as_view()),          # ancienne adresse : même contrôle (MFA compris)
    path('auth/mfa/verify/', v.MfaVerifyView.as_view()),
    path('accounts/me/', v.MeView.as_view()),
    path('accounts/me/mfa/', v.MfaSetupView.as_view()),
    path('accounts/users/', v.UsersView.as_view()),
    path('accounts/users/<int:user_id>/', v.UserDetailView.as_view()),
]
