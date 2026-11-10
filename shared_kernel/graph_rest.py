"""Client REST minimal vers Microsoft Graph, partagé par les modules.

Utile quand le SDK est lourd à manier : rapports, endpoints beta, requêtes groupées ($batch).
Le jeton vient de `shared_kernel.api.Graph.access_token()` (mis en cache par azure-identity).
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

import httpx

BASE = "https://graph.microsoft.com/v1.0"
BETA = "https://graph.microsoft.com/beta"
_TIMEOUT = httpx.Timeout(60.0, connect=15.0)
MAX_RETRIES = 5
_SAFE_ID = re.compile(r"^[A-Za-z0-9!_\-=.,$~]{1,512}$")


def _retry_after(r: httpx.Response, attempt: int) -> float:
    try:
        return float(r.headers.get("Retry-After", ""))
    except ValueError:
        return float(2 ** attempt)  # repli : attente exponentielle


class DataUnavailable(Exception):
    """Donnée inaccessible : permission applicative manquante ou licence insuffisante."""

    def __init__(self, reason: str, requires: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.requires = requires


class GraphError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Graph {status}: {message}"[:400])
        self.status = status


def safe_id(value: str) -> str:
    """Valide un identifiant avant de l'insérer dans une URL Graph (anti-injection de chemin)."""
    if not isinstance(value, str) or not _SAFE_ID.match(value) or ".." in value:
        raise ValueError("Identifiant invalide.")
    return value


class GraphRest:
    def __init__(self, graph):
        self.graph = graph

    async def _headers(self) -> dict:
        token = await asyncio.to_thread(self.graph.access_token)  # get_token est synchrone
        return {"Authorization": f"Bearer {token}", "ConsistencyLevel": "eventual"}

    async def _send(self, http: httpx.AsyncClient, method: str, url: str, **kw) -> httpx.Response:
        """Envoie la requête ; en cas de limitation (429) ou d'indisponibilité (503), attend le
        délai demandé par Microsoft (Retry-After) puis réessaie, jusqu'à MAX_RETRIES fois."""
        for attempt in range(MAX_RETRIES + 1):
            r = await http.request(method, url, headers=await self._headers(), **kw)
            if r.status_code not in (429, 503) or attempt == MAX_RETRIES:
                return r
            await asyncio.sleep(min(_retry_after(r, attempt), 30))
        return r  # pragma: no cover

    async def get(self, path: str, params: Optional[dict] = None, *, requires: str = "") -> dict:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            r = await self._send(http, "GET", f"{BASE}{path}", params=params)
        return self._check(r, requires)

    async def get_text(self, path: str, *, requires: str = "") -> str:
        """GET renvoyant du texte (ex. rapports CSV : Graph répond par une redirection vers le fichier)."""
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as http:
            r = await self._send(http, "GET", f"{BASE}{path}")
        if r.status_code >= 400:
            self._check(r, requires)
        return r.text

    async def get_all(self, path: str, params: Optional[dict] = None, *, requires: str = "",
                      max_items: Optional[int] = None) -> list[dict]:
        out: list[dict] = []
        url, q = f"{BASE}{path}", params
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            while url:
                r = await self._send(http, "GET", url, params=q)
                data = self._check(r, requires)
                out.extend(data.get("value", []))
                if max_items is not None and len(out) >= max_items:
                    return out[:max_items]
                url, q = data.get("@odata.nextLink"), None
        return out

    async def put(self, path: str, body: dict, *, requires: str = "", base: str = BASE) -> dict:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            r = await self._send(http, "PUT", f"{base}{path}", json=body)
        return self._check(r, requires, empty_ok=True)

    async def get_from(self, base: str, path: str, *, requires: str = "") -> dict:
        """GET sur une autre racine (ex. BETA pour les endpoints pas encore en v1.0)."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            r = await self._send(http, "GET", f"{base}{path}")
        return self._check(r, requires)

    async def post(self, path: str, body: dict, *, requires: str = "", base: str = BASE) -> dict:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            r = await self._send(http, "POST", f"{base}{path}", json=body)
        return self._check(r, requires, empty_ok=True)

    async def patch(self, path: str, body: dict, *, requires: str = "", base: str = BASE) -> dict:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            r = await self._send(http, "PATCH", f"{base}{path}", json=body)
        return self._check(r, requires, empty_ok=True)

    async def delete(self, path: str, *, requires: str = "", base: str = BASE) -> None:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            r = await self._send(http, "DELETE", f"{base}{path}")
        self._check(r, requires, empty_ok=True)

    async def batch_get(self, paths: list[str], *, requires: str = "", concurrency: int = 4) -> list[Optional[dict]]:
        """Jusqu'à 20 GET par requête groupée. Renvoie, dans l'ordre, le corps JSON de chaque
        réponse (None pour un 404/403 sur cet élément). Les éléments limités (429/503) sont
        retentés ; s'il en reste après MAX_RETRIES, on lève une erreur plutôt que de renvoyer
        un résultat incomplet (qui masquerait des partages lors d'un audit)."""
        results: list[Optional[dict]] = [None] * len(paths)
        sem = asyncio.Semaphore(concurrency)

        async def chunk(http: httpx.AsyncClient, first: int) -> None:
            async with sem:
                pending = list(enumerate(paths[first:first + 20], first))
                for attempt in range(MAX_RETRIES + 1):
                    body = {"requests": [{"id": str(i), "method": "GET", "url": p} for i, p in pending]}
                    r = await self._send(http, "POST", f"{BASE}/$batch", json=body)
                    data = self._check(r, requires)
                    by_id = dict(pending)
                    retry, wait = [], 0
                    for resp in data.get("responses", []):
                        i, st = int(resp["id"]), resp.get("status", 0)
                        if st == 200:
                            results[i] = resp.get("body")
                        elif st in (429, 503):
                            retry.append((i, by_id[i]))
                            wait = max(wait, int((resp.get("headers") or {}).get("Retry-After", 2)))
                    if not retry:
                        return
                    if attempt == MAX_RETRIES:
                        raise GraphError(429, f"{len(retry)} élément(s) toujours limités par Microsoft Graph.")
                    await asyncio.sleep(min(wait or 2 ** attempt, 30))
                    pending = retry

        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            await asyncio.gather(*(chunk(http, first) for first in range(0, len(paths), 20)))
        return results

    @staticmethod
    def _check(r: httpx.Response, requires: str, empty_ok: bool = False) -> dict:
        if r.status_code in (401, 403):
            raise DataUnavailable("Accès refusé par Microsoft Graph (permission manquante).", requires)
        if r.status_code == 400 and "license" in r.text.lower():
            raise DataUnavailable("Licence Microsoft Entra ID insuffisante pour cette donnée.", requires)
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except ValueError:
                msg = r.text
            raise GraphError(r.status_code, msg)
        if empty_ok and not r.content:
            return {}
        return r.json()
