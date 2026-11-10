import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('overview')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
# Les tâches vivent dans interface/tasks.py de chaque module (pas à la racine de l'app).
app.conf.imports = (
    'module.microsoft_outlook.interface.tasks',
    'module.microsoft_compliance.interface.tasks',
    'module.microsoft_identity.interface.tasks',
    'module.microsoft_onedrive.interface.tasks',
    'module.insights.interface.tasks',
    'module.protection.interface.tasks',
)


# Multi-MSP : le schéma du MSP qui publie une tâche voyage dans ses en-têtes et est réactivé par le worker.
from shared_kernel.tenancy import install_celery_signals  # noqa: E402

install_celery_signals()
