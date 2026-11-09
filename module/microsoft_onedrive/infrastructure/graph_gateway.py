from __future__ import annotations

import asyncio
from typing import Optional

from module.microsoft_onedrive.domain.ports import DriveGateway
from shared_kernel.graph_rest import DataUnavailable, GraphError, GraphRest, safe_id

ITEM_SELECT = "id,name,size,folder,file,webUrl,lastModifiedDateTime,parentReference,shared"
_FILES = "Files.Read.All"
_SITES = "Sites.Read.All"


def _path_of(item: dict) -> str:
    """Chemin lisible d'un élément (parentReference.path = « /drives/{id}/root:/Dossier »)."""
    parent = (item.get("parentReference") or {}).get("path", "")
    parent = parent.split("root:", 1)[-1] if "root:" in parent else ""
    return f"{parent}/{item.get('name', '')}".replace("//", "/")


class GraphDriveGateway(DriveGateway):
    def __init__(self, graph):
        self.rest = GraphRest(graph)

    async def internal_domains(self) -> list[str]:
        users = await self.rest.get_all("/users", {"$select": "userPrincipalName,userType", "$top": "999"},
                                        requires="User.Read.All")
        return sorted({u["userPrincipalName"].rsplit("@", 1)[-1].lower() for u in users
                       if u.get("userType", "Member") == "Member" and "@" in (u.get("userPrincipalName") or "")
                       and "#EXT#" not in u["userPrincipalName"]})

    async def list_drives(self) -> list[dict]:
        users = await self.rest.get_all(
            "/users", {"$select": "id,displayName,userPrincipalName,userType,accountEnabled", "$top": "999"},
            requires="User.Read.All")
        members = [u for u in users if u.get("userType", "Member") == "Member"]
        bodies = await self.rest.batch_get(
            [f"/users/{safe_id(u['id'])}/drive?$select=id,name,webUrl" for u in members], requires=_FILES)
        drives = [
            {"id": b["id"], "label": f"OneDrive — {u.get('displayName') or u['userPrincipalName']}",
             "owner": u.get("userPrincipalName", ""), "kind": "onedrive", "web_url": b.get("webUrl", "")}
            for u, b in zip(members, bodies) if b and b.get("id")
        ]
        try:  # bibliothèques de documents SharePoint (les OneDrive personnels sont déjà couverts)
            sites = await self.rest.get_all("/sites", {"search": "*", "$select": "id,displayName,webUrl"},
                                            requires=_SITES)
        except DataUnavailable:
            return drives
        sites = [s for s in sites if "-my.sharepoint.com" not in (s.get("webUrl") or "")]
        site_drives = await self.rest.batch_get(
            [f"/sites/{safe_id(s['id'])}/drives?$select=id,name,webUrl" for s in sites], requires=_SITES)
        for s, body in zip(sites, site_drives):
            for d in (body or {}).get("value", []):
                drives.append({
                    "id": d["id"], "label": f"{s.get('displayName') or s['webUrl']} — {d.get('name', 'Documents')}",
                    "owner": "", "kind": "sharepoint", "web_url": d.get("webUrl", ""),
                })
        return drives

    async def children(self, drive_id, parent_id):
        base = f"/drives/{safe_id(drive_id)}/" + (f"items/{safe_id(parent_id)}" if parent_id else "root")
        items = await self.rest.get_all(f"{base}/children", {"$select": ITEM_SELECT, "$top": "200"},
                                        requires=_FILES, max_items=1000)
        return [{**i, "path": _path_of(i)} for i in items]

    async def permissions(self, drive_id, item_id):
        return await self.rest.get_all(
            f"/drives/{safe_id(drive_id)}/items/{safe_id(item_id)}/permissions", requires=_FILES)

    async def scan_items(self, drive_id, max_items):
        did = safe_id(drive_id)
        sem = asyncio.Semaphore(8)
        items: list[dict] = []
        frontier: list[Optional[str]] = [None]  # None = racine
        truncated = False

        async def kids(parent):
            async with sem:
                base = f"/drives/{did}/" + (f"items/{safe_id(parent)}" if parent else "root")
                try:
                    return await self.rest.get_all(f"{base}/children", {"$select": ITEM_SELECT, "$top": "200"},
                                                   requires=_FILES, max_items=2000)
                except GraphError as e:
                    if e.status == 404:
                        return []  # dossier supprimé pendant le parcours
                    raise  # ex. 429 persistant : le drive est signalé « en erreur », jamais « sans partage »

        while frontier:
            level = await asyncio.gather(*(kids(f) for f in frontier))
            frontier = []
            for children in level:
                for c in children:
                    if len(items) >= max_items:
                        truncated = True
                        break
                    items.append({**c, "path": _path_of(c)})
                    if c.get("folder"):
                        frontier.append(c["id"])
                if truncated:
                    break
            if truncated:
                break
        return items, truncated

    async def permissions_batch(self, drive_id, item_ids):
        did = safe_id(drive_id)
        bodies = await self.rest.batch_get(
            [f"/drives/{did}/items/{safe_id(i)}/permissions" for i in item_ids], requires=_FILES)
        return [(b.get("value", []) if b else None) for b in bodies]

    async def shared_scopes(self, drive_id, item_ids):
        did = safe_id(drive_id)
        bodies = await self.rest.batch_get(
            [f"/drives/{did}/items/{safe_id(i)}?$select=id,shared" for i in item_ids], requires=_FILES)
        return {i: ((b or {}).get("shared") or {}).get("scope") if b else None for i, b in zip(item_ids, bodies)}

    async def delete_permission(self, drive_id, item_id, permission_id):
        await self.rest.delete(
            f"/drives/{safe_id(drive_id)}/items/{safe_id(item_id)}/permissions/{safe_id(permission_id)}",
            requires="Files.ReadWrite.All")
