"""Lanceur de tests multi-MSP : crée le tenant public et un MSP « test » (domaine testserver), puis exécute tous les
tests dans le schéma de ce MSP, comme une requête réelle."""
from django.conf import settings
from django.test.runner import DiscoverRunner


class TenantTestRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        config = super().setup_databases(**kwargs)
        from django.db import connection
        from module.platform.models import Domain, Msp
        from shared_kernel.tenancy import clear_domain_cache, set_default_schema
        connection.set_schema_to_public()
        public, _ = Msp.objects.get_or_create(schema_name="public", defaults={"name": "Plateforme", "slug": "platform"})
        Domain.objects.get_or_create(domain="platform.localhost", defaults={"tenant": public, "is_primary": True})
        if not Msp.objects.filter(schema_name="test").exists():
            Msp(schema_name="test", name="MSP de test", slug="test").save()      # crée et migre le schéma
        test = Msp.objects.get(schema_name="test")
        Domain.objects.get_or_create(domain="testserver", defaults={"tenant": test, "is_primary": True})
        clear_domain_cache()
        settings.TENANT_FALLBACK_SCHEMA = "test"
        set_default_schema("test")
        connection.set_schema("test")
        return config
