"""MIG-17: on_conflict=fail stops guest stage."""

from __future__ import annotations

import logging
from uuid import UUID

import pytest

from scripts.migrate.core.adapter import SourceAdapter
from scripts.migrate.core.audit_log import MigrationAuditLogger
from scripts.migrate.core.pipeline import MigrationPipeline, OnConflictFailError
from scripts.migrate.models.records import GuestRecord, ValidationResult


class _StubAdapter(SourceAdapter):
    def extract_guests(self) -> list:
        return []

    def extract_room_types(self) -> list:
        return []

    def extract_rooms(self) -> list:
        return []

    def extract_rate_plans(self) -> list:
        return []

    def extract_bookings(self) -> list:
        return []

    def validate(self) -> ValidationResult:
        return ValidationResult(ok=True)


class _PreflightDupClient:
    def list_guests(self, **kwargs: object) -> dict:
        return {
            "items": [
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "email": "exists@example.com",
                },
            ],
            "total": 1,
            "limit": 10,
            "offset": 0,
        }

    def create_guest(self, body: dict) -> dict:
        raise AssertionError("should not POST when preflight matches")


def test_fail_on_preflight_duplicate() -> None:
    lg = logging.getLogger("migrate.test.ocf")
    lg.handlers.clear()
    lg.addHandler(logging.NullHandler())
    lg.propagate = False
    audit = MigrationAuditLogger(lg)
    g = GuestRecord(
        external_id="gx",
        first_name="A",
        last_name="B",
        email="exists@example.com",
        phone="+10000000000",
    )
    client = _PreflightDupClient()
    pipe = MigrationPipeline(
        _StubAdapter(),
        client,  # type: ignore[arg-type]
        property_id=UUID(int=2),
        property_label="t",
        source_name="Preno",
        dry_run=False,
        on_conflict="fail",
        audit=audit,
        state=None,
        resume=False,
        source_paths=[],
    )
    with pytest.raises(OnConflictFailError):
        pipe._stage_guests(client, [g])  # type: ignore[arg-type]
