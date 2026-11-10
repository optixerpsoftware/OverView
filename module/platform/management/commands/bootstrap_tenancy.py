from django.core.management.base import BaseCommand, CommandError

from module.platform.models import Domain, Msp
from module.platform.service import SignupError, adopt_public_data, ensure_public_tenant
from shared_kernel.tenancy import PUBLIC, clear_domain_cache, use_schema


class Command(BaseCommand):
    help = ("Passage au multi-MSP d'une installation existante : crée le tenant public, le MSP d'origine et y copie les "
            "données restées dans le schéma public. Sans risque si relancée : ne fait rien si le MSP existe déjà.")

    def add_arguments(self, p):
        p.add_argument("--slug", required=True)
        p.add_argument("--name", required=True)
        p.add_argument("--domain", action="append", default=[], help="adresses qui mènent à ce MSP (répétable)")
        p.add_argument("--platform-domain", action="append", default=[], help="adresses de la plateforme (inscription)")
        p.add_argument("--dry-run", action="store_true")

    def handle(self, *a, **o):
        ensure_public_tenant(tuple(o["platform_domain"]))
        schema = o["slug"].replace("-", "_")
        with use_schema(PUBLIC):
            exists = Msp.objects.filter(schema_name=schema).exists()
            if exists:
                self.stdout.write(f"Le MSP {schema} existe déjà : reprise des données si son schéma est encore vide.")
            elif o["dry_run"]:
                self.stdout.write(f"Créerait le MSP {schema} et copierait les données publiques.")
                return
            msp = Msp.objects.get(schema_name=schema) if exists else Msp(schema_name=schema, name=o["name"], slug=o["slug"])
            if not exists:
                msp.save()
            for i, d in enumerate(o["domain"]):
                Domain.objects.get_or_create(domain=d.lower(), defaults={"tenant": msp, "is_primary": i == 0})
        clear_domain_cache()
        try:
            r = adopt_public_data(schema)
        except SignupError as e:
            raise CommandError(str(e))
        total = sum(r["copied"].values())
        self.stdout.write(self.style.SUCCESS(f"MSP {schema} créé ; {total} lignes reprises dans {len(r['copied'])} tables."))
        for t, n in sorted(r["copied"].items()):
            if n:
                self.stdout.write(f"  {t} : {n}")
