"""Portée d'un compte OverView : les organisations clientes qu'il peut voir, fixée pour la durée de la requête.

`None` = toutes les organisations du MSP (administrateurs, techniciens « tous les clients »). Sinon un ensemble
d'identifiants d'organisations. Les services filtrent leurs lectures avec `scope_filter`, les vues vérifient chaque
identifiant reçu avec `org_allowed`.
"""
from __future__ import annotations

import contextlib
import contextvars
import uuid
from typing import Iterable, Iterator, Optional

ALLOWED_ORGS: contextvars.ContextVar[Optional[frozenset]] = contextvars.ContextVar("overview_allowed_orgs", default=None)


def allowed_orgs() -> Optional[frozenset]:
    return ALLOWED_ORGS.get()


def org_allowed(org_id) -> bool:
    scope = ALLOWED_ORGS.get()
    if scope is None:
        return True
    try:
        return uuid.UUID(str(org_id)) in scope
    except (ValueError, TypeError):
        return False


def scope_filter(qs, field: str = "org_id"):
    """Restreint un QuerySet aux organisations permises (aucun effet si le compte voit tout)."""
    scope = ALLOWED_ORGS.get()
    return qs if scope is None else qs.filter(**{f"{field}__in": list(scope)})


def scope_rows(rows: Iterable[dict], key: str = "org_id") -> list[dict]:
    scope = ALLOWED_ORGS.get()
    return list(rows) if scope is None else [r for r in rows if r.get(key) in scope]


@contextlib.contextmanager
def use_scope(orgs: Optional[Iterable]) -> Iterator[None]:
    token = ALLOWED_ORGS.set(None if orgs is None else frozenset(uuid.UUID(str(o)) for o in orgs))
    try:
        yield
    finally:
        ALLOWED_ORGS.reset(token)


class OrgScopedQuerysetMixin:
    """Pour les ViewSets DRF : liste et détail limités aux organisations permises (objet hors portée → 404)."""
    scope_field = "org_id"

    def get_queryset(self):
        return scope_filter(super().get_queryset(), self.scope_field)
