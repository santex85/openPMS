"""Migration run report (stdout / file / JSON)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


@dataclass
class StageStats:
    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    existed: int = 0
    errors: int = 0


@dataclass
class MigrationReport:
    source: str
    property_label: str
    started_at: datetime
    finished_at: datetime | None = None
    dry_run: bool = False
    final_status: Literal["SUCCESS", "PARTIAL", "FAILED"] = "SUCCESS"
    stages: dict[str, StageStats] = field(default_factory=dict)
    room_type_mapping: dict[str, str] = field(
        default_factory=dict,
        metadata={"description": "Preno name -> OpenPMS room type UUID"},
    )
    errors: list[dict[str, str]] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        end = self.finished_at or self.started_at
        return max(0.0, (end - self.started_at).total_seconds())


class ReportGenerator:
    def __init__(self, report: MigrationReport) -> None:
        self._report = report

    @property
    def data(self) -> MigrationReport:
        return self._report

    def add_error(self, *, entity: str, ref: str, message: str) -> None:
        self._report.errors.append(
            {"entity": entity, "ref": ref, "message": message[:2000]},
        )

    def text(self) -> str:
        r = self._report
        lines = [
            "=== OpenPMS Migration Report ===",
            f"Source:     {r.source}",
            f"Property:   {r.property_label}",
            f"Date:       {r.started_at.astimezone(UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC",
            f"Duration:   {r.duration_s:.1f}s",
            f"Dry run:    {r.dry_run}",
            "",
        ]
        order = [
            "room_types",
            "rooms",
            "rate_plans",
            "rates",
            "guests",
            "bookings",
            "verify",
        ]
        for key in order:
            st = r.stages.get(key)
            if st is None:
                continue
            label = key.replace("_", " ").title()
            lines.append(
                f"{label}: total={st.total} created={st.created} updated={st.updated} "
                f"skipped={st.skipped} existed={st.existed} errors={st.errors}",
            )
        if r.room_type_mapping:
            lines.append("")
            lines.append("Room type mapping (source name -> OpenPMS id):")
            for k, v in sorted(r.room_type_mapping.items()):
                lines.append(f"  {k!r} -> {v}")
        if r.errors:
            lines.append("")
            lines.append("Errors:")
            for e in r.errors[:200]:
                lines.append(
                    f"  [{e.get('entity', '?')}] {e.get('ref', '')}: {e.get('message', '')}",
                )
            if len(r.errors) > 200:
                lines.append(f"  ... and {len(r.errors) - 200} more")
        lines.append("")
        lines.append(f"Status: {r.final_status}")
        return "\n".join(lines)

    def to_json(self) -> str:
        r = self._report

        def _stats(s: StageStats) -> dict[str, int]:
            return {
                "total": s.total,
                "created": s.created,
                "updated": s.updated,
                "skipped": s.skipped,
                "existed": s.existed,
                "errors": s.errors,
            }

        payload: dict[str, Any] = {
            "source": r.source,
            "property": r.property_label,
            "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "dry_run": r.dry_run,
            "duration_s": r.duration_s,
            "final_status": r.final_status,
            "stages": {k: _stats(v) for k, v in r.stages.items()},
            "room_type_mapping": r.room_type_mapping,
            "errors": r.errors,
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)

    def print(self) -> None:
        print(self.text())
