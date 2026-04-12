"""Phase 0: tax_configs + tax_mode enum, Stripe tables, folio source_channel.

Revision ID: w0x1y2z3a4b5
Revises: v4w5x6y7z8a9
Create Date: 2026-04-12

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "w0x1y2z3a4b5"
down_revision: Union[str, Sequence[str], None] = "v4w5x6y7z8a9"
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
    tax_mode = postgresql.ENUM(
        "off",
        "inclusive",
        "exclusive",
        name="tax_mode",
        create_type=True,
    )
    tax_mode.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "tax_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column(
            "tax_mode",
            postgresql.ENUM(
                "off",
                "inclusive",
                "exclusive",
                name="tax_mode",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("tax_name", sa.String(length=255), nullable=False),
        sa.Column("tax_rate", sa.Numeric(5, 4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tax_configs_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_tax_configs_property_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="tax_configs_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "property_id",
            name="uq_tax_configs_tenant_property",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_tax_configs_tenant_id_id",
        ),
    )
    op.create_index(
        "ix_tax_configs_property_id",
        "tax_configs",
        ["property_id"],
        unique=False,
    )
    _apply_rls_tenant_scoped("tax_configs")

    op.create_table(
        "stripe_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("stripe_account_id", sa.Text(), nullable=False),
        sa.Column("livemode", sa.Boolean(), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_stripe_connections_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_stripe_connections_property_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="stripe_connections_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "property_id",
            name="uq_stripe_connections_tenant_property",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_stripe_connections_tenant_id_id",
        ),
    )
    _apply_rls_tenant_scoped("stripe_connections")

    op.create_table(
        "stripe_payment_methods",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("booking_id", sa.Uuid(), nullable=True),
        sa.Column("stripe_pm_id", sa.String(length=255), nullable=False),
        sa.Column("card_last4", sa.String(length=4), nullable=True),
        sa.Column("card_brand", sa.String(length=32), nullable=True),
        sa.Column("card_exp_month", sa.SmallInteger(), nullable=True),
        sa.Column("card_exp_year", sa.SmallInteger(), nullable=True),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_stripe_payment_methods_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_stripe_payment_methods_property_composite",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "booking_id"],
            ["bookings.tenant_id", "bookings.id"],
            name="fk_stripe_payment_methods_booking_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="stripe_payment_methods_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_stripe_payment_methods_tenant_id_id",
        ),
    )
    _apply_rls_tenant_scoped("stripe_payment_methods")

    op.create_table(
        "stripe_charges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("booking_id", sa.Uuid(), nullable=True),
        sa.Column("folio_tx_id", sa.Uuid(), nullable=True),
        sa.Column("stripe_charge_id", sa.String(length=255), nullable=False),
        sa.Column("stripe_pm_id", sa.String(length=255), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'refunded', 'partial_refund')",
            name="ck_stripe_charges_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_stripe_charges_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_stripe_charges_property_composite",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "booking_id"],
            ["bookings.tenant_id", "bookings.id"],
            name="fk_stripe_charges_booking_composite",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "folio_tx_id"],
            ["folio_transactions.tenant_id", "folio_transactions.id"],
            name="fk_stripe_charges_folio_tx_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="stripe_charges_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_stripe_charges_tenant_id_id",
        ),
    )
    _apply_rls_tenant_scoped("stripe_charges")

    op.add_column(
        "folio_transactions",
        sa.Column("source_channel", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("folio_transactions", "source_channel")

    for tbl in (
        "stripe_charges",
        "stripe_payment_methods",
        "stripe_connections",
        "tax_configs",
    ):
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl}"))

    op.drop_table("stripe_charges")
    op.drop_table("stripe_payment_methods")
    op.drop_table("stripe_connections")

    op.drop_index("ix_tax_configs_property_id", table_name="tax_configs")
    op.drop_table("tax_configs")

    tax_mode = postgresql.ENUM(name="tax_mode")
    tax_mode.drop(op.get_bind(), checkfirst=True)
