"""Choisit le MSP (schéma) d'après l'adresse de la requête et le fixe pour toute la durée de la requête.

Compatible sync et async : sous Daphne, la vue async et les accès base qu'elle déclenche (sync_to_async) héritent
du schéma par la variable de contexte ; il n'est jamais partagé avec une autre requête traitée en parallèle.
"""
from __future__ import annotations

from asgiref.sync import iscoroutinefunction, markcoroutinefunction, sync_to_async
from django.conf import settings
from django.http import JsonResponse

from shared_kernel.scope import ALLOWED_ORGS
from shared_kernel.tenancy import CURRENT_SCHEMA, PUBLIC, schema_for_host


def _not_found():
    return JsonResponse({"detail": "Espace OverView introuvable pour cette adresse."}, status=404)


class TenantMiddleware:
    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async:
            markcoroutinefunction(self)

    @staticmethod
    def _prepare(request, schema):
        request.tenant_schema = schema
        if schema == PUBLIC:
            request.urlconf = settings.PUBLIC_SCHEMA_URLCONF

    def __call__(self, request):
        if self.is_async:
            return self.__acall__(request)
        schema = schema_for_host(request.get_host())
        if not schema:
            return _not_found()
        self._prepare(request, schema)
        token, scope = CURRENT_SCHEMA.set(schema), ALLOWED_ORGS.set(None)
        try:
            return self.get_response(request)
        finally:
            ALLOWED_ORGS.reset(scope)
            CURRENT_SCHEMA.reset(token)

    async def __acall__(self, request):
        schema = await sync_to_async(schema_for_host, thread_sensitive=True)(request.get_host())
        if not schema:
            return _not_found()
        self._prepare(request, schema)
        token, scope = CURRENT_SCHEMA.set(schema), ALLOWED_ORGS.set(None)
        try:
            return await self.get_response(request)
        finally:
            ALLOWED_ORGS.reset(scope)
            CURRENT_SCHEMA.reset(token)
