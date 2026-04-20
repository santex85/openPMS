"""Structured migration audit log (TZ §7: migration.log format)."""

from __future__ import annotations

import logging
def setup_migration_audit_logger(
    *,
    log_file: str | None = "migration.log",
    stream: bool = True,
    level: int = logging.INFO,
) -> logging.Logger:
    """
    Logger name ``openpms.migration.audit``.
    Each log line message body: ``stage | entity | source_id | result | details``
    Formatter prepends timestamp: ``%(asctime)s | %(message)s``.
    """
    log = logging.getLogger("openpms.migration.audit")
    log.handlers.clear()
    log.setLevel(level)
    log.propagate = False
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if stream:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    return log


class MigrationAuditLogger:
    """Thin wrapper: one structured event per call."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("openpms.migration.audit")

    def event(
        self,
        stage: str,
        entity: str,
        source_id: str,
        result: str,
        details: str = "",
        *,
        level: int = logging.INFO,
    ) -> None:
        sid = (source_id or "").replace("\n", " ")[:500]
        det = (details or "").replace("\n", " ")[:2000]
        msg = f"{stage} | {entity} | {sid} | {result} | {det}"
        self._log.log(level, msg)
