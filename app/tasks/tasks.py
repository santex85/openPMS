"""Celery autodiscovery entrypoint for the ``app.tasks`` package (``tasks`` submodule).

``autodiscover_tasks(["app.tasks"])`` imports ``app.tasks.tasks``; import task modules here.
"""

from app.tasks import ping as _ping  # noqa: F401
