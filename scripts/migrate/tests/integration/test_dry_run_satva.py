"""MIG-19: dry-run on real Satva Samui CSV (requires MIG_SATVA_DIR)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest

from scripts.migrate.adapters.preno import PrenoAdapter
from scripts.migrate.core.pipeline import MigrationPipeline

from .conftest import REPO_ROOT, resolved_satva_paths

pytestmark = pytest.mark.integration


def test_dry_run_pipeline_api(satva_dir: Path) -> None:
    guests_glob = str(satva_dir / "guests_export_*.csv")
    bookings_glob = str(satva_dir / "bookings_report*.csv")
    adapter = PrenoAdapter(
        guests_glob=guests_glob,
        bookings_glob=bookings_glob,
        include_cancelled=False,
    )
    vr = adapter.validate()
    assert vr.ok, [i.message for i in vr.issues]

    source_paths = resolved_satva_paths(satva_dir)
    pipe = MigrationPipeline(
        adapter,
        None,
        property_id=UUID("00000000-0000-0000-0000-000000000001"),
        property_label="satva-dry-run",
        source_name="Preno",
        dry_run=True,
        source_paths=source_paths,
    )
    report = pipe.run()
    assert report.final_status == "SUCCESS"
    assert len(report.errors) == 0

    g = report.stages["guests"]
    assert g.total >= 2000
    assert g.skipped >= 1

    b = report.stages["bookings"]
    assert b.total >= 1500

    assert len(adapter.extract_room_types()) >= 1
    assert len(adapter.extract_rate_plans()) >= 1


def test_cli_dry_run_subprocess(satva_dir: Path, tmp_path: Path) -> None:
    report_path = tmp_path / "cli_report.txt"
    guests_glob = str(satva_dir / "guests_export_*.csv")
    bookings_glob = str(satva_dir / "bookings_report*.csv")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.migrate",
            "--source",
            "preno",
            "--property-id",
            "00000000-0000-0000-0000-000000000001",
            "--guests",
            guests_glob,
            "--bookings",
            bookings_glob,
            "--dry-run",
            "--report",
            str(report_path),
            "--no-log-file",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    text = report_path.read_text(encoding="utf-8")
    assert "Dry run:" in text
