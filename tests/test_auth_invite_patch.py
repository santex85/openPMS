"""POST /auth/invite and PATCH /auth/users/:id — roles and guardrails."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4


def test_invite_owner_role_returns_422(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    r = client.post(
        "/auth/invite",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "email": f"owner-role-{uuid4()}@example.com",
            "full_name": "O",
            "role": "owner",
        },
    )
    assert r.status_code == 422


def test_invite_viewer_role_returns_422(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    """Invite only allows certain roles — unknown role must yield 422."""
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    r = client.post(
        "/auth/invite",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "email": f"invalid-role-{uuid4()}@example.com",
            "full_name": "V",
            "role": "not-a-listed-role",
        },
    )
    assert r.status_code == 422


def test_invite_manager_by_owner_success(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    r = client.post(
        "/auth/invite",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "email": f"mgr-owner-{uuid4()}@example.com",
            "full_name": "Manager",
            "role": "manager",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["user"]["role"] == "manager"


def test_invite_manager_by_manager_success(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    tid = smoke_scenario["tenant_id"]
    mid = smoke_scenario["manager_id"]
    r = client.post(
        "/auth/invite",
        headers=auth_headers(tid, user_id=mid, role="manager"),
        json={
            "email": f"mgr-by-mgr-{uuid4()}@example.com",
            "full_name": "Invited Manager",
            "role": "manager",
        },
    )
    assert r.status_code == 201, r.text


def test_invite_duplicate_email_returns_409(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    email = f"dup-patch-{uuid4()}@example.com"
    client.post(
        "/auth/invite",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={"email": email, "full_name": "First", "role": "receptionist"},
    )
    r2 = client.post(
        "/auth/invite",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "email": email,
            "full_name": "Second",
            "role": "receptionist",
        },
    )
    assert r2.status_code == 409


def test_patch_manager_cannot_modify_owner(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    mid = smoke_scenario["manager_id"]
    r = client.patch(
        f"/auth/users/{oid}",
        headers=auth_headers(tid, user_id=mid, role="manager"),
        json={"role": "receptionist"},
    )
    assert r.status_code == 403


def test_patch_manager_cannot_assign_owner_role(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    mid = smoke_scenario["manager_id"]

    invite = client.post(
        "/auth/invite",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "email": f"temp-user-{uuid4()}@example.com",
            "full_name": "Temp",
            "role": "receptionist",
        },
    )
    assert invite.status_code == 201, invite.text
    uid = invite.json()["user"]["id"]
    r = client.patch(
        f"/auth/users/{uid}",
        headers=auth_headers(tid, user_id=mid, role="manager"),
        json={"role": "owner"},
    )
    assert r.status_code == 403


def test_patch_cannot_deactivate_self(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    r = client.patch(
        f"/auth/users/{oid}",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={"is_active": False},
    )
    assert r.status_code == 400


def test_patch_last_owner_cannot_be_deactivated(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    """Single owner cannot self-deactivate (guarded before tenant owner-count rule)."""
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    r = client.patch(
        f"/auth/users/{oid}",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={"is_active": False},
    )
    assert r.status_code == 400


def test_patch_last_owner_role_cannot_be_changed(
    client, smoke_scenario: dict[str, UUID], auth_headers
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    r = client.patch(
        f"/auth/users/{oid}",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={"role": "viewer"},
    )
    assert r.status_code == 409


@patch("app.services.auth_service.send_invite_email", new_callable=AsyncMock)
def test_invite_sends_email(
    mock_send: AsyncMock,
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    email = f"invite-email-{uuid4()}@example.com"
    r = client.post(
        "/auth/invite",
        headers=auth_headers(tid, user_id=oid, role="owner"),
        json={
            "email": email,
            "full_name": "Invited Manager",
            "role": "manager",
        },
    )
    assert r.status_code == 201, r.text
    mock_send.assert_awaited_once()
    kw = mock_send.await_args.kwargs
    assert kw["to_email"] == email.lower().strip()
    assert kw["full_name"] == "Invited Manager"
    assert kw["temporary_password"].strip()
    assert kw["tenant_name"] == "SmokeTenant"


def test_invite_email_failure_does_not_rollback_user(
    client,
    smoke_scenario: dict[str, UUID],
    auth_headers,
) -> None:
    tid = smoke_scenario["tenant_id"]
    oid = smoke_scenario["owner_id"]
    email = f"no-resend-{uuid4()}@example.com"
    with patch("app.services.email_service.get_settings") as gs:
        gs.return_value = MagicMock(resend_api_key="")
        r = client.post(
            "/auth/invite",
            headers=auth_headers(tid, user_id=oid, role="owner"),
            json={
                "email": email,
                "full_name": "No Resend Key",
                "role": "viewer",
            },
        )
    assert r.status_code == 201, r.text
    pwd = r.json()["temporary_password"]
    lr = client.post(
        "/auth/login",
        json={
            "tenant_id": str(tid),
            "email": email,
            "password": pwd,
        },
    )
    assert lr.status_code == 200, lr.text
