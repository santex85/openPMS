"""Resume: skip guests already in StateStore."""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from scripts.migrate.core.adapter import SourceAdapter
from scripts.migrate.core.audit_log import MigrationAuditLogger
from scripts.migrate.core.pipeline import MigrationPipeline
from scripts.migrate.core.state import StateStore
from scripts.migrate.models.records import GuestRecord, ValidationResult


class _StubAdapter(SourceAdapter):
    def __init__(self, guests: list[GuestRecord]) -> None:
        self._guests = guests

    def extract_guests(self) -> list[GuestRecord]:
        return self._guests

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


class _GuestOnlyClient:
    def __init__(self) -> None:
        self.create_guest_calls = 0

    def list_guests(self, **kwargs: object) -> dict:
        return {"items": [], "total": 0, "limit": 100, "offset": 0}

    def create_guest(self, body: dict) -> dict:
        self.create_guest_calls += 1
        return {"id": f"00000000-0000-0000-0000-{self.create_guest_calls:012d}"}


def _guest(n: str) -> GuestRecord:
    return GuestRecord(
        external_id=n,
        first_name="A",
        last_name="B",
        email=f"{n}@example.com",
        phone="+10000000000",
    )


def test_resume_skips_processed_guests(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    store = StateStore(db)
    lg = logging.getLogger("migrate.test.resume")
    lg.handlers.clear()
    lg.addHandler(logging.NullHandler())
    lg.propagate = False
    audit = MigrationAuditLogger(lg)
    run_id = "testrun01"
    store.ensure_run(run_id, "Preno", "prop")
    store.mark_processed(run_id, "guest", "g1", openpms_id="u1", result="created")
    store.mark_processed(run_id, "guest", "g2", openpms_id="u2", result="created")
    try:
        adapter = _StubAdapter([_guest("g1"), _guest("g2"), _guest("g3")])
        client = _GuestOnlyClient()
        pipe = MigrationPipeline(
            adapter,
            client,  # type: ignore[arg-type]
            property_id=UUID(int=7),
            property_label="t",
            source_name="Preno",
            dry_run=False,
            audit=audit,
            state=store,
            resume=True,
            source_paths=[],
        )
        pipe._run_id = run_id
        pipe._stage_guests(client, adapter.extract_guests())  # type: ignore[arg-type]
        assert client.create_guest_calls == 1
        st = pipe.report_generator.data.stages["guests"]
        assert st.skipped == 2 and st.created == 1
    finally:
        store.close()
