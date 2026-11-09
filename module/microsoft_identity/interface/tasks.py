from celery import shared_task

from module.microsoft_identity.application.security_sync import SecuritySyncService


@shared_task
def sync_mailbox_security(mailbox_id: str) -> dict:
    """Synchronise un compte (profil, appareils, MFA, connexions incrémentales). Le verrou « en cours »
    est déjà pris par la demande de synchro ; en cas d'erreur il est libéré avec le message."""
    return SecuritySyncService().run_sync(mailbox_id)


@shared_task
def sync_all_mailboxes_security() -> dict:
    """Planifié toutes les 10 minutes : met en file la synchro de chaque compte actif."""
    return SecuritySyncService().sync_all()
