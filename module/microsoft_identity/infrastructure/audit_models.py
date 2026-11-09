from django.db import models

from shared_kernel.infrastructure.base import BaseModel


class SecurityAction(BaseModel):
    """Journal des actions de sécurité (jamais de mot de passe ici)."""
    mailbox_id = models.UUIDField(db_index=True)
    action = models.CharField(max_length=50)
    ok = models.BooleanField()
    detail = models.TextField(blank=True, default="")
    performed_by = models.CharField(max_length=150)

    class Meta:
        db_table = 'identity_security_action'
        ordering = ['-create_at']


class TenantSettingChange(BaseModel):
    """Journal des modifications de réglages du tenant (ex. politique d'inscription des appareils)."""
    connector_id = models.UUIDField(db_index=True)
    setting = models.CharField(max_length=60)
    changes = models.JSONField(default=list)      # lignes lisibles « avant → après »
    ok = models.BooleanField()
    detail = models.TextField(blank=True, default="")
    performed_by = models.CharField(max_length=150)

    class Meta:
        db_table = 'identity_tenant_setting_change'
        ordering = ['-create_at']
