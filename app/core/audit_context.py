"""Request-scoped audit metadata (user, client IP) via contextvars."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class AuditContext:
    user_id: UUID | None
    ip_address: str | None


_audit_ctx: ContextVar[AuditContext | None] = ContextVar("openpms_audit_ctx", default=None)


def get_audit_context() -> AuditContext | None:
    return _audit_ctx.get()


def bind_audit_context(*, user_id: UUID | None, ip_address: str | None) -> Token[AuditContext | None]:
    return _audit_ctx.set(AuditContext(user_id=user_id, ip_address=ip_address))


def reset_audit_context(token: Token[AuditContext | None]) -> None:
    _audit_ctx.reset(token)
