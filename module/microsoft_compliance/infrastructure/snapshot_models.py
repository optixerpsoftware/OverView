from django.db import models

from shared_kernel.infrastructure.base import BaseModel


class ComplianceSnapshot(BaseModel):
    """Instantané d'une analyse de gouvernance d'un tenant (historique). `org_id` = Organization.id."""
    connector_id = models.UUIDField(db_index=True)
    score = models.FloatField(null=True, blank=True)
    findings = models.JSONField(default=list)
    licenses = models.JSONField(default=dict)

    class Meta:
        db_table = 'compliance_snapshot'
        ordering = ['-create_at']
