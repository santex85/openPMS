# TM30 extension (integrator) — webhook contract

OpenPMS does not implement TM30 filing in core. A tenant registers a **`country_pack_extensions`** row (e.g. code `th_tm30`) with a `webhook_url`, then activates it on a property via **`property_extensions`**.

## Trigger

When a booking transitions to **`checked_in`**:

1. Tenant **webhook subscriptions** (if any) receive **`guest.checked_in`** via the normal `WebhookSubscription` delivery pipeline.
2. Each **active** `property_extensions` row (with an active `country_pack_extensions` definition) receives a direct **HTTP POST** to `webhook_url`:

```json
{
  "event": "guest.checked_in",
  "extension_code": "th_tm30",
  "data": { "...": "see table below" },
  "property_extension_config": { "property_registration_number": "..." }
}
```

Header: `X-OpenPMS-Event: guest.checked_in`

The `data` object includes, in addition to `booking_id` / `guest_id` / `room_id`:

| Field | Description |
|-------|-------------|
| `property_id` | UUID |
| `first_name`, `last_name` | Guest |
| `nationality` | ISO 3166-1 alpha-2 when present |
| `passport_data` | Opaque string from PMS |
| `date_of_birth` | ISO date string or null |
| `check_in_date` | From booking snapshot when available |
| `property_address` | Currently property `name` (placeholder) |
| `property_registration_number` | Reserved (null unless supplied by future config) |

## Required guest fields (activation)

Configure `required_fields` on the extension JSON (e.g. `["passport_data", "nationality", "date_of_birth"]`). If any are missing at check-in, the API returns **400** and the status is not updated.

## Property settings (`config`)

Store integrator-specific values in `property_extensions.config` (validated in your API client against `ui_config_schema` if you publish JSON Schema). Examples: `property_registration_number`, `tm30_report_email`, `auto_submit`.
