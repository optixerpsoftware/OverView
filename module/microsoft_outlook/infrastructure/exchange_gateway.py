"""Passerelle vers l'API d'administration Exchange Online (cmdlets exposées en REST : `InvokeCommand`).

Nécessite, côté Azure : la permission d'application « Office 365 Exchange Online → Exchange.ManageAsApp » (consentement
administrateur) ET le rôle d'annuaire « Exchange Administrator » sur le principal de service de l'application.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from shared_kernel.graph_rest import DataUnavailable, GraphError, MAX_RETRIES, _retry_after

EXO_SCOPE = ["https://outlook.office365.com/.default"]
EXO_BASE = "https://outlook.office365.com/adminapi/beta"
REQUIRES = ("Exchange.ManageAsApp (Office 365 Exchange Online) + rôle « Exchange Administrator » "
            "sur le principal de service de l'application")
_TIMEOUT = httpx.Timeout(120.0, connect=15.0)


class ExchangeGateway:
    def __init__(self, graph, tenant_id: str):
        self.graph = graph
        self.tenant_id = str(tenant_id)

    async def invoke(self, cmdlet: str, params: Optional[dict] = None) -> list[dict]:
        token = await asyncio.to_thread(self.graph.access_token, EXO_SCOPE)
        headers = {
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "X-AnchorMailbox": f"APP:SystemMailbox{{bb558c35-97f1-4cb9-8ff7-d53741dc928c}}@{self.tenant_id}",
        }
        body = {"CmdletInput": {"CmdletName": cmdlet, "Parameters": params or {}}}
        url = f"{EXO_BASE}/{self.tenant_id}/InvokeCommand"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            for attempt in range(MAX_RETRIES + 1):
                r = await http.post(url, headers=headers, json=body)
                if r.status_code not in (429, 503) or attempt == MAX_RETRIES:
                    break
                await asyncio.sleep(min(_retry_after(r, attempt), 30))
        if r.status_code in (401, 403):
            raise DataUnavailable("Accès refusé par Exchange Online (permission ou rôle manquant).", REQUIRES)
        if r.status_code >= 400:
            try:
                err = r.json().get("error", {})
                msg = err.get("message") or (err.get("innererror") or {}).get("message") or r.text
            except ValueError:
                msg = r.text
            raise GraphError(r.status_code, f"Exchange : {msg}")
        data: Any = r.json() if r.content else {}
        return data.get("value", []) if isinstance(data, dict) else []

    async def message_trace(self, *, start: str, end: str, sender: str = "", recipient: str = "",
                            status: str = "", limit: int = 1000) -> list[dict]:
        p: dict = {"StartDate": start, "EndDate": end, "ResultSize": limit}
        if sender:
            p["SenderAddress"] = sender
        if recipient:
            p["RecipientAddress"] = recipient
        if status:
            p["Status"] = status
        return await self.invoke("Get-MessageTraceV2", p)

    async def quarantine(self, *, start: str, end: str, type_: str = "", page_size: int = 500) -> list[dict]:
        p: dict = {"StartReceivedDate": start, "EndReceivedDate": end, "PageSize": page_size}
        if type_:
            p["Type"] = type_
        return await self.invoke("Get-QuarantineMessage", p)

    async def quarantine_one(self, identity: str) -> Optional[dict]:
        rows = await self.invoke("Get-QuarantineMessage", {"Identity": identity})
        return rows[0] if rows else None

    async def release(self, identity: str, *, release_to_all: bool, allow_sender: bool = False) -> None:
        p: dict = {"Identity": identity, "ReleaseToAll": True} if release_to_all else {"Identity": identity, "ReleaseToAll": False}
        if allow_sender:
            p["AllowSender"] = True
        await self.invoke("Release-QuarantineMessage", p)
