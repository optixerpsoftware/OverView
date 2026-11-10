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


class IdentityHygieneSnapshot(BaseModel):
    """Dernière analyse d'hygiène des identités d'un tenant (comptes inactifs, invités, admins, applis tierces)."""
    connector_id = models.UUIDField(db_index=True)
    data = models.JSONField(default=dict)
    errors = models.JSONField(default=list)

    class Meta:
        db_table = 'identity_hygiene_snapshot'
        ordering = ['-create_at']


class AuthMethodChange(BaseModel):
    """Méthode d'authentification apparue ou disparue entre deux synchronisations d'un compte suivi."""
    mailbox_id = models.UUIDField(db_index=True)
    method_id = models.CharField(max_length=128)
    kind = models.CharField(max_length=40)
    label = models.CharField(max_length=200, blank=True, default="")
    detail = models.CharField(max_length=300, blank=True, default="")
    change = models.CharField(max_length=10)          # added | removed
    at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'identity_auth_method_change'
        ordering = ['-at']


class AccountQuarantine(BaseModel):
    """Compte mis en quarantaine (réversible) : réglages de protocoles d'origine mémorisés pour la levée."""
    mailbox_id = models.UUIDField(db_index=True)
    started_by = models.CharField(max_length=150)
    reason = models.CharField(max_length=500, blank=True, default="")
    previous_cas = models.JSONField(null=True, blank=True)
    detail = models.TextField(blank=True, default="")
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        db_table = 'identity_account_quarantine'
        ordering = ['-create_at']
