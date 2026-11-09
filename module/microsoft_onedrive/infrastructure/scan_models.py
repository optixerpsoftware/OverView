from django.db import models

from shared_kernel.infrastructure.base import BaseModel


class SharingScan(BaseModel):
    """Audit des liens de partage d'un tenant. `org_id` = Organization.id."""
    STATUS = [("running", "running"), ("done", "done"), ("failed", "failed")]

    connector_id = models.UUIDField(db_index=True)
    status = models.CharField(max_length=10, choices=STATUS, default="running")
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    summary = models.JSONField(default=dict)
    records = models.JSONField(default=list)
    truncated = models.JSONField(default=list)   # drives dont le parcours a été limité
    errors = models.JSONField(default=list)      # drives illisibles

    class Meta:
        db_table = 'onedrive_sharing_scan'
        ordering = ['-create_at']


class SharingAction(BaseModel):
    """Journal des révocations de partage (qui a supprimé quoi)."""
    connector_id = models.UUIDField(db_index=True)
    drive_id = models.CharField(max_length=255)
    item_id = models.CharField(max_length=255)
    permission_id = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    ok = models.BooleanField()
    detail = models.TextField(blank=True, default="")
    performed_by = models.CharField(max_length=150)

    class Meta:
        db_table = 'onedrive_sharing_action'
        ordering = ['-create_at']
