from django.apps import AppConfig


class PlatformConfig(AppConfig):
    """Plateforme SaaS : les MSP (un schéma PostgreSQL chacun) et leurs domaines."""
    name = 'module.platform'
    label = 'platform'
