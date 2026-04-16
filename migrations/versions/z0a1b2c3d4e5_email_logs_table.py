"""email_logs audit table for transactional email (TZ-16).

Revision ID: z0a1b2c3d4e5
Revises: x1y2z3a4b5c6
Create Date: 2026-04-13

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "z0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "x1y2z3a4b5c6"
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
            """,
        ),
    )


def upgrade() -> None:
    op.create_table(
        "email_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=True),
        sa.Column("booking_id", sa.Uuid(), nullable=True),
        sa.Column("to_address", sa.String(length=320), nullable=False),
        sa.Column("template_name", sa.String(length=128), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resend_id", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('sent', 'failed')",
            name="ck_email_logs_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_email_logs_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_email_logs_property_composite",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "booking_id"],
            ["bookings.tenant_id", "bookings.id"],
            name="fk_email_logs_booking_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="email_logs_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_email_logs_tenant_id_id",
        ),
    )
    op.create_index(
        "ix_email_logs_tenant_booking_id",
        "email_logs",
        ["tenant_id", "booking_id"],
        unique=False,
    )
    op.create_index(
        "ix_email_logs_tenant_sent_at",
        "email_logs",
        ["tenant_id", "sent_at"],
        unique=False,
    )
    _apply_rls_tenant_scoped("email_logs")


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON email_logs"))
    op.execute(sa.text("ALTER TABLE email_logs NO FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE email_logs DISABLE ROW LEVEL SECURITY"))
    op.drop_index("ix_email_logs_tenant_sent_at", table_name="email_logs")
    op.drop_index("ix_email_logs_tenant_booking_id", table_name="email_logs")
    op.drop_table("email_logs")
