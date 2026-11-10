from django.db import models
from encrypted_fields.fields import EncryptedCharField

from shared_kernel.infrastructure.base import BaseModel


class MicrosoftConnector(BaseModel):
    """Connexion M365 (une app Azure / un tenant) d'une organisation.

    `org_id` (hérité de BaseModel) référence Organization.id.
    Une organisation peut avoir plusieurs connecteurs.
    """
    name = models.CharField(max_length=255)
    tenant_id = models.UUIDField()
    client_id = models.UUIDField()
    client_secret = EncryptedCharField(max_length=512)
    is_active = models.BooleanField(default=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = 'microsoft_connector'
        constraints = [
            models.UniqueConstraint(
                fields=['org_id', 'tenant_id', 'client_id'],
                name='uniq_connector_per_org_app',
            ),
        ]


class ConsentRequest(BaseModel):
    """Demande de connexion par consentement administrateur en cours : l'identifiant sert de jeton anti-rejeu
    (signé dans `state`), utilisable une seule fois et pendant 15 minutes."""
    name = models.CharField(max_length=255, blank=True, default="")
    created_by = models.CharField(max_length=150)
    used_at = models.DateTimeField(null=True, blank=True)
    connector_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = 'microsoft_connector_consent'
