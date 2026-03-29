"""Block 10: composite indexes for tenant+date scans, audit_log + RLS.

Revision ID: h7i8j9k0l1m2
Revises: g1h2i3j4k5l6
Create Date: 2026-03-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "h7i8j9k0l1m2"
down_revision: Union[str, Sequence[str], None] = "g1h2i3j4k5l6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _apply_rls_tenant_scoped(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
    op.execute(
        sa.text(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            FOR ALL
            TO PUBLIC
            USING (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
            )
            """
        )
    )


def upgrade() -> None:
    op.create_index(
        "ix_booking_lines_tenant_date",
        "booking_lines",
        ["tenant_id", "date"],
        unique=False,
    )
    op.create_index(
        "ix_booking_lines_tenant_room_date",
        "booking_lines",
        ["tenant_id", "room_id", "date"],
        unique=False,
        postgresql_where=sa.text("room_id IS NOT NULL"),
    )
    op.create_index(
        "ix_rates_tenant_date",
        "rates",
        ["tenant_id", "date"],
        unique=False,
    )
    op.create_index(
        "ix_availability_ledger_tenant_date",
        "availability_ledger",
        ["tenant_id", "date"],
        unique=False,
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=128), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("old_values", postgresql.JSONB(), nullable=True),
        sa.Column("new_values", postgresql.JSONB(), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="audit_log_pkey"),
    )
    _apply_rls_tenant_scoped("audit_log")


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON audit_log"))
    op.execute(sa.text("ALTER TABLE audit_log NO FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE audit_log DISABLE ROW LEVEL SECURITY"))
    op.drop_table("audit_log")

    op.drop_index("ix_availability_ledger_tenant_date", table_name="availability_ledger")
    op.drop_index("ix_rates_tenant_date", table_name="rates")
    op.drop_index("ix_booking_lines_tenant_room_date", table_name="booking_lines")
    op.drop_index("ix_booking_lines_tenant_date", table_name="booking_lines")
