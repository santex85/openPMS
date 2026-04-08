"""Celery worker application (separate process from FastAPI / uvicorn)."""

from celery import Celery

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
    )
    return app


celery_app = create_celery_app()
celery_app.autodiscover_tasks(["app.tasks"])
