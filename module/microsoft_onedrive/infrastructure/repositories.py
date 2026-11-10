from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Optional

from django.utils import timezone

from module.microsoft_onedrive.domain.ports import SharingScanRepository
from module.microsoft_onedrive.domain.sharing import summarize
from module.microsoft_onedrive.infrastructure.scan_models import SharingScan

STALE_AFTER = timedelta(minutes=30)


def _dict(s: SharingScan) -> dict:
    return {
        "id": s.id, "connector_id": s.connector_id, "status": s.status, "created_at": s.create_at,
        "finished_at": s.finished_at, "error": s.error, "summary": s.summary, "records": s.records,
        "truncated": s.truncated, "errors": s.errors, "progress": s.progress,
    }


class DjangoSharingScanRepository(SharingScanRepository):
    def start(self, *, connector_id, org_id) -> dict:
        return _dict(SharingScan.objects.create(connector_id=connector_id, org_id=org_id, status="running"))

    def finish(self, scan_id, *, records, summary, truncated, errors) -> dict:
        SharingScan.objects.filter(pk=scan_id).update(
            status="done", finished_at=timezone.now(), records=records, summary=summary,
            truncated=truncated, errors=errors, error="")
        return _dict(SharingScan.objects.get(pk=scan_id))

    def progress(self, scan_id, progress: dict) -> None:
        SharingScan.objects.filter(pk=scan_id, status="running").update(progress=progress)

    def fail(self, scan_id, error: str) -> None:
        SharingScan.objects.filter(pk=scan_id).update(
            status="failed", finished_at=timezone.now(), error=error[:1000])

    def running(self, connector_id) -> Optional[dict]:
        s = SharingScan.objects.filter(connector_id=connector_id, status="running").first()
        if s and timezone.now() - s.create_at > STALE_AFTER:  # worker mort / redémarré
            self.fail(s.id, "Analyse interrompue (délai dépassé).")
            return None
        return _dict(s) if s else None

    def latest(self, connector_id) -> Optional[dict]:
        self.running(connector_id)  # nettoie une analyse périmée éventuelle
        s = SharingScan.objects.filter(connector_id=connector_id).first()
        return _dict(s) if s else None

    def history(self, connector_id, limit=60) -> list[dict]:
        rows = SharingScan.objects.filter(connector_id=connector_id, status="done").values(
            "id", "create_at", "summary")[:limit]
        return [{"id": r["id"], "created_at": r["create_at"], "summary": r["summary"]} for r in reversed(list(rows))]

    def mark_revoked(self, connector_id, drive_id, item_id, permission_id) -> None:
        s = SharingScan.objects.filter(connector_id=connector_id, status="done").first()
        if not s:
            return
        s.records = [r for r in s.records if not (
            r["drive_id"] == drive_id and r["item_id"] == item_id and r["permission_id"] == permission_id)]
        s.summary = summarize(s.records)
        s.save(update_fields=["records", "summary", "modified_at"])
