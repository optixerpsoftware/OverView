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
