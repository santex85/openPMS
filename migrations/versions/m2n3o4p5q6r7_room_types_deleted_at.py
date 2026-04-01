"""Soft-delete column on room_types.

Revision ID: m2n3o4p5q6r7
Revises: k0l1m2n3o4p5
Create Date: 2026-03-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m2n3o4p5q6r7"
down_revision: Union[str, Sequence[str], None] = "k0l1m2n3o4p5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "room_types",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_room_types_tenant_deleted_at",
        "room_types",
        ["tenant_id", "deleted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_room_types_tenant_deleted_at", table_name="room_types")
    op.drop_column("room_types", "deleted_at")
