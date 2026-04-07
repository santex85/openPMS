"""Property ↔ extension activation (M:N) + RLS.

Revision ID: countrypack_005
Revises: countrypack_004
Create Date: 2026-04-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "countrypack_005"
down_revision: Union[str, Sequence[str], None] = "countrypack_004"
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
        "property_extensions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("extension_id", sa.Uuid(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_property_extensions_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_property_extensions_property_composite",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "extension_id"],
            ["country_pack_extensions.tenant_id", "country_pack_extensions.id"],
            name="fk_property_extensions_extension_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="property_extensions_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "property_id",
            "extension_id",
            name="uq_property_extensions_prop_ext",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_property_extensions_tenant_id_id",
        ),
    )
    _apply_rls_tenant_scoped("property_extensions")


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON property_extensions"))
    op.execute(sa.text("ALTER TABLE property_extensions NO FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE property_extensions DISABLE ROW LEVEL SECURITY"))
    op.drop_table("property_extensions")
