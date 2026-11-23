import os

from celery import Celery
from celery.signals import beat_init


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("quakelens")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@beat_init.connect
def queue_initial_import(**kwargs):
    app.send_task("earthquakes.tasks.refresh_catalogue")
