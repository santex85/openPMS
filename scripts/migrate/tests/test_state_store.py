"""StateStore (SQLite) tests."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

from scripts.migrate.core.report import StageStats
from scripts.migrate.core.state import StateStore, compute_run_id, stage_stats_from_json, stage_stats_to_json


def test_compute_run_id_stable(tmp_path: Path) -> None:
    f = tmp_path / "a.csv"
    f.write_text("x")
    pid = "550e8400-e29b-41d4-a716-446655440000"
    p1 = compute_run_id(pid, [str(f)])
    p2 = compute_run_id(pid, [str(f)])
    assert p1 == p2
    assert len(p1) == 12


def test_compute_run_id_changes_with_mtime(tmp_path: Path) -> None:
    f = tmp_path / "b.csv"
    f.write_text("v1")
    pid = "550e8400-e29b-41d4-a716-446655440000"
    r1 = compute_run_id(pid, [str(f)])
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    r2 = compute_run_id(pid, [str(f)])
    assert r1 != r2


def test_stage_roundtrip() -> None:
    s = StageStats(total=3, created=2, skipped=1, errors=0, existed=0, updated=0)
    raw = stage_stats_to_json(s)
    assert raw is not None
    s2 = stage_stats_from_json(raw)
    assert s2 is not None
    assert s2.total == 3 and s2.created == 2 and s2.skipped == 1


def test_state_store_processed_and_mappings(tmp_path: Path) -> None:
    db = tmp_path / "st.db"
    st = StateStore(db)
    try:
        st.ensure_run("run1", "Preno", "prop-1")
        st.set_stage_status("run1", "guests", "running", None)
        st.mark_processed("run1", "guest", "g1", openpms_id="uuid-1", result="created")
        ok, oid = st.is_processed("run1", "guest", "g1")
        assert ok and oid == "uuid-1"
        st.put_mapping("run1", "room_type_name", "Deluxe", "rt-uuid")
        assert st.get_mapping("run1", "room_type_name", "Deluxe") == "rt-uuid"
        assert st.all_mappings("run1", "room_type_name") == {"Deluxe": "rt-uuid"}
        st.set_stage_status(
            "run1",
            "guests",
            "done",
            StageStats(total=1, created=1),
        )
        status, stats = st.get_stage_status("run1", "guests")
        assert status == "done" and stats is not None and stats.created == 1
        st.finalize_run("run1", "SUCCESS")
    finally:
        st.close()


def test_state_run_id_property_uuid_string() -> None:
    pid = str(UUID(int=42))
    r = compute_run_id(pid, [])
    assert len(r) == 12
