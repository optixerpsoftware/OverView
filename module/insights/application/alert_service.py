"""Évaluation des alertes (après chaque synchronisation), cycle de vie et notifications."""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from asgiref.sync import async_to_sync
from django.utils import timezone as djtz

from module.insights.domain import alerts as rules
from module.insights.domain.alerts import AlertCandidate, at_least
from module.insights.infrastructure import notifier, readers
from module.insights.infrastructure.models import Alert, NotificationSetting
from shared_kernel.aio import db
from shared_kernel.realtime import apublish

log = logging.getLogger(__name__)

# Catégories « état » : l'alerte se clôt d'elle-même quand la situation disparaît (lien révoqué, MFA ajoutée…).
# Les catégories « événement » (connexion, quarantaine) restent ouvertes jusqu'à ce qu'un humain les traite.
AUTO_RESOLVE = {"sharing", "rule", "identity", "governance", "app"}
ACTIVE = ("open", "acknowledged")


class AlertNotFound(Exception):
    pass


def _default_quarantine(connector) -> Callable:
    """Lecture de la quarantaine via Exchange Online (facultative : ignorée si l'accès n'est pas configuré)."""
    async def read() -> list[dict]:
        from module.microsoft_outlook.application.mailflow_service import MailflowService
        res = await MailflowService().quarantine_async(connector["id"], days=2)
        return [{"identity": m.identity, "type": m.type, "subject": m.subject, "sender": m.sender,
                 "recipients": list(m.recipients), "released": m.released} for m in res["messages"]]
    return read


class AlertService:
    def __init__(self, quarantine_reader: Optional[Callable] = None, now: Optional[Callable[[], datetime]] = None,
                 notify: bool = True):
        self._quarantine = quarantine_reader or _default_quarantine
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._notify = notify

    # ------------------------------------------------------------------ évaluation
    def _collect_local(self, connector_id: uuid.UUID) -> tuple[list[AlertCandidate], set[str]]:
        cid = str(connector_id)
        out: list[AlertCandidate] = []
        scopes: set[str] = set()
        share = readers.latest_sharing(connector_id)
        if share:
            out += rules.sharing_alerts(cid, share["records"])
            scopes.add("sharing")
        out += rules.rule_alerts(readers.rules(connector_id), readers.internal_domains(connector_id))
        scopes.add("rule")
        boxes = [m for m in readers.mailboxes(connector_id) if m["is_active"]]
        snaps = readers.security([m["id"] for m in boxes])
        now = self._now()
        for m in boxes:
            s = snaps.get(m["id"])
            if s and s["last_synced_at"]:
                out += rules.identity_alerts(m, s["auth_methods"] or [], s["profile"])
            history = readers.sign_ins(m["id"], now - timedelta(days=30))
            out += rules.signin_alerts(m, history, now)
            out += rules.mfa_change_alerts(m, readers.mfa_added_since(m["id"], now - timedelta(days=7)), history, now)
        scopes |= {"identity", "signin"}
        hyg = readers.latest_hygiene(connector_id)
        if hyg:
            out += rules.hygiene_alerts(cid, hyg)
            scopes.add("app")
        comp = readers.latest_compliance(connector_id)
        if comp:
            out += rules.governance_alerts(cid, comp["findings"])
            scopes.add("governance")
        return out, scopes

    async def evaluate_async(self, connector_id: uuid.UUID) -> dict:
        connector = await db(readers.connector)(connector_id)
        if connector is None or not connector["is_active"]:
            return {"created": 0, "resolved": 0}
        candidates, scopes = await db(self._collect_local)(connector_id)
        try:
            msgs = await self._quarantine(connector)()
            candidates += rules.quarantine_alerts(str(connector_id), msgs)
            scopes.add("quarantine")
        except Exception as e:  # noqa: BLE001 : Exchange non configuré ou indisponible → catégorie non évaluée
            log.info("Quarantaine non évaluée pour %s : %s", connector_id, e)
        created, resolved = await db(self._apply)(connector, candidates, scopes)
        if created and self._notify:
            await self._send(connector, created)
        if created or resolved:
            await apublish("alerts.updated", {"connector_id": str(connector_id)})
        return {"created": len(created), "resolved": resolved}

    def evaluate(self, connector_id: uuid.UUID) -> dict:
        return async_to_sync(self.evaluate_async)(connector_id)

    def _apply(self, connector: dict, candidates: list[AlertCandidate], scopes: set[str]) -> tuple[list[dict], int]:
        now = djtz.now()
        active = {a.key: a for a in Alert.objects.filter(connector_id=connector["id"], status__in=ACTIVE,
                                                         category__in=scopes)}
        new, gone = rules.reconcile(set(active), candidates)
        by_key = {c.key: c for c in candidates}
        for key, a in active.items():
            c = by_key.get(key)
            if c:
                Alert.objects.filter(pk=a.pk).update(last_seen=now, title=c.title[:300], detail=c.detail,
                                                     severity=c.severity, data=c.data)
        to_resolve = [active[k].pk for k in gone if active[k].category in AUTO_RESOLVE]
        resolved = Alert.objects.filter(pk__in=to_resolve).update(status="resolved", resolved_at=now)
        created = []
        for c in new:
            a = Alert.objects.create(
                connector_id=connector["id"], org_id=connector["org_id"], key=c.key[:400], category=c.category,
                severity=c.severity, title=c.title[:300], detail=c.detail, link=c.link[:300], data=c.data)
            created.append(_dict(a))
        return created, resolved

    async def _send(self, connector: dict, created: list[dict]) -> None:
        setting, org_name = await db(self._setting)(connector["org_id"])
        if not setting or not setting.enabled:
            return
        due = [a for a in created if at_least(a["severity"], setting.min_severity)]
        if not due:
            return
        base = os.environ.get("APP_PUBLIC_URL", "").rstrip("/")
        due.sort(key=lambda a: rules.SEVERITY_RANK.get(a["severity"], 9))
        sent = False
        if setting.emails:
            try:
                await db(notifier.send_email)(setting.emails, org_name, due, base)
                sent = True
            except Exception:  # noqa: BLE001
                log.warning("Courriel d'alerte non envoyé", exc_info=True)
        if setting.teams_webhook:
            try:
                await db(notifier.send_teams)(setting.teams_webhook, org_name, due, base)
                sent = True
            except Exception:  # noqa: BLE001
                log.warning("Notification Teams non envoyée", exc_info=True)
        if sent:
            await db(lambda: Alert.objects.filter(pk__in=[a["id"] for a in due]).update(notified=True))()

    @staticmethod
    def _setting(org_id):
        from module.organization.infrastructure.models import Organization
        s = NotificationSetting.objects.filter(org_id=org_id).first()
        org = Organization.objects.filter(pk=org_id).values_list("name", flat=True).first() or ""
        return s, org

    def evaluate_all(self) -> dict:
        total = {"created": 0, "resolved": 0, "failed": 0}
        for c in readers.connectors(active_only=True):
            try:
                r = self.evaluate(c["id"])
                total["created"] += r["created"]
                total["resolved"] += r["resolved"]
            except Exception:  # noqa: BLE001 : un tenant en erreur n'empêche pas les autres
                log.exception("Évaluation des alertes impossible pour %s", c["id"])
                total["failed"] += 1
        return total

    # ------------------------------------------------------------------ lecture et traitement
    @staticmethod
    def list(*, status: str = "active", connector_id=None, org_id=None, severity: str = "", category: str = "",
             limit: int = 500) -> list[dict]:
        from shared_kernel.scope import scope_filter
        qs = scope_filter(Alert.objects.all())
        if status == "active":
            qs = qs.filter(status__in=ACTIVE)
        elif status in ("open", "acknowledged", "resolved"):
            qs = qs.filter(status=status)
        if connector_id:
            qs = qs.filter(connector_id=connector_id)
        if org_id:
            qs = qs.filter(org_id=org_id)
        if severity:
            qs = qs.filter(severity=severity)
        if category:
            qs = qs.filter(category=category)
        names = {c["id"]: c["name"] for c in readers.connectors()}
        rows = [_dict(a) | {"connector_name": names.get(a.connector_id, "")} for a in qs[:limit]]
        rows.sort(key=lambda a: (a["status"] != "open", rules.SEVERITY_RANK.get(a["severity"], 9)))
        return rows

    @staticmethod
    def counts(org_id=None) -> dict:
        from shared_kernel.scope import scope_filter
        qs = scope_filter(Alert.objects.filter(status="open"))
        if org_id:
            qs = qs.filter(org_id=org_id)
        out = {s: 0 for s in rules.SEVERITIES}
        for sev in qs.values_list("severity", flat=True):
            out[sev] = out.get(sev, 0) + 1
        out["total"] = sum(out[s] for s in rules.SEVERITIES)
        return out

    @staticmethod
    def set_status(alert_ids: list, status: str, by: str) -> int:
        if status not in ("open", "acknowledged"):
            raise ValueError("Statut invalide.")
        from shared_kernel.scope import scope_filter
        qs = scope_filter(Alert.objects.filter(pk__in=alert_ids).exclude(status="resolved"))
        if status == "acknowledged":
            return qs.update(status=status, acknowledged_by=by[:150], acknowledged_at=djtz.now())
        return qs.update(status=status, acknowledged_by="", acknowledged_at=None)


def _dict(a: Alert) -> dict:
    return {"id": a.id, "connector_id": a.connector_id, "org_id": a.org_id, "key": a.key, "category": a.category,
            "severity": a.severity, "title": a.title, "detail": a.detail, "link": a.link, "data": a.data,
            "status": a.status, "first_seen": a.first_seen, "last_seen": a.last_seen, "resolved_at": a.resolved_at,
            "acknowledged_by": a.acknowledged_by, "notified": a.notified, "acknowledged_at": a.acknowledged_at,
            "assigned_to": a.assigned_to, "snoozed_until": a.snoozed_until, "note": a.note}
