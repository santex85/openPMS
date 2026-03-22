"""Task 2.2: rate_plans, rates, availability_ledger + RLS.

Revision ID: f8a92b3c4d5e
Revises: e7f91a2b3c4d
Create Date: 2026-03-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8a92b3c4d5e"
down_revision: Union[str, Sequence[str], None] = "e7f91a2b3c4d"
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


def _drop_rls_and_table(table: str) -> None:
    op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
    op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    op.drop_table(table)


def upgrade() -> None:
    op.create_table(
        "rate_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("cancellation_policy", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_rate_plans_property_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="rate_plans_pkey"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_rate_plans_tenant_id_id"),
    )
    _apply_rls_tenant_scoped("rate_plans")

    op.create_table(
        "rates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("room_type_id", sa.Uuid(), nullable=False),
        sa.Column("rate_plan_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "room_type_id"],
            ["room_types.tenant_id", "room_types.id"],
            name="fk_rates_room_type_composite",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rate_plan_id"],
            ["rate_plans.tenant_id", "rate_plans.id"],
            name="fk_rates_rate_plan_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="rates_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "room_type_id",
            "rate_plan_id",
            "date",
            name="uq_rates_tenant_room_type_plan_date",
        ),
    )
    _apply_rls_tenant_scoped("rates")

    op.create_table(
        "availability_ledger",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("room_type_id", sa.Uuid(), nullable=False),
        sa.Column("total_rooms", sa.Integer(), nullable=False),
        sa.Column(
            "booked_rooms",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "blocked_rooms",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "room_type_id"],
            ["room_types.tenant_id", "room_types.id"],
            name="fk_availability_ledger_room_type_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="availability_ledger_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "room_type_id",
            "date",
            name="uq_availability_ledger_tenant_room_type_date",
        ),
    )
    _apply_rls_tenant_scoped("availability_ledger")


def downgrade() -> None:
    _drop_rls_and_table("availability_ledger")
    _drop_rls_and_table("rates")
    _drop_rls_and_table("rate_plans")
