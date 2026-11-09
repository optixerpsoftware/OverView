from django.db import models

from shared_kernel.infrastructure.base import BaseModel


class Mailbox(BaseModel):
    """Boîte aux lettres surveillée. `org_id` = Organization.id."""
    connector_id = models.UUIDField()  # MicrosoftConnector.id
    user_principal_name = models.CharField(max_length=255)
    graph_user_id = models.CharField(max_length=36, blank=True, default="")
    is_active = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'outlook_mailbox'
        constraints = [
            models.UniqueConstraint(
                fields=['connector_id', 'user_principal_name'],
                name='uniq_mailbox_per_connector',
            ),
        ]


class EmailQueue(BaseModel):
    email = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'outlook_email_queue'


class OutlookRules(BaseModel):
    mailbox = models.ForeignKey(
        Mailbox, on_delete=models.CASCADE, related_name='rules',
    )
    rule_id = models.CharField(max_length=36)
    display_name = models.CharField(max_length=100)
    sequence = models.IntegerField()
    is_enabled = models.BooleanField()
    has_error = models.BooleanField()
    is_read_only = models.BooleanField()
    conditions = models.JSONField(blank=True, null=True)
    actions = models.JSONField(blank=True, null=True)

    class Meta:
        db_table = 'outlook_rules'
        constraints = [
            models.UniqueConstraint(
                fields=['mailbox', 'rule_id'], name='uniq_rule_per_mailbox',
            ),
        ]


class QuarantineAction(BaseModel):
    """Journal des libérations de messages en quarantaine (qui, quoi, résultat)."""
    connector_id = models.UUIDField(db_index=True)
    identity = models.CharField(max_length=100)
    subject = models.CharField(max_length=300, blank=True, default="")
    quarantine_type = models.CharField(max_length=30, blank=True, default="")
    action = models.CharField(max_length=30)
    ok = models.BooleanField()
    detail = models.TextField(blank=True, default="")
    performed_by = models.CharField(max_length=150)

    class Meta:
        db_table = 'outlook_quarantine_action'
        ordering = ['-create_at']
