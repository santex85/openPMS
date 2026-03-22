"""Task 2.1: core domain (tenants, properties, room_types, rooms) + RLS; drop mt_rls_smoke.

Revision ID: e7f91a2b3c4d
Revises: c4f8a1b2d3e0
Create Date: 2026-03-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7f91a2b3c4d"
down_revision: Union[str, Sequence[str], None] = "c4f8a1b2d3e0"
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


def _apply_rls_tenants() -> None:
    op.execute(sa.text("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE tenants FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON tenants"))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON tenants
            FOR ALL
            TO PUBLIC
            USING (
                id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
            )
            WITH CHECK (
                id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
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
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON mt_rls_smoke"))
    op.execute(sa.text("ALTER TABLE mt_rls_smoke NO FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE mt_rls_smoke DISABLE ROW LEVEL SECURITY"))
    op.drop_table("mt_rls_smoke")

    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("billing_email", sa.String(length=320), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name="tenants_pkey"),
    )
    _apply_rls_tenants()

    op.create_table(
        "properties",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("checkin_time", sa.Time(), nullable=False),
        sa.Column("checkout_time", sa.Time(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_properties_tenant_id_tenants",
        ),
        sa.PrimaryKeyConstraint("id", name="properties_pkey"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_properties_tenant_id_id"),
    )
    _apply_rls_tenant_scoped("properties")

    op.create_table(
        "room_types",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("base_occupancy", sa.Integer(), nullable=False),
        sa.Column("max_occupancy", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_room_types_property_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="room_types_pkey"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_room_types_tenant_id_id"),
    )
    _apply_rls_tenant_scoped("room_types")

    op.create_table(
        "rooms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("room_type_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "room_type_id"],
            ["room_types.tenant_id", "room_types.id"],
            name="fk_rooms_room_type_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="rooms_pkey"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_rooms_tenant_id_id"),
    )
    _apply_rls_tenant_scoped("rooms")


def _recreate_mt_rls_smoke() -> None:
    op.create_table(
        "mt_rls_smoke",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="mt_rls_smoke_pkey"),
    )
    op.execute(sa.text("ALTER TABLE mt_rls_smoke ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE mt_rls_smoke FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON mt_rls_smoke"))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON mt_rls_smoke
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


def downgrade() -> None:
    _drop_rls_and_table("rooms")
    _drop_rls_and_table("room_types")
    _drop_rls_and_table("properties")
    _drop_rls_and_table("tenants")
    _recreate_mt_rls_smoke()
