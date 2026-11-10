from celery import shared_task

from module.insights.application.alert_service import AlertService


@shared_task
def evaluate_all_alerts() -> dict:
    """Toutes les 10 min, après les synchronisations : nouvelles alertes, alertes résolues, notifications."""
    return AlertService().evaluate_all()
