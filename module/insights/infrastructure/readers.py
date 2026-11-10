"""Lectures transverses dans les données locales des autres modules (aucun appel à Microsoft).

Les modules restent propriétaires de leurs tables ; ici on ne fait que lire, pour bâtir le tableau de bord, les alertes
et la recherche. Fonctions synchrones (ORM) : à appeler via `shared_kernel.aio.db` depuis du code async.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from django.db.models import Q

from shared_kernel.scope import scope_filter

from module.microsoft_compliance.infrastructure.snapshot_models import ComplianceSnapshot
from module.microsoft_connector.infrastructure.models import MicrosoftConnector
from module.microsoft_identity.infrastructure.sync_models import IdentityHygieneSnapshot, MailboxSecuritySnapshot, SignInRecord
from module.microsoft_onedrive.infrastructure.scan_models import SharingScan
from module.microsoft_outlook.infrastructure.models import Mailbox, OutlookRules
from module.organization.infrastructure.models import Organization


def organizations() -> list[dict]:
    return list(scope_filter(Organization.objects.order_by("name"), "id").values("id", "name", "description"))


def connectors(org_id: Optional[uuid.UUID] = None, active_only: bool = False) -> list[dict]:
    qs = scope_filter(MicrosoftConnector.objects.order_by("name"))
    if org_id:
        qs = qs.filter(org_id=org_id)
    if active_only:
        qs = qs.filter(is_active=True)
    return list(qs.values("id", "org_id", "name", "tenant_id", "is_active", "last_error", "last_checked_at"))


def connector(connector_id: uuid.UUID) -> Optional[dict]:
    rows = connectors()
    return next((c for c in rows if c["id"] == connector_id), None)


def mailboxes(connector_id: uuid.UUID) -> list[dict]:
    return [{"id": m["id"], "upn": m["user_principal_name"], "is_active": m["is_active"]}
            for m in Mailbox.objects.filter(connector_id=connector_id).values("id", "user_principal_name", "is_active")]


def internal_domains(connector_id: uuid.UUID) -> set[str]:
    return {m["upn"].rsplit("@", 1)[-1].lower() for m in mailboxes(connector_id) if "@" in m["upn"]}


def rules(connector_id: uuid.UUID) -> list[dict]:
    upn = {m["id"]: m["upn"] for m in mailboxes(connector_id)}
    rows = OutlookRules.objects.filter(mailbox_id__in=list(upn)).values(
        "id", "mailbox_id", "display_name", "is_enabled", "actions")
    return [{**r, "mailbox_upn": upn.get(r["mailbox_id"], "")} for r in rows]


def security(mailbox_ids: list[uuid.UUID]) -> dict:
    return {s["mailbox_id"]: s for s in MailboxSecuritySnapshot.objects.filter(mailbox_id__in=mailbox_ids).values(
        "mailbox_id", "profile", "auth_methods", "last_synced_at", "status")}


def sign_ins(mailbox_id: uuid.UUID, since: datetime) -> list[dict]:
    out = []
    for r in SignInRecord.objects.filter(mailbox_id=mailbox_id, at__gte=since).values("at", "data"):
        d = r["data"] or {}
        out.append({"at": r["at"], "success": bool(d.get("success")), "country": d.get("country") or "",
                    "ip": d.get("ip") or ""})
    return out


def latest_sharing(connector_id: uuid.UUID) -> Optional[dict]:
    return SharingScan.objects.filter(connector_id=connector_id, status="done").values(
        "id", "finished_at", "summary", "records").first()


def latest_sharing_summary(connector_id: uuid.UUID) -> Optional[dict]:
    return SharingScan.objects.filter(connector_id=connector_id, status="done").values("finished_at", "summary").first()


def latest_hygiene(connector_id: uuid.UUID) -> Optional[dict]:
    return IdentityHygieneSnapshot.objects.filter(connector_id=connector_id).values_list("data", flat=True).first()


def latest_compliance(connector_id: uuid.UUID) -> Optional[dict]:
    return ComplianceSnapshot.objects.filter(connector_id=connector_id).values("create_at", "score", "findings").first()


def compliance_scores(connector_id: uuid.UUID, days: int = 30) -> list[float]:
    since = datetime.now().astimezone() - timedelta(days=days)
    return [s for s in ComplianceSnapshot.objects.filter(connector_id=connector_id, create_at__gte=since)
            .order_by("create_at").values_list("score", flat=True) if s is not None]


# ---------------------------------------------------------------- recherche
def search(q: str, limit: int = 8) -> dict:
    orgs = list(scope_filter(Organization.objects.filter(Q(name__icontains=q) | Q(description__icontains=q)), "id")
                .values("id", "name", "description")[:limit])
    conns = list(scope_filter(MicrosoftConnector.objects.filter(name__icontains=q)).values("id", "name", "org_id")[:limit])
    try:
        conns += list(scope_filter(MicrosoftConnector.objects.filter(tenant_id=uuid.UUID(q))).values("id", "name", "org_id")[:1])
    except ValueError:
        pass
    boxes = list(scope_filter(Mailbox.objects.filter(user_principal_name__icontains=q))
                 .values("id", "user_principal_name", "connector_id")[:limit])
    rules_ = list(scope_filter(OutlookRules.objects.filter(display_name__icontains=q), "mailbox__org_id")
                  .values("id", "display_name", "mailbox_id", "mailbox__user_principal_name")[:limit])
    return {"orgs": orgs, "connectors": conns, "mailboxes": boxes, "rules": rules_}


def search_shared(q: str, limit: int = 8) -> list[dict]:
    """Fichiers partagés (dernier audit de chaque tenant) dont le nom, le chemin ou un destinataire correspond."""
    ql, out = q.lower(), []
    permitted = {c["id"] for c in connectors()}
    for scan in SharingScan.objects.filter(status="done").order_by("connector_id", "-create_at").distinct("connector_id") \
            .values("connector_id", "records") if _supports_distinct_on() else _latest_scans():
        if scan["connector_id"] not in permitted:
            continue
        for r in scan["records"]:
            hay = f"{r.get('name', '')} {r.get('path', '')} {' '.join(r.get('grantees') or [])}".lower()
            if ql in hay:
                out.append({**r, "connector_id": scan["connector_id"]})
                if len(out) >= limit:
                    return out
    return out


def _supports_distinct_on() -> bool:
    from django.db import connection
    return connection.vendor == "postgresql"


def _latest_scans() -> list[dict]:
    seen, out = set(), []
    for s in SharingScan.objects.filter(status="done").order_by("-create_at").values("connector_id", "records"):
        if s["connector_id"] not in seen:
            seen.add(s["connector_id"])
            out.append(s)
    return out


# ---------------------------------------------------------------- graphiques et rapport
def alerts_since(since, connector_ids=None) -> list[dict]:
    """Alertes encore actives OU créées / résolues depuis `since` (pour les courbes et le rapport)."""
    from module.insights.infrastructure.models import Alert
    qs = Alert.objects.filter(Q(first_seen__gte=since) | Q(resolved_at__gte=since) | Q(status__in=("open", "acknowledged")))
    if connector_ids is not None:
        qs = qs.filter(connector_id__in=list(connector_ids))
    return list(qs.values("id", "connector_id", "category", "severity", "title", "detail", "status",
                          "first_seen", "resolved_at", "link"))


def compliance_scores_since(connector_id, since) -> list[float]:
    return [s for s in ComplianceSnapshot.objects.filter(connector_id=connector_id, create_at__gte=since)
            .order_by("create_at").values_list("score", flat=True) if s is not None]


def actions_since(connector_id, since) -> list[dict]:
    """Remédiations faites depuis OverView : révocations de partage, libérations, réglages du tenant."""
    from module.microsoft_identity.infrastructure.audit_models import TenantSettingChange
    from module.microsoft_onedrive.infrastructure.scan_models import SharingAction
    from module.microsoft_outlook.infrastructure.models import QuarantineAction
    out = []
    for r in SharingAction.objects.filter(connector_id=connector_id, create_at__gte=since, ok=True).values(
            "create_at", "description", "performed_by"):
        out.append({"at": r["create_at"], "kind": "Partage révoqué", "detail": r["description"], "by": r["performed_by"]})
    for r in QuarantineAction.objects.filter(connector_id=connector_id, create_at__gte=since, ok=True).values(
            "create_at", "subject", "performed_by"):
        out.append({"at": r["create_at"], "kind": "Message libéré", "detail": r["subject"], "by": r["performed_by"]})
    for r in TenantSettingChange.objects.filter(connector_id=connector_id, create_at__gte=since, ok=True).values(
            "create_at", "changes", "performed_by"):
        out.append({"at": r["create_at"], "kind": "Réglage du tenant", "detail": " · ".join(r["changes"] or []),
                    "by": r["performed_by"]})
    return sorted(out, key=lambda x: x["at"], reverse=True)


def mfa_added_since(mailbox_id, since) -> list[dict]:
    from module.microsoft_identity.infrastructure.sync_models import AuthMethodChange
    return list(AuthMethodChange.objects.filter(mailbox_id=mailbox_id, change="added", at__gte=since).values(
        "method_id", "kind", "label", "detail", "at"))
