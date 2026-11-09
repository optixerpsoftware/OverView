from __future__ import annotations

import asyncio

from module.microsoft_compliance.domain.derive import (
    activity_to_iso, parse_activity_report, registration_from_methods,
)
from module.microsoft_compliance.domain.ports import ComplianceGateway, DataUnavailable
from shared_kernel.graph_rest import GraphRest, safe_id


class GraphComplianceGateway(ComplianceGateway):
    def __init__(self, graph):
        self.rest = GraphRest(graph)

    async def licenses(self):
        return await self.rest.get_all("/subscribedSkus", requires="Organization.Read.All")

    async def users(self):
        select = "id,displayName,userPrincipalName,userType,accountEnabled,createdDateTime"
        try:  # signInActivity exige P1 + AuditLog.Read.All
            return await self.rest.get_all(
                "/users", {"$select": select + ",signInActivity", "$top": "999"}, requires="User.Read.All")
        except DataUnavailable:
            pass
        users = await self.rest.get_all("/users", {"$select": select, "$top": "999"}, requires="User.Read.All")
        # Sans P1 : dernière activité Microsoft 365 (Exchange, OneDrive, SharePoint, Teams…) via les rapports.
        try:
            text = await self.rest.get_text("/reports/getOffice365ActiveUserDetail(period='D180')",
                                            requires="Reports.Read.All")
            activity = parse_activity_report(text)
        except Exception:
            activity = None
        if activity is not None:
            for u in users:
                key = (u.get("userPrincipalName") or "").lower()
                if key in activity:
                    u["signInActivity"] = {"lastSignInDateTime": activity_to_iso(activity[key])}
        return users

    async def registrations(self):
        try:
            return await self.rest.get_all(
                "/reports/authenticationMethods/userRegistrationDetails", {"$top": "999"},
                requires="AuditLog.Read.All (ou Reports.Read.All) + licence Entra ID P1")
        except DataUnavailable:
            return await self._registrations_from_methods()

    async def _registrations_from_methods(self) -> list[dict]:
        """Sans P1 : méthodes d'authentification de chaque utilisateur (UserAuthenticationMethod.Read.All)."""
        users = [u for u in await self.users() if u.get("userType", "Member") == "Member"]
        try:
            admins = {(m.get("userPrincipalName") or "").lower() for r in await self.roles() for m in r["members"]}
        except DataUnavailable:
            admins = set()
        bodies = await self.rest.batch_get(
            [f"/users/{safe_id(u['id'])}/authentication/methods" for u in users],
            requires="UserAuthenticationMethod.Read.All")
        return [
            registration_from_methods(u["userPrincipalName"], b.get("value", []),
                                      u["userPrincipalName"].lower() in admins)
            for u, b in zip(users, bodies) if b is not None
        ]

    async def roles(self):
        roles = await self.rest.get_all("/directoryRoles", requires="RoleManagement.Read.Directory")

        async def members(role):
            ms = await self.rest.get_all(f"/directoryRoles/{role['id']}/members", requires="RoleManagement.Read.Directory")
            return {"displayName": role.get("displayName"), "members": [
                {"userPrincipalName": m.get("userPrincipalName"), "displayName": m.get("displayName"),
                 "type": m.get("@odata.type")} for m in ms]}

        return list(await asyncio.gather(*(members(r) for r in roles)))

    async def ca_policies(self):
        return await self.rest.get_all("/identity/conditionalAccess/policies",
                                       requires="Policy.Read.All + licence Entra ID P1")

    async def security_defaults(self):
        return await self.rest.get("/policies/identitySecurityDefaultsEnforcementPolicy", requires="Policy.Read.All")

    async def secure_score(self):
        data = await self.rest.get("/security/secureScores", {"$top": "1"}, requires="SecurityEvents.Read.All")
        return (data.get("value") or [None])[0]

    async def score_profiles(self):
        try:
            return await self.rest.get_all("/security/secureScoreControlProfiles", requires="SecurityEvents.Read.All")
        except DataUnavailable:
            return []

    async def applications(self):
        return await self.rest.get_all(
            "/applications", {"$select": "id,displayName,appId,passwordCredentials,keyCredentials", "$top": "999"},
            requires="Application.Read.All")
