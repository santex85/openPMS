"""Booking status FSM: allowed and forbidden PATCH transitions."""

from __future__ import annotations

from uuid import UUID

import pytest

from app.domain.booking_status import (
    BookingStatusTransitionError,
    validate_status_transition,
)


def test_status_happy_path_confirmed_to_checked_out(
    client,
    folio_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tenant_id)
    r1 = client.patch(
        f"/bookings/{booking_id}", headers=h, json={"status": "checked_in"}
    )
    assert r1.status_code == 204
    r2 = client.patch(
        f"/bookings/{booking_id}", headers=h, json={"status": "checked_out"}
    )
    assert r2.status_code in (200, 204)


def test_patch_confirmed_to_checked_out_rejected(
    client, folio_scenario: dict, auth_headers
) -> None:
    tenant_id: UUID = folio_scenario["tenant_id"]  # type: ignore[assignment]
    booking_id: UUID = folio_scenario["booking_id"]  # type: ignore[assignment]
    h = auth_headers(tenant_id)
    r = client.patch(
        f"/bookings/{booking_id}", headers=h, json={"status": "checked_out"}
    )
    assert r.status_code == 409


def test_fsm_allows_pending_confirmed_cancelled() -> None:
    validate_status_transition("pending", "confirmed")
    validate_status_transition("pending", "cancelled")
    validate_status_transition("confirmed", "checked_in")
    validate_status_transition("confirmed", "cancelled")
    validate_status_transition("confirmed", "no_show")
    validate_status_transition("checked_in", "checked_out")


def test_fsm_idempotent() -> None:
    validate_status_transition("confirmed", "confirmed")


def test_fsm_forbidden_confirmed_checked_out() -> None:
    with pytest.raises(BookingStatusTransitionError):
        validate_status_transition("confirmed", "checked_out")


def test_fsm_forbidden_checked_out_to_checked_in() -> None:
    with pytest.raises(BookingStatusTransitionError):
        validate_status_transition("checked_out", "checked_in")


def test_fsm_forbidden_checked_in_cancelled() -> None:
    with pytest.raises(BookingStatusTransitionError):
        validate_status_transition("checked_in", "cancelled")
