from celery import shared_task

from module.microsoft_compliance.application.services import ComplianceService


@shared_task
def scan_tenant(connector_id: str) -> float | None:
    return ComplianceService().scan(connector_id)["score"]


@shared_task
def scan_all_tenants() -> dict:
    return ComplianceService().scan_all()
