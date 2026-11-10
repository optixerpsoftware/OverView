"""Tableau de bord global, recherche transverse et actions en lot."""
from __future__ import annotations

import uuid
from typing import Optional

from datetime import datetime, timedelta, timezone

from module.insights.application.alert_service import AlertService
from module.insights.domain import report as rp
from module.insights.infrastructure import readers
from module.insights.infrastructure.models import BulkActionLog
from shared_kernel.aio import db

MAX_BULK = 100
BULK_WORD = {"rules.delete": "SUPPRIMER", "sharing.revoke": "REVOQUER"}


class InvalidBulk(Exception):
    pass


# ---------------------------------------------------------------- tableau de bord
class DashboardService:
    @staticmethod
    def build() -> dict:
        names = {o["id"]: o["name"] for o in readers.organizations()}
        tenants, totals = [], {"tenants": 0, "mailboxes": 0, "no_mfa": 0, "external_shares": 0, "anonymous_links": 0,
                               "anonymous_edit": 0, "suspicious_rules": 0}
        alert_rows = AlertService.list(status="open", limit=5000)
        for c in readers.connectors():
            boxes = [m for m in readers.mailboxes(c["id"]) if m["is_active"]]
            snaps = readers.security([m["id"] for m in boxes])
            no_mfa = sum(1 for s in snaps.values()
                         if s["auth_methods"] and not any(m.get("is_mfa") for m in s["auth_methods"])
                         and (s["profile"] or {}).get("account_enabled", True))
            share = readers.latest_sharing_summary(c["id"]) or {}
            summary = share.get("summary") or {}
            comp = readers.latest_compliance(c["id"]) or {}
            mine = [a for a in alert_rows if a["connector_id"] == c["id"]]
            sev = {s: sum(1 for a in mine if a["severity"] == s) for s in ("critical", "high", "medium", "low")}
            rules_flagged = len({a["data"].get("rule_id") for a in mine if a["category"] == "rule"})
            tenants.append({
                "connector_id": c["id"], "name": c["name"], "org_id": c["org_id"], "org_name": names.get(c["org_id"], ""),
                "is_active": c["is_active"], "connection_error": c["last_error"],
                "score": comp.get("score"), "score_at": comp.get("create_at"),
                "score_trend": readers.compliance_scores(c["id"]),
                "mailboxes": len(boxes), "no_mfa": no_mfa,
                "sharing": {"at": share.get("finished_at"), "external": summary.get("external", 0),
                            "anonymous": summary.get("anonymous", 0), "anonymous_edit": summary.get("anonymous_edit", 0)},
                "suspicious_rules": rules_flagged, "alerts": sev | {"total": len(mine)},
            })
            totals["tenants"] += 1
            totals["mailboxes"] += len(boxes)
            totals["no_mfa"] += no_mfa
            totals["external_shares"] += summary.get("external", 0)
            totals["anonymous_links"] += summary.get("anonymous", 0)
            totals["anonymous_edit"] += summary.get("anonymous_edit", 0)
            totals["suspicious_rules"] += rules_flagged
        # Les tenants qui ont le plus d'alertes graves d'abord.
        tenants.sort(key=lambda t: (-t["alerts"]["critical"], -t["alerts"]["high"], -t["alerts"]["total"], t["name"]))
        top = alert_rows[:8]
        recent = readers.alerts_since(datetime.now(timezone.utc) - timedelta(days=30))
        open_now = [a for a in recent if a["status"] in ("open", "acknowledged")]
        charts = {"trend": rp.alert_trend(recent, datetime.now(timezone.utc).date(), 30), **rp.breakdown(open_now),
                  "mttr_hours": rp.mean_time_to_resolve(a for a in recent if a["resolved_at"])}
        return {"totals": totals, "alerts": AlertService.counts(), "tenants": tenants, "top_alerts": top, "charts": charts}

    async def build_async(self) -> dict:
        return await db(self.build)()


# ---------------------------------------------------------------- recherche globale
class SearchService:
    @staticmethod
    def search(q: str) -> list[dict]:
        q = (q or "").strip()
        if len(q) < 2:
            return []
        r = readers.search(q)
        out: list[dict] = []
        out += [{"type": "org", "label": o["name"], "sub": o["description"] or "Organisation",
                 "href": f"/organizations/{o['id']}"} for o in r["orgs"]]
        out += [{"type": "tenant", "label": c["name"], "sub": "Tenant Microsoft 365",
                 "href": f"/organizations/{c['org_id']}"} for c in {c["id"]: c for c in r["connectors"]}.values()]
        out += [{"type": "mailbox", "label": m["user_principal_name"], "sub": "Sécurité du compte",
                 "href": f"/mailboxes/{m['id']}"} for m in r["mailboxes"]]
        out += [{"type": "rule", "label": x["display_name"], "sub": f"Règle Outlook · {x['mailbox__user_principal_name']}",
                 "href": f"/mailboxes/{x['mailbox_id']}?tab=rules"} for x in r["rules"]]
        out += [{"type": "file", "label": f.get("name", "?"),
                 "sub": f"{f.get('drive_label', '')} · {', '.join(f.get('grantees') or []) or f.get('scope', '')}",
                 "href": f"/sharing/{f['connector_id']}?q={f.get('name', '')}"} for f in readers.search_shared(q)]
        return out

    async def search_async(self, q: str) -> list[dict]:
        return await db(self.search)(q)


# ---------------------------------------------------------------- actions en lot
class BulkService:
    """Actions groupées : sélection explicite, mot de confirmation tapé, plafond, résultat par élément, journal.
    Chaque élément passe par le service du module concerné (mêmes garde-fous que l'action unitaire)."""

    def __init__(self, outlook=None, sharing=None):
        self._outlook = outlook
        self._sharing = sharing

    @staticmethod
    def _check(action: str, items: list, confirm: str) -> None:
        if not items:
            raise InvalidBulk("Aucun élément sélectionné.")
        if len(items) > MAX_BULK:
            raise InvalidBulk(f"{MAX_BULK} éléments au maximum par action.")
        if (confirm or "").strip().upper() != BULK_WORD[action]:
            raise InvalidBulk(f"Tapez {BULK_WORD[action]} pour confirmer.")

    async def delete_rules_async(self, rule_ids: list, *, confirm: str, by: str) -> dict:
        self._check("rules.delete", rule_ids, confirm)
        from module.microsoft_outlook.application.services import OutlookService
        svc = self._outlook or OutlookService()
        results = []
        for rid in rule_ids:
            try:
                await svc.delete_rule_async(uuid.UUID(str(rid)))
                results.append({"id": str(rid), "ok": True})
            except Exception as e:  # noqa: BLE001 : on continue, l'échec est rapporté pour cet élément
                results.append({"id": str(rid), "ok": False, "error": f"{type(e).__name__}: {e}"[:300]})
        return await self._log("rules.delete", by, results)

    async def revoke_shares_async(self, connector_id: uuid.UUID, items: list[dict], *, confirm: str, by: str) -> dict:
        self._check("sharing.revoke", items, confirm)
        from module.microsoft_onedrive.application.services import SharingService
        svc = self._sharing or SharingService()
        results = []
        for it in items:
            ident = f"{it.get('drive_id')}|{it.get('item_id')}|{it.get('permission_id')}"
            try:
                await svc.revoke_async(connector_id, str(it["drive_id"]), str(it["item_id"]), str(it["permission_id"]), by)
                results.append({"id": ident, "ok": True})
            except Exception as e:  # noqa: BLE001
                results.append({"id": ident, "ok": False, "error": f"{type(e).__name__}: {e}"[:300]})
        out = await self._log("sharing.revoke", by, results)
        try:   # les alertes de partage concernées se ferment tout de suite
            await AlertService(notify=False, quarantine_reader=_no_quarantine).evaluate_async(connector_id)
        except Exception:  # noqa: BLE001
            pass
        return out

    @staticmethod
    async def _log(action: str, by: str, results: list[dict]) -> dict:
        ok = sum(1 for r in results if r["ok"])

        def save():
            BulkActionLog.objects.create(action=action, performed_by=by[:150], total=len(results), succeeded=ok,
                                         results=results)
        await db(save)()
        return {"total": len(results), "succeeded": ok, "failed": len(results) - ok, "results": results}


def _no_quarantine(connector) -> object:
    async def read() -> list:
        raise RuntimeError("quarantaine non relue pendant une action en lot")
    return read


__all__ = ["DashboardService", "SearchService", "BulkService", "InvalidBulk", "MAX_BULK", "BULK_WORD", "Optional"]



# ---------------------------------------------------------------- rapport d'organisation
class ReportService:
    """Rapport d'une organisation sur une période : à imprimer ou exporter en PDF depuis le navigateur."""

    @staticmethod
    def build(org_id: uuid.UUID, days: int = 30) -> Optional[dict]:
        if days not in (7, 30, 90):
            raise ValueError("Période : 7, 30 ou 90 jours.")
        org = next((o for o in readers.organizations() if o["id"] == org_id), None)
        if org is None:
            return None
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=days)
        conns = readers.connectors(org_id=org_id)
        alerts = readers.alerts_since(since, [c["id"] for c in conns])
        tenants = []
        for c in conns:
            mine = [a for a in alerts if a["connector_id"] == c["id"]]
            open_ = [a for a in mine if a["status"] in ("open", "acknowledged")]
            comp = readers.latest_compliance(c["id"]) or {}
            scores = readers.compliance_scores_since(c["id"], since)
            share = (readers.latest_sharing_summary(c["id"]) or {}).get("summary") or {}
            hyg = (readers.latest_hygiene(c["id"]) or {}).get("summary") or {}
            failed = sorted((f for f in comp.get("findings") or [] if f.get("status") == "fail"),
                            key=lambda f: {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(f.get("severity"), 9))
            t = {
                "connector_id": c["id"], "name": c["name"], "score": comp.get("score"), "score_trend": scores,
                "score_delta": rp.score_delta(scores),
                "alerts": {
                    "opened": sum(1 for a in mine if a["first_seen"] >= since),
                    "resolved": sum(1 for a in mine if a["resolved_at"] and a["resolved_at"] >= since),
                    "open_by_severity": rp.breakdown(open_)["by_severity"],
                    "by_category": rp.breakdown(open_)["by_category"],
                    "mttr_hours": rp.mean_time_to_resolve(a for a in mine if a["resolved_at"]),
                    "top": sorted(open_, key=lambda a: ("critical", "high", "medium", "low").index(a["severity"]))[:10],
                },
                "trend": rp.alert_trend(mine, now.date(), days),
                "sharing": {"external": share.get("external", 0), "anonymous": share.get("anonymous", 0),
                            "anonymous_edit": share.get("anonymous_edit", 0)},
                "hygiene": hyg,
                "findings": [{"title": f.get("title"), "severity": f.get("severity"), "summary": f.get("summary"),
                              "recommendation": f.get("recommendation")} for f in failed[:8]],
                "actions": readers.actions_since(c["id"], since)[:30],
            }
            t["highlights"] = rp.highlights(t)
            tenants.append(t)
        return {"organization": org, "period_days": days, "since": since, "generated_at": now, "tenants": tenants}

    async def build_async(self, org_id, days=30):
        return await db(self.build)(org_id, days)
