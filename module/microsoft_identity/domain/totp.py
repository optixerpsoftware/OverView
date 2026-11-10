"""Réexport : le calcul TOTP vit dans le noyau partagé (utilisé aussi pour le MFA des comptes OverView)."""
from shared_kernel.totp import generate_secret, otpauth_uri, totp_code  # noqa: F401
