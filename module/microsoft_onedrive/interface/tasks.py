from shared_kernel.tenancy import for_each_tenant
from celery import shared_task

from module.microsoft_onedrive.application.services import SharingService


@shared_task
def run_sharing_scan(scan_id: str, connector_id: str) -> dict:
    scan = SharingService().run_scan(scan_id, connector_id)
    return scan["summary"]


@shared_task
def scan_all_sharing() -> dict:
    return for_each_tenant(lambda: SharingService().scan_all())
