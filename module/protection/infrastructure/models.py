from django.db import models

from shared_kernel.infrastructure.base import BaseModel


class ProtectionPolicy(BaseModel):
    """Limites de fréquence d'une organisation (`org_id`) et réponse automatique."""
    mode = models.CharField(max_length=10, default="monitor")          # off | monitor | enforce
    devices_per_user_per_day = models.IntegerField(default=3)
    devices_per_org_per_day = models.IntegerField(default=20)
    failed_signins_per_user_per_hour = models.IntegerField(default=20)
    disable_on_device_limit = models.BooleanField(default=True)
    disable_on_signin_limit = models.BooleanField(default=False)
    emails_per_user_window = models.IntegerField(default=200)
    email_window_minutes = models.IntegerField(default=60)
    disable_on_email_limit = models.BooleanField(default=True)
    neutralize_rules = models.BooleanField(default=True)
    neutralize_forwarding = models.BooleanField(default=True)
    block_risky_consents = models.BooleanField(default=True)
    exclusions = models.JSONField(default=list)
    updated_by = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        db_table = 'protection_policy'
        constraints = [models.UniqueConstraint(fields=['org_id'], name='uniq_protection_per_org')]


class ProtectionEvent(BaseModel):
    """Déclenchement d'une limite : ce qui a été constaté et ce qui a été fait (alerte, désactivation…)."""
    ACTIONS = [("alerted", "alerted"), ("disabled", "disabled"), ("disable_failed", "disable_failed"),
               ("blocked", "blocked"), ("neutralized", "neutralized"), ("neutralize_failed", "neutralize_failed")]

    connector_id = models.UUIDField(db_index=True)
    key = models.CharField(max_length=300, unique=True)
    rule = models.CharField(max_length=30)
    upn = models.CharField(max_length=255, blank=True, default="")
    user_id = models.CharField(max_length=64, blank=True, default="")
    count = models.IntegerField(default=0)
    limit = models.IntegerField(default=0)
    detail = models.TextField(blank=True, default="")
    items = models.JSONField(default=list)
    action = models.CharField(max_length=20, choices=ACTIONS)
    action_detail = models.TextField(blank=True, default="")
    reenabled_at = models.DateTimeField(null=True, blank=True)
    reenabled_by = models.CharField(max_length=150, blank=True, default="")
    target = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        db_table = 'protection_event'
        ordering = ['-create_at']



class ProtectionState(BaseModel):
    """Mémoire entre deux évaluations d'un tenant (ex. applications tierces déjà connues)."""
    connector_id = models.UUIDField(unique=True)
    known_apps = models.JSONField(null=True, blank=True)   # None tant que la première lecture n'a pas eu lieu

    class Meta:
        db_table = 'protection_state'
