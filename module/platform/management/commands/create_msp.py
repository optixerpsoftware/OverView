from getpass import getpass

from django.core.management.base import BaseCommand, CommandError

from module.platform.service import SignupError, create_msp


class Command(BaseCommand):
    help = "Crée l'espace d'un MSP : schéma PostgreSQL, domaine <slug>.<TENANT_BASE_DOMAIN> et premier administrateur."

    def add_arguments(self, p):
        p.add_argument("slug")
        p.add_argument("--name", required=True)
        p.add_argument("--admin", required=True, help="nom d'utilisateur du premier administrateur")
        p.add_argument("--email", default="")
        p.add_argument("--domain", action="append", default=[], help="domaine supplémentaire (répétable)")

    def handle(self, *a, **o):
        pw = getpass("Mot de passe de l'administrateur : ")
        try:
            r = create_msp(name=o["name"], slug=o["slug"], admin_username=o["admin"], admin_password=pw,
                           admin_email=o["email"], extra_domains=tuple(o["domain"]))
        except SignupError as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(f"MSP créé : {r['name']} → schéma {r['schema']}, adresse {r['domain']}"))
