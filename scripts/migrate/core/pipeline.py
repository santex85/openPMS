"""Orchestrates migration stages (Preno → OpenPMS)."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable, Literal
from uuid import UUID

from scripts.migrate.core.adapter import SourceAdapter
from scripts.migrate.core.audit_log import MigrationAuditLogger
from scripts.migrate.core.client import APIConflictError, APIValidationError, OpenPMSClient
from scripts.migrate.core.report import MigrationReport, ReportGenerator, StageStats
from scripts.migrate.core.state import StateStore, compute_run_id
from scripts.migrate.models.records import BookingRecord, GuestRecord

log = logging.getLogger(__name__)


class OnConflictFailError(Exception):
    """Stopped migration because --on-conflict fail and a duplicate was encountered."""


def _iter_batches(items: list, batch_size: int):
    for i in range(0, len(items), max(1, batch_size)):
        yield items[i : i + batch_size]


def _is_synthetic_guest_email(email: str) -> bool:
    return str(email).strip().lower().endswith("@migrate.openpms.local")


def _dedupe_guests_by_email(
    guests: list[GuestRecord],
    audit: MigrationAuditLogger,
) -> tuple[list[GuestRecord], int]:
    """Drop duplicate real emails (keep lowest external_id); keep synthetic / missing emails."""
    seen: dict[str, GuestRecord] = {}
    out: list[GuestRecord] = []
    n_dup = 0
    for g in sorted(guests, key=lambda x: x.external_id):
        email_norm = _safe_email_for_guest(g)
        if _is_synthetic_guest_email(email_norm):
            out.append(g)
            continue
        key = email_norm.strip().lower()
        if key in seen:
            n_dup += 1
            audit.event("guests", "guest", g.external_id, "source_dup", key)
            continue
        seen[key] = g
        out.append(g)
    return out, n_dup


def _guest_patch_payload(g: GuestRecord) -> dict:
    body: dict = {}
    phone = _safe_phone(g)
    if phone and phone != "+10000000000":
        body["phone"] = phone
    if g.notes:
        body["notes"] = g.notes
    if g.nationality and len(str(g.nationality).strip()) == 2:
        body["nationality"] = str(g.nationality).strip().upper()
    body["vip_status"] = bool(g.vip_status)
    return body


def _max_parallel_rooms_by_type(bookings: list[BookingRecord]) -> dict[str, int]:
    """How many physical rooms of each category are needed (overlap count)."""

    by_type: dict[str, list[tuple[date, date]]] = defaultdict(list)
    for b in bookings:
        if b.status == "cancelled":
            continue
        by_type[b.room_type_name].append((b.check_in, b.check_out))
    out: dict[str, int] = {}
    for rt, intervals in by_type.items():
        events: list[tuple[date, int]] = []
        for a, b in intervals:
            events.append((a, 1))
            events.append((b, -1))
        events.sort(key=lambda t: (t[0], t[1]))
        cur = 0
        best = 0
        for _, delta in events:
            cur += delta
            best = max(best, cur)
        out[rt] = max(1, best)
    return out


def _iter_date_chunks(start: date, end: date, max_days: int = 366):
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=max_days - 1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def _safe_email_for_guest(g: GuestRecord) -> str:
    if g.email and str(g.email).strip():
        return str(g.email).strip().lower()
    return f"noemail-{re.sub(r'[^a-zA-Z0-9_-]+', '-', g.external_id)}@migrate.openpms.local"


def _safe_phone(g: GuestRecord) -> str:
    if g.phone and str(g.phone).strip():
        return str(g.phone).strip()
    return "+10000000000"


def _null_audit_logger() -> MigrationAuditLogger:
    lg = logging.getLogger("openpms.migration.audit.null")
    lg.handlers.clear()
    lg.addHandler(logging.NullHandler())
    lg.propagate = False
    return MigrationAuditLogger(lg)


class MigrationPipeline:
    def __init__(
        self,
        adapter: SourceAdapter,
        client: OpenPMSClient | None,
        *,
        property_id: UUID,
        property_label: str,
        source_name: str,
        dry_run: bool,
        on_conflict: Literal["skip", "update", "fail"] = "skip",
        batch_size: int = 50,
        default_night_rate: Decimal = Decimal("100.00"),
        audit: MigrationAuditLogger | None = None,
        state: StateStore | None = None,
        resume: bool = False,
        source_paths: list[str] | None = None,
        precheck_bookings: bool = False,
    ) -> None:
        self._adapter = adapter
        self._client = client
        self._property_id = property_id
        self._property_label = property_label
        self._source_name = source_name
        self._dry_run = dry_run
        self._on_conflict = on_conflict
        self._batch_size = max(1, batch_size)
        self._default_night_rate = default_night_rate
        self._audit = audit if audit is not None else _null_audit_logger()
        self._state = state
        self._resume = resume
        self._source_paths = source_paths or []
        self._precheck_bookings = precheck_bookings
        self._run_id = ""
        self._report = MigrationReport(
            source=source_name,
            property_label=property_label,
            started_at=datetime.now().astimezone(),
            dry_run=dry_run,
        )
        self._rg = ReportGenerator(self._report)

    @property
    def report_generator(self) -> ReportGenerator:
        return self._rg

    @property
    def run_id(self) -> str:
        return self._run_id

    def _init_run_state(self) -> None:
        if self._state is None or self._dry_run or self._client is None:
            self._run_id = ""
            return
        self._run_id = compute_run_id(str(self._property_id), self._source_paths)
        self._state.ensure_run(self._run_id, self._source_name, str(self._property_id))

    def _maybe_skip_stage(self, stage_name: str) -> bool:
        if not (self._resume and self._state and self._run_id):
            return False
        st, stats = self._state.get_stage_status(self._run_id, stage_name)
        if st != "done" or stats is None:
            return False
        self._report.stages[stage_name] = stats
        if stage_name == "room_types":
            for k, v in self._state.all_mappings(self._run_id, "room_type_name").items():
                self._report.room_type_mapping[k] = v
        self._audit.event(
            stage_name,
            "stage",
            stage_name,
            "skipped",
            "resume: stage already completed",
        )
        return True

    def _begin_stage(self, stage_name: str) -> None:
        if self._state and self._run_id:
            self._state.set_stage_status(self._run_id, stage_name, "running", None)
        self._audit.event(stage_name, "stage", stage_name, "started", "")

    def _end_stage(self, stage_name: str, *, failed: bool) -> None:
        if not (self._state and self._run_id):
            return
        stats = self._report.stages.get(stage_name)
        status = "failed" if failed else "done"
        self._state.set_stage_status(self._run_id, stage_name, status, stats)
        self._audit.event(
            stage_name,
            "stage",
            stage_name,
            status,
            "",
            level=logging.ERROR if failed else logging.INFO,
        )

    def run(self) -> MigrationReport:
        vr = self._adapter.validate()
        if not vr.ok:
            for issue in vr.issues:
                self._rg.add_error(
                    entity="validation",
                    ref="adapter",
                    message=issue.message,
                )
                self._audit.event(
                    "validation",
                    "validation",
                    "adapter",
                    "error",
                    issue.message,
                    level=logging.ERROR,
                )
            self._report.final_status = "FAILED"
            self._report.finished_at = datetime.now().astimezone()
            return self._report

        guests = self._adapter.extract_guests()
        bookings = self._adapter.extract_bookings()
        room_types = self._adapter.extract_room_types()
        rate_plans = self._adapter.extract_rate_plans()
        rooms_from_source = self._adapter.extract_rooms()

        if self._dry_run or self._client is None:
            self._dry_run_stats(
                guests,
                bookings,
                room_types,
                rate_plans,
                rooms_from_source,
            )
            self._report.finished_at = datetime.now().astimezone()
            return self._report

        assert self._client is not None
        client = self._client
        self._init_run_state()

        stage_runners: list[tuple[str, Callable[[], None]]] = [
            ("room_types", lambda: self._stage_room_types(client, room_types)),
            ("rooms", lambda: self._stage_rooms(client, bookings, rooms_from_source)),
            ("rate_plans", lambda: self._stage_rate_plans(client, rate_plans)),
            ("rates", lambda: self._stage_rates(client, bookings)),
            ("guests", lambda: self._stage_guests(client, guests)),
            ("bookings", lambda: self._stage_bookings(client, bookings)),
            ("verify", lambda: self._stage_verify(client, bookings)),
        ]

        try:
            for stage_name, fn in stage_runners:
                if self._maybe_skip_stage(stage_name):
                    continue
                self._begin_stage(stage_name)
                try:
                    fn()
                except Exception:
                    self._end_stage(stage_name, failed=True)
                    raise
                self._end_stage(stage_name, failed=False)
        except OnConflictFailError as exc:
            log.warning("migration stopped: %s", exc)
            self._rg.add_error(entity="pipeline", ref="run", message=str(exc))
            self._audit.event(
                "pipeline",
                "pipeline",
                "run",
                "error",
                str(exc),
                level=logging.ERROR,
            )
            self._report.final_status = "FAILED"
        except Exception as exc:  # noqa: BLE001
            log.exception("migration failed")
            self._rg.add_error(entity="pipeline", ref="run", message=str(exc))
            self._audit.event(
                "pipeline",
                "pipeline",
                "run",
                "error",
                str(exc),
                level=logging.ERROR,
            )
            self._report.final_status = "FAILED"
        else:
            if self._report.errors:
                self._report.final_status = "PARTIAL"
            else:
                self._report.final_status = "SUCCESS"
        finally:
            self._report.finished_at = datetime.now().astimezone()
            if self._state and self._run_id:
                self._state.finalize_run(self._run_id, self._report.final_status)
        return self._report

    def _dry_run_stats(
        self,
        guests: list[GuestRecord],
        bookings: list[BookingRecord],
        room_types: list,
        rate_plans: list,
        rooms_from_source: list,
    ) -> None:
        need_rooms = _max_parallel_rooms_by_type(bookings)
        total_synth = sum(need_rooms.values()) if not rooms_from_source else len(rooms_from_source)
        self._report.stages["room_types"] = StageStats(
            total=len(room_types),
            created=len(room_types),
        )
        self._report.stages["rooms"] = StageStats(
            total=total_synth,
            created=total_synth,
        )
        self._report.stages["rate_plans"] = StageStats(
            total=len(rate_plans),
            created=len(rate_plans),
        )
        self._report.stages["rates"] = StageStats(total=1, created=1)
        g2, g_dup = _dedupe_guests_by_email(guests, self._audit)
        self._report.stages["guests"] = StageStats(
            total=len(guests),
            created=len(g2),
            skipped=g_dup,
        )
        self._report.stages["bookings"] = StageStats(total=len(bookings), created=len(bookings))
        self._report.stages["verify"] = StageStats(total=1, skipped=1)

    def _room_type_map(self, client: OpenPMSClient) -> dict[str, UUID]:
        existing = client.list_room_types(self._property_id)
        return {str(r["name"]).strip().lower(): UUID(str(r["id"])) for r in existing}

    def _persist_room_type(
        self,
        *,
        rt_name: str,
        key: str,
        openpms_id: UUID,
        result: str,
    ) -> None:
        if self._state and self._run_id:
            self._state.put_mapping(
                self._run_id,
                "room_type_name",
                rt_name,
                str(openpms_id),
            )
            self._state.mark_processed(
                self._run_id,
                "room_type",
                key,
                openpms_id=str(openpms_id),
                result=result,
            )

    def _stage_room_types(self, client: OpenPMSClient, room_types: list) -> None:
        stats = StageStats(total=len(room_types))
        self._report.stages["room_types"] = stats
        by_name = self._room_type_map(client)
        for rt in room_types:
            key = rt.name.strip().lower()
            if key in by_name:
                stats.existed += 1
                rid = by_name[key]
                self._report.room_type_mapping[rt.name] = str(rid)
                self._persist_room_type(
                    rt_name=rt.name,
                    key=key,
                    openpms_id=rid,
                    result="existed",
                )
                self._audit.event(
                    "room_types",
                    "room_type",
                    key,
                    "existed",
                    str(rid),
                )
                continue
            try:
                row = client.create_room_type(
                    property_id=self._property_id,
                    name=rt.name,
                    base_occupancy=rt.base_occupancy,
                    max_occupancy=rt.max_occupancy,
                )
                rid = UUID(str(row["id"]))
                by_name[key] = rid
                stats.created += 1
                self._report.room_type_mapping[rt.name] = str(rid)
                self._persist_room_type(
                    rt_name=rt.name,
                    key=key,
                    openpms_id=rid,
                    result="created",
                )
                self._audit.event(
                    "room_types",
                    "room_type",
                    key,
                    "created",
                    str(rid),
                )
            except (APIConflictError, APIValidationError, Exception) as exc:
                stats.errors += 1
                self._rg.add_error(entity="room_type", ref=rt.name, message=str(exc))
                self._audit.event(
                    "room_types",
                    "room_type",
                    key,
                    "error",
                    str(exc),
                    level=logging.ERROR,
                )

    def _resolve_room_type_ids(self, client: OpenPMSClient) -> dict[str, UUID]:
        return self._room_type_map(client)

    def _stage_rooms(
        self,
        client: OpenPMSClient,
        bookings: list[BookingRecord],
        rooms_from_source: list,
    ) -> None:
        stats = StageStats()
        self._report.stages["rooms"] = stats
        rt_ids = self._resolve_room_type_ids(client)

        if rooms_from_source:
            by_type: dict[str, list[dict[str, str]]] = defaultdict(list)
            for r in rooms_from_source:
                by_type[r.room_type_name.strip().lower()].append(
                    {"name": r.name, "status": r.status},
                )
            for rt_key, items in by_type.items():
                rt_uuid = rt_ids.get(rt_key)
                if rt_uuid is None:
                    stats.errors += len(items)
                    for it in items:
                        msg = f"room type {rt_key!r} not found"
                        self._rg.add_error(entity="room", ref=it["name"], message=msg)
                        self._audit.event(
                            "rooms",
                            "room",
                            it["name"],
                            "error",
                            msg,
                            level=logging.ERROR,
                        )
                    continue
                stats.total += len(items)
                for i in range(0, len(items), 200):
                    chunk = items[i : i + 200]
                    try:
                        res = client.bulk_create_rooms(
                            room_type_id=rt_uuid,
                            rooms=chunk,
                            on_conflict="skip",
                        )
                        stats.created += len(res.get("created", []))
                        stats.skipped += len(res.get("skipped", []))
                        self._audit.event(
                            "rooms",
                            "room_bulk",
                            str(rt_uuid),
                            "created",
                            f"n={len(res.get('created', []))}",
                        )
                    except Exception as exc:  # noqa: BLE001
                        stats.errors += len(chunk)
                        self._rg.add_error(
                            entity="room_bulk",
                            ref=str(rt_uuid),
                            message=str(exc),
                        )
                        self._audit.event(
                            "rooms",
                            "room_bulk",
                            str(rt_uuid),
                            "error",
                            str(exc),
                            level=logging.ERROR,
                        )
            return

        need = _max_parallel_rooms_by_type(bookings)
        for rt_name, n in need.items():
            key = rt_name.strip().lower()
            rt_uuid = rt_ids.get(key)
            if rt_uuid is None:
                stats.errors += n
                msg = "room type missing before room synthesis"
                self._rg.add_error(entity="room", ref=rt_name, message=msg)
                self._audit.event(
                    "rooms",
                    "room",
                    rt_name,
                    "error",
                    msg,
                    level=logging.ERROR,
                )
                continue
            items = [
                {"name": f"Migrated-{rt_name}-{i + 1}", "status": "available"}
                for i in range(n)
            ]
            stats.total += len(items)
            for i in range(0, len(items), 200):
                chunk = items[i : i + 200]
                try:
                    res = client.bulk_create_rooms(
                        room_type_id=rt_uuid,
                        rooms=chunk,
                        on_conflict="skip",
                    )
                    stats.created += len(res.get("created", []))
                    stats.skipped += len(res.get("skipped", []))
                    self._audit.event(
                        "rooms",
                        "room_bulk",
                        str(rt_uuid),
                        "created",
                        f"n={len(res.get('created', []))}",
                    )
                except Exception as exc:  # noqa: BLE001
                    stats.errors += len(chunk)
                    self._rg.add_error(
                        entity="room_bulk",
                        ref=str(rt_uuid),
                        message=str(exc),
                    )
                    self._audit.event(
                        "rooms",
                        "room_bulk",
                        str(rt_uuid),
                        "error",
                        str(exc),
                        level=logging.ERROR,
                    )

    def _rate_plan_map(self, client: OpenPMSClient) -> dict[str, UUID]:
        rows = client.list_rate_plans(self._property_id)
        return {str(r["name"]).strip().lower(): UUID(str(r["id"])) for r in rows}

    def _persist_rate_plan(
        self,
        *,
        display_name: str,
        key: str,
        openpms_id: UUID,
        result: str,
    ) -> None:
        if self._state and self._run_id:
            self._state.put_mapping(
                self._run_id,
                "rate_plan_name",
                display_name,
                str(openpms_id),
            )
            self._state.mark_processed(
                self._run_id,
                "rate_plan",
                key,
                openpms_id=str(openpms_id),
                result=result,
            )

    def _stage_rate_plans(self, client: OpenPMSClient, rate_plans: list) -> None:
        stats = StageStats(total=len(rate_plans))
        self._report.stages["rate_plans"] = stats
        by_name = self._rate_plan_map(client)
        for rp in rate_plans:
            key = rp.name.strip().lower()
            if key in by_name:
                stats.existed += 1
                rid = by_name[key]
                self._persist_rate_plan(
                    display_name=rp.name,
                    key=key,
                    openpms_id=rid,
                    result="existed",
                )
                self._audit.event(
                    "rate_plans",
                    "rate_plan",
                    key,
                    "existed",
                    str(rid),
                )
                continue
            try:
                row = client.create_rate_plan(
                    property_id=self._property_id,
                    name=rp.name,
                    cancellation_policy=rp.cancellation_policy or "standard",
                )
                rid = UUID(str(row["id"]))
                by_name[key] = rid
                stats.created += 1
                self._persist_rate_plan(
                    display_name=rp.name,
                    key=key,
                    openpms_id=rid,
                    result="created",
                )
                self._audit.event(
                    "rate_plans",
                    "rate_plan",
                    key,
                    "created",
                    str(rid),
                )
            except Exception as exc:  # noqa: BLE001
                stats.errors += 1
                self._rg.add_error(entity="rate_plan", ref=rp.name, message=str(exc))
                self._audit.event(
                    "rate_plans",
                    "rate_plan",
                    key,
                    "error",
                    str(exc),
                    level=logging.ERROR,
                )

    def _stage_rates(self, client: OpenPMSClient, bookings: list[BookingRecord]) -> None:
        stats = StageStats()
        self._report.stages["rates"] = stats
        if not bookings:
            return
        d_min = min(b.check_in for b in bookings)
        d_max = max(b.check_out - timedelta(days=1) for b in bookings)
        if d_max < d_min:
            d_max = d_min

        rt_map = self._resolve_room_type_ids(client)
        rp_map = self._rate_plan_map(client)

        pairs: set[tuple[UUID, UUID]] = set()
        for b in bookings:
            rk = b.room_type_name.strip().lower()
            pk = b.rate_plan_name.strip().lower()
            if rk in rt_map and pk in rp_map:
                pairs.add((rt_map[rk], rp_map[pk]))

        price = str(self._default_night_rate)
        rows_upserted = 0
        for rt_id, rp_id in pairs:
            for c_start, c_end in _iter_date_chunks(d_min, d_max, max_days=366):
                seg = {
                    "room_type_id": str(rt_id),
                    "rate_plan_id": str(rp_id),
                    "start_date": c_start.isoformat(),
                    "end_date": c_end.isoformat(),
                    "price": price,
                }
                ref = f"{rt_id}/{rp_id}/{c_start}->{c_end}"
                try:
                    res = client.bulk_put_rates([seg])
                    n = int(res.get("rows_upserted", 0))
                    rows_upserted += n
                    self._audit.event(
                        "rates",
                        "rates_bulk",
                        ref,
                        "created",
                        f"rows_upserted={n}",
                    )
                except Exception as exc:  # noqa: BLE001
                    stats.errors += 1
                    self._rg.add_error(
                        entity="rates",
                        ref=f"{rt_id}/{rp_id}",
                        message=str(exc),
                    )
                    self._audit.event(
                        "rates",
                        "rates_bulk",
                        ref,
                        "error",
                        str(exc),
                        level=logging.ERROR,
                    )
        stats.total = len(pairs)
        stats.created = rows_upserted

    def _guest_processed_skip(self, g: GuestRecord) -> bool:
        if not (self._state and self._run_id):
            return False
        done, _ = self._state.is_processed(self._run_id, "guest", g.external_id)
        return done

    def _guest_lookup_openpms(self, client: OpenPMSClient, email_norm: str) -> str | None:
        if self._state and self._run_id:
            mid = self._state.get_mapping(self._run_id, "guest_email", email_norm)
            if mid:
                return mid
        page = client.list_guests(q=email_norm, limit=10)
        for item in page.get("items", []):
            if str(item.get("email", "")).strip().lower() == email_norm:
                gid = str(item["id"])
                if self._state and self._run_id:
                    self._state.put_mapping(
                        self._run_id,
                        "guest_email",
                        email_norm,
                        gid,
                    )
                return gid
        return None

    def _finalize_guest_existing(
        self,
        client: OpenPMSClient,
        g: GuestRecord,
        openpms_guest_id: str,
        stats: StageStats,
    ) -> None:
        if self._on_conflict == "fail":
            raise OnConflictFailError(
                f"guest {g.external_id!r} matches existing OpenPMS guest {openpms_guest_id}",
            )
        if self._on_conflict == "skip":
            stats.existed += 1
            if self._state and self._run_id:
                self._state.mark_processed(
                    self._run_id,
                    "guest",
                    g.external_id,
                    openpms_id=openpms_guest_id,
                    result="existed",
                )
            self._audit.event(
                "guests",
                "guest",
                g.external_id,
                "existed",
                openpms_guest_id,
            )
            return
        patch = _guest_patch_payload(g)
        if patch:
            client.patch_guest(UUID(openpms_guest_id), patch)
        stats.updated += 1
        if self._state and self._run_id:
            self._state.mark_processed(
                self._run_id,
                "guest",
                g.external_id,
                openpms_id=openpms_guest_id,
                result="updated",
            )
        self._audit.event(
            "guests",
            "guest",
            g.external_id,
            "updated",
            openpms_guest_id,
        )

    def _handle_guest_post_conflict(
        self,
        client: OpenPMSClient,
        g: GuestRecord,
        email_norm: str,
        stats: StageStats,
    ) -> None:
        gid = self._guest_lookup_openpms(client, email_norm)
        if not gid:
            stats.skipped += 1
            if self._state and self._run_id:
                self._state.mark_processed(
                    self._run_id,
                    "guest",
                    g.external_id,
                    openpms_id="",
                    result="skipped",
                )
            self._audit.event(
                "guests",
                "guest",
                g.external_id,
                "skipped",
                "409 unresolved guest",
            )
            return
        if self._on_conflict == "fail":
            raise OnConflictFailError(f"guest {g.external_id!r} duplicate email (409)")
        if self._on_conflict == "skip":
            stats.skipped += 1
            if self._state and self._run_id:
                self._state.mark_processed(
                    self._run_id,
                    "guest",
                    g.external_id,
                    openpms_id=gid,
                    result="skipped",
                )
            self._audit.event(
                "guests",
                "guest",
                g.external_id,
                "skipped",
                "409 conflict",
            )
            return
        patch = _guest_patch_payload(g)
        if patch:
            client.patch_guest(UUID(gid), patch)
        stats.updated += 1
        if self._state and self._run_id:
            self._state.mark_processed(
                self._run_id,
                "guest",
                g.external_id,
                openpms_id=gid,
                result="updated",
            )
        self._audit.event("guests", "guest", g.external_id, "updated", gid)

    def _stage_guests(self, client: OpenPMSClient, guests: list[GuestRecord]) -> None:
        raw_n = len(guests)
        guests, n_src_dup = _dedupe_guests_by_email(guests, self._audit)
        stats = StageStats(total=raw_n)
        stats.skipped += n_src_dup
        self._report.stages["guests"] = stats

        n_work = len(guests)
        done = 0
        for chunk in _iter_batches(guests, self._batch_size):
            for g in chunk:
                if self._guest_processed_skip(g):
                    stats.skipped += 1
                    self._audit.event(
                        "guests",
                        "guest",
                        g.external_id,
                        "skipped",
                        "resume: already processed",
                    )
                else:
                    email_norm = _safe_email_for_guest(g)
                    if not _is_synthetic_guest_email(email_norm):
                        existing_id = self._guest_lookup_openpms(client, email_norm)
                        if existing_id:
                            self._finalize_guest_existing(
                                client,
                                g,
                                existing_id,
                                stats,
                            )
                        else:
                            body: dict = {
                                "first_name": g.first_name,
                                "last_name": g.last_name,
                                "email": email_norm,
                                "phone": _safe_phone(g),
                                "notes": g.notes,
                                "vip_status": bool(g.vip_status),
                            }
                            if g.nationality and len(g.nationality) == 2:
                                body["nationality"] = g.nationality.upper()
                            try:
                                row = client.create_guest(body)
                                stats.created += 1
                                gid = str(row["id"])
                                if self._state and self._run_id:
                                    self._state.mark_processed(
                                        self._run_id,
                                        "guest",
                                        g.external_id,
                                        openpms_id=gid,
                                        result="created",
                                    )
                                    if not _is_synthetic_guest_email(email_norm):
                                        self._state.put_mapping(
                                            self._run_id,
                                            "guest_email",
                                            email_norm,
                                            gid,
                                        )
                                self._audit.event(
                                    "guests",
                                    "guest",
                                    g.external_id,
                                    "created",
                                    gid,
                                )
                            except APIConflictError:
                                self._handle_guest_post_conflict(
                                    client,
                                    g,
                                    email_norm,
                                    stats,
                                )
                            except APIValidationError:
                                body.pop("nationality", None)
                                try:
                                    row = client.create_guest(body)
                                    stats.created += 1
                                    gid = str(row["id"])
                                    if self._state and self._run_id:
                                        self._state.mark_processed(
                                            self._run_id,
                                            "guest",
                                            g.external_id,
                                            openpms_id=gid,
                                            result="created",
                                        )
                                        if not _is_synthetic_guest_email(email_norm):
                                            self._state.put_mapping(
                                                self._run_id,
                                                "guest_email",
                                                email_norm,
                                                gid,
                                            )
                                    self._audit.event(
                                        "guests",
                                        "guest",
                                        g.external_id,
                                        "created",
                                        gid,
                                    )
                                except APIConflictError:
                                    self._handle_guest_post_conflict(
                                        client,
                                        g,
                                        email_norm,
                                        stats,
                                    )
                                except Exception as exc2:  # noqa: BLE001
                                    stats.errors += 1
                                    self._rg.add_error(
                                        entity="guest",
                                        ref=g.external_id,
                                        message=str(exc2),
                                    )
                                    self._audit.event(
                                        "guests",
                                        "guest",
                                        g.external_id,
                                        "error",
                                        str(exc2),
                                        level=logging.ERROR,
                                    )
                            except Exception as exc:  # noqa: BLE001
                                stats.errors += 1
                                self._rg.add_error(
                                    entity="guest",
                                    ref=g.external_id,
                                    message=str(exc),
                                )
                                self._audit.event(
                                    "guests",
                                    "guest",
                                    g.external_id,
                                    "error",
                                    str(exc),
                                    level=logging.ERROR,
                                )
                    else:
                        body = {
                            "first_name": g.first_name,
                            "last_name": g.last_name,
                            "email": email_norm,
                            "phone": _safe_phone(g),
                            "notes": g.notes,
                            "vip_status": bool(g.vip_status),
                        }
                        if g.nationality and len(g.nationality) == 2:
                            body["nationality"] = g.nationality.upper()
                        try:
                            row = client.create_guest(body)
                            stats.created += 1
                            gid = str(row["id"])
                            if self._state and self._run_id:
                                self._state.mark_processed(
                                    self._run_id,
                                    "guest",
                                    g.external_id,
                                    openpms_id=gid,
                                    result="created",
                                )
                            self._audit.event(
                                "guests",
                                "guest",
                                g.external_id,
                                "created",
                                gid,
                            )
                        except APIConflictError:
                            self._handle_guest_post_conflict(
                                client,
                                g,
                                email_norm,
                                stats,
                            )
                        except APIValidationError:
                            body.pop("nationality", None)
                            try:
                                row = client.create_guest(body)
                                stats.created += 1
                                gid = str(row["id"])
                                if self._state and self._run_id:
                                    self._state.mark_processed(
                                        self._run_id,
                                        "guest",
                                        g.external_id,
                                        openpms_id=gid,
                                        result="created",
                                    )
                                self._audit.event(
                                    "guests",
                                    "guest",
                                    g.external_id,
                                    "created",
                                    gid,
                                )
                            except APIConflictError:
                                self._handle_guest_post_conflict(
                                    client,
                                    g,
                                    email_norm,
                                    stats,
                                )
                            except Exception as exc2:  # noqa: BLE001
                                stats.errors += 1
                                self._rg.add_error(
                                    entity="guest",
                                    ref=g.external_id,
                                    message=str(exc2),
                                )
                                self._audit.event(
                                    "guests",
                                    "guest",
                                    g.external_id,
                                    "error",
                                    str(exc2),
                                    level=logging.ERROR,
                                )
                        except Exception as exc:  # noqa: BLE001
                            stats.errors += 1
                            self._rg.add_error(
                                entity="guest",
                                ref=g.external_id,
                                message=str(exc),
                            )
                            self._audit.event(
                                "guests",
                                "guest",
                                g.external_id,
                                "error",
                                str(exc),
                                level=logging.ERROR,
                            )
                done += 1

            log.info("guests progress: %s/%s", done, n_work)
            self._audit.event(
                "guests",
                "progress",
                f"{done}/{n_work}",
                "chunk_done",
                f"n={len(chunk)}",
            )

    def _resolve_booking_ids(
        self,
        client: OpenPMSClient,
    ) -> tuple[dict[str, UUID], dict[str, UUID]]:
        rt_map = self._resolve_room_type_ids(client)
        rp_map = self._rate_plan_map(client)
        return rt_map, rp_map

    def _booking_processed_skip(self, b: BookingRecord) -> bool:
        if not (self._state and self._run_id):
            return False
        done, _ = self._state.is_processed(self._run_id, "booking", b.external_id)
        return done

    def _sync_booking_lifecycle_status(
        self,
        client: OpenPMSClient,
        booking_id: UUID,
        current: str,
        target: str,
    ) -> None:
        if target == current:
            return
        try:
            client.patch_booking(booking_id, {"status": target})
        except Exception as exc:  # noqa: BLE001
            self._audit.event(
                "bookings",
                "booking",
                str(booking_id),
                "warning",
                f"patch status {target!r}: {exc}",
                level=logging.WARNING,
            )

    def _handle_booking_found_existing(
        self,
        client: OpenPMSClient,
        b: BookingRecord,
        row: dict,
        stats: StageStats,
        *,
        reason: str,
    ) -> None:
        bid = UUID(str(row["id"]))
        cur_status = str(row.get("status") or "")
        if self._on_conflict == "fail":
            raise OnConflictFailError(
                f"booking {b.external_id!r} already exists ({reason})",
            )
        if self._on_conflict == "skip":
            if reason == "precheck":
                stats.existed += 1
                res = "existed"
            else:
                stats.skipped += 1
                res = "skipped"
            if self._state and self._run_id:
                self._state.mark_processed(
                    self._run_id,
                    "booking",
                    b.external_id,
                    openpms_id=str(bid),
                    result=res,
                )
            self._audit.event(
                "bookings",
                "booking",
                b.external_id,
                "existed" if reason == "precheck" else "skipped",
                str(bid),
            )
            return
        self._sync_booking_lifecycle_status(client, bid, cur_status, b.status)
        stats.updated += 1
        if self._state and self._run_id:
            self._state.mark_processed(
                self._run_id,
                "booking",
                b.external_id,
                openpms_id=str(bid),
                result="updated",
            )
        self._audit.event(
            "bookings",
            "booking",
            b.external_id,
            "updated",
            str(bid),
        )

    def _try_create_booking(
        self,
        client: OpenPMSClient,
        b: BookingRecord,
        rt_id: UUID,
        rp_id: UUID,
        stats: StageStats,
    ) -> None:
        body = {
            "property_id": str(self._property_id),
            "room_type_id": str(rt_id),
            "rate_plan_id": str(rp_id),
            "check_in": b.check_in.isoformat(),
            "check_out": b.check_out.isoformat(),
            "guest": {
                "first_name": b.guest.first_name,
                "last_name": b.guest.last_name,
                "email": str(b.guest.email).strip().lower(),
                "phone": b.guest.phone,
            },
            "status": "confirmed",
            "source": (b.source or "migration")[:64],
            "force_new_guest": False,
            "external_booking_id": b.external_id[:128],
        }
        try:
            out = client.create_booking(body)
            stats.created += 1
            bid = UUID(str(out["booking_id"]))
            if self._state and self._run_id:
                self._state.mark_processed(
                    self._run_id,
                    "booking",
                    b.external_id,
                    openpms_id=str(bid),
                    result="created",
                )
            self._audit.event(
                "bookings",
                "booking",
                b.external_id,
                "created",
                str(bid),
            )
            target_status = b.status
            if target_status not in {"pending", "confirmed"}:
                self._sync_booking_lifecycle_status(
                    client,
                    bid,
                    "confirmed",
                    target_status,
                )
        except APIConflictError:
            row2 = client.get_booking_by_external_id(b.external_id)
            if row2:
                self._handle_booking_found_existing(
                    client,
                    b,
                    row2,
                    stats,
                    reason="409",
                )
            else:
                stats.skipped += 1
                if self._state and self._run_id:
                    self._state.mark_processed(
                        self._run_id,
                        "booking",
                        b.external_id,
                        openpms_id="",
                        result="skipped",
                    )
                self._audit.event(
                    "bookings",
                    "booking",
                    b.external_id,
                    "skipped",
                    "409 duplicate external_booking_id (no row)",
                )

    def _stage_bookings(self, client: OpenPMSClient, bookings: list[BookingRecord]) -> None:
        stats = StageStats(total=len(bookings))
        self._report.stages["bookings"] = stats
        rt_map, rp_map = self._resolve_booking_ids(client)

        total = len(bookings)
        done = 0
        for chunk in _iter_batches(bookings, self._batch_size):
            for b in chunk:
                if self._booking_processed_skip(b):
                    stats.skipped += 1
                    self._audit.event(
                        "bookings",
                        "booking",
                        b.external_id,
                        "skipped",
                        "resume: already processed",
                    )
                else:
                    rk = b.room_type_name.strip().lower()
                    pk = b.rate_plan_name.strip().lower()
                    rt_id = rt_map.get(rk)
                    rp_id = rp_map.get(pk)
                    if rt_id is None or rp_id is None:
                        stats.errors += 1
                        msg = f"missing room type or rate plan ({rk!r}, {pk!r})"
                        self._rg.add_error(
                            entity="booking",
                            ref=b.external_id,
                            message=msg,
                        )
                        self._audit.event(
                            "bookings",
                            "booking",
                            b.external_id,
                            "error",
                            msg,
                            level=logging.ERROR,
                        )
                    elif self._precheck_bookings:
                        row = client.get_booking_by_external_id(b.external_id)
                        if row:
                            self._handle_booking_found_existing(
                                client,
                                b,
                                row,
                                stats,
                                reason="precheck",
                            )
                        else:
                            try:
                                self._try_create_booking(client, b, rt_id, rp_id, stats)
                            except OnConflictFailError:
                                raise
                            except Exception as exc:  # noqa: BLE001
                                stats.errors += 1
                                self._rg.add_error(
                                    entity="booking",
                                    ref=b.external_id,
                                    message=str(exc),
                                )
                                self._audit.event(
                                    "bookings",
                                    "booking",
                                    b.external_id,
                                    "error",
                                    str(exc),
                                    level=logging.ERROR,
                                )
                    else:
                        try:
                            self._try_create_booking(client, b, rt_id, rp_id, stats)
                        except OnConflictFailError:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            stats.errors += 1
                            self._rg.add_error(
                                entity="booking",
                                ref=b.external_id,
                                message=str(exc),
                            )
                            self._audit.event(
                                "bookings",
                                "booking",
                                b.external_id,
                                "error",
                                str(exc),
                                level=logging.ERROR,
                            )
                done += 1

            log.info("bookings progress: %s/%s", done, total)
            self._audit.event(
                "bookings",
                "progress",
                f"{done}/{total}",
                "chunk_done",
                f"n={len(chunk)}",
            )

    def _stage_verify(self, client: OpenPMSClient, bookings: list[BookingRecord]) -> None:
        stats = StageStats(total=1)
        self._report.stages["verify"] = stats
        if not bookings:
            stats.skipped = 1
            self._audit.event("verify", "verify", "list_bookings", "skipped", "no bookings")
            return
        start = min(b.check_in for b in bookings)
        end = max(b.check_out for b in bookings)
        try:
            page = client.list_bookings_window(
                property_id=self._property_id,
                start_date=start,
                end_date=end,
                limit=500,
            )
            n = len(page.get("items", []))
            log.info("verify: bookings visible in window: %s", n)
            stats.created = 1
            self._audit.event(
                "verify",
                "verify",
                "list_bookings",
                "ok",
                f"items_in_window={n}",
            )
        except Exception as exc:  # noqa: BLE001
            stats.errors = 1
            self._rg.add_error(entity="verify", ref="list_bookings", message=str(exc))
            self._audit.event(
                "verify",
                "verify",
                "list_bookings",
                "error",
                str(exc),
                level=logging.ERROR,
            )
