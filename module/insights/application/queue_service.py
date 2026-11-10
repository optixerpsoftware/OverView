"""File de travail « À faire » : alertes actives de tous les clients, avec leurs actions de résolution sur place."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from django.db.models import Q
from django.utils import timezone

from module.insights.domain.alerts import SEVERITY_RANK
from module.insights.domain.queue import quick_actions
from module.insights.infrastructure import readers
from module.insights.infrastructure.models import Alert
from shared_kernel.scope import scope_filter

SNOOZE_HOURS = (1, 4, 24, 72, 168)


class QueueService:
    @staticmethod
    def items(user: str, *, scope: str = "all", org_id=None, include_snoozed: bool = False) -> list[dict]:
        from module.insights.application.alert_service import _dict
        now = timezone.now()
        qs = scope_filter(Alert.objects.filter(status__in=("open", "acknowledged")))
        if not include_snoozed:
            qs = qs.filter(Q(snoozed_until__isnull=True) | Q(snoozed_until__lte=now))
        if scope == "mine":
            qs = qs.filter(assigned_to=user)
        elif scope == "unassigned":
            qs = qs.filter(assigned_to="")
        if org_id:
            qs = qs.filter(org_id=org_id)
        orgs = {o["id"]: o["name"] for o in readers.organizations()}
        conns = {c["id"]: c["name"] for c in readers.connectors()}
        rows = []
        for a in qs[:1000]:
            d = _dict(a)
            d.update(org_name=orgs.get(a.org_id, ""), connector_name=conns.get(a.connector_id, ""), actions=quick_actions(d))
            rows.append(d)
        # Nouvelles d'abord, puis par gravité, puis les plus anciennes (elles attendent depuis le plus longtemps).
        rows.sort(key=lambda r: (r["status"] != "open", SEVERITY_RANK.get(r["severity"], 9), r["first_seen"]))
        return rows

    @staticmethod
    def stats(user: str) -> dict:
        now = timezone.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        active = scope_filter(Alert.objects.filter(status__in=("open", "acknowledged")))
        visible = active.filter(Q(snoozed_until__isnull=True) | Q(snoozed_until__lte=now))
        return {
            "todo": visible.count(),
            "new": visible.filter(status="open").count(),
            "critical": visible.filter(severity="critical").count(),
            "mine": visible.filter(assigned_to=user).count(),
            "unassigned": visible.filter(assigned_to="").count(),
            "snoozed": active.filter(snoozed_until__gt=now).count(),
            "handled_today": scope_filter(Alert.objects.filter(Q(resolved_at__gte=start) | Q(acknowledged_at__gte=start))).count(),
        }

    @staticmethod
    def assign(ids: list, to: Optional[str]) -> int:
        return scope_filter(Alert.objects.filter(pk__in=ids).exclude(status="resolved")).update(assigned_to=(to or "")[:150])

    @staticmethod
    def snooze(ids: list, hours: int) -> int:
        if hours not in SNOOZE_HOURS:
            raise ValueError("Durée de report invalide.")
        until = timezone.now() + timedelta(hours=hours)
        return scope_filter(Alert.objects.filter(pk__in=ids).exclude(status="resolved")).update(snoozed_until=until)

    @staticmethod
    def unsnooze(ids: list) -> int:
        return scope_filter(Alert.objects.filter(pk__in=ids)).update(snoozed_until=None)

    @staticmethod
    def set_note(alert_id, text: str, by: str) -> dict:
        a = scope_filter(Alert.objects.filter(pk=alert_id)).first()
        if a is None:
            raise ValueError("Alerte introuvable.")
        stamp = timezone.now().strftime("%Y-%m-%d %H:%M")
        line = (text or "").strip()[:1000]
        if line:
            a.note = (a.note + "\n" if a.note else "") + f"[{stamp} · {by}] {line}"
            a.save(update_fields=["note", "modified_at"])
        return {"id": a.id, "note": a.note}
