from __future__ import annotations

from datetime import timedelta
from typing import Optional

from django.db.models import Max, Q
from django.utils import timezone

from module.microsoft_identity.domain.entities import SignInInfo
from module.microsoft_identity.domain.ports import SecuritySyncRepository
from module.microsoft_identity.infrastructure.sync_models import MailboxSecuritySnapshot, SignInRecord

STALE_RUNNING = timedelta(minutes=15)   # une synchro « en cours » depuis plus longtemps est considérée comme morte


def _snap(s: MailboxSecuritySnapshot) -> dict:
    return {
        "status": s.status, "started_at": s.started_at, "last_synced_at": s.last_synced_at, "last_error": s.last_error,
        "profile": s.profile, "devices": s.devices, "devices_error": s.devices_error,
        "auth_methods": s.auth_methods, "auth_methods_error": s.auth_methods_error, "sign_ins_error": s.sign_ins_error,
    }


def _record_id(s: SignInInfo) -> str:
    return s.id or f"{s.at.isoformat() if s.at else ''}|{s.app}|{s.ip}"


class DjangoSecuritySyncRepository(SecuritySyncRepository):
    def get(self, mailbox_id) -> Optional[dict]:
        s = MailboxSecuritySnapshot.objects.filter(mailbox_id=mailbox_id).first()
        if s and s.status == "running" and s.started_at and timezone.now() - s.started_at > STALE_RUNNING:
            MailboxSecuritySnapshot.objects.filter(pk=s.pk).update(
                status="failed", last_error="Synchronisation interrompue (délai dépassé).")
            s.refresh_from_db()
        return _snap(s) if s else None

    def try_start(self, mailbox_id, org_id=None) -> bool:
        now = timezone.now()
        MailboxSecuritySnapshot.objects.get_or_create(mailbox_id=mailbox_id, defaults={"org_id": org_id})
        # Mise à jour conditionnelle atomique : un seul appelant « gagne », même avec plusieurs workers.
        n = MailboxSecuritySnapshot.objects.filter(mailbox_id=mailbox_id).filter(
            ~Q(status="running") | Q(started_at__lt=now - STALE_RUNNING)).update(status="running", started_at=now)
        return n == 1

    def save_snapshot(self, mailbox_id, *, profile, devices, devices_error, auth_methods, auth_methods_error,
                      sign_ins_error) -> None:
        MailboxSecuritySnapshot.objects.filter(mailbox_id=mailbox_id).update(
            profile=profile, devices=devices, devices_error=devices_error, auth_methods=auth_methods,
            auth_methods_error=auth_methods_error, sign_ins_error=sign_ins_error)

    def finish(self, mailbox_id, error: str = "") -> None:
        if error:
            MailboxSecuritySnapshot.objects.filter(mailbox_id=mailbox_id).update(
                status="failed", last_error=error[:1000])
        else:
            MailboxSecuritySnapshot.objects.filter(mailbox_id=mailbox_id).update(
                status="idle", last_error="", last_synced_at=timezone.now())

    def latest_sign_in_at(self, mailbox_id):
        return SignInRecord.objects.filter(mailbox_id=mailbox_id).aggregate(m=Max("at"))["m"]

    def add_sign_ins(self, mailbox_id, org_id, sign_ins: list[SignInInfo]) -> int:
        rows = [
            SignInRecord(mailbox_id=mailbox_id, org_id=org_id, graph_id=_record_id(s)[:128], at=s.at, data={
                "app": s.app, "ip": s.ip, "city": s.city, "country": s.country, "client": s.client, "os": s.os,
                "browser": s.browser, "interactive": s.interactive, "success": s.success, "error_code": s.error_code,
            })
            for s in sign_ins if s.at is not None
        ]
        before = SignInRecord.objects.filter(mailbox_id=mailbox_id).count()
        SignInRecord.objects.bulk_create(rows, ignore_conflicts=True)     # les doublons (chevauchement) sont ignorés
        return SignInRecord.objects.filter(mailbox_id=mailbox_id).count() - before

    def list_sign_ins(self, mailbox_id, limit: int = 100) -> list[SignInInfo]:
        return [SignInInfo(at=r.at, id=r.graph_id, **r.data)
                for r in SignInRecord.objects.filter(mailbox_id=mailbox_id)[:limit]]

    def prune_sign_ins(self, mailbox_id, before) -> int:
        return SignInRecord.objects.filter(mailbox_id=mailbox_id, at__lt=before).delete()[0]
