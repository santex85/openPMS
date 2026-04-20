"""CLI entrypoint for OpenPMS migration (`python -m scripts.migrate`)."""

from __future__ import annotations

import glob as glob_mod
import logging
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Literal

import click

from scripts.migrate.adapters.preno import PrenoAdapter
from scripts.migrate.core.audit_log import MigrationAuditLogger, setup_migration_audit_logger
from scripts.migrate.core.client import OpenPMSClient
from scripts.migrate.core.pipeline import MigrationPipeline
from scripts.migrate.core.state import StateStore

log = logging.getLogger(__name__)


def _collect_source_paths(
    guests: str | None,
    bookings: str | None,
    rooms: str | None,
) -> list[str]:
    paths: list[str] = []
    for pattern in (guests, bookings):
        if pattern:
            paths.extend(glob_mod.glob(pattern))
    if rooms:
        p = Path(rooms)
        if p.is_file():
            paths.append(str(p.resolve()))
    resolved: set[str] = set()
    for p in paths:
        try:
            resolved.add(str(Path(p).resolve()))
        except OSError:
            resolved.add(p)
    return sorted(resolved)


def _configure_app_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--source", type=click.Choice(["preno"]), required=True)
@click.option(
    "--api-url",
    default="http://localhost:8000",
    show_default=True,
    help="OpenPMS API base URL",
)
@click.option("--api-token", default="", help="JWT Bearer token")
@click.option("--property-id", required=True, type=click.UUID)
@click.option("--guests", default=None, help="Glob for Preno guests_export_*.csv")
@click.option("--bookings", default=None, help="Glob for Preno bookings_report_*.csv")
@click.option("--rooms", default=None, help="Optional single rooms_export.csv path")
@click.option("--dry-run", is_flag=True, help="Analyze only; no API writes")
@click.option(
    "--on-conflict",
    type=click.Choice(["skip", "update", "fail"]),
    default="skip",
    show_default=True,
    help=(
        "Guests/bookings: skip=ignore duplicates; update=PATCH existing; "
        "fail=abort run on first duplicate"
    ),
)
@click.option("--include-cancelled", is_flag=True, help="Import cancelled Preno rows")
@click.option("--batch-size", default=50, show_default=True, type=int)
@click.option(
    "--default-night-rate",
    default="100.00",
    show_default=True,
    help="Placeholder nightly rate (decimal) for /rates/bulk seeding",
)
@click.option("--report", default=None, type=click.Path(path_type=Path), help="Write report file")
@click.option(
    "--resume",
    is_flag=True,
    help="Continue from SQLite state: skip completed stages and processed guests/bookings",
)
@click.option(
    "--state",
    default="migration_state.sqlite3",
    show_default=True,
    type=click.Path(path_type=Path),
    help="SQLite state file for progress / resume",
)
@click.option(
    "--log-file",
    default="migration.log",
    show_default=True,
    type=str,
    help="Structured audit log path (TZ §7); use --no-log-file to disable",
)
@click.option(
    "--no-log-file",
    is_flag=True,
    help="Do not write migration.log to disk (stdout only)",
)
@click.option(
    "--log-level",
    default="INFO",
    show_default=True,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
)
@click.option(
    "--precheck-bookings",
    is_flag=True,
    help="Before POST, GET /bookings?external_booking_id=… to skip or update existing rows",
)
@click.option("-v", "--verbose", is_flag=True)
def main(
    source: str,
    api_url: str,
    api_token: str,
    property_id: uuid.UUID,
    guests: str | None,
    bookings: str | None,
    rooms: str | None,
    dry_run: bool,
    on_conflict: Literal["skip", "update", "fail"],
    include_cancelled: bool,
    batch_size: int,
    default_night_rate: str,
    report: Path | None,
    resume: bool,
    state: Path,
    log_file: str,
    no_log_file: bool,
    log_level: str,
    precheck_bookings: bool,
    verbose: bool,
) -> None:
    if resume and dry_run:
        raise click.UsageError("--resume cannot be used together with --dry-run")

    _configure_app_logging(verbose)
    audit_level = getattr(logging, log_level.upper(), logging.INFO)
    lf = None if no_log_file else (log_file or None)
    audit_backend = setup_migration_audit_logger(
        log_file=lf,
        stream=True,
        level=audit_level,
    )
    audit = MigrationAuditLogger(audit_backend)

    if source != "preno":
        raise click.ClickException("Only --source preno is supported in this release.")

    source_paths = _collect_source_paths(guests, bookings, rooms)

    adapter: PrenoAdapter = PrenoAdapter(
        guests_glob=guests,
        bookings_glob=bookings,
        rooms_csv=rooms,
        include_cancelled=include_cancelled,
    )

    client: OpenPMSClient | None = None
    store: StateStore | None = None
    if not dry_run:
        if not api_token.strip():
            raise click.ClickException("--api-token is required unless --dry-run")
        client = OpenPMSClient(api_url, api_token)
        store = StateStore(state)

    try:
        try:
            night_rate = Decimal(default_night_rate)
        except Exception as exc:
            raise click.ClickException(f"Invalid --default-night-rate: {exc}") from exc

        pipeline = MigrationPipeline(
            adapter,
            client,
            property_id=property_id,
            property_label=str(property_id),
            source_name="Preno",
            dry_run=dry_run,
            on_conflict=on_conflict,
            batch_size=batch_size,
            default_night_rate=night_rate,
            audit=audit,
            state=store,
            resume=resume,
            source_paths=source_paths,
            precheck_bookings=precheck_bookings,
        )
        result = pipeline.run()
        rg = pipeline.report_generator
        rg.print()
        if report is not None:
            report.write_text(rg.text(), encoding="utf-8")
            json_path = report.with_suffix(".json")
            json_path.write_text(rg.to_json(), encoding="utf-8")
            log.info("Wrote %s and %s", report, json_path)

        if result.final_status == "FAILED":
            sys.exit(1)
        if result.final_status == "PARTIAL":
            sys.exit(2)
    finally:
        if client is not None:
            client.close()
        if store is not None:
            store.close()


if __name__ == "__main__":
    main()
