"""email_settings per property (TZ-16 seq 40).

Revision ID: z2b3c4d5e6f7
Revises: z1a2b3c4d5e6
Create Date: 2026-04-16

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "z2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "z1a2b3c4d5e6"
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
        "email_settings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("property_id", sa.UUID(), nullable=False),
        sa.Column("sender_name", sa.String(length=255), nullable=False),
        sa.Column("reply_to", sa.String(length=320), nullable=True),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_email_settings_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_email_settings_property_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="email_settings_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_email_settings_tenant_id_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "property_id",
            name="uq_email_settings_tenant_property",
        ),
    )
    _apply_rls_tenant_scoped("email_settings")


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON email_settings"))
    op.execute(sa.text("ALTER TABLE email_settings NO FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE email_settings DISABLE ROW LEVEL SECURITY"))
    op.drop_table("email_settings")
