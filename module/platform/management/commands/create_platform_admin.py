from getpass import getpass

from django.core.management.base import BaseCommand, CommandError

from module.platform.console import PlatformError, create_platform_admin


class Command(BaseCommand):
    help = "Crée un super-administrateur de la plateforme (console sur platform.<domaine>). Affiche sa clé TOTP une seule fois."

    def add_arguments(self, p):
        p.add_argument("username")
        p.add_argument("--email", default="")

    def handle(self, *a, **o):
        pw = getpass("Mot de passe : ")
        if pw != getpass("Confirmer : "):
            raise CommandError("Les mots de passe ne correspondent pas.")
        try:
            r = create_platform_admin(o["username"], pw, o["email"])
        except PlatformError as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(f"Super-administrateur « {r['username']} » créé."))
        self.stdout.write("Ajoute ce compte dans ton application d'authentification (affiché UNE seule fois) :")
        self.stdout.write(f"  Clé : {r['secret']}")
        self.stdout.write(f"  URI : {r['otpauth']}")
        try:
            import qrcode  # facultatif
            q = qrcode.QRCode(border=1)
            q.add_data(r["otpauth"])
            q.print_ascii(invert=True)
        except Exception:  # noqa: BLE001
            pass
