"""HTTP client for OpenPMS REST API (migration / bulk import)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any
from uuid import UUID

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
)

_log = logging.getLogger("openpms.migration.client")


class APIRateLimitError(Exception):
    """HTTP 429 — rate limited; optional Retry-After hint."""

    def __init__(
        self,
        retry_after: float | None = None,
        *,
        message: str = "rate limited",
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.status_code = 429


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Parse Retry-After header: delta-seconds or HTTP-date."""
    raw = response.headers.get("Retry-After")
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip()
    try:
        sec = float(text)
        if sec >= 0:
            return sec
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(text)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delay = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delay)
    except (TypeError, ValueError, OSError):
        return None


def _exp_wait_seconds(attempt_number: int) -> float:
    """1, 2, 4, 8, 16 seconds for successive retries (1-based attempt after failure)."""
    idx = max(0, attempt_number - 1)
    return float(min(2**idx, 16))


def _wait_retry(retry_state: RetryCallState) -> float:
    exc = retry_state.outcome.exception()
    if isinstance(exc, APIRateLimitError):
        if exc.retry_after is not None and exc.retry_after >= 0:
            return float(exc.retry_after)
        return _exp_wait_seconds(retry_state.attempt_number)
    return _exp_wait_seconds(retry_state.attempt_number)


def _before_sleep_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception()
    wait_s = _wait_retry(retry_state)
    status = getattr(exc, "status_code", None)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
    elif isinstance(exc, APIRateLimitError):
        status = 429
    _log.warning(
        "retry status=%s wait=%.1fs attempt=%s/5 exc=%s",
        status,
        wait_s,
        retry_state.attempt_number,
        type(exc).__name__,
    )


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, APIRateLimitError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


class APIConflictError(Exception):
    """HTTP 409 — duplicate or conflict."""


class APIValidationError(Exception):
    """HTTP 422 — validation failed on server."""


class OpenPMSClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 60.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base,
            headers={
                "Authorization": f"Bearer {token.strip()}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenPMSClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @retry(
        stop=stop_after_attempt(5),
        wait=_wait_retry,
        retry=retry_if_exception(_is_retryable),
        before_sleep=_before_sleep_retry,
        reraise=True,
    )
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
    ) -> httpx.Response:
        r = self._client.request(method, path, params=params, json=json)
        if r.status_code == 429:
            raise APIRateLimitError(_parse_retry_after(r))
        if r.status_code == 409:
            raise APIConflictError(r.text)
        if r.status_code == 422:
            raise APIValidationError(r.text)
        r.raise_for_status()
        return r

    # --- Room types ---

    def list_room_types(self, property_id: UUID) -> list[dict[str, Any]]:
        r = self._request(
            "GET",
            "/room-types",
            params={"property_id": str(property_id)},
        )
        return r.json()

    def create_room_type(
        self,
        *,
        property_id: UUID,
        name: str,
        base_occupancy: int = 2,
        max_occupancy: int = 4,
    ) -> dict[str, Any]:
        r = self._request(
            "POST",
            "/room-types",
            json={
                "property_id": str(property_id),
                "name": name,
                "base_occupancy": base_occupancy,
                "max_occupancy": max_occupancy,
            },
        )
        return r.json()

    # --- Rooms bulk ---

    def bulk_create_rooms(
        self,
        *,
        room_type_id: UUID,
        rooms: list[dict[str, str]],
        on_conflict: str = "skip",
    ) -> dict[str, Any]:
        r = self._request(
            "POST",
            "/rooms/bulk",
            json={
                "room_type_id": str(room_type_id),
                "rooms": rooms,
                "on_conflict": on_conflict,
            },
        )
        return r.json()

    # --- Rate plans ---

    def list_rate_plans(self, property_id: UUID) -> list[dict[str, Any]]:
        r = self._request(
            "GET",
            "/rate-plans",
            params={"property_id": str(property_id)},
        )
        return r.json()

    def create_rate_plan(
        self,
        *,
        property_id: UUID,
        name: str,
        cancellation_policy: str = "standard",
    ) -> dict[str, Any]:
        r = self._request(
            "POST",
            "/rate-plans",
            json={
                "property_id": str(property_id),
                "name": name,
                "cancellation_policy": cancellation_policy,
            },
        )
        return r.json()

    # --- Nightly rates ---

    def bulk_put_rates(
        self,
        segments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        r = self._request("PUT", "/rates/bulk", json={"segments": segments})
        return r.json()

    # --- Guests ---

    def list_guests(self, *, q: str | None = None, limit: int = 100) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": 0}
        if q:
            params["q"] = q
        r = self._request("GET", "/guests", params=params)
        return r.json()

    def create_guest(self, body: dict[str, Any]) -> dict[str, Any]:
        r = self._request("POST", "/guests", json=body)
        return r.json()

    def patch_guest(self, guest_id: UUID, body: dict[str, Any]) -> dict[str, Any]:
        r = self._request("PATCH", f"/guests/{guest_id}", json=body)
        return r.json()

    # --- Bookings ---

    def list_bookings_window(
        self,
        *,
        property_id: UUID,
        start_date: date,
        end_date: date,
        limit: int = 500,
    ) -> dict[str, Any]:
        r = self._request(
            "GET",
            "/bookings",
            params={
                "property_id": str(property_id),
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "limit": limit,
                "offset": 0,
            },
        )
        return r.json()

    def create_booking(self, body: dict[str, Any]) -> dict[str, Any]:
        r = self._request("POST", "/bookings", json=body)
        return r.json()

    def get_booking_by_external_id(self, external_id: str) -> dict[str, Any] | None:
        ext = str(external_id).strip()[:128]
        if not ext:
            return None
        r = self._request(
            "GET",
            "/bookings",
            params={
                "external_booking_id": ext,
                "limit": 5,
                "offset": 0,
            },
        )
        data = r.json()
        items = data.get("items") or []
        if not items:
            return None
        return items[0]

    def patch_booking(self, booking_id: UUID, body: dict[str, Any]) -> None:
        self._request("PATCH", f"/bookings/{booking_id}", json=body)
