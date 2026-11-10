from django.conf import settings
from django.db import models
from encrypted_fields.fields import EncryptedCharField


class OperatorProfile(models.Model):
    """Rôle et MFA d'un compte OverView. La clé TOTP est chiffrée en base."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="overview_profile")
    role = models.CharField(max_length=10, default="viewer")
    totp_secret = EncryptedCharField(max_length=64, blank=True, null=True)
    totp_enabled = models.BooleanField(default=False)
    totp_last_counter = models.BigIntegerField(null=True, blank=True)   # anti-rejeu : dernier code accepté
    failed_mfa = models.IntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    create_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'accounts_operator_profile'
