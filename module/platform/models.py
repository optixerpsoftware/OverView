from django.db import models
from django_tenants.models import DomainMixin, TenantMixin


class Msp(TenantMixin):
    """Un MSP abonné à OverView : ses organisations clientes, comptes et données vivent dans son propre schéma."""
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=40, unique=True)
    app_url = models.CharField(max_length=300, blank=True, default="")   # ex. https://acme.overview.ca (retours, liens)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    auto_create_schema = True     # le schéma est créé et migré à l'enregistrement
    auto_drop_schema = False      # jamais de suppression automatique des données d'un MSP

    class Meta:
        db_table = 'platform_msp'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # La création du schéma lance ses migrations en changeant le schéma de la connexion : on suspend, le temps
        # de l'opération, le schéma imposé par la requête en cours (sinon les tables iraient dans le mauvais schéma).
        from shared_kernel.tenancy import connection_controls_schema
        with connection_controls_schema():
            super().save(*args, **kwargs)


class Domain(DomainMixin):
    """Adresse qui mène à un MSP (ex. acme.overview.ca, ou localhost en développement)."""

    class Meta:
        db_table = 'platform_domain'
