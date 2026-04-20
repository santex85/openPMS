"""Structured audit log format."""

from __future__ import annotations

import logging
from pathlib import Path

from scripts.migrate.core.audit_log import MigrationAuditLogger, setup_migration_audit_logger


def test_audit_log_file_format(tmp_path: Path) -> None:
    log_path = tmp_path / "mig.log"
    lg = setup_migration_audit_logger(
        log_file=str(log_path),
        stream=False,
        level=logging.INFO,
    )
    MigrationAuditLogger(lg).event(
        "guests",
        "guest",
        "ext-1",
        "created",
        "openpms-uuid",
    )
    text = log_path.read_text(encoding="utf-8")
    assert "guests | guest | ext-1 | created | openpms-uuid" in text
