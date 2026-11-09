from __future__ import annotations

from shared_kernel.graph_rest import BASE, BETA, GraphError, GraphRest, safe_id

_PATH = "/policies/deviceRegistrationPolicy"
_READ = "Policy.Read.All"
_WRITE = "Policy.ReadWrite.DeviceConfiguration"


class GraphDevicePolicyGateway:
    """Lecture / mise à jour de la politique d'inscription des appareils (v1.0, repli sur beta)."""

    def __init__(self, graph):
        self.rest = GraphRest(graph)
        self._base = BASE

    async def get(self) -> dict:
        try:
            return await self.rest.get_from(BASE, _PATH, requires=_READ)
        except GraphError as e:
            if e.status not in (400, 404):
                raise
        self._base = BETA  # endpoint absent de la v1.0 sur ce tenant : repli sur beta
        return await self.rest.get_from(BETA, _PATH, requires=_READ)

    async def put(self, raw: dict) -> None:
        body = {k: v for k, v in raw.items() if not k.startswith("@odata.context")}
        await self.rest.put(_PATH, body, requires=_WRITE, base=self._base)

    async def groups(self, query: str = "", limit: int = 200) -> list[dict]:
        """Groupes de sécurité du tenant (Group.Read.All). Filtrés par nom côté serveur applicatif."""
        items = await self.rest.get_all(
            "/groups", {"$select": "id,displayName,securityEnabled,groupTypes", "$top": "100",
                        "$filter": "securityEnabled eq true"},
            requires="Group.Read.All", max_items=limit)
        q = query.strip().lower()
        return [{"id": g["id"], "name": g.get("displayName") or g["id"]} for g in items
                if not q or q in (g.get("displayName") or "").lower()]

    async def group_names(self, ids: list[str]) -> dict[str, str | None]:
        """Nom de chaque groupe (None si illisible : permission manquante ou groupe supprimé)."""
        if not ids:
            return {}
        try:
            bodies = await self.rest.batch_get([f"/groups/{safe_id(i)}?$select=id,displayName" for i in ids],
                                               requires="Group.Read.All")
        except Exception:
            return {i: None for i in ids}
        return {i: (b or {}).get("displayName") for i, b in zip(ids, bodies)}

    async def directory_users(self) -> list[dict]:
        """Comptes membres actifs du tenant (pour choisir les membres d'un groupe)."""
        users = await self.rest.get_all(
            "/users", {"$select": "id,displayName,userPrincipalName,userType,accountEnabled", "$top": "200"},
            requires="User.Read.All", max_items=500)
        return [{"id": u["id"], "name": u.get("displayName") or u["userPrincipalName"], "upn": u["userPrincipalName"]}
                for u in users if u.get("userType", "Member") == "Member" and u.get("accountEnabled", True)]

    async def create_security_group(self, name: str, nickname: str, description: str, member_ids: list[str]) -> dict:
        body = {
            "displayName": name, "description": description, "mailEnabled": False,
            "mailNickname": nickname, "securityEnabled": True,
            "members@odata.bind": [f"https://graph.microsoft.com/v1.0/users/{safe_id(i)}" for i in member_ids],
        }
        res = await self.rest.post("/groups", body, requires="Group.ReadWrite.All")
        return {"id": res["id"], "name": res.get("displayName", name)}
