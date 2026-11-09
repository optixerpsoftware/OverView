"""Aides pour mélanger code async (appels Graph, vues) et code synchrone (ORM Django, dépôts).

Règle d'architecture : les services applicatifs sont **async d'abord** (`<nom>_async`) et gardent une version
synchrone (`<nom>`) pour Celery, la ligne de commande et les tests. L'ORM Django est synchrone : depuis une
coroutine on l'appelle via `db(...)`, qui l'exécute dans le thread dédié aux accès base (même connexion et même
transaction que le code synchrone), sans bloquer la boucle d'événements.
"""
from __future__ import annotations

from typing import Awaitable, Callable, TypeVar

from asgiref.sync import sync_to_async

T = TypeVar("T")


def db(fn: Callable[..., T]) -> Callable[..., Awaitable[T]]:
    """`await db(repo.get)(id)` : exécute du code synchrone (ORM) sans bloquer la boucle d'événements."""
    return sync_to_async(fn, thread_sensitive=True)
