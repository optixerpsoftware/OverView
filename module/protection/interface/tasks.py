from celery import shared_task

from module.protection.application.service import ProtectionService


@shared_task
def evaluate_all_protection() -> dict:
    """Toutes les 10 minutes : limites de fréquence de chaque tenant, réponse automatique selon le mode choisi."""
    return ProtectionService().evaluate_all()
