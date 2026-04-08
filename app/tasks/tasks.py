"""Celery autodiscovery entrypoint for the ``app.tasks`` package (``tasks`` submodule).

``autodiscover_tasks(["app.tasks"])`` imports ``app.tasks.tasks``; import task modules here.
"""

from app.tasks import channex_ari_sync as _channex_ari_sync  # noqa: F401
from app.tasks import channex_incremental_ari as _channex_incremental_ari  # noqa: F401
from app.tasks import channex_webhook_task as _channex_webhook_task  # noqa: F401
from app.tasks import ping as _ping  # noqa: F401
