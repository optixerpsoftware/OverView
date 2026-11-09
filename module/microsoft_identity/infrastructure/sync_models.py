from django.db import models

from shared_kernel.infrastructure.base import BaseModel


class MailboxSecuritySnapshot(BaseModel):
    """Dernier état connu de la sécurité d'un compte (profil, appareils, méthodes MFA), mis à jour par la
    synchronisation en arrière-plan : la page lit cet instantané au lieu d'interroger Microsoft en direct."""
    STATUS = [("idle", "idle"), ("running", "running"), ("failed", "failed")]

    mailbox_id = models.UUIDField(unique=True)
    status = models.CharField(max_length=10, choices=STATUS, default="idle")
    started_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    profile = models.JSONField(null=True, blank=True)
    devices = models.JSONField(default=list)
    devices_error = models.TextField(blank=True, default="")
    auth_methods = models.JSONField(default=list)
    auth_methods_error = models.TextField(blank=True, default="")
    sign_ins_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = 'identity_security_snapshot'


class SignInRecord(BaseModel):
    """Connexion d'un compte conservée localement : la synchro n'ajoute que les nouvelles (curseur = la plus récente)."""
    mailbox_id = models.UUIDField(db_index=True)
    graph_id = models.CharField(max_length=128)
    at = models.DateTimeField(db_index=True)
    data = models.JSONField()

    class Meta:
        db_table = 'identity_sign_in'
        ordering = ['-at']
        constraints = [models.UniqueConstraint(fields=['mailbox_id', 'graph_id'], name='uniq_signin_per_mailbox')]
