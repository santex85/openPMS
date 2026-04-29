"""Composite index refresh_tokens tenant_id + token_hash for auth lookups.

The table already has ix_refresh_tokens_token_hash (single-column). This migration
adds (tenant_id, token_hash) to match WHERE tenant_id = ? AND token_hash = ?
used by refresh and logout flows.

Revision ID: p1q2r3s4t5u6
Revises: c1d2e3f4b5a6
Create Date: 2026-04-29

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "p1q2r3s4t5u6"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4b5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_refresh_tokens_tenant_token_hash",
        "refresh_tokens",
        ["tenant_id", "token_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_refresh_tokens_tenant_token_hash",
        table_name="refresh_tokens",
    )
