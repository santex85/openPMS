import structlog

from app.worker import celery_app


@celery_app.task(name="ping")
def ping() -> dict[str, str]:
    log = structlog.get_logger()
    log.info("ping_task_executed")
    return {"status": "pong"}
