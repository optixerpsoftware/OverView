"""Graph : appareils récemment inscrits (avec propriétaires), administrateurs, désactivation / réactivation de compte."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from module.microsoft_compliance.infrastructure.graph_gateway import GraphComplianceGateway
from shared_kernel.graph_rest import safe_id

PRIVILEGED = {
    "Global Administrator", "Privileged Role Administrator", "Security Administrator", "Exchange Administrator",
    "SharePoint Administrator", "User Administrator", "Helpdesk Administrator", "Conditional Access Administrator",
    "Application Administrator", "Cloud Application Administrator", "Authentication Administrator",
    "Privileged Authentication Administrator", "Billing Administrator", "Intune Administrator", "Teams Administrator",
}


class GraphProtectionGateway(GraphComplianceGateway):
    async def recent_devices(self, hours: int = 26) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        devices = await self.rest.get_all("/devices", {
            "$filter": f"registrationDateTime ge {since}", "$count": "true", "$top": "999",
            "$select": "id,displayName,registrationDateTime,trustType,operatingSystem"}, requires="Device.Read.All")
        if devices:
            owners = await self.rest.batch_get(
                [f"/devices/{safe_id(d['id'])}/registeredOwners?$select=id,userPrincipalName" for d in devices],
                requires="Device.Read.All")
            for d, o in zip(devices, owners):
                d["owners"] = [x for x in (o or {}).get("value", []) if x.get("userPrincipalName")]
        return devices

    async def admin_upns(self) -> set[str]:
        return {(m.get("userPrincipalName") or "").lower() for r in await self.roles()
                if r.get("displayName") in PRIVILEGED for m in r["members"] if m.get("userPrincipalName")}

    async def disable_and_revoke(self, user_id: str) -> None:
        uid = safe_id(user_id)
        await self.rest.patch(f"/users/{uid}", {"accountEnabled": False}, requires="User.ReadWrite.All")
        await self.rest.post(f"/users/{uid}/revokeSignInSessions", {}, requires="User.ReadWrite.All")

    async def enable(self, user_id: str) -> None:
        await self.rest.patch(f"/users/{safe_id(user_id)}", {"accountEnabled": True}, requires="User.ReadWrite.All")

    async def internal_domains(self) -> list[str]:
        rows = await self.rest.get_all("/domains", {"$select": "id,isVerified"}, requires="Domain.Read.All (ou Directory.Read.All)")
        return [d["id"].lower() for d in rows if d.get("isVerified")]

    async def sent_messages(self, tenant_id: str, minutes: int) -> list[dict]:
        """Suivi des messages Exchange (Get-MessageTraceV2) sur la fenêtre : une ligne par destinataire.
        Exchange publie le suivi avec quelques minutes de retard."""
        from module.microsoft_outlook.domain.mailflow import parse_trace
        from module.microsoft_outlook.infrastructure.exchange_gateway import ExchangeGateway
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes + 15)   # marge pour le retard de publication
        rows = await ExchangeGateway(self.rest.graph, tenant_id).message_trace(
            start=start.strftime("%Y-%m-%dT%H:%M:%SZ"), end=end.strftime("%Y-%m-%dT%H:%M:%SZ"), limit=5000)
        out = []
        for r in rows:
            m = parse_trace(r)
            out.append({"sender": m.sender, "recipient": m.recipient, "at": m.received, "message_id": m.message_id,
                        "subject": m.subject})
        return out

    # ---- règles Outlook, transferts de boîte, applis tierces
    async def disable_rule(self, upn: str, rule_id: str) -> None:
        await self.rest.patch(f"/users/{safe_id(upn)}/mailFolders/inbox/messageRules/{safe_id(rule_id)}",
                              {"isEnabled": False}, requires="MailboxSettings.ReadWrite")

    async def mailbox_forwarding(self, tenant_id: str) -> list[dict]:
        from module.microsoft_outlook.infrastructure.exchange_gateway import ExchangeGateway
        rows = await ExchangeGateway(self.rest.graph, tenant_id).invoke("Get-Mailbox", {"ResultSize": "Unlimited"})
        return [{k: r.get(k) for k in ("UserPrincipalName", "PrimarySmtpAddress", "ForwardingSmtpAddress",
                                       "ForwardingAddress", "DeliverToMailboxAndForward")}
                for r in rows if r.get("ForwardingSmtpAddress")]

    async def clear_forwarding(self, tenant_id: str, upn: str) -> None:
        from module.microsoft_outlook.infrastructure.exchange_gateway import ExchangeGateway
        await ExchangeGateway(self.rest.graph, tenant_id).invoke(
            "Set-Mailbox", {"Identity": upn, "ForwardingSmtpAddress": None, "DeliverToMailboxAndForward": False})

    async def third_party_apps(self, tenant_id: str) -> list[dict]:
        """Applis tierces et leurs droits (mêmes règles que la page Hygiène des identités)."""
        from module.microsoft_identity.domain import hygiene as hy
        from module.microsoft_identity.infrastructure.hygiene_gateway import GraphHygieneGateway
        h = GraphHygieneGateway(self.rest.graph)
        own = await h.organization_id() or tenant_id
        sps, grants = await h.service_principals(), await h.grants()
        third = [sp for sp in sps if hy.is_third_party(sp, own)]
        assignments = await h.assignments([sp["id"] for sp in third]) if third else {}
        ids = sorted({a["resourceId"] for rows in assignments.values() for a in rows}
                     | {g["resourceId"] for g in grants if g.get("clientId") in {sp["id"] for sp in third}})
        roles, names = await h.resources(ids) if ids else ({}, {})
        return hy.app_consents(sps, grants, assignments, roles, names, own)

    async def disable_app(self, sp_id: str) -> None:
        await self.rest.patch(f"/servicePrincipals/{safe_id(sp_id)}", {"accountEnabled": False},
                              requires="Application.ReadWrite.All")

    async def user_id(self, upn: str) -> str:
        return (await self.rest.get(f"/users/{safe_id(upn)}", {"$select": "id"}, requires="User.Read.All")).get("id", "")
