"""MIG-16: --precheck-bookings avoids POST when external id exists."""

from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

from scripts.migrate.core.adapter import SourceAdapter
from scripts.migrate.core.audit_log import MigrationAuditLogger
from scripts.migrate.core.pipeline import MigrationPipeline
from scripts.migrate.models.records import (
    BookingGuestSnapshot,
    BookingRecord,
    ValidationResult,
)


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


class _PrecheckClient:
    def __init__(self) -> None:
        self.create_booking_calls = 0

    def list_room_types(self, property_id: UUID) -> list[dict]:
        return [{"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "name": "Std"}]

    def list_rate_plans(self, property_id: UUID) -> list[dict]:
        return [{"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "name": "BAR"}]

    def get_booking_by_external_id(self, external_id: str) -> dict | None:
        return {
            "id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "status": "confirmed",
            "external_booking_id": external_id,
        }

    def create_booking(self, body: dict) -> dict:
        self.create_booking_calls += 1
        return {"booking_id": "dddddddd-dddd-dddd-dddd-dddddddddddd"}


def test_precheck_skips_post() -> None:
    lg = logging.getLogger("migrate.test.precheck")
    lg.handlers.clear()
    lg.addHandler(logging.NullHandler())
    lg.propagate = False
    audit = MigrationAuditLogger(lg)
    b = BookingRecord(
        external_id="preno-77",
        check_in=date(2026, 4, 1),
        check_out=date(2026, 4, 3),
        room_type_name="Std",
        rate_plan_name="BAR",
        guest=BookingGuestSnapshot(
            first_name="A",
            last_name="B",
            email="ab@example.com",
            phone="+10000000000",
        ),
        status="confirmed",
    )
    client = _PrecheckClient()
    pipe = MigrationPipeline(
        _StubAdapter(),
        client,  # type: ignore[arg-type]
        property_id=UUID(int=9),
        property_label="t",
        source_name="Preno",
        dry_run=False,
        audit=audit,
        state=None,
        resume=False,
        source_paths=[],
        precheck_bookings=True,
    )
    pipe._stage_bookings(client, [b])  # type: ignore[arg-type]
    assert client.create_booking_calls == 0
    st = pipe.report_generator.data.stages["bookings"]
    assert st.existed == 1
