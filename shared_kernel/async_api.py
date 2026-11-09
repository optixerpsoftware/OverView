"""Vues HTTP natives async (servies par Daphne) avec authentification JWT.

Pourquoi pas DRF : `APIView` est synchrone, et appeler du code async (SDK Microsoft Graph) depuis une vue synchrone
impose `async_to_sync` — une boucle d'événements et un thread par requête. Ici la vue est une coroutine : les appels
Graph sont « awaités » directement et le thread de la requête n'est jamais bloqué pendant l'attente réseau.

Compatibilité avec le frontend : mêmes URL, mêmes statuts, mêmes corps JSON (`{"detail": ...}` pour les erreurs),
même en-tête `Authorization: Bearer <jwt>` et mêmes jetons SimpleJWT que le reste de l'API.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from asgiref.sync import sync_to_async
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.utils.encoders import JSONEncoder
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken, TokenError

log = logging.getLogger(__name__)
_BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

__all__ = ["AsyncAPIView", "Response", "status", "no_store"]


def Response(data: Any = None, status: int = 200, headers: Optional[dict] = None) -> HttpResponse:  # noqa: N802
    """Réponse JSON (UUID, dates, Decimal… sérialisés comme DRF). 204 : corps vide."""
    if data is None and status == 204:
        resp: HttpResponse = HttpResponse(status=204)
    else:
        resp = JsonResponse(data, encoder=JSONEncoder, safe=False, status=status, json_dumps_params={"ensure_ascii": False})
    for k, v in (headers or {}).items():
        resp[k] = v
    return resp


def no_store(resp: HttpResponse) -> HttpResponse:
    """Interdit toute mise en cache (mots de passe générés, codes d'accès, données sensibles)."""
    resp["Cache-Control"] = "no-store"
    resp["Pragma"] = "no-cache"
    return resp


@method_decorator(csrf_exempt, name="dispatch")   # authentification par jeton Bearer, pas de cookie de session
class AsyncAPIView(View):
    """Base : authentification JWT, corps JSON dans `request.data`, paramètres d'URL dans `request.query_params`.

    Les gestionnaires (`async def get/post/put/patch/delete`) reçoivent la requête et renvoient `Response(...)`.
    Surcharge `handle_exception(exc)` pour traduire les exceptions métier en réponses ; appelle `super()` en dernier."""

    authentication_required = True
    _auth = JWTAuthentication()

    async def dispatch(self, request, *args, **kwargs):
        try:
            handler = getattr(self, request.method.lower(), None) if request.method.lower() in self.http_method_names else None
            if handler is None:
                return Response({"detail": f"Méthode « {request.method} » non autorisée."}, status.HTTP_405_METHOD_NOT_ALLOWED,
                                {"Allow": ", ".join(m.upper() for m in self.http_method_names if hasattr(self, m))})
            if self.authentication_required:
                request.user = await self._authenticate(request)
            request.query_params = request.GET
            request.data = self._parse_body(request)
            return await handler(request, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 : toute erreur devient une réponse JSON maîtrisée
            return self.handle_exception(exc)

    async def _authenticate(self, request):
        try:
            # SimpleJWT lit l'utilisateur en base (ORM synchrone) : exécuté dans le thread dédié aux accès base.
            result = await sync_to_async(self._auth.authenticate, thread_sensitive=True)(request)
        except (InvalidToken, TokenError, AuthenticationFailed) as e:
            raise _Unauthorized(_detail(e)) from e
        if result is None:
            raise _Unauthorized("Informations d'authentification non fournies.")
        return result[0]

    @staticmethod
    def _parse_body(request) -> Any:
        if request.method not in _BODY_METHODS or not request.body:
            return {}
        declared_json = "json" in (request.content_type or "")
        try:
            return json.loads(request.body)
        except (ValueError, UnicodeDecodeError) as e:
            if declared_json:                       # corps déclaré JSON mais invalide : erreur du client
                raise _BadRequest("Corps JSON invalide.") from e
            return {}                               # autre type de contenu (formulaire vide, etc.) : l'API est JSON-only

    @staticmethod
    def _internal_errors() -> tuple:
        """Erreurs levées par la base elle-même (authentification, JSON invalide) : à traiter par `super()`."""
        return (_Unauthorized, _BadRequest)

    def handle_exception(self, exc: Exception) -> HttpResponse:
        if isinstance(exc, _Unauthorized):
            return Response({"detail": str(exc), "code": "not_authenticated"}, status.HTTP_401_UNAUTHORIZED,
                            {"WWW-Authenticate": "Bearer"})
        if isinstance(exc, _BadRequest):
            return Response({"detail": str(exc)}, status.HTTP_400_BAD_REQUEST)
        log.exception("Erreur non gérée dans %s", type(self).__name__, exc_info=exc)
        return Response({"detail": "Erreur interne du serveur."}, status.HTTP_500_INTERNAL_SERVER_ERROR)


class _Unauthorized(Exception):
    pass


class _BadRequest(Exception):
    pass


def _detail(e: Exception) -> str:
    d = getattr(e, "detail", None)
    return str(d.get("detail", d) if isinstance(d, dict) else d or e)


def plain(obj: Any) -> Any:
    """Dataclasses (imbriquées) -> dict/list JSON-compatibles."""
    from dataclasses import asdict, is_dataclass
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: plain(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    return obj


def graph_error_response(e: Exception) -> HttpResponse:
    """Traduit une erreur Microsoft Graph en réponse HTTP lisible par le frontend.

    - permission/licence manquante : 502 + `requires` (nom de la permission à ajouter) ;
    - conflit fonctionnel (400/404/409 de Microsoft, ex. un téléphone du même type existe déjà) : statut et message conservés ;
    - le reste : 502 avec le type d'erreur."""
    from shared_kernel.graph_rest import DataUnavailable, GraphError
    if isinstance(e, DataUnavailable):
        msg = e.reason + (f" Permission requise : {e.requires}." if e.requires else "")
        return Response({"detail": msg, "requires": e.requires}, status.HTTP_502_BAD_GATEWAY)
    if isinstance(e, GraphError) and e.status in (400, 404, 409):
        return Response({"detail": str(e)}, e.status)
    return Response({"detail": f"{type(e).__name__}: {e}"[:500]}, status.HTTP_502_BAD_GATEWAY)


__all__ += ["plain", "graph_error_response"]
