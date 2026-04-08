"""Async HTTP client for Channex REST API (retry, rate-limit friendly)."""

from __future__ import annotations

import asyncio
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
            base = {"id": str(item.get("id", ""))}
            out = {**base, **cast(dict[str, Any], attrs)}
            return out
        return item

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
