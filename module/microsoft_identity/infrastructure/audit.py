from module.microsoft_identity.domain.ports import AuditLog
from module.microsoft_identity.infrastructure.audit_models import SecurityAction


class DjangoAuditLog(AuditLog):
    def record(self, *, mailbox_id, action, ok, detail, performed_by, org_id=None):
        SecurityAction.objects.create(
            mailbox_id=mailbox_id, org_id=org_id, action=action, ok=ok,
            detail=(detail or "")[:2000], performed_by=performed_by,
        )

    def recent(self, mailbox_id, limit=20):
        return list(SecurityAction.objects.filter(mailbox_id=mailbox_id).values(
            'id', 'action', 'ok', 'detail', 'performed_by', 'create_at')[:limit])
