"""Fixtures for Satva Samui + local OpenPMS integration tests."""

from __future__ import annotations

import glob
import os
from pathlib import Path
from uuid import UUID

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def satva_dir() -> Path:
    raw = os.environ.get("MIG_SATVA_DIR")
    if not raw:
        pytest.skip("MIG_SATVA_DIR not set (directory with guests_export_*.csv and bookings_report*.csv)")
    p = Path(raw).expanduser().resolve()
    if not p.is_dir():
        pytest.skip(f"MIG_SATVA_DIR is not a directory: {p}")
    if not list(p.glob("guests_export_*.csv")):
        pytest.skip(f"No guests_export_*.csv under {p}")
    if not list(p.glob("bookings_report*.csv")):
        pytest.skip(f"No bookings_report*.csv under {p}")
    return p


@pytest.fixture(scope="module")
def openpms_migration_config() -> dict:
    url = os.environ.get("MIG_OPENPMS_URL")
    token = os.environ.get("MIG_OPENPMS_TOKEN")
    pid = os.environ.get("MIG_PROPERTY_ID")
    if not url or not token or not pid:
        pytest.skip(
            "MIG_OPENPMS_URL, MIG_OPENPMS_TOKEN, MIG_PROPERTY_ID required for full migration test",
        )
    return {
        "url": url.rstrip("/"),
        "token": token,
        "property_id": UUID(pid.strip()),
    }


def resolved_satva_paths(satva_dir: Path) -> list[str]:
    patterns = (
        str(satva_dir / "guests_export_*.csv"),
        str(satva_dir / "bookings_report*.csv"),
    )
    paths: set[str] = set()
    for pat in patterns:
        for p in glob.glob(pat):
            paths.add(str(Path(p).resolve()))
    return sorted(paths)
