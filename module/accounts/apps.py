from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Comptes OverView : rôles (lecture seule, technicien, administrateur) et MFA à la connexion."""
    name = 'module.accounts'
    label = 'accounts'

    def ready(self):
        from shared_kernel.tenancy import install_token_claim
        install_token_claim()
