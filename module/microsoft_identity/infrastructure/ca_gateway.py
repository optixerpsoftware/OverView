"""Accès conditionnel et Security Defaults via Microsoft Graph."""
from __future__ import annotations

import asyncio
from typing import Optional

from shared_kernel.graph_rest import DataUnavailable, GraphError, GraphRest, safe_id

_CA = "/identity/conditionalAccess/policies"
_SD = "/policies/identitySecurityDefaultsEnforcementPolicy"
READ = "Policy.Read.All"
WRITE_CA = "Policy.ReadWrite.ConditionalAccess (+ Policy.Read.All, Application.Read.All)"


class GraphConditionalAccessGateway:
    def __init__(self, graph):
        self.rest = GraphRest(graph)

    async def security_defaults(self) -> dict:
        return await self.rest.get(_SD, requires=READ)

    async def set_security_defaults(self, enabled: bool) -> None:
        await self.rest.patch(_SD, {"isEnabled": bool(enabled)}, requires=WRITE_CA)

    async def policies(self) -> Optional[list[dict]]:
        """None si l'accès conditionnel est illisible (licence Entra ID P1 absente ou permission manquante)."""
        try:
            return await self.rest.get_all(_CA, requires=READ)
        except DataUnavailable:
            return None
        except GraphError as e:
            if e.status in (400, 403):
                return None
            raise

    async def named_locations(self) -> list[dict]:
        try:
            return await self.rest.get_all("/identity/conditionalAccess/namedLocations", requires=READ)
        except (DataUnavailable, GraphError):
            return []

    async def skus(self) -> list[dict]:
        try:
            return (await self.rest.get("/subscribedSkus", {"$select": "skuPartNumber,servicePlans"},
                                        requires="Organization.Read.All")).get("value", [])
        except (DataUnavailable, GraphError):
            return []

    async def resolve_names(self, object_ids: set[str], role_ids: set[str], app_ids: set[str]) -> dict[str, str]:
        """ID → nom lisible (utilisateurs, groupes, rôles, applications). Meilleur effort : un ID inconnu reste tel quel."""
        names: dict[str, str] = {}

        async def objects():
            ids = [i for i in object_ids if _guid(i)]
            for start in range(0, len(ids), 1000):
                try:
                    r = await self.rest.post("/directoryObjects/getByIds",
                                             {"ids": ids[start:start + 1000], "types": ["user", "group"]},
                                             requires="Directory.Read.All")
                except (DataUnavailable, GraphError):
                    return
                for o in r.get("value", []):
                    names[o["id"]] = o.get("displayName") or o.get("userPrincipalName") or o["id"]

        async def roles():
            if not role_ids:
                return
            try:
                rows = await self.rest.get_all("/directoryRoleTemplates", requires="Directory.Read.All")
            except (DataUnavailable, GraphError):
                return
            for t in rows:
                names[t["id"]] = t.get("displayName") or t["id"]

        async def apps():
            for a in list(app_ids)[:50]:
                if not _guid(a):
                    continue
                try:
                    r = await self.rest.get("/servicePrincipals", {"$filter": f"appId eq '{safe_id(a)}'",
                                                                    "$select": "appId,displayName"},
                                            requires="Application.Read.All")
                except (DataUnavailable, GraphError, ValueError):
                    continue
                for sp in r.get("value", []):
                    names[sp["appId"]] = sp.get("displayName") or sp["appId"]

        async def locations():
            for loc in await self.named_locations():
                names[loc["id"]] = loc.get("displayName") or loc["id"]
            names.setdefault("AllTrusted", "emplacements approuvés")
            names.setdefault("All", "tous les emplacements")

        await asyncio.gather(objects(), roles(), apps(), locations())
        return names

    async def create_policy(self, body: dict) -> dict:
        return await self.rest.post(_CA, body, requires=WRITE_CA)


def _guid(v: str) -> bool:
    return len(v) == 36 and v.count("-") == 4
