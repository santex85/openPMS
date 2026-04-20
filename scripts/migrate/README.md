# OpenPMS migration CLI (`scripts.migrate`)

Imports historical data from **Preno** CSV exports into OpenPMS via the REST API.

## Run

From the OpenPMS repo root (with dependencies installed, `PYTHONPATH` set):

```bash
cd /path/to/OpenPMS
PYTHONPATH=. python -m scripts.migrate --help
```

Example dry-run:

```bash
PYTHONPATH=. python -m scripts.migrate \
  --source preno \
  --property-id YOUR-PROPERTY-UUID \
  --guests './guests_export_*.csv' \
  --bookings './bookings_report_*.csv' \
  --dry-run
```

Full import (requires JWT with write scopes):

```bash
PYTHONPATH=. python -m scripts.migrate \
  --source preno \
  --api-url http://localhost:8000 \
  --api-token "$JWT" \
  --property-id YOUR-PROPERTY-UUID \
  --guests './guests_export_*.csv' \
  --bookings './bookings_report_*.csv' \
  --report ./migration-report.txt
```

Bookings are created with `external_booking_id` set to Preno **Booking ID**; duplicates return HTTP **409** and are skipped when `--on-conflict skip` (default).

Placeholder nightly rates are seeded with `--default-night-rate` (default `100.00`) for each `(room_type, rate_plan)` pair over the stay date range so `POST /bookings` pricing succeeds.

## State file and resume (v1.1)

Progress and idempotency for **guests** and **bookings** (plus room type / rate plan name → UUID mappings) are stored in a **SQLite** file.

- **`--state`** — path to the SQLite DB (default: `./migration_state.sqlite3`).
- **`--resume`** — skip pipeline stages already marked `done`, and skip guests/bookings already recorded in state.

The **run id** is derived from `--property-id` and resolved paths + mtimes of input CSV files, so the same import uses the same state file across restarts.

```bash
# First run (creates / updates state DB)
PYTHONPATH=. python -m scripts.migrate \
  --source preno \
  --api-url http://localhost:8000 \
  --api-token "$JWT" \
  --property-id YOUR-PROPERTY-UUID \
  --guests './guests_export_*.csv' \
  --bookings './bookings_report_*.csv' \
  --state ./migration_state.sqlite3

# After an interruption — same globs, property, and state path:
PYTHONPATH=. python -m scripts.migrate \
  --resume \
  --state ./migration_state.sqlite3 \
  --source preno \
  --api-url http://localhost:8000 \
  --api-token "$JWT" \
  --property-id YOUR-PROPERTY-UUID \
  --guests './guests_export_*.csv' \
  --bookings './bookings_report_*.csv'
```

`--resume` cannot be combined with `--dry-run`.

To run a **fresh** full import with the same CSVs, remove or rename the state file (otherwise the same run id reuses old progress).

## Structured log (`migration.log`, v1.1)

Audit lines follow TZ §7:

`timestamp | stage | entity | source_id | result | details`

- **`--log-file`** — file path (default `./migration.log`).
- **`--no-log-file`** — log only to stdout (no file).
- **`--log-level`** — `DEBUG`, `INFO`, `WARNING`, `ERROR` for the audit logger.
- **`-v` / `--verbose`** — raises general app loggers to `DEBUG` (HTTP library noise may increase).

## Rate limiting (v1.2)

The migration HTTP client (`OpenPMSClient`) retries transient failures with **tenacity**:

- Up to **5** attempts per request.
- **HTTP 429**: respects **`Retry-After`** when present (seconds or HTTP-date); otherwise exponential backoff **1 → 2 → 4 → 8 → 16** seconds.
- **HTTP 5xx** and **transport errors**: same exponential backoff.
- Retries are logged on the logger **`openpms.migration.client`** at **WARNING** (`retry status=… wait=…`).

## Deduplication (v1.2)

- **Guests (source)**: before POST, rows with the same real **email** (non-synthetic) are collapsed to a single row (first by `external_id`); duplicates are counted as **`skipped`** on the guests stage and emit `source_dup` audit lines. Synthetic emails (`…@migrate.openpms.local`) are not collapsed by email.
- **Guests (OpenPMS)**: for real emails, the client runs **`GET /guests?q=<email>`** and applies **`--on-conflict`** when an exact email match exists. Mappings **`guest_email`** → OpenPMS guest id are stored in **`--state`** SQLite to avoid repeat lookups.
- **Bookings**: optional **`--precheck-bookings`** calls **`GET /bookings?external_booking_id=…`** before **`POST /bookings`**. Without the flag, duplicates are still detected via **HTTP 409** on `external_booking_id` (P0 behavior).

## On-conflict strategies (v1.2)

**`--on-conflict`** applies to **guests** and **bookings** only (`skip` | `update` | `fail`):

| Mode | Guests | Bookings |
|------|--------|----------|
| **skip** (default) | Skip when the email already exists (preflight or 409). | Skip on 409; with **`--precheck-bookings`**, count as **existed** when found. |
| **update** | **`PATCH /guests/{id}`** with non-empty `phone`, `notes`, `nationality`, `vip_status`. | **`PATCH /bookings/{id}`** with **`status`** when it differs from the source row (invalid transitions may log a **WARNING**). |
| **fail** | Abort the run on the first duplicate (**`OnConflictFailError`** → report **`FAILED`**). | Same. |

Example with pre-check and update-on-duplicate:

```bash
PYTHONPATH=. python -m scripts.migrate \
  --source preno \
  --api-url http://localhost:8000 \
  --api-token "$JWT" \
  --property-id YOUR-PROPERTY-UUID \
  --guests './guests_export_*.csv' \
  --bookings './bookings_report_*.csv' \
  --on-conflict update \
  --precheck-bookings
```

## Batching / progress (v1.2)

**`--batch-size`** (default **50**) chunks **guest** and **booking** processing for progress logging: after each chunk, stdout logs `guests progress:` / `bookings progress:` and the audit logger records a **`progress`** / **`chunk_done`** line. Room bulk creation still uses API chunks of **200** (unchanged).

## Integration tests (MIG-19 / MIG-20)

Optional pytest tests under [`scripts/migrate/tests/integration/`](tests/integration/) are marked **`integration`** and **skip** unless env vars are set.

| Variable | Used for |
|----------|----------|
| `MIG_SATVA_DIR` | Directory containing `guests_export_*.csv` and `bookings_report*.csv` (Satva Samui export). |
| `MIG_OPENPMS_URL` | API base URL (e.g. `http://localhost:8000`). |
| `MIG_OPENPMS_TOKEN` | JWT with write scopes. |
| `MIG_PROPERTY_ID` | Target property UUID. |

Examples (from repo root, venv active):

```bash
# Unit + integration dry-run only (no API writes)
export MIG_SATVA_DIR=/path/to/satva/csv
pytest -m integration scripts/migrate/tests/integration/test_dry_run_satva.py

# Full migration + resume idempotency (writes to OpenPMS)
export MIG_SATVA_DIR=...
export MIG_OPENPMS_URL=http://localhost:8000
export MIG_OPENPMS_TOKEN="$JWT"
export MIG_PROPERTY_ID="00000000-0000-0000-0000-000000000000"
pytest -m integration scripts/migrate/tests/integration/test_full_migration_localhost.py
```

Default CI / local run without these variables: **`pytest scripts/migrate/tests/`** — integration tests are skipped; extended **PrenoAdapter** unit tests in `test_preno_adapter_mapping.py` always run.
