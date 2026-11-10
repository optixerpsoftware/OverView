from django.db import models
from encrypted_fields.fields import EncryptedCharField

from shared_kernel.infrastructure.base import BaseModel


class Alert(BaseModel):
    """Alerte de sécurité détectée sur un tenant. `key` identifie la situation (une seule alerte active par clé)."""
    STATUS = [("open", "open"), ("acknowledged", "acknowledged"), ("resolved", "resolved")]

    connector_id = models.UUIDField(db_index=True)
    key = models.CharField(max_length=400, db_index=True)
    category = models.CharField(max_length=20, db_index=True)
    severity = models.CharField(max_length=10, db_index=True)
    title = models.CharField(max_length=300)
    detail = models.TextField(blank=True, default="")
    link = models.CharField(max_length=300, blank=True, default="")
    data = models.JSONField(default=dict)
    status = models.CharField(max_length=15, choices=STATUS, default="open", db_index=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.CharField(max_length=150, blank=True, default="")
    notified = models.BooleanField(default=False)

    class Meta:
        db_table = 'insights_alert'
        ordering = ['-first_seen']
        indexes = [models.Index(fields=['connector_id', 'status'])]


class NotificationSetting(BaseModel):
    """Où envoyer les nouvelles alertes d'une organisation (`org_id`). Le webhook Teams est chiffré en base."""
    emails = models.JSONField(default=list)
    teams_webhook = EncryptedCharField(max_length=1000, blank=True, null=True)
    min_severity = models.CharField(max_length=10, default="high")
    enabled = models.BooleanField(default=True)

    class Meta:
        db_table = 'insights_notification_setting'
        constraints = [models.UniqueConstraint(fields=['org_id'], name='uniq_notification_per_org')]


class BulkActionLog(BaseModel):
    """Journal des actions en lot (qui, quoi, combien, résultat par élément)."""
    action = models.CharField(max_length=40)
    performed_by = models.CharField(max_length=150)
    total = models.IntegerField(default=0)
    succeeded = models.IntegerField(default=0)
    results = models.JSONField(default=list)

    class Meta:
        db_table = 'insights_bulk_action'
        ordering = ['-create_at']
