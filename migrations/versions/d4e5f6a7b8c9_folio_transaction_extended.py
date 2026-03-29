"""Folio: description, created_at, created_by, category + FK users.

Revision ID: d4e5f6a7b8c9
Revises: c1d2e3f4a5b6
Create Date: 2026-03-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "folio_transactions",
        sa.Column("description", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "folio_transactions",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "folio_transactions",
        sa.Column("created_by", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "folio_transactions",
        sa.Column(
            "category",
            sa.String(length=32),
            nullable=False,
            server_default="room_charge",
        ),
    )
    op.create_foreign_key(
        "fk_folio_transactions_created_by_user",
        "folio_transactions",
        "users",
        ["tenant_id", "created_by"],
        ["tenant_id", "id"],
    )
    op.alter_column(
        "folio_transactions",
        "category",
        server_default=None,
        existing_type=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_folio_transactions_created_by_user",
        "folio_transactions",
        type_="foreignkey",
    )
    op.drop_column("folio_transactions", "category")
    op.drop_column("folio_transactions", "created_by")
    op.drop_column("folio_transactions", "created_at")
    op.drop_column("folio_transactions", "description")
