"""Multi-tenant (django-tenants) : un schéma PostgreSQL par MSP, choisi PAR REQUÊTE.

Pourquoi une variable de contexte : sous Daphne (ASGI), plusieurs requêtes de MSP différents s'entrelacent sur le même
fil et la même connexion à la base. Un `connection.set_tenant()` posé par l'une serait vu par l'autre. Le schéma du
MSP est donc porté par la requête (`ContextVar`, propagée par asgiref dans `sync_to_async` et par Celery via un en-tête)
et réappliqué par le moteur de base de données AVANT CHAQUE requête SQL (voir `config/db_backend`).
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
from typing import Callable, Iterator, Optional

from django.conf import settings

log = logging.getLogger(__name__)
PUBLIC = "public"
CURRENT_SCHEMA: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("overview_schema", default=None)
# Schéma par défaut quand aucune requête n'en a fixé (tests uniquement : défini par le lanceur de tests).
_DEFAULT: dict = {"schema": None}


def set_default_schema(schema: Optional[str]) -> None:
    _DEFAULT["schema"] = schema


_SUSPENDED: contextvars.ContextVar[bool] = contextvars.ContextVar("overview_schema_suspended", default=False)


def requested_schema() -> Optional[str]:
    if _SUSPENDED.get():
        return None
    return CURRENT_SCHEMA.get() or _DEFAULT["schema"]


@contextlib.contextmanager
def connection_controls_schema() -> Iterator[None]:
    """Laisse django-tenants piloter lui-même le schéma de la connexion (création / migration d'un schéma de MSP)."""
    token = _SUSPENDED.set(True)
    try:
        yield
    finally:
        _SUSPENDED.reset(token)


def current_schema() -> str:
    """Schéma actif : celui de la requête / de la tâche en cours, sinon celui de la connexion."""
    s = requested_schema()
    if s:
        return s
    from django.db import connection
    return getattr(connection, "schema_name", None) or PUBLIC


@contextlib.contextmanager
def use_schema(schema: str) -> Iterator[None]:
    """Exécute un bloc dans le schéma d'un MSP (tâches de fond, commandes, callback de consentement)."""
    from django.db import connection
    token = CURRENT_SCHEMA.set(schema)
    previous = getattr(connection, "schema_name", PUBLIC)
    try:
        if schema == PUBLIC:
            connection.set_schema_to_public()
        else:
            connection.set_schema(schema)
        yield
    finally:
        CURRENT_SCHEMA.reset(token)
        if previous == PUBLIC:
            connection.set_schema_to_public()
        else:
            connection.set_schema(previous)


def tenant_schemas() -> list[str]:
    from module.platform.models import Msp
    from datetime import date

    from django.db.models import Q
    with use_schema(PUBLIC):
        return list(Msp.objects.exclude(schema_name=PUBLIC).filter(is_active=True)
                    .filter(Q(expires_at__isnull=True) | Q(expires_at__gte=date.today()))
                    .values_list("schema_name", flat=True))


def for_each_tenant(fn: Callable[[], dict]) -> dict:
    """Exécute une tâche planifiée dans chaque MSP, l'un après l'autre. Un MSP en erreur n'arrête pas les autres.
    Si la tâche a été lancée depuis un MSP précis (en-tête Celery), elle ne concerne que celui-ci."""
    own = CURRENT_SCHEMA.get()
    if own and own != PUBLIC:
        return {own: fn()}
    out = {}
    for schema in tenant_schemas():
        try:
            with use_schema(schema):
                out[schema] = fn()
        except Exception as e:  # noqa: BLE001
            log.exception("Tâche planifiée en échec pour le MSP %s", schema)
            out[schema] = {"error": f"{type(e).__name__}: {e}"[:300]}
    return out


# ---------------------------------------------------------------- résolution du MSP depuis l'adresse
_DOMAIN_CACHE: dict[str, tuple[Optional[str], float]] = {}
_DOMAIN_TTL = 60.0     # secondes : une suspension ou une expiration prend effet en moins d'une minute


def schema_for_host(host: str) -> Optional[str]:
    """Nom d'hôte (sans port) → schéma du MSP. Cache en mémoire (vidé à chaque création / modification de domaine)."""
    import time
    from module.platform.limits import is_expired
    host = (host or "").split(":")[0].lower().strip(".")
    hit = _DOMAIN_CACHE.get(host)
    if hit and time.monotonic() - hit[1] < _DOMAIN_TTL:
        return hit[0]
    from module.platform.models import Domain
    with use_schema(PUBLIC):
        d = Domain.objects.select_related("tenant").filter(domain=host).first()
        if d is None:
            return getattr(settings, "TENANT_FALLBACK_SCHEMA", None)
        if not d.tenant.is_active or is_expired(d.tenant.expires_at):   # MSP suspendu ou abonnement échu
            _DOMAIN_CACHE[host] = (None, time.monotonic())
            return None
        _DOMAIN_CACHE[host] = (d.tenant.schema_name, time.monotonic())
        return d.tenant.schema_name


def clear_domain_cache() -> None:
    _DOMAIN_CACHE.clear()


# ---------------------------------------------------------------- jetons liés au MSP
TENANT_CLAIM = "tnt"


# TenantJWTAuthentication : voir shared_kernel.tenant_auth (import tardif des modèles d'authentification).


# ---------------------------------------------------------------- Celery : le schéma voyage avec la tâche
SCHEMA_HEADER = "overview_schema"


def install_celery_signals() -> None:
    from celery.signals import before_task_publish, task_postrun, task_prerun

    @before_task_publish.connect(weak=False)
    def _add_schema(headers=None, **_):
        s = requested_schema()
        if headers is not None and s:
            headers[SCHEMA_HEADER] = s

    @task_prerun.connect(weak=False)
    def _enter_schema(task=None, **_):
        s = getattr(task.request, SCHEMA_HEADER, None) or (task.request.get(SCHEMA_HEADER) if hasattr(task.request, "get") else None)
        task.request._overview_token = CURRENT_SCHEMA.set(s) if s else None

    @task_postrun.connect(weak=False)
    def _leave_schema(task=None, **_):
        tok = getattr(task.request, "_overview_token", None)
        if tok is not None:
            CURRENT_SCHEMA.reset(tok)


# ---------------------------------------------------------------- tout jeton émis porte le MSP
def install_token_claim() -> None:
    """`Token.for_user` (connexion, rafraîchissement, tests) ajoute le schéma du MSP courant dans le jeton."""
    from rest_framework_simplejwt.tokens import Token
    if getattr(Token.for_user, "_overview_tenant", False):
        return
    original = Token.for_user.__func__

    def for_user(cls, user):
        token = original(cls, user)
        token[TENANT_CLAIM] = current_schema()
        return token
    for_user._overview_tenant = True
    Token.for_user = classmethod(for_user)
