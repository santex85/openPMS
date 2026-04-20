"""MIG-17: on_conflict=update patches guest after 409."""

from __future__ import annotations

import logging
from uuid import UUID

from scripts.migrate.core.adapter import SourceAdapter
from scripts.migrate.core.audit_log import MigrationAuditLogger
from scripts.migrate.core.client import APIConflictError
from scripts.migrate.core.pipeline import MigrationPipeline
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


class _ConflictClient:
    def __init__(self) -> None:
        self.patch_guest_calls = 0
        self._list_calls = 0

    def list_guests(self, **kwargs: object) -> dict:
        self._list_calls += 1
        if self._list_calls >= 2:
            return {
                "items": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "email": "merge@example.com",
                    },
                ],
                "total": 1,
                "limit": 10,
                "offset": 0,
            }
        return {"items": [], "total": 0, "limit": 10, "offset": 0}

    def create_guest(self, body: dict) -> dict:
        raise APIConflictError("duplicate")

    def patch_guest(self, guest_id: UUID, body: dict) -> dict:
        self.patch_guest_calls += 1
        return {"id": str(guest_id)}


def test_guest_409_triggers_patch_when_update() -> None:
    lg = logging.getLogger("migrate.test.ocu")
    lg.handlers.clear()
    lg.addHandler(logging.NullHandler())
    lg.propagate = False
    audit = MigrationAuditLogger(lg)
    g = GuestRecord(
        external_id="g1",
        first_name="A",
        last_name="B",
        email="merge@example.com",
        phone="+19999999999",
        vip_status=True,
    )
    client = _ConflictClient()
    pipe = MigrationPipeline(
        _StubAdapter(),
        client,  # type: ignore[arg-type]
        property_id=UUID(int=1),
        property_label="t",
        source_name="Preno",
        dry_run=False,
        on_conflict="update",
        audit=audit,
        state=None,
        resume=False,
        source_paths=[],
    )
    pipe._stage_guests(client, [g])  # type: ignore[arg-type]
    assert client.patch_guest_calls == 1
    st = pipe.report_generator.data.stages["guests"]
    assert st.updated == 1
