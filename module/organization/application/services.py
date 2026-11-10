from __future__ import annotations

from typing import Callable, Iterable, Optional
import uuid

from django.db import transaction

from module.organization.domain.entities import OrganizationEntity
from module.organization.domain.repositories import OrganizationRepository
from module.organization.infrastructure.repositories import DjangoOrganizationRepository


class OrganizationMissing(Exception):
    pass


class OrganizationService:
    """Cas d'usage de l'agrégat Organization."""

    def __init__(self, repo: Optional[OrganizationRepository] = None):
        self.repo = repo or DjangoOrganizationRepository()

    def create(self, name: str, description: Optional[str] = None) -> OrganizationEntity:
        from module.platform.limits import check
        check("organizations")                 # limite d'organisations clientes de l'abonnement
        return self.repo.add(OrganizationEntity(name=name, description=description))


    @transaction.atomic
    def delete(self, org_id: uuid.UUID,
               purgers: Iterable[Callable[[uuid.UUID], int]] = ()) -> dict:
        """Supprime l'organisation et, via les `purgers` fournis par les autres
        contextes (connecteurs, Outlook…), toutes les données qui s'y rattachent.
        Les données locales seulement : rien n'est supprimé dans Microsoft 365."""
        if not self.repo.exists(org_id):
            raise OrganizationMissing(str(org_id))
        removed = {p.__qualname__: p(org_id) for p in purgers}
        self.repo.delete(org_id)
        return removed
