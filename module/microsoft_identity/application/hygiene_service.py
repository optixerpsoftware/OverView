"""Hygiène des identités : analyse (enregistrée), lecture, et actions réversibles encadrées."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from asgiref.sync import async_to_sync

from module.microsoft_connector.application.graph_factory import build_graph
from module.microsoft_connector.infrastructure.repositories import DjangoConnectorRepository
from module.microsoft_identity.domain import hygiene as hy
from module.microsoft_identity.infrastructure.audit_models import TenantSettingChange
from module.microsoft_identity.infrastructure.hygiene_gateway import GraphHygieneGateway, gather_safe
from module.microsoft_identity.infrastructure.sync_models import IdentityHygieneSnapshot
from shared_kernel.aio import db
from shared_kernel.realtime import apublish

log = logging.getLogger(__name__)
WORD_DISABLE = "DESACTIVER"
WORD_ENABLE = "REACTIVER"
MAX_ACCOUNTS = 50


class HygieneUnavailable(Exception):
    pass


class HygieneRefused(Exception):
    pass


def _msg(e: BaseException) -> str:
    return getattr(e, "reason", None) or f"{type(e).__name__}: {e}"[:300]


class HygieneService:
    def __init__(self, connectors=None, gateway_factory: Callable[[Any], Any] = None,
                 now: Optional[Callable[[], datetime]] = None):
        self.connectors = connectors or DjangoConnectorRepository()
        self.gateway_factory = gateway_factory or (lambda c: GraphHygieneGateway(build_graph(c)))
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _ctx(self, connector_id):
        c = self.connectors.get(connector_id)
        if c is None or not c.is_active:
            raise HygieneUnavailable(str(connector_id))
        return c, self.gateway_factory(c)

    # ------------------------------------------------------------------ analyse
    async def scan_async(self, connector_id: uuid.UUID) -> dict:
        connector, gw = await db(self._ctx)(connector_id)
        (users, roles, regs, sps, grants, org_id, activities), errs = await gather_safe(
            gw.users_full(), gw.roles(), gw.registrations(), gw.service_principals(), gw.grants(), gw.organization_id(),
            gw.sp_activities())
        errors = [_msg(e) for e in errs]
        if users is None:
            raise HygieneUnavailable("Lecture des comptes impossible : " + "; ".join(errors))
        now = self._now()
        inactive = hy.inactive_accounts(users, now)
        guest_rows = hy.guests(users, now)
        admin_info = hy.admins(roles or [], regs or [])
        apps: list[dict] = []
        if sps is not None:
            own = org_id or str(connector.tenant_id)
            third = [sp for sp in sps if hy.is_third_party(sp, own)]
            try:
                assignments = await gw.assignments([sp["id"] for sp in third]) if third else {}
                resource_ids = sorted({a["resourceId"] for rows in assignments.values() for a in rows}
                                      | {g["resourceId"] for g in (grants or []) if g.get("clientId") in {sp["id"] for sp in third}})
                role_names, resource_names = await gw.resources(resource_ids) if resource_ids else ({}, {})
                principals = sorted({g.get("principalId") for g in (grants or []) if g.get("principalId")})
                users_by_id = {u["id"]: u.get("userPrincipalName") for u in users}
                missing = [p for p in principals if p not in users_by_id]
                user_names = {**users_by_id, **(await gw.user_names(missing))}
                apps = hy.app_consents(sps, grants or [], assignments, role_names, resource_names, own, user_names)
                for a in apps:     # dernière utilisation (trafic) de chaque appli
                    a["activity"] = hy.app_activity((activities or {}).get(a.get("app_id")), now)
            except Exception as e:  # noqa: BLE001
                errors.append("Applications tierces : " + _msg(e))
        data = {"scanned_at": now.isoformat(), "inactive": inactive, "guests": guest_rows, "admins": admin_info,
                "apps": apps, "summary": hy.summary(inactive, guest_rows, admin_info, apps),
                "activity_source": "signInActivity" if users and any((u.get("signInActivity") or {}).get("lastSuccessfulSignInDateTime") or (u.get("signInActivity") or {}).get("lastNonInteractiveSignInDateTime") for u in users) else "rapports Microsoft 365"}

        def save():
            IdentityHygieneSnapshot.objects.create(connector_id=connector.id, org_id=connector.org_id, data=data, errors=errors)
            # On ne garde que les 10 dernières analyses par tenant.
            old = IdentityHygieneSnapshot.objects.filter(connector_id=connector.id).values_list("id", flat=True)[10:]
            IdentityHygieneSnapshot.objects.filter(id__in=list(old)).delete()
        await db(save)()
        await apublish("hygiene.scan", {"connector_id": str(connector_id)})
        return {**data, "errors": errors}

    def scan(self, connector_id) -> dict:
        return async_to_sync(self.scan_async)(connector_id)

    @staticmethod
    def latest(connector_id) -> Optional[dict]:
        s = IdentityHygieneSnapshot.objects.filter(connector_id=connector_id).first()
        return {**s.data, "errors": s.errors} if s else None

    def scan_all(self) -> dict:
        ok = failed = 0
        for c in self.connectors.list_active():
            try:
                self.scan(c.id)
                ok += 1
            except Exception:  # noqa: BLE001
                log.exception("Analyse d'hygiène impossible pour %s", c.id)
                failed += 1
        return {"scanned": ok, "failed": failed}

    # ------------------------------------------------------------------ actions réversibles
    async def set_accounts_enabled_async(self, connector_id, user_ids: list[str], enabled: bool, *, confirm: str, by: str) -> dict:
        word = WORD_ENABLE if enabled else WORD_DISABLE
        if (confirm or "").strip().upper() != word:
            raise HygieneRefused(f"Tapez {word} pour confirmer.")
        if not user_ids or len(user_ids) > MAX_ACCOUNTS:
            raise HygieneRefused(f"Sélectionne entre 1 et {MAX_ACCOUNTS} comptes.")
        connector, gw = await db(self._ctx)(connector_id)
        snap = await db(self.latest)(connector_id)
        if not snap:
            raise HygieneRefused("Lance d'abord une analyse.")
        # Garde-fous : seuls les comptes signalés par l'analyse (inactifs / invités) sont modifiables ici,
        # jamais un administrateur.
        allowed = {r["id"]: r for r in (snap.get("inactive") or []) + (snap.get("guests") or [])}
        admin_upns = {p["upn"].lower() for p in snap["admins"]["people"]}
        results = []
        for uid in user_ids:
            row = allowed.get(uid)
            if row is None:
                results.append({"id": uid, "ok": False, "error": "Compte non signalé par la dernière analyse."})
                continue
            if row["upn"].lower() in admin_upns:
                results.append({"id": uid, "upn": row["upn"], "ok": False, "error": "Compte administrateur : à traiter manuellement."})
                continue
            try:
                await gw.set_user_enabled(uid, enabled)
                results.append({"id": uid, "upn": row["upn"], "ok": True})
            except Exception as e:  # noqa: BLE001
                results.append({"id": uid, "upn": row["upn"], "ok": False, "error": _msg(e)})
        done = [r["upn"] for r in results if r["ok"]]
        if done or results:
            await db(self._audit)(connector, "account_enable" if enabled else "account_disable",
                                  [f"{'Réactivé' if enabled else 'Désactivé'} : {u}" for u in done] or ["Aucun changement"],
                                  bool(done), "; ".join(f"{r.get('upn', r['id'])} : {r['error']}" for r in results if not r["ok"]), by)
        return {"succeeded": len(done), "failed": len(results) - len(done), "results": results}

    async def set_app_enabled_async(self, connector_id, sp_id: str, enabled: bool, *, confirm: str, by: str) -> dict:
        word = WORD_ENABLE if enabled else WORD_DISABLE
        if (confirm or "").strip().upper() != word:
            raise HygieneRefused(f"Tapez {word} pour confirmer.")
        connector, gw = await db(self._ctx)(connector_id)
        snap = await db(self.latest)(connector_id) or {}
        app = next((a for a in snap.get("apps") or [] if a["id"] == sp_id), None)
        if app is None:
            raise HygieneRefused("Application non signalée par la dernière analyse (seules les applis tierces sont gérées ici).")
        change = [f"Application {'réactivée' if enabled else 'désactivée'} : {app['name']}"]
        try:
            await gw.set_app_enabled(sp_id, enabled)
        except Exception as e:
            await db(self._audit)(connector, "app_enable" if enabled else "app_disable", change, False, _msg(e), by)
            raise
        await db(self._audit)(connector, "app_enable" if enabled else "app_disable", change, True, "", by)

        def mark():   # met l'instantané à jour sans relancer toute l'analyse
            s = IdentityHygieneSnapshot.objects.filter(connector_id=connector_id).first()
            for a in s.data.get("apps") or []:
                if a["id"] == sp_id:
                    a["enabled"] = enabled
            s.save(update_fields=["data", "modified_at"])
        await db(mark)()
        return {"id": sp_id, "enabled": enabled}

    @staticmethod
    def _audit(connector, setting, changes, ok, detail, by):
        TenantSettingChange.objects.create(connector_id=connector.id, org_id=connector.org_id, setting=setting,
                                           changes=changes, ok=ok, detail=(detail or "")[:2000], performed_by=by[:150])

    @staticmethod
    def history(connector_id) -> list[dict]:
        return list(TenantSettingChange.objects.filter(
            connector_id=connector_id, setting__in=("account_disable", "account_enable", "app_disable", "app_enable", "app_delete")).values(
            "id", "setting", "changes", "ok", "detail", "performed_by", "create_at")[:30])



# ---------------------------------------------------------------- trafic détaillé et suppression d'une application
class AppNotManaged(Exception):
    pass


def _app_from_snapshot(connector_id, sp_id) -> dict:
    snap = HygieneService.latest(connector_id) or {}
    app = next((a for a in snap.get("apps") or [] if a["id"] == sp_id), None)
    if app is None:
        raise AppNotManaged("Application non signalée par la dernière analyse (seules les applis tierces sont gérées ici).")
    return app


async def app_traffic_async(service: "HygieneService", connector_id, sp_id: str, days: int = 30) -> dict:
    """Trafic d'une appli : connexions des {days} derniers jours (journal Microsoft) + dernière activité connue."""
    if days not in (7, 30):
        raise HygieneRefused("Période : 7 ou 30 jours.")
    _, gw = await db(service._ctx)(connector_id)
    app = await db(_app_from_snapshot)(connector_id, sp_id)
    now = service._now()
    since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    error, rows = "", []
    try:
        rows = await gw.app_sign_ins(app["app_id"], since)
    except Exception as e:  # noqa: BLE001 : journal indisponible (licence, délai) → on affiche ce qu'on sait
        error = _msg(e)
    summary = hy.traffic_summary(rows, now, days)
    recent = [{"at": r.get("createdDateTime"), "user": r.get("userPrincipalName"), "ip": r.get("ipAddress"),
               "country": (r.get("location") or {}).get("countryOrRegion"),
               "ok": ((r.get("status") or {}).get("errorCode") or 0) == 0,
               "client": r.get("clientAppUsed") or (r.get("deviceDetail") or {}).get("browser")} for r in rows[:50]]
    return {"app": {k: app.get(k) for k in ("id", "name", "publisher", "verified", "risk", "activity", "enabled")},
            "days": days, "summary": summary, "recent": recent, "error": error,
            "note": "Le journal couvre les connexions au nom d'utilisateurs. Les accès sans utilisateur (permissions "
                    "d'application) n'y figurent pas : voir « dernière utilisation sans utilisateur »."}


async def delete_app_async(service: "HygieneService", connector_id, sp_id: str, *, confirm: str, by: str) -> dict:
    if (confirm or "").strip().upper() != "SUPPRIMER":
        raise HygieneRefused("Tapez SUPPRIMER pour confirmer.")
    connector, gw = await db(service._ctx)(connector_id)
    app = await db(_app_from_snapshot)(connector_id, sp_id)
    change = [f"Application supprimée du tenant : {app['name']} ({app.get('publisher') or 'éditeur inconnu'})"]
    try:
        await gw.delete_app(sp_id)
    except Exception as e:
        await db(service._audit)(connector, "app_delete", change, False, _msg(e), by)
        raise
    await db(service._audit)(connector, "app_delete", change, True, "", by)

    def forget():
        s = IdentityHygieneSnapshot.objects.filter(connector_id=connector_id).first()
        if s:
            s.data["apps"] = [a for a in s.data.get("apps") or [] if a["id"] != sp_id]
            s.data.setdefault("summary", {})["apps"] = len(s.data["apps"])
            s.data["summary"]["apps_high"] = sum(1 for a in s.data["apps"] if a.get("risk") == "high")
            s.save(update_fields=["data", "modified_at"])
    await db(forget)()
    return {"id": sp_id, "deleted": True, "name": app["name"]}
