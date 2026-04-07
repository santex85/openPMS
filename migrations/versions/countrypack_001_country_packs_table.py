"""Country packs table (data-driven presets) + RLS for builtin vs tenant rows.

Revision ID: countrypack_001
Revises: q7r8s9t0u1v2
Create Date: 2026-04-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "countrypack_001"
down_revision: Union[str, Sequence[str], None] = "q7r8s9t0u1v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "country_packs",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("currency_symbol", sa.String(length=8), nullable=False),
        sa.Column("currency_symbol_position", sa.String(length=6), nullable=False),
        sa.Column("currency_decimal_places", sa.SmallInteger(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("date_format", sa.String(length=20), nullable=False),
        sa.Column("locale", sa.String(length=10), nullable=False),
        sa.Column("default_checkin_time", sa.Time(), nullable=False),
        sa.Column("default_checkout_time", sa.Time(), nullable=False),
        sa.Column("taxes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payment_methods", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fiscal_year_start", sa.String(length=5), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default="false"),
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
            name="fk_country_packs_tenant_id_tenants",
        ),
        sa.PrimaryKeyConstraint("code", name="country_packs_pkey"),
        sa.CheckConstraint(
            "currency_symbol_position IN ('before', 'after')",
            name="ck_country_packs_currency_symbol_position",
        ),
        sa.CheckConstraint(
            "(is_builtin = true AND tenant_id IS NULL) OR (is_builtin = false AND tenant_id IS NOT NULL)",
            name="ck_country_packs_builtin_tenant",
        ),
    )
    op.create_index(
        "ix_country_packs_tenant_id",
        "country_packs",
        ["tenant_id"],
        unique=False,
    )
    op.execute(sa.text("ALTER TABLE country_packs ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE country_packs FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP POLICY IF EXISTS country_pack_read ON country_packs"))
    op.execute(sa.text("DROP POLICY IF EXISTS country_pack_insert ON country_packs"))
    op.execute(sa.text("DROP POLICY IF EXISTS country_pack_update ON country_packs"))
    op.execute(sa.text("DROP POLICY IF EXISTS country_pack_delete ON country_packs"))
    op.execute(
        sa.text(
            """
            CREATE POLICY country_pack_read ON country_packs
            FOR SELECT
            TO PUBLIC
            USING (
                (is_builtin = true AND tenant_id IS NULL)
                OR (
                    tenant_id IS NOT NULL
                    AND tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                )
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE POLICY country_pack_insert ON country_packs
            FOR INSERT
            TO PUBLIC
            WITH CHECK (
                tenant_id IS NOT NULL
                AND tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                AND is_builtin = false
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE POLICY country_pack_update ON country_packs
            FOR UPDATE
            TO PUBLIC
            USING (
                tenant_id IS NOT NULL
                AND tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                AND is_builtin = false
            )
            WITH CHECK (
                tenant_id IS NOT NULL
                AND tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                AND is_builtin = false
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE POLICY country_pack_delete ON country_packs
            FOR DELETE
            TO PUBLIC
            USING (
                tenant_id IS NOT NULL
                AND tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                AND is_builtin = false
            )
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS country_pack_read ON country_packs"))
    op.execute(sa.text("DROP POLICY IF EXISTS country_pack_insert ON country_packs"))
    op.execute(sa.text("DROP POLICY IF EXISTS country_pack_update ON country_packs"))
    op.execute(sa.text("DROP POLICY IF EXISTS country_pack_delete ON country_packs"))
    op.execute(sa.text("ALTER TABLE country_packs NO FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE country_packs DISABLE ROW LEVEL SECURITY"))
    op.drop_index("ix_country_packs_tenant_id", table_name="country_packs")
    op.drop_table("country_packs")
