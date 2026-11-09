from __future__ import annotations

import uuid
from typing import Optional

from module.microsoft_compliance.domain.ports import SnapshotRepository
from module.microsoft_compliance.infrastructure.snapshot_models import ComplianceSnapshot


def _dict(s: ComplianceSnapshot) -> dict:
    return {"id": s.id, "connector_id": s.connector_id, "created_at": s.create_at,
            "score": s.score, "findings": s.findings, "licenses": s.licenses}


class DjangoSnapshotRepository(SnapshotRepository):
    def add(self, *, connector_id, org_id, score, findings, licenses) -> dict:
        return _dict(ComplianceSnapshot.objects.create(
            connector_id=connector_id, org_id=org_id, score=score, findings=findings, licenses=licenses))

    def latest(self, connector_id: uuid.UUID) -> Optional[dict]:
        s = ComplianceSnapshot.objects.filter(connector_id=connector_id).first()
        return _dict(s) if s else None

    def history(self, connector_id: uuid.UUID, limit: int = 60) -> list[dict]:
        rows = ComplianceSnapshot.objects.filter(connector_id=connector_id).values(
            "id", "create_at", "score")[:limit]
        return [{"id": r["id"], "created_at": r["create_at"], "score": r["score"]} for r in reversed(list(rows))]
