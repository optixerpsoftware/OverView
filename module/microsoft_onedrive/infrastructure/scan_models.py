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
    progress = models.JSONField(default=dict)    # avancement pendant l'analyse (espaces traités / total)

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



class DriveDirectory(BaseModel):
    """Liste mémorisée des espaces (OneDrive + bibliothèques SharePoint) d'un tenant : l'explorateur l'affiche tout de
    suite au lieu d'interroger Microsoft (plusieurs secondes) à chaque ouverture. Rafraîchie par l'audit quotidien,
    par le bouton « Actualiser », ou si elle a plus de 12 heures."""
    connector_id = models.UUIDField(unique=True)
    drives = models.JSONField(default=list)
    fetched_at = models.DateTimeField()

    class Meta:
        db_table = 'onedrive_drive_directory'
