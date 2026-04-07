"""Country pack extensions registry (per tenant) + RLS.

Revision ID: countrypack_004
Revises: countrypack_003
Create Date: 2026-04-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "countrypack_004"
down_revision: Union[str, Sequence[str], None] = "countrypack_003"
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
    op.create_table(
        "country_pack_extensions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("webhook_url", sa.Text(), nullable=False),
        sa.Column(
            "required_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("ui_config_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
            name="fk_country_pack_extensions_tenant_id_tenants",
        ),
        sa.PrimaryKeyConstraint("id", name="country_pack_extensions_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "code",
            name="uq_country_pack_extensions_tenant_code",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_country_pack_extensions_tenant_id_id",
        ),
    )
    _apply_rls_tenant_scoped("country_pack_extensions")


def downgrade() -> None:
    op.execute(
        sa.text("DROP POLICY IF EXISTS tenant_isolation ON country_pack_extensions")
    )
    op.execute(
        sa.text("ALTER TABLE country_pack_extensions NO FORCE ROW LEVEL SECURITY")
    )
    op.execute(sa.text("ALTER TABLE country_pack_extensions DISABLE ROW LEVEL SECURITY"))
    op.drop_table("country_pack_extensions")
