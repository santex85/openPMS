"""Task 2.3: guests, bookings, booking_lines, folio_transactions + RLS.

Revision ID: a9b03c4d5e6f
Revises: f8a92b3c4d5e
Create Date: 2026-03-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9b03c4d5e6f"
down_revision: Union[str, Sequence[str], None] = "f8a92b3c4d5e"
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
        "guests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("first_name", sa.String(length=255), nullable=False),
        sa.Column("last_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=64), nullable=False),
        sa.Column("passport_data", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_guests_tenant_id_tenants",
        ),
        sa.PrimaryKeyConstraint("id", name="guests_pkey"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_guests_tenant_id_id"),
    )
    _apply_rls_tenant_scoped("guests")

    op.create_table(
        "bookings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("guest_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_bookings_property_composite",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "guest_id"],
            ["guests.tenant_id", "guests.id"],
            name="fk_bookings_guest_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="bookings_pkey"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_bookings_tenant_id_id"),
    )
    _apply_rls_tenant_scoped("bookings")

    op.create_table(
        "booking_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("booking_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("room_type_id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=True),
        sa.Column("price_for_date", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "booking_id"],
            ["bookings.tenant_id", "bookings.id"],
            name="fk_booking_lines_booking_composite",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "room_type_id"],
            ["room_types.tenant_id", "room_types.id"],
            name="fk_booking_lines_room_type_composite",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "room_id"],
            ["rooms.tenant_id", "rooms.id"],
            name="fk_booking_lines_room_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="booking_lines_pkey"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_booking_lines_tenant_id_id"),
    )
    _apply_rls_tenant_scoped("booking_lines")

    op.create_table(
        "folio_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("booking_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("payment_method", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "booking_id"],
            ["bookings.tenant_id", "bookings.id"],
            name="fk_folio_transactions_booking_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="folio_transactions_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_folio_transactions_tenant_id_id",
        ),
    )
    _apply_rls_tenant_scoped("folio_transactions")


def downgrade() -> None:
    _drop_rls_and_table("folio_transactions")
    _drop_rls_and_table("booking_lines")
    _drop_rls_and_table("bookings")
    _drop_rls_and_table("guests")
