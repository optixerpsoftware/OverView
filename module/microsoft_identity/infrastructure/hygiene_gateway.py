"""Lectures Graph pour l'hygiène des identités, et deux actions réversibles (désactiver un compte / une application)."""
from __future__ import annotations

import asyncio

from module.microsoft_compliance.domain.derive import activity_to_iso, parse_activity_report
from module.microsoft_compliance.infrastructure.graph_gateway import GraphComplianceGateway
import httpx

from shared_kernel.graph_rest import BASE, BETA, DataUnavailable, GraphError, safe_id

_USER_SELECT = ("id,displayName,userPrincipalName,userType,accountEnabled,createdDateTime,mail,"
                "externalUserState,externalUserStateChangeDateTime,assignedLicenses")


class GraphHygieneGateway(GraphComplianceGateway):
    """Hérite des lectures de la gouvernance (rôles, inscriptions MFA avec repli sans P1)."""

    async def users_full(self) -> list[dict]:
        try:   # signInActivity exige Entra ID P1
            return await self.rest.get_all("/users", {"$select": _USER_SELECT + ",signInActivity", "$top": "999"},
                                           requires="User.Read.All")
        except (DataUnavailable, GraphError):
            pass
        users = await self.rest.get_all("/users", {"$select": _USER_SELECT, "$top": "999"}, requires="User.Read.All")
        try:   # sans P1 : dernière activité Microsoft 365 (rapports d'utilisation, 180 jours)
            activity = parse_activity_report(await self.rest.get_text(
                "/reports/getOffice365ActiveUserDetail(period='D180')", requires="Reports.Read.All"))
        except Exception:  # noqa: BLE001
            activity = None
        if activity is not None:
            for u in users:
                key = (u.get("userPrincipalName") or "").lower()
                # Absent du rapport = aucune activité sur 180 jours : on le note comme tel (pas « inconnu »).
                u["signInActivity"] = {"lastSignInDateTime": activity_to_iso(activity[key])} if key in activity else {}
        return users

    async def organization_id(self) -> str:
        org = await self.rest.get("/organization", {"$select": "id"}, requires="Organization.Read.All")
        return ((org.get("value") or [{}])[0]).get("id", "")

    async def service_principals(self) -> list[dict]:
        return await self.rest.get_all("/servicePrincipals", {
            "$select": "id,appId,displayName,appOwnerOrganizationId,publisherName,verifiedPublisher,"
                       "servicePrincipalType,accountEnabled,createdDateTime", "$top": "999"},
            requires="Application.Read.All")

    async def grants(self) -> list[dict]:
        return await self.rest.get_all("/oauth2PermissionGrants", {"$top": "999"}, requires="Directory.Read.All")

    async def assignments(self, sp_ids: list[str]) -> dict[str, list[dict]]:
        bodies = await self.rest.batch_get([f"/servicePrincipals/{safe_id(i)}/appRoleAssignments" for i in sp_ids],
                                           requires="Application.Read.All")
        return {i: (b or {}).get("value", []) for i, b in zip(sp_ids, bodies)}

    async def resources(self, resource_ids: list[str]) -> tuple[dict, dict]:
        """(resourceId, appRoleId) → valeur de la permission ; resourceId → nom de l'API."""
        bodies = await self.rest.batch_get(
            [f"/servicePrincipals/{safe_id(i)}?$select=id,displayName,appRoles" for i in resource_ids],
            requires="Application.Read.All")
        roles, names = {}, {}
        for rid, b in zip(resource_ids, bodies):
            if not b:
                continue
            names[rid] = b.get("displayName") or rid
            for r in b.get("appRoles") or []:
                roles[(rid, r["id"])] = r.get("value") or r.get("displayName") or r["id"]
        return roles, names

    async def user_names(self, ids: list[str]) -> dict[str, str]:
        ids = [i for i in ids if i]
        if not ids:
            return {}
        try:
            r = await self.rest.post("/directoryObjects/getByIds", {"ids": ids[:1000], "types": ["user"]},
                                     requires="Directory.Read.All")
        except (DataUnavailable, GraphError):
            return {}
        return {o["id"]: o.get("userPrincipalName") or o.get("displayName") or o["id"] for o in r.get("value", [])}

    async def sp_activities(self) -> dict[str, dict]:
        """Dernières connexions de chaque appli (rapport bêta, sans licence Entra P1) : appId → ligne du rapport."""
        try:
            rows = []
            url = f"{BETA}/reports/servicePrincipalSignInActivities"
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as http:
                while url:
                    r = await self.rest._send(http, "GET", url)
                    data = self.rest._check(r, "AuditLog.Read.All")
                    rows += data.get("value", [])
                    url = data.get("@odata.nextLink")
            return {r.get("appId"): r for r in rows if r.get("appId")}
        except (DataUnavailable, GraphError):
            return {}

    async def app_sign_ins(self, app_id: str, since_iso: str, limit: int = 500) -> list[dict]:
        """Journal des connexions d'une appli (au nom d'utilisateurs). Lent côté Microsoft : jusqu'à ~1 min."""
        params = {"$filter": f"appId eq '{safe_id(app_id)}' and createdDateTime ge {since_iso}", "$top": "200"}
        out, url = [], f"{BASE}/auditLogs/signIns"
        async with httpx.AsyncClient(timeout=httpx.Timeout(150.0, connect=15.0)) as http:
            while url and len(out) < limit:
                r = await self.rest._send(http, "GET", url, params=params)
                data = self.rest._check(r, "AuditLog.Read.All")
                out += data.get("value", [])
                url, params = data.get("@odata.nextLink"), None
        return out[:limit]

    async def delete_app(self, sp_id: str) -> None:
        """Supprime l'application du tenant (principal de service) : ses permissions et consentements disparaissent.
        Elle peut être réinstallée plus tard par un nouveau consentement."""
        await self.rest.delete(f"/servicePrincipals/{safe_id(sp_id)}", requires="Application.ReadWrite.All")

    # ------------------------------------------------------------------ actions (réversibles)
    async def set_user_enabled(self, user_id: str, enabled: bool) -> None:
        await self.rest.patch(f"/users/{safe_id(user_id)}", {"accountEnabled": bool(enabled)}, requires="User.ReadWrite.All")

    async def set_app_enabled(self, sp_id: str, enabled: bool) -> None:
        await self.rest.patch(f"/servicePrincipals/{safe_id(sp_id)}", {"accountEnabled": bool(enabled)},
                              requires="Application.ReadWrite.All")


async def gather_safe(*coros):
    """Comme asyncio.gather, mais une source illisible donne None au lieu de faire échouer toute l'analyse."""
    res = await asyncio.gather(*coros, return_exceptions=True)
    return [None if isinstance(r, BaseException) else r for r in res], [r for r in res if isinstance(r, BaseException)]
