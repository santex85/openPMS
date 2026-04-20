"""MIG-20: full migration + resume idempotency (requires MIG_SATVA_DIR + OpenPMS env)."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from scripts.migrate.adapters.preno import PrenoAdapter
from scripts.migrate.core.audit_log import MigrationAuditLogger, setup_migration_audit_logger
from scripts.migrate.core.client import OpenPMSClient
from scripts.migrate.core.pipeline import MigrationPipeline
from scripts.migrate.core.state import StateStore

from .conftest import resolved_satva_paths

pytestmark = pytest.mark.integration


def test_full_migration_and_resume_idempotent(
    satva_dir: Path,
    openpms_migration_config: dict,
    tmp_path: Path,
) -> None:
    url: str = openpms_migration_config["url"]
    token: str = openpms_migration_config["token"]
    property_id: UUID = openpms_migration_config["property_id"]

    guests_glob = str(satva_dir / "guests_export_*.csv")
    bookings_glob = str(satva_dir / "bookings_report*.csv")
    source_paths = resolved_satva_paths(satva_dir)

    adapter = PrenoAdapter(
        guests_glob=guests_glob,
        bookings_glob=bookings_glob,
        include_cancelled=False,
    )
    assert adapter.validate().ok
    bookings = adapter.extract_bookings()
    assert len(bookings) >= 3

    client = OpenPMSClient(url, token)
    state_path = tmp_path / "migration_integration.sqlite3"
    store = StateStore(state_path)
    audit_log_path = tmp_path / "migration_audit.log"
    backend = setup_migration_audit_logger(
        log_file=str(audit_log_path),
        stream=False,
        level=logging.INFO,
    )
    audit = MigrationAuditLogger(backend)

    start = min(b.check_in for b in bookings)
    end = max(b.check_out for b in bookings)

    def guest_total() -> int:
        page = client.list_guests(limit=1, offset=0)
        return int(page.get("total", 0))

    def booking_total() -> int:
        page = client.list_bookings_window(
            property_id=property_id,
            start_date=start,
            end_date=end,
            limit=500,
        )
        return int(page.get("total", 0))

    try:
        pipe1 = MigrationPipeline(
            adapter,
            client,
            property_id=property_id,
            property_label=str(property_id),
            source_name="Preno",
            dry_run=False,
            on_conflict="skip",
            precheck_bookings=True,
            state=store,
            audit=audit,
            resume=False,
            source_paths=source_paths,
            default_night_rate=Decimal("100.00"),
        )
        report1 = pipe1.run()
        assert report1.final_status in {"SUCCESS", "PARTIAL"}

        g1 = report1.stages["guests"]
        assert g1.created + g1.existed >= 1
        b1 = report1.stages["bookings"]
        assert b1.created + b1.existed + b1.skipped >= 1

        assert guest_total() >= 1
        for b in bookings[:3]:
            row = client.get_booking_by_external_id(b.external_id)
            assert row is not None, b.external_id
            ext = row.get("external_booking_id")
            assert ext is not None and str(ext) == str(b.external_id)

        n_guests_mid = guest_total()
        n_bookings_mid = booking_total()

        pipe2 = MigrationPipeline(
            adapter,
            client,
            property_id=property_id,
            property_label=str(property_id),
            source_name="Preno",
            dry_run=False,
            on_conflict="skip",
            precheck_bookings=True,
            state=store,
            audit=audit,
            resume=True,
            source_paths=source_paths,
            default_night_rate=Decimal("100.00"),
        )
        report2 = pipe2.run()
        assert report2.final_status != "FAILED"
        assert guest_total() == n_guests_mid
        assert booking_total() == n_bookings_mid
    finally:
        client.close()
        store.close()
