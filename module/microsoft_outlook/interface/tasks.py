from shared_kernel.tenancy import for_each_tenant
from celery import shared_task

from module.microsoft_outlook.application.services import OutlookService


@shared_task
def sync_mailbox(mailbox_id: str) -> int:
    return OutlookService().sync_rules(mailbox_id)


@shared_task
def sync_all_mailboxes() -> dict:
    return for_each_tenant(lambda: OutlookService().sync_all())
