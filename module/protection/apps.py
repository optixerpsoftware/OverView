from django.apps import AppConfig


class ProtectionConfig(AppConfig):
    """Protection automatique : limites de fréquence par organisation et réponse automatique (désactivation)."""
    name = 'module.protection'
    label = 'protection'
