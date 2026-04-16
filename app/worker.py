"""Celery worker application (separate process from FastAPI / uvicorn)."""

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings


def create_celery_app() -> Celery:
    settings = get_settings()
    app = Celery("openpms", broker=settings.celery_broker_url)
    app.conf.update(
        result_backend=None,
        task_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        # Channex tasks use the default ``celery`` queue so a plain
        # ``celery -A app.worker:celery_app worker`` consumes them. A separate
        # ``channex_ari`` queue is optional for scaled deployments (route tasks there in ops).
    )
    return app


celery_app = create_celery_app()
celery_app.autodiscover_tasks(["app.tasks"])
# Nightly full ARI push (365d) for every active Channex property link — TZ-15 seq 210.
# Time is UTC (not property timezone).
celery_app.conf.beat_schedule = {
    "channex-nightly-ari-sync": {
        "task": "channex_full_ari_sync_all_properties",
        "schedule": crontab(hour=2, minute=0),
    },
    "send-checkin-reminders-daily": {
        "task": "send_checkin_reminders",
        "schedule": crontab(hour=9, minute=0),
    },
}
