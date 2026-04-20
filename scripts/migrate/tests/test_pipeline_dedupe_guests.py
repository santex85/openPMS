"""MIG-15: source dedupe by email before guest POSTs."""

from __future__ import annotations

import logging
from uuid import UUID

from scripts.migrate.core.audit_log import MigrationAuditLogger
from scripts.migrate.core.pipeline import MigrationPipeline
from scripts.migrate.core.adapter import SourceAdapter
from scripts.migrate.models.records import GuestRecord, ValidationResult


class _StubAdapter(SourceAdapter):
    def extract_guests(self) -> list[GuestRecord]:
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


class _GuestClient:
    def __init__(self) -> None:
        self.create_guest_calls = 0

    def list_guests(self, **kwargs: object) -> dict:
        return {"items": [], "total": 0, "limit": 100, "offset": 0}

    def create_guest(self, body: dict) -> dict:
        self.create_guest_calls += 1
        return {"id": f"00000000-0000-0000-0000-{self.create_guest_calls:012d}"}


def test_two_guests_same_real_email_one_post() -> None:
    lg = logging.getLogger("migrate.test.dedupe")
    lg.handlers.clear()
    lg.addHandler(logging.NullHandler())
    lg.propagate = False
    audit = MigrationAuditLogger(lg)
    g1 = GuestRecord(
        external_id="a",
        first_name="A",
        last_name="One",
        email="dup@example.com",
        phone="+10000000001",
    )
    g2 = GuestRecord(
        external_id="b",
        first_name="B",
        last_name="Two",
        email="dup@example.com",
        phone="+10000000002",
    )
    client = _GuestClient()
    pipe = MigrationPipeline(
        _StubAdapter(),
        client,  # type: ignore[arg-type]
        property_id=UUID(int=3),
        property_label="t",
        source_name="Preno",
        dry_run=False,
        audit=audit,
        state=None,
        resume=False,
        source_paths=[],
    )
    pipe._stage_guests(client, [g1, g2])  # type: ignore[arg-type]
    assert client.create_guest_calls == 1
    st = pipe.report_generator.data.stages["guests"]
    assert st.total == 2
    assert st.skipped == 1
    assert st.created == 1
