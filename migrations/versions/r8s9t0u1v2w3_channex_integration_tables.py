"""Channex integration tables (property links, maps, revisions, logs).

Revision ID: r8s9t0u1v2w3
Revises: countrypack_005
Create Date: 2026-04-09

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "r8s9t0u1v2w3"
down_revision: Union[str, Sequence[str], None] = "countrypack_005"
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
        "channex_property_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("channex_property_id", sa.String(length=36), nullable=False),
        sa.Column("channex_api_key", sa.Text(), nullable=False),
        sa.Column(
            "channex_env",
            sa.String(length=20),
            nullable=False,
            server_default="production",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_channex_property_links_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            ["properties.tenant_id", "properties.id"],
            name="fk_channex_property_links_property_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="channex_property_links_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_channex_property_links_tenant_id_id",
        ),
        sa.UniqueConstraint(
            "property_id",
            name="uq_channex_property_links_property_id",
        ),
    )
    op.create_table(
        "channex_webhook_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column(
            "processed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_channex_webhook_logs_tenant_id_tenants",
        ),
        sa.PrimaryKeyConstraint("id", name="channex_webhook_logs_pkey"),
    )
    op.create_table(
        "channex_room_type_maps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("property_link_id", sa.Uuid(), nullable=False),
        sa.Column("room_type_id", sa.Uuid(), nullable=False),
        sa.Column("channex_room_type_id", sa.String(length=36), nullable=False),
        sa.Column("channex_room_type_name", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_link_id"],
            [
                "channex_property_links.tenant_id",
                "channex_property_links.id",
            ],
            name="fk_channex_room_type_maps_property_link_composite",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "room_type_id"],
            ["room_types.tenant_id", "room_types.id"],
            name="fk_channex_room_type_maps_room_type_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="channex_room_type_maps_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_channex_room_type_maps_tenant_id_id",
        ),
        sa.UniqueConstraint(
            "property_link_id",
            "room_type_id",
            name="uq_channex_room_type_maps_link_room_type",
        ),
        sa.UniqueConstraint(
            "property_link_id",
            "channex_room_type_id",
            name="uq_channex_room_type_maps_link_channex_rt",
        ),
    )
    op.create_table(
        "channex_rate_plan_maps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("room_type_map_id", sa.Uuid(), nullable=False),
        sa.Column("rate_plan_id", sa.Uuid(), nullable=False),
        sa.Column("channex_rate_plan_id", sa.String(length=36), nullable=False),
        sa.Column("channex_rate_plan_name", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "room_type_map_id"],
            [
                "channex_room_type_maps.tenant_id",
                "channex_room_type_maps.id",
            ],
            name="fk_channex_rate_plan_maps_room_type_map_composite",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rate_plan_id"],
            ["rate_plans.tenant_id", "rate_plans.id"],
            name="fk_channex_rate_plan_maps_rate_plan_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="channex_rate_plan_maps_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_channex_rate_plan_maps_tenant_id_id",
        ),
        sa.UniqueConstraint(
            "room_type_map_id",
            "rate_plan_id",
            name="uq_channex_rate_plan_maps_map_rate",
        ),
    )
    op.create_table(
        "channex_booking_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("property_link_id", sa.Uuid(), nullable=False),
        sa.Column("channex_revision_id", sa.String(length=36), nullable=False),
        sa.Column("channex_booking_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("channel_code", sa.String(length=50), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "processing_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("openpms_booking_id", sa.Uuid(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_link_id"],
            [
                "channex_property_links.tenant_id",
                "channex_property_links.id",
            ],
            name="fk_channex_booking_revisions_property_link_composite",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "openpms_booking_id"],
            ["bookings.tenant_id", "bookings.id"],
            name="fk_channex_booking_revisions_booking_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="channex_booking_revisions_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_channex_booking_revisions_tenant_id_id",
        ),
        sa.UniqueConstraint(
            "channex_revision_id",
            name="uq_channex_booking_revisions_channex_revision_id",
        ),
    )
    op.create_index(
        "ix_channex_booking_revisions_status_received",
        "channex_booking_revisions",
        ["processing_status", "received_at"],
        unique=False,
    )
    op.create_table(
        "channex_ari_push_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("property_link_id", sa.Uuid(), nullable=False),
        sa.Column(
            "request_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_link_id"],
            [
                "channex_property_links.tenant_id",
                "channex_property_links.id",
            ],
            name="fk_channex_ari_push_logs_property_link_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="channex_ari_push_logs_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_channex_ari_push_logs_tenant_id_id",
        ),
    )

    for tbl in (
        "channex_property_links",
        "channex_room_type_maps",
        "channex_rate_plan_maps",
        "channex_booking_revisions",
        "channex_ari_push_logs",
    ):
        _apply_rls_tenant_scoped(tbl)


def downgrade() -> None:
    for tbl in (
        "channex_ari_push_logs",
        "channex_booking_revisions",
        "channex_rate_plan_maps",
        "channex_room_type_maps",
        "channex_property_links",
    ):
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl}"))
        op.execute(sa.text(f"ALTER TABLE {tbl} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY"))

    op.drop_table("channex_ari_push_logs")
    op.drop_index(
        "ix_channex_booking_revisions_status_received",
        table_name="channex_booking_revisions",
    )
    op.drop_table("channex_booking_revisions")
    op.drop_table("channex_rate_plan_maps")
    op.drop_table("channex_room_type_maps")
    op.drop_table("channex_webhook_logs")
    op.drop_table("channex_property_links")
