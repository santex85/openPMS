"""Per-tenant folio charge category catalog.

Revision ID: c1d2e3f4b5a6
Revises: a3b4c5d6e7f8
Create Date: 2026-04-20

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4b5a6"
down_revision: Union[str, Sequence[str], None] = "a3b4c5d6e7f8"
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
        "folio_charge_categories",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
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
            name="fk_folio_charge_categories_tenant_id_tenants",
        ),
        sa.PrimaryKeyConstraint("id", name="folio_charge_categories_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "code",
            name="uq_folio_charge_categories_tenant_code",
        ),
    )
    _apply_rls_tenant_scoped("folio_charge_categories")

    op.execute(
        sa.text(
            """
            INSERT INTO folio_charge_categories (
                id, tenant_id, code, label, is_builtin, is_active, sort_order,
                created_at, updated_at
            )
            SELECT gen_random_uuid(), t.id, v.code, v.label, true, true, v.sort_order,
                   now(), now()
            FROM tenants t
            CROSS JOIN (
                VALUES
                    ('room_charge', 'Room charge', 0),
                    ('food_beverage', 'Food & beverage', 10),
                    ('spa', 'Spa', 20),
                    ('minibar', 'Minibar', 30),
                    ('tax', 'Tax', 40),
                    ('discount', 'Discount', 50),
                    ('misc', 'Miscellaneous', 60),
                    ('service', 'Service', 70)
            ) AS v(code, label, sort_order)
            """,
        ),
    )


def downgrade() -> None:
    op.execute(
        sa.text("DROP POLICY IF EXISTS tenant_isolation ON folio_charge_categories"),
    )
    op.execute(
        sa.text("ALTER TABLE folio_charge_categories NO FORCE ROW LEVEL SECURITY"),
    )
    op.execute(
        sa.text("ALTER TABLE folio_charge_categories DISABLE ROW LEVEL SECURITY"),
    )
    op.drop_table("folio_charge_categories")
