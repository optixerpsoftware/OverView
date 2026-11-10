"""Moteur PostgreSQL multi-tenant : django-tenants + schéma de la requête réappliqué avant chaque requête SQL.

Sous ASGI, plusieurs requêtes (de MSP différents) partagent le même fil et la même connexion : on ne se fie donc pas
au dernier `set_tenant()` de la connexion, mais au schéma porté par la requête courante (shared_kernel.tenancy).
"""
from django_tenants.postgresql_backend.base import DatabaseWrapper as TenantDatabaseWrapper

from shared_kernel.tenancy import PUBLIC, requested_schema


class DatabaseWrapper(TenantDatabaseWrapper):
    def _cursor(self, name=None):
        wanted = requested_schema()
        if wanted and wanted != self.schema_name:
            if wanted == PUBLIC:
                self.set_schema_to_public()
            else:
                self.set_schema(wanted)
        return super()._cursor(name)
