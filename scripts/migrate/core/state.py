"""SQLite state store for migration progress and idempotency (v1.1)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from scripts.migrate.core.report import StageStats

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  started_at TEXT,
  finished_at TEXT,
  source TEXT,
  property_id TEXT,
  final_status TEXT
);
CREATE TABLE IF NOT EXISTS stages (
  run_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  stats_json TEXT,
  PRIMARY KEY (run_id, stage)
);
CREATE TABLE IF NOT EXISTS processed (
  run_id TEXT NOT NULL,
  entity TEXT NOT NULL,
  external_id TEXT NOT NULL,
  openpms_id TEXT,
  result TEXT NOT NULL,
  PRIMARY KEY (run_id, entity, external_id)
);
CREATE TABLE IF NOT EXISTS mappings (
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  openpms_id TEXT NOT NULL,
  PRIMARY KEY (run_id, kind, name)
);
"""

STAGE_ORDER = [
    "room_types",
    "rooms",
    "rate_plans",
    "rates",
    "guests",
    "bookings",
    "verify",
]


def stage_stats_to_json(stats: StageStats | None) -> str | None:
    if stats is None:
        return None
    return json.dumps(asdict(stats), ensure_ascii=False)


def stage_stats_from_json(raw: str | None) -> StageStats | None:
    if not raw:
        return None
    d = json.loads(raw)
    return StageStats(
        total=int(d.get("total", 0)),
        created=int(d.get("created", 0)),
        updated=int(d.get("updated", 0)),
        skipped=int(d.get("skipped", 0)),
        existed=int(d.get("existed", 0)),
        errors=int(d.get("errors", 0)),
    )


def compute_run_id(property_id: str, resolved_paths: list[str]) -> str:
    """Deterministic run id from property and input file paths + mtimes."""
    parts: list[str] = [property_id]
    uniq: set[str] = set()
    for x in resolved_paths:
        if not x:
            continue
        try:
            uniq.add(str(Path(x).resolve()))
        except OSError:
            uniq.add(str(x))
    for p in sorted(uniq):
        pp = Path(p)
        if pp.is_file():
            st = pp.stat()
            parts.append(f"{p}:{st.st_mtime_ns}")
        else:
            parts.append(f"{p}:missing")
    blob = "|".join(parts).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:12]


class StateStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def ensure_run(self, run_id: str, source: str, property_id: str) -> None:
        row = self._conn.execute(
            "SELECT run_id FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            self._conn.execute(
                """
                INSERT INTO runs (run_id, started_at, source, property_id, final_status)
                VALUES (?, datetime('now'), ?, ?, 'running')
                """,
                (run_id, source, property_id),
            )

    def finalize_run(self, run_id: str, final_status: str) -> None:
        self._conn.execute(
            """
            UPDATE runs SET finished_at = datetime('now'), final_status = ?
            WHERE run_id = ?
            """,
            (final_status, run_id),
        )

    def set_stage_status(
        self,
        run_id: str,
        stage: str,
        status: str,
        stats: StageStats | None = None,
    ) -> None:
        sj = stage_stats_to_json(stats)
        self._conn.execute(
            """
            INSERT INTO stages (run_id, stage, status, stats_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id, stage) DO UPDATE SET
              status = excluded.status,
              stats_json = excluded.stats_json
            """,
            (run_id, stage, status, sj),
        )

    def get_stage_status(self, run_id: str, stage: str) -> tuple[str | None, StageStats | None]:
        row = self._conn.execute(
            "SELECT status, stats_json FROM stages WHERE run_id = ? AND stage = ?",
            (run_id, stage),
        ).fetchone()
        if row is None:
            return None, None
        return str(row["status"]), stage_stats_from_json(row["stats_json"])

    def mark_processed(
        self,
        run_id: str,
        entity: str,
        external_id: str,
        *,
        openpms_id: str = "",
        result: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO processed (run_id, entity, external_id, openpms_id, result)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id, entity, external_id) DO UPDATE SET
              openpms_id = excluded.openpms_id,
              result = excluded.result
            """,
            (run_id, entity, external_id, openpms_id or "", result),
        )

    def is_processed(
        self,
        run_id: str,
        entity: str,
        external_id: str,
    ) -> tuple[bool, str | None]:
        row = self._conn.execute(
            """
            SELECT openpms_id, result FROM processed
            WHERE run_id = ? AND entity = ? AND external_id = ?
            """,
            (run_id, entity, external_id),
        ).fetchone()
        if row is None:
            return False, None
        oid = row["openpms_id"] if row["openpms_id"] else None
        return True, oid

    def put_mapping(self, run_id: str, kind: str, name: str, openpms_id: str) -> None:
        self._conn.execute(
            """
            INSERT INTO mappings (run_id, kind, name, openpms_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id, kind, name) DO UPDATE SET
              openpms_id = excluded.openpms_id
            """,
            (run_id, kind, name, openpms_id),
        )

    def get_mapping(self, run_id: str, kind: str, name: str) -> str | None:
        row = self._conn.execute(
            """
            SELECT openpms_id FROM mappings
            WHERE run_id = ? AND kind = ? AND name = ?
            """,
            (run_id, kind, name),
        ).fetchone()
        if row is None:
            return None
        return str(row["openpms_id"])

    def all_mappings(self, run_id: str, kind: str) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT name, openpms_id FROM mappings WHERE run_id = ? AND kind = ?",
            (run_id, kind),
        ).fetchall()
        return {str(r["name"]): str(r["openpms_id"]) for r in rows}
