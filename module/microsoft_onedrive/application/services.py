from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable, Optional

from asgiref.sync import async_to_sync

from shared_kernel.aio import db
from shared_kernel.realtime import publish

from module.microsoft_connector.application.graph_factory import build_graph
from module.microsoft_connector.domain.repositories import ConnectorRepository
from module.microsoft_connector.infrastructure.repositories import DjangoConnectorRepository
from module.microsoft_onedrive.domain.ports import DriveGateway, SharingScanRepository
from module.microsoft_onedrive.domain.sharing import (
    Entry, SharedRecord, classify, parse_permission, reconcile_anonymous, summarize,
)
from module.microsoft_onedrive.infrastructure.graph_gateway import GraphDriveGateway
from module.microsoft_onedrive.infrastructure.repositories import DjangoSharingScanRepository
from module.microsoft_onedrive.infrastructure.scan_models import SharingAction
from shared_kernel.graph_rest import DataUnavailable

MAX_ITEMS_PER_DRIVE = 1500
MAX_RECORDS = 5000


class ConnectorUnavailable(Exception):
    pass


class PermissionNotFound(Exception):
    pass


class NotRevocable(Exception):
    """Permission héritée du dossier parent, ou propriétaire : à traiter ailleurs."""


def entry_to_dict(e: Entry) -> dict:
    return {
        "permission_id": e.permission_id, "kind": e.kind, "scope": e.scope, "link_type": e.link_type,
        "roles": list(e.roles), "link_url": e.link_url, "expires_at": e.expires_at,
        "has_password": e.has_password, "grantees": list(e.grantees), "external": e.external,
        "risk": classify(e).value,
    }


def _default_gateway(connector) -> DriveGateway:
    return GraphDriveGateway(build_graph(connector))


def _msg(e: BaseException) -> str:
    if isinstance(e, DataUnavailable):
        return f"{e.reason} ({e.requires})" if e.requires else e.reason
    return f"{type(e).__name__}: {e}"[:300]


class SharingService:
    """Explorer OneDrive/SharePoint, auditer les liens de partage et les révoquer."""

    def __init__(
        self,
        connectors: Optional[ConnectorRepository] = None,
        scans: Optional[SharingScanRepository] = None,
        gateway_factory: Callable[[Any], DriveGateway] = _default_gateway,
    ):
        self.connectors = connectors or DjangoConnectorRepository()
        self.scans = scans or DjangoSharingScanRepository()
        self.gateway_factory = gateway_factory

    def _ctx(self, connector_id: uuid.UUID):
        c = self.connectors.get(connector_id)
        if c is None or not c.is_active:
            raise ConnectorUnavailable(str(connector_id))
        return c, self.gateway_factory(c)

    # ---------------- exploration ----------------
    # Async d'abord (`<nom>_async`, appelé par les vues Daphne) + version synchrone (`<nom>`) pour Celery et les tests.
    async def _actx(self, connector_id: uuid.UUID):
        return await db(self._ctx)(connector_id)

    async def drives_async(self, connector_id: uuid.UUID) -> list[dict]:
        _, gw = await self._actx(connector_id)
        return await gw.list_drives()

    def drives(self, connector_id: uuid.UUID) -> list[dict]:
        return async_to_sync(self.drives_async)(connector_id)

    def children(self, connector_id: uuid.UUID, drive_id: str, parent_id: Optional[str]) -> list[dict]:
        return async_to_sync(self.children_async)(connector_id, drive_id, parent_id)

    async def children_async(self, connector_id: uuid.UUID, drive_id: str, parent_id: Optional[str]) -> list[dict]:
        _, gw = await self._actx(connector_id)
        items = await gw.children(drive_id, parent_id)
        out = [{
            "id": i["id"], "name": i.get("name", ""), "is_folder": bool(i.get("folder")),
            "child_count": (i.get("folder") or {}).get("childCount"), "size": i.get("size", 0),
            "web_url": i.get("webUrl", ""), "modified_at": i.get("lastModifiedDateTime"),
            "path": i.get("path", ""), "shared": bool(i.get("shared")),
        } for i in items]
        out.sort(key=lambda x: (not x["is_folder"], x["name"].lower()))
        return out

    def permissions(self, connector_id: uuid.UUID, drive_id: str, item_id: str) -> list[dict]:
        return async_to_sync(self.permissions_async)(connector_id, drive_id, item_id)

    async def permissions_async(self, connector_id: uuid.UUID, drive_id: str, item_id: str) -> list[dict]:
        _, gw = await self._actx(connector_id)
        raw, domains = await asyncio.gather(gw.permissions(drive_id, item_id), gw.internal_domains(),
                                            return_exceptions=True)
        if isinstance(raw, BaseException):
            raise raw
        domains = [] if isinstance(domains, BaseException) else domains
        parsed = [(p, parse_permission({k: v for k, v in p.items() if k != "inheritedFrom"}, domains)) for p in raw]
        scope = None
        if any(e and e.scope == "anonymous" for _, e in parsed):
            try:
                scope = (await gw.shared_scopes(drive_id, [item_id])).get(item_id)
            except Exception:
                scope = None  # inconnu : on garde l'alerte
        out = []
        for p, e in parsed:
            if e is None:
                continue
            e, note = reconcile_anonymous(e, scope)
            out.append({**entry_to_dict(e), "inherited": bool(p.get("inheritedFrom")), "note": note})
        return out

    # ---------------- révocation ----------------
    def revoke(self, connector_id: uuid.UUID, drive_id: str, item_id: str, permission_id: str, by: str) -> None:
        return async_to_sync(self.revoke_async)(connector_id, drive_id, item_id, permission_id, by)

    async def revoke_async(self, connector_id: uuid.UUID, drive_id: str, item_id: str, permission_id: str,
                           by: str) -> None:
        """Supprime un lien ou un partage direct dans Microsoft 365.

        Garde-fous : la permission est relue en direct (pas de confiance dans le client),
        les permissions héritées et celles du propriétaire sont refusées, et l'action est journalisée."""
        connector, gw = await self._actx(connector_id)
        raw = await gw.permissions(drive_id, item_id)
        target = next((p for p in raw if p.get("id") == permission_id), None)
        if target is None:
            raise PermissionNotFound(permission_id)
        if target.get("inheritedFrom"):
            raise NotRevocable("Permission héritée du dossier parent : à révoquer sur le parent.")
        if not target.get("link") and tuple(target.get("roles") or []) == ("owner",):
            raise NotRevocable("Permission du propriétaire.")
        entry = parse_permission(target, [])
        desc = (f"{'lien ' + entry.scope + '/' + entry.link_type if entry and entry.kind == 'link' else 'partage direct'}"
                f" — {', '.join(entry.grantees) if entry else ''}")
        try:
            await gw.delete_permission(drive_id, item_id, permission_id)
        except Exception as e:
            await db(self._log)(connector, drive_id, item_id, permission_id, desc, False, _msg(e), by)
            raise
        await db(self._log)(connector, drive_id, item_id, permission_id, desc, True, "", by)
        await db(self.scans.mark_revoked)(connector_id, drive_id, item_id, permission_id)

    @staticmethod
    def _log(connector, drive_id, item_id, permission_id, desc, ok, detail, by) -> None:
        SharingAction.objects.create(
            connector_id=connector.id, org_id=connector.org_id, drive_id=drive_id, item_id=item_id,
            permission_id=permission_id, description=desc[:1000], ok=ok, detail=detail[:1000], performed_by=by)

    # ---------------- audit ----------------
    def start_scan(self, connector_id: uuid.UUID) -> tuple[dict, bool]:
        """Crée une analyse « en cours ». Renvoie (scan, créée?) ; si une analyse tourne déjà, on la réutilise."""
        connector, _ = self._ctx(connector_id)
        running = self.scans.running(connector_id)
        if running:
            return running, False
        scan = self.scans.start(connector_id=connector_id, org_id=connector.org_id)
        publish("sharing.scan", {"connector_id": str(connector_id), "status": "running"})
        return scan, True

    def run_scan(self, scan_id: uuid.UUID, connector_id: uuid.UUID) -> dict:
        """Exécute l'audit complet (long : appelé par le worker)."""
        try:
            _, gw = self._ctx(connector_id)
            records, truncated, errors = async_to_sync(self._scan)(gw)
            done = self.scans.finish(scan_id, records=records, summary=summarize(records),
                                     truncated=truncated, errors=errors)
            publish("sharing.scan", {"connector_id": str(connector_id), "status": "done"})
            return done
        except Exception as e:
            self.scans.fail(scan_id, _msg(e))
            publish("sharing.scan", {"connector_id": str(connector_id), "status": "failed"})
            raise

    async def _scan(self, gw: DriveGateway):
        domains, drives = await asyncio.gather(gw.internal_domains(), gw.list_drives())
        sem = asyncio.Semaphore(3)
        records: list[dict] = []
        truncated: list[str] = []
        errors: list[str] = []

        async def scan_drive(d: dict) -> None:
            async with sem:
                try:
                    items, cut = await gw.scan_items(d["id"], MAX_ITEMS_PER_DRIVE)
                    perms = await gw.permissions_batch(d["id"], [i["id"] for i in items])
                except Exception as e:
                    errors.append(f"{d['label']} : {_msg(e)}")
                    return
                if cut:
                    truncated.append(d["label"])
                parsed = [(item, e) for item, plist in zip(items, perms) for p in plist or []
                          if (e := parse_permission(p, domains)) is not None]
                # Recoupe les liens « anonymes » avec le périmètre réel de l'élément (faux positifs Teams).
                suspects = sorted({i["id"] for i, e in parsed if e.scope == "anonymous"})
                try:
                    scopes = await gw.shared_scopes(d["id"], suspects) if suspects else {}
                except Exception as e:
                    errors.append(f"{d['label']} : vérification du périmètre impossible ({_msg(e)}) — "
                                  "liens anonymes non recoupés")
                    scopes = {}
                for item, entry in parsed:
                    entry, note = reconcile_anonymous(entry, scopes.get(item["id"]))
                    rec = SharedRecord(
                        drive_id=d["id"], drive_label=d["label"], item_id=item["id"], name=item.get("name", ""),
                        path=item.get("path", ""), web_url=item.get("webUrl", ""),
                        is_folder=bool(item.get("folder")), entry=entry, risk=classify(entry), note=note)
                    records.append(rec.to_dict())

        await asyncio.gather(*(scan_drive(d) for d in drives))
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        records.sort(key=lambda r: (order[r["risk"]], r["drive_label"], r["path"]))
        if len(records) > MAX_RECORDS:
            truncated.append(f"résultats limités à {MAX_RECORDS} partages")
            records = records[:MAX_RECORDS]
        return records, truncated, errors

    def latest(self, connector_id: uuid.UUID) -> Optional[dict]:
        self._ctx(connector_id)
        return self.scans.latest(connector_id)

    def history(self, connector_id: uuid.UUID) -> list[dict]:
        self._ctx(connector_id)
        return self.scans.history(connector_id)

    def scan_all(self) -> dict:
        """Lance l'audit de tous les connecteurs actifs (en série ; appelé chaque jour par le worker)."""
        ok = failed = 0
        for c in self.connectors.list_active():
            try:
                scan, created = self.start_scan(c.id)
                if created:
                    self.run_scan(scan["id"], c.id)
                ok += 1
            except Exception:
                failed += 1
        return {"scanned": ok, "failed": failed}
