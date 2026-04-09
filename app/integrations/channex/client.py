"""Async HTTP client for Channex REST API (retry, rate-limit friendly)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import httpx
import structlog
from tenacity import retry, retry_if_result, stop_after_attempt, wait_exponential

from app.integrations.channex.schemas import (
    ChannexBookingRevisionPayload,
    ChannexProperty,
    ChannexRatePlan,
    ChannexRoomType,
)

log = structlog.get_logger()

CHANNEX_PROD_BASE = "https://api.channex.io/api/v1"
CHANNEX_SANDBOX_BASE = "https://staging.channex.io/api/v1"


class ChannexApiError(Exception):
    """Non-retriable or exhausted HTTP error from Channex."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def ensure_no_channex_ari_warnings(payload: Any, *, endpoint: str) -> None:
    """
    Channex returns HTTP 200 with ``meta.warnings`` when some ARI rows are rejected.
    Treat non-empty warnings as failure so sync tasks can persist ``error_message``.
    """
    if not isinstance(payload, dict):
        return
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return
    warnings = meta.get("warnings")
    if not isinstance(warnings, list) or len(warnings) == 0:
        return
    log.warning(
        "channex_ari_meta_warnings",
        endpoint=endpoint,
        warnings_count=len(warnings),
        warnings_preview=warnings[:5],
        hint=(
            "Verify OpenPMS Channex rate_plan_id maps match Channex UI; "
            "multi-occupancy rate plans need a rates[] payload, not a single rate."
        ),
    )
    detail = json.dumps(warnings, default=str)[:1900]
    raise ChannexApiError(
        f"Channex rejected part of ARI update ({endpoint}): {detail}",
        status_code=200,
        body=detail,
    )


class ChannexClient:
    def __init__(self, api_key: str, env: str = "production") -> None:
        env_lower = (env or "production").strip().lower()
        self._base = (
            CHANNEX_SANDBOX_BASE
            if env_lower == "sandbox"
            else CHANNEX_PROD_BASE
        )
        self._headers = {
            "user-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self._base.rstrip('/')}/{path.lstrip('/')}"

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = self._url(path)
        async with httpx.AsyncClient(timeout=15.0) as client:
            return await client.request(
                method,
                url,
                headers=self._headers,
                json=json_body,
                params=params,
            )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=4),
            retry=retry_if_result(lambda resp: resp.status_code == 429),
            reraise=True,
            sleep=asyncio.sleep,
        )
        async def _with_429_retries() -> httpx.Response:
            return await self._raw_request(
                method,
                path,
                json_body=json_body,
                params=params,
            )

        r = await _with_429_retries()

        attempts_5xx = 1
        while 500 <= r.status_code < 600 and attempts_5xx < 3:
            log.warning(
                "channex_server_error_retry",
                path=path,
                status=r.status_code,
                attempt=attempts_5xx,
            )
            await asyncio.sleep(5)
            r = await self._raw_request(
                method,
                path,
                json_body=json_body,
                params=params,
            )
            attempts_5xx += 1

        if r.status_code >= 400:
            body = r.text
            log.warning(
                "channex_http_error",
                path=path,
                status=r.status_code,
                body_preview=body[:500] if body else None,
            )
            raise ChannexApiError(
                f"Channex API error: HTTP {r.status_code}",
                status_code=r.status_code,
                body=body,
            )

        return r

    @staticmethod
    def _unwrap_items(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [cast(dict[str, Any], x) for x in data if isinstance(x, dict)]
        if isinstance(data, dict) and "data" in data:
            inner = data["data"]
            if inner is None:
                return []
            if isinstance(inner, list):
                return [
                    cast(dict[str, Any], x) for x in inner if isinstance(x, dict)
                ]
            if isinstance(inner, dict):
                return [cast(dict[str, Any], inner)]
        if isinstance(data, dict):
            return [cast(dict[str, Any], data)]
        return []

    @staticmethod
    def _attributes_obj(item: dict[str, Any]) -> dict[str, Any]:
        """Support JSON:API-ish { id, attributes: { title: ... } } payloads."""
        attrs = item.get("attributes")
        if isinstance(attrs, dict):
            top_id = item.get("id")
            aid = attrs.get("id")
            resolved = top_id if top_id is not None else aid
            base = {"id": str(resolved).strip() if resolved is not None else ""}
            out = {**base, **cast(dict[str, Any], attrs)}
            return out
        return item

    @staticmethod
    def extract_created_resource_id(payload: dict[str, Any]) -> str | None:
        """Parse id from a typical Channex create response (wrapped or bare)."""
        raw_list = ChannexClient._unwrap_items(payload)
        if not raw_list:
            return None
        flat = ChannexClient._attributes_obj(raw_list[0])
        rid = flat.get("id")
        if rid is None or str(rid).strip() == "":
            return None
        return str(rid).strip()

    async def get_properties(self) -> list[ChannexProperty]:
        r = await self._request("GET", "properties")
        payload = r.json()
        raw_list = self._unwrap_items(payload)
        items: list[ChannexProperty] = []
        for row in raw_list:
            flat = self._attributes_obj(row)
            if flat.get("id"):
                items.append(ChannexProperty.model_validate(flat))
        return items

    async def create_property(
        self,
        title: str,
        currency: str,
        timezone: str | None = None,
    ) -> ChannexProperty:
        prop_body: dict[str, Any] = {
            "title": title.strip(),
            "currency": currency.strip().upper(),
            "property_type": "hotel",
        }
        if timezone and timezone.strip():
            prop_body["timezone"] = timezone.strip()
        r = await self._request(
            "POST",
            "properties",
            json_body={"property": prop_body},
        )
        payload = r.json()
        raw_list = self._unwrap_items(payload)
        flat: dict[str, Any]
        if raw_list:
            flat = self._attributes_obj(raw_list[0])
        elif isinstance(payload, dict):
            flat = self._attributes_obj(payload)
        else:
            flat = {}
        if not flat.get("id"):
            raise ChannexApiError(
                "Channex create property: missing id in response",
                status_code=None,
                body=str(payload)[:500] if payload else None,
            )
        return ChannexProperty.model_validate(flat)

    async def create_room_type(
        self,
        *,
        property_id: str,
        title: str,
        count_of_rooms: int,
        occ_adults: int,
        occ_children: int,
        occ_infants: int,
        default_occupancy: int,
    ) -> ChannexRoomType:
        body = {
            "room_type": {
                "property_id": property_id.strip(),
                "title": title.strip(),
                "count_of_rooms": int(count_of_rooms),
                "occ_adults": int(occ_adults),
                "occ_children": int(occ_children),
                "occ_infants": int(occ_infants),
                "default_occupancy": int(default_occupancy),
                "facilities": [],
                "room_kind": "room",
            },
        }
        r = await self._request("POST", "room_types", json_body=body)
        payload = r.json()
        raw_list = self._unwrap_items(payload)
        flat: dict[str, Any]
        if raw_list:
            flat = self._attributes_obj(raw_list[0])
        elif isinstance(payload, dict):
            flat = self._attributes_obj(payload)
        else:
            flat = {}
        if not flat.get("id"):
            raise ChannexApiError(
                "Channex create room type: missing id in response",
                status_code=None,
                body=str(payload)[:500] if payload else None,
            )
        return ChannexRoomType.model_validate(flat)

    async def create_rate_plan(
        self,
        *,
        property_id: str,
        room_type_id: str,
        title: str,
        currency: str,
        primary_occupancy: int,
    ) -> ChannexRatePlan:
        z7 = [0, 0, 0, 0, 0, 0, 0]
        f7 = [False, False, False, False, False, False, False]
        o7 = [1, 1, 1, 1, 1, 1, 1]
        occ = max(1, int(primary_occupancy))
        body = {
            "rate_plan": {
                "title": title.strip()[:255],
                "property_id": property_id.strip(),
                "room_type_id": room_type_id.strip(),
                "parent_rate_plan_id": None,
                "children_fee": "0.00",
                "infant_fee": "0.00",
                "max_stay": z7,
                "min_stay_arrival": o7,
                "min_stay_through": o7,
                "closed_to_arrival": f7,
                "closed_to_departure": f7,
                "stop_sell": f7,
                "options": [{"occupancy": occ, "is_primary": True, "rate": 0}],
                "currency": currency.strip().upper(),
                "sell_mode": "per_room",
                "rate_mode": "manual",
                "inherit_rate": False,
                "inherit_closed_to_arrival": False,
                "inherit_closed_to_departure": False,
                "inherit_stop_sell": False,
                "inherit_min_stay_arrival": False,
                "inherit_min_stay_through": False,
                "inherit_max_stay": False,
                "inherit_max_sell": False,
                "inherit_max_availability": False,
                "inherit_availability_offset": False,
                "auto_rate_settings": None,
            },
        }
        r = await self._request("POST", "rate_plans", json_body=body)
        payload = r.json()
        raw_list = self._unwrap_items(payload)
        flat: dict[str, Any]
        if raw_list:
            flat = self._attributes_obj(raw_list[0])
        elif isinstance(payload, dict):
            flat = self._attributes_obj(payload)
        else:
            flat = {}
        if not flat.get("id"):
            raise ChannexApiError(
                "Channex create rate plan: missing id in response",
                status_code=None,
                body=str(payload)[:500] if payload else None,
            )
        return ChannexRatePlan.model_validate(flat)

    async def get_room_types(self, property_id: str) -> list[ChannexRoomType]:
        r = await self._request(
            "GET",
            "room_types",
            params={"property_id": property_id},
        )
        payload = r.json()
        raw_list = self._unwrap_items(payload)
        items: list[ChannexRoomType] = []
        for row in raw_list:
            flat = self._attributes_obj(row)
            if flat.get("id"):
                items.append(ChannexRoomType.model_validate(flat))
        return items

    async def get_rate_plans(self, property_id: str) -> list[ChannexRatePlan]:
        r = await self._request(
            "GET",
            "rate_plans",
            params={"property_id": property_id},
        )
        payload = r.json()
        raw_list = self._unwrap_items(payload)
        items: list[ChannexRatePlan] = []
        for row in raw_list:
            flat = self._attributes_obj(row)
            if flat.get("id"):
                items.append(ChannexRatePlan.model_validate(flat))
        return items

    async def push_availability(self, values: list[dict[str, Any]]) -> dict[str, Any]:
        """POST /availability — room-type level inventory."""
        r = await self._request("POST", "availability", json_body={"values": values})
        payload = r.json()
        ensure_no_channex_ari_warnings(payload, endpoint="availability")
        return cast(dict[str, Any], payload)

    async def push_restrictions(self, values: list[dict[str, Any]]) -> dict[str, Any]:
        """POST /restrictions — rate and restrictions per rate plan."""
        r = await self._request("POST", "restrictions", json_body={"values": values})
        payload = r.json()
        ensure_no_channex_ari_warnings(payload, endpoint="restrictions")
        return cast(dict[str, Any], payload)

    async def push_ari(self, values: list[dict[str, Any]]) -> dict[str, Any]:
        r = await self._request("POST", "ari_upload", json_body={"values": values})
        return cast(dict[str, Any], r.json())

    async def get_booking_revision(self, revision_id: str) -> ChannexBookingRevisionPayload:
        r = await self._request("GET", f"booking_revisions/{revision_id}")
        payload = r.json()
        raw_list = self._unwrap_items(payload)
        flat: dict[str, Any]
        if raw_list:
            flat = self._attributes_obj(raw_list[0])
        elif isinstance(payload, dict):
            flat = self._attributes_obj(payload)
        else:
            flat = {}
        return ChannexBookingRevisionPayload.model_validate(flat)

    async def acknowledge_revision(self, revision_id: str) -> dict[str, Any]:
        r = await self._request(
            "POST",
            f"booking_revisions/{revision_id}/acknowledge",
        )
        if not r.content:
            return {}
        return cast(dict[str, Any], r.json())

    async def create_webhook(self, url: str, events: list[str]) -> dict[str, Any]:
        r = await self._request(
            "POST",
            "webhooks",
            json_body={"url": url, "events": events},
        )
        return cast(dict[str, Any], r.json())

    async def delete_webhook(self, webhook_id: str) -> None:
        await self._request("DELETE", f"webhooks/{webhook_id}")

    async def get_bookings(
        self,
        property_id: str,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"property_id": property_id, **filters}
        r = await self._request("GET", "bookings", params=params)
        payload = r.json()
        return self._unwrap_items(payload)
