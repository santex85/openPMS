"""Stripe Connect OAuth (Phase 2): service state, HTTP, mocks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
import stripe

import app.services.stripe_connect_service as scs
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import clear_settings_cache, get_settings
from app.models.billing.stripe_connection import StripeConnection
from app.services.stripe_connect_service import (
    StripeConnectError,
    decode_oauth_state,
    disconnect_stripe_connection,
    encode_oauth_state,
    exchange_code_for_connection,
    get_stripe_status,
    oauth_state_hmac_key,
)
from tests.test_tax_config import _seed_minimal_property


def _minimal_settings_ns(**overrides: object) -> SimpleNamespace:
    base = dict(
        stripe_oauth_state_secret=None,
        jwt_algorithm="HS256",
        jwt_secret="pytest-jwt-secret-key-minimum-32-characters!!",
        webhook_secret_fernet_key=None,
        stripe_client_id="ca_openpms_pytest_client_id_placeholder________________",
        stripe_redirect_uri="http://test/stripe/oauth/callback",
        stripe_secret_key=(
            "sk_test_openpms_pytest_placeholder_key_min_len_______________"
        ),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_oauth_state_hmac_uses_explicit_secret() -> None:
    s = _minimal_settings_ns(stripe_oauth_state_secret="  dedicated_hmac  ")
    assert oauth_state_hmac_key(s) == "dedicated_hmac"


def test_oauth_state_hmac_rs256_uses_webhook_fernet() -> None:
    s = _minimal_settings_ns(
        jwt_algorithm="RS256",
        stripe_oauth_state_secret=None,
        webhook_secret_fernet_key="  whsec_fernet_material__________  ",
    )
    assert oauth_state_hmac_key(s) == "whsec_fernet_material__________"


def test_oauth_state_hmac_rs256_without_material_raises() -> None:
    s = _minimal_settings_ns(
        jwt_algorithm="RS256",
        stripe_oauth_state_secret=None,
        webhook_secret_fernet_key="",
    )
    with pytest.raises(ValueError, match="STRIPE_OAUTH_STATE_SECRET"):
        oauth_state_hmac_key(s)


def test_decode_oauth_state_bad_uuid_in_payload() -> None:
    settings = get_settings()
    key = oauth_state_hmac_key(settings)
    payload = {"p": "not-a-uuid", "t": str(uuid4()), "ts": int(time.time())}
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    b64 = base64.urlsafe_b64encode(body).decode().rstrip("=")
    sig = hmac.new(key.encode("utf-8"), b64.encode(), hashlib.sha256).hexdigest()
    state = f"{b64}.{sig}"
    with pytest.raises(StripeConnectError) as ei:
        decode_oauth_state(settings, state)
    assert ei.value.status_code == 400


def test_build_connect_authorize_url_missing_client_503() -> None:
    s = _minimal_settings_ns(stripe_client_id="")
    with pytest.raises(StripeConnectError) as ei:
        scs.build_connect_authorize_url(s, uuid4(), uuid4())
    assert ei.value.status_code == 503


def test_build_connect_authorize_url_missing_redirect_503() -> None:
    s = _minimal_settings_ns(stripe_redirect_uri=" ")
    with pytest.raises(StripeConnectError) as ei:
        scs.build_connect_authorize_url(s, uuid4(), uuid4())
    assert ei.value.status_code == 503


def test_oauth_state_roundtrip() -> None:
    clear_settings_cache()
    try:
        tid = uuid4()
        pid = uuid4()
        settings = get_settings()
        state = encode_oauth_state(settings, tid, pid)
        t2, p2 = decode_oauth_state(settings, state)
        assert t2 == tid
        assert p2 == pid
    finally:
        clear_settings_cache()


def test_oauth_state_expired() -> None:
    clear_settings_cache()
    try:
        settings = get_settings()
        fixed_ts = 1_700_000_000
        with patch(
            "app.services.stripe_connect_service.time.time", return_value=fixed_ts
        ):
            state = encode_oauth_state(settings, uuid4(), uuid4())
        with patch(
            "app.services.stripe_connect_service.time.time",
            return_value=fixed_ts + 3600 + 10,
        ):
            with pytest.raises(StripeConnectError) as ei:
                decode_oauth_state(settings, state)
            assert ei.value.status_code == 400
            assert "expired" in ei.value.detail.lower()
    finally:
        clear_settings_cache()


def test_build_connect_authorize_url() -> None:
    clear_settings_cache()
    try:
        settings = get_settings()
        url = scs.build_connect_authorize_url(settings, uuid4(), uuid4())
        assert url.startswith("https://connect.stripe.com/oauth/authorize")
        assert "state=" in url
    finally:
        clear_settings_cache()


def test_oauth_state_invalid_sig() -> None:
    clear_settings_cache()
    try:
        settings = get_settings()
        state = encode_oauth_state(settings, uuid4(), uuid4())
        bad = state[:-5] + "xxxxx"
        with pytest.raises(StripeConnectError) as ei:
            decode_oauth_state(settings, bad)
        assert ei.value.status_code == 400
    finally:
        clear_settings_cache()


def test_stripe_status_not_connected(
    client,
    smoke_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = smoke_scenario["tenant_id"]  # type: ignore[assignment]
    property_id: UUID = smoke_scenario["property_id"]  # type: ignore[assignment]
    r = client.get(
        f"/properties/{property_id}/stripe/status",
        headers=auth_headers(tenant_id, role="manager"),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "not_connected"


def test_connect_url_shape(
    client,
    smoke_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = smoke_scenario["tenant_id"]  # type: ignore[assignment]
    property_id: UUID = smoke_scenario["property_id"]  # type: ignore[assignment]
    r = client.get(
        f"/properties/{property_id}/stripe/connect-url",
        headers=auth_headers(tenant_id, role="owner"),
    )
    assert r.status_code == 200
    data = r.json()
    assert "url" in data
    url = data["url"]
    assert "connect.stripe.com/oauth/authorize" in url
    assert "client_id=" in url
    settings = get_settings()
    assert settings.stripe_client_id.strip() in url
    assert "redirect_uri=" in url
    assert "state=" in url


def test_connect_url_manager_forbidden(
    client,
    smoke_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = smoke_scenario["tenant_id"]  # type: ignore[assignment]
    property_id: UUID = smoke_scenario["property_id"]  # type: ignore[assignment]
    r = client.get(
        f"/properties/{property_id}/stripe/connect-url",
        headers=auth_headers(tenant_id, role="manager"),
    )
    assert r.status_code == 403


def test_oauth_callback_invalid_state_400(client) -> None:
    r = client.get(
        "/stripe/oauth/callback",
        params={"code": "ac_fake", "state": "not-a-valid-state"},
    )
    assert r.status_code == 400


def test_oauth_callback_missing_code_400(client) -> None:
    settings = get_settings()
    state = encode_oauth_state(settings, uuid4(), uuid4())
    r = client.get(
        "/stripe/oauth/callback",
        params={"state": state},
    )
    assert r.status_code == 400


def test_oauth_callback_error_redirects(client) -> None:
    r = client.get(
        "/stripe/oauth/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    loc = r.headers["location"]
    assert "stripe_error=" in loc


@pytest.mark.asyncio
async def test_exchange_code_persists_encrypted_account(db_engine) -> None:
    url = __import__("os").environ.get("DATABASE_URL") or __import__(
        "os",
    ).environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL required")
    clear_settings_cache()
    try:
        tenant_id = uuid4()
        property_id = await _seed_minimal_property(
            url,
            tenant_id=tenant_id,
            user_id=None,
            user_role="owner",
        )
        settings = get_settings()
        state = encode_oauth_state(settings, tenant_id, property_id)
        factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        tok = SimpleNamespace(stripe_user_id="acct_test_openpms_1", livemode=False)
        with patch(
            "app.services.stripe_connect_service.stripe.OAuth.token",
            return_value=tok,
        ):
            async with factory() as session:
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                    ),
                    {"tid": str(tenant_id)},
                )
                row = await exchange_code_for_connection(
                    settings,
                    session,
                    "ac_test_code",
                    state,
                )
                await session.commit()
                assert row.stripe_account_id != "acct_test_openpms_1"
                assert row.livemode is False

        async with factory() as session:
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tenant_id)},
            )
            loaded = await session.scalar(
                select(StripeConnection).where(
                    StripeConnection.tenant_id == tenant_id,
                    StripeConnection.property_id == property_id,
                ),
            )
            assert loaded is not None
            assert loaded.disconnected_at is None
    finally:
        clear_settings_cache()


def test_oauth_callback_success_redirect(
    client,
    smoke_scenario: dict,
    auth_headers,
) -> None:
    tenant_id: UUID = smoke_scenario["tenant_id"]  # type: ignore[assignment]
    property_id: UUID = smoke_scenario["property_id"]  # type: ignore[assignment]
    settings = get_settings()
    state = encode_oauth_state(settings, tenant_id, property_id)
    tok = SimpleNamespace(stripe_user_id="acct_test_cb_1", livemode=True)
    with patch(
        "app.services.stripe_connect_service.stripe.OAuth.token",
        return_value=tok,
    ):
        r = client.get(
            "/stripe/oauth/callback",
            params={"code": "ac_test_cb", "state": state},
            follow_redirects=False,
        )
    assert r.status_code == 302
    loc = r.headers["location"]
    assert "connected=1" in loc
    assert str(property_id) in loc

    st = client.get(
        f"/properties/{property_id}/stripe/status",
        headers=auth_headers(tenant_id, role="owner"),
    )
    assert st.status_code == 200
    body = st.json()
    assert body["status"] == "connected"
    assert body["livemode"] is True


def test_disconnect_then_second_404(
    client,
    smoke_scenario: dict,
    auth_headers_user,
) -> None:
    tenant_id: UUID = smoke_scenario["tenant_id"]  # type: ignore[assignment]
    property_id: UUID = smoke_scenario["property_id"]  # type: ignore[assignment]
    owner_id: UUID = smoke_scenario["owner_id"]  # type: ignore[assignment]
    settings = get_settings()
    state = encode_oauth_state(settings, tenant_id, property_id)
    tok = SimpleNamespace(stripe_user_id="acct_test_dc_1", livemode=False)
    with patch(
        "app.services.stripe_connect_service.stripe.OAuth.token",
        return_value=tok,
    ):
        assert (
            client.get(
                "/stripe/oauth/callback",
                params={"code": "ac_dc1", "state": state},
                follow_redirects=False,
            ).status_code
            == 302
        )
    with patch(
        "app.services.stripe_connect_service.stripe.OAuth.deauthorize",
        return_value=SimpleNamespace(stripe_user_id="acct_test_dc_1"),
    ):
        d1 = client.delete(
            f"/properties/{property_id}/stripe/disconnect",
            headers=auth_headers_user(tenant_id, owner_id, role="owner"),
        )
        assert d1.status_code == 204
        d2 = client.delete(
            f"/properties/{property_id}/stripe/disconnect",
            headers=auth_headers_user(tenant_id, owner_id, role="owner"),
        )
        assert d2.status_code == 404


def test_tenant_b_cannot_see_stripe_status(
    client,
    auth_headers,
    tenant_isolation_booking_scenario: dict,
) -> None:
    tid_b: UUID = tenant_isolation_booking_scenario["tenant_b"]  # type: ignore[assignment]
    prop_a: UUID = tenant_isolation_booking_scenario["property_id"]  # type: ignore[assignment]
    r = client.get(
        f"/properties/{prop_a}/stripe/status",
        headers=auth_headers(tid_b, role="owner"),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_exchange_code_property_not_found(db_engine) -> None:
    url = __import__("os").environ.get("DATABASE_URL") or __import__(
        "os",
    ).environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    missing_property = uuid4()
    settings = get_settings()
    state = encode_oauth_state(settings, tenant_id, missing_property)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    with patch(
        "app.services.stripe_connect_service.stripe.OAuth.token",
        return_value=SimpleNamespace(stripe_user_id="acct_x", livemode=False),
    ):
        async with factory() as session:
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tenant_id)},
            )
            with pytest.raises(StripeConnectError) as ei:
                await exchange_code_for_connection(
                    settings,
                    session,
                    "ac_code",
                    state,
                )
            assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_exchange_code_stripe_token_error(db_engine) -> None:
    url = __import__("os").environ.get("DATABASE_URL") or __import__(
        "os",
    ).environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL required")
    clear_settings_cache()
    try:
        tenant_id = uuid4()
        property_id = await _seed_minimal_property(
            url,
            tenant_id=tenant_id,
            user_id=None,
            user_role="owner",
        )
        settings = get_settings()
        state = encode_oauth_state(settings, tenant_id, property_id)
        factory = async_sessionmaker(
            db_engine, class_=AsyncSession, expire_on_commit=False
        )
        with patch(
            "app.services.stripe_connect_service.stripe.OAuth.token",
            side_effect=stripe.StripeError("bad code"),
        ):
            async with factory() as session:
                await session.execute(
                    text(
                        "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                    ),
                    {"tid": str(tenant_id)},
                )
                with pytest.raises(StripeConnectError) as ei:
                    await exchange_code_for_connection(
                        settings,
                        session,
                        "ac_bad",
                        state,
                    )
                assert ei.value.status_code == 400
    finally:
        clear_settings_cache()


@pytest.mark.asyncio
async def test_exchange_code_missing_stripe_user_id(db_engine) -> None:
    url = __import__("os").environ.get("DATABASE_URL") or __import__(
        "os",
    ).environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    property_id = await _seed_minimal_property(
        url,
        tenant_id=tenant_id,
        user_id=None,
        user_role="owner",
    )
    settings = get_settings()
    state = encode_oauth_state(settings, tenant_id, property_id)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    tok = SimpleNamespace(stripe_user_id=None, livemode=False)
    with patch(
        "app.services.stripe_connect_service.stripe.OAuth.token",
        return_value=tok,
    ):
        async with factory() as session:
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tenant_id)},
            )
            with pytest.raises(StripeConnectError) as ei:
                await exchange_code_for_connection(
                    settings,
                    session,
                    "ac_x",
                    state,
                )
            assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_exchange_code_upserts_existing_connection(db_engine) -> None:
    url = __import__("os").environ.get("DATABASE_URL") or __import__(
        "os",
    ).environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    property_id = await _seed_minimal_property(
        url,
        tenant_id=tenant_id,
        user_id=None,
        user_role="owner",
    )
    settings = get_settings()
    state = encode_oauth_state(settings, tenant_id, property_id)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    first_ct: bytes | None = None
    with patch(
        "app.services.stripe_connect_service.stripe.OAuth.token",
        return_value=SimpleNamespace(stripe_user_id="acct_first", livemode=False),
    ):
        async with factory() as session:
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tenant_id)},
            )
            row1 = await exchange_code_for_connection(
                settings,
                session,
                "ac_1",
                state,
            )
            first_ct = row1.stripe_account_id
            await session.commit()
    with patch(
        "app.services.stripe_connect_service.stripe.OAuth.token",
        return_value=SimpleNamespace(stripe_user_id="acct_second", livemode=True),
    ):
        async with factory() as session:
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tenant_id)},
            )
            row2 = await exchange_code_for_connection(
                settings,
                session,
                "ac_2",
                state,
            )
            await session.commit()
            assert row2.id == row1.id
            assert row2.stripe_account_id != first_ct
            assert row2.livemode is True
            assert row2.disconnected_at is None


@pytest.mark.asyncio
async def test_get_stripe_status_connected_row(db_engine) -> None:
    url = __import__("os").environ.get("DATABASE_URL") or __import__(
        "os",
    ).environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    property_id = await _seed_minimal_property(
        url,
        tenant_id=tenant_id,
        user_id=None,
        user_role="owner",
    )
    settings = get_settings()
    state = encode_oauth_state(settings, tenant_id, property_id)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    with patch(
        "app.services.stripe_connect_service.stripe.OAuth.token",
        return_value=SimpleNamespace(stripe_user_id="acct_gs1", livemode=True),
    ):
        async with factory() as session:
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tenant_id)},
            )
            await exchange_code_for_connection(settings, session, "ac_gs", state)
            await session.commit()
    async with factory() as session:
        await session.execute(
            text(
                "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
            ),
            {"tid": str(tenant_id)},
        )
        st = await get_stripe_status(session, tenant_id, property_id)
        assert st.status == "connected"
        assert st.livemode is True
        assert st.connected_at is not None


@pytest.mark.asyncio
async def test_disconnect_deauthorize_stripe_error(db_engine) -> None:
    url = __import__("os").environ.get("DATABASE_URL") or __import__(
        "os",
    ).environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL required")
    tenant_id = uuid4()
    property_id = await _seed_minimal_property(
        url,
        tenant_id=tenant_id,
        user_id=None,
        user_role="owner",
    )
    settings = get_settings()
    state = encode_oauth_state(settings, tenant_id, property_id)
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    with patch(
        "app.services.stripe_connect_service.stripe.OAuth.token",
        return_value=SimpleNamespace(stripe_user_id="acct_da1", livemode=False),
    ):
        async with factory() as session:
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tenant_id)},
            )
            await exchange_code_for_connection(settings, session, "ac_da", state)
            await session.commit()
    with patch(
        "app.services.stripe_connect_service.stripe.OAuth.deauthorize",
        side_effect=stripe.StripeError("deauth failed"),
    ):
        async with factory() as session:
            await session.execute(
                text(
                    "SELECT set_config('app.tenant_id', CAST(:tid AS text), true)",
                ),
                {"tid": str(tenant_id)},
            )
            with pytest.raises(StripeConnectError) as ei:
                await disconnect_stripe_connection(
                    settings,
                    session,
                    tenant_id,
                    property_id,
                )
            assert ei.value.status_code == 502
