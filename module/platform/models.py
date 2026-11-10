from django.conf import settings
from django.db import models
from encrypted_fields.fields import EncryptedCharField
from django_tenants.models import DomainMixin, TenantMixin


class Msp(TenantMixin):
    """Un MSP abonné à OverView : ses organisations clientes, comptes et données vivent dans son propre schéma."""
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=40, unique=True)
    app_url = models.CharField(max_length=300, blank=True, default="")   # ex. https://acme.overview.ca (retours, liens)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Abonnement : forfait, limites (None = illimité), échéance (espace suspendu automatiquement après).
    plan = models.CharField(max_length=20, default="personnalise")
    max_tenants = models.PositiveIntegerField(null=True, blank=True)
    max_organizations = models.PositiveIntegerField(null=True, blank=True)
    max_users = models.PositiveIntegerField(null=True, blank=True)
    expires_at = models.DateField(null=True, blank=True)
    contact_name = models.CharField(max_length=150, blank=True, default="")
    contact_email = models.CharField(max_length=254, blank=True, default="")
    notes = models.TextField(blank=True, default="")

    auto_create_schema = True     # le schéma est créé et migré à l'enregistrement
    auto_drop_schema = False      # jamais de suppression automatique des données d'un MSP

    class Meta:
        db_table = 'platform_msp'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # La création du schéma lance ses migrations en changeant le schéma de la connexion : on suspend, le temps
        # de l'opération, le schéma imposé par la requête en cours (sinon les tables iraient dans le mauvais schéma).
        from shared_kernel.tenancy import connection_controls_schema
        with connection_controls_schema():
            super().save(*args, **kwargs)


class Domain(DomainMixin):
    """Adresse qui mène à un MSP (ex. acme.overview.ca, ou localhost en développement)."""

    class Meta:
        db_table = 'platform_domain'



class PlatformAdmin(models.Model):
    """Super-administrateur de la plateforme (gère les MSP). Compte du schéma public, MFA TOTP toujours exigé."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="platform_admin")
    totp_secret = EncryptedCharField(max_length=64)
    totp_last_counter = models.BigIntegerField(null=True, blank=True)
    failed = models.IntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'platform_admin'


class PlatformSetting(models.Model):
    """Réglages de la plateforme modifiables depuis la console (priment sur les variables d'environnement)."""
    key = models.CharField(max_length=60, unique=True)
    value = models.JSONField(null=True)

    class Meta:
        db_table = 'platform_setting'


class PlatformAudit(models.Model):
    """Journal des actions de la console plateforme."""
    at = models.DateTimeField(auto_now_add=True)
    actor = models.CharField(max_length=150)
    action = models.CharField(max_length=60)
    target = models.CharField(max_length=200, blank=True, default="")
    detail = models.TextField(blank=True, default="")

    class Meta:
        db_table = 'platform_audit'
        ordering = ['-at']
