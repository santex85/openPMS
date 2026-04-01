"""structlog setup for the API process."""

from __future__ import annotations

import logging
import os

import structlog


def configure_logging() -> None:
    """Idempotent-ish configure; safe to call from lifespan."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if os.environ.get("LOG_JSON", "").lower() in ("1", "true", "yes"):
        processors = [
            *shared,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = [
            *shared,
            structlog.dev.ConsoleRenderer(colors=False),
        ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=None),
        cache_logger_on_first_use=False,
    )
