"""Authentification JWT liée au MSP : un jeton émis pour un MSP est refusé chez un autre."""
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

from shared_kernel.tenancy import TENANT_CLAIM, current_schema


class TenantJWTAuthentication(JWTAuthentication):
    """Les identifiants d'utilisateurs se recoupent d'un schéma à l'autre : sans ce contrôle, l'utilisateur n° 1 d'un
    MSP pourrait être pris pour l'utilisateur n° 1 d'un autre."""

    def get_validated_token(self, raw_token):
        token = super().get_validated_token(raw_token)
        if token.get(TENANT_CLAIM) != current_schema():
            raise InvalidToken({"detail": "Jeton émis pour un autre espace OverView.", "code": "token_not_valid"})
        return token
