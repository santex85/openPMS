"""Guest profile fields, unique email per tenant; room housekeeping; event log.

Revision ID: c1d2e3f4a5b6
Revises: b2c3d4e5f6a7
Create Date: 2026-03-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
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
    op.execute(sa.text("UPDATE guests SET email = lower(trim(email))"))

    op.add_column(
        "guests",
        sa.Column("nationality", sa.String(length=2), nullable=True),
    )
    op.add_column("guests", sa.Column("date_of_birth", sa.Date(), nullable=True))
    op.add_column("guests", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column(
        "guests",
        sa.Column(
            "vip_status",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "guests",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "guests",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_unique_constraint(
        "uq_guests_tenant_email",
        "guests",
        ["tenant_id", "email"],
    )

    op.add_column(
        "rooms",
        sa.Column(
            "housekeeping_status",
            sa.String(length=32),
            nullable=False,
            server_default="clean",
        ),
    )
    op.add_column(
        "rooms",
        sa.Column(
            "housekeeping_priority",
            sa.String(length=32),
            nullable=False,
            server_default="normal",
        ),
    )

    op.create_table(
        "room_housekeeping_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("old_status", sa.String(length=32), nullable=False),
        sa.Column("new_status", sa.String(length=32), nullable=False),
        sa.Column("changed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "room_id"],
            ["rooms.tenant_id", "rooms.id"],
            name="fk_hk_events_room_composite",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "changed_by_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_hk_events_user_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="room_housekeeping_events_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_room_housekeeping_events_tenant_id_id",
        ),
    )
    _apply_rls_tenant_scoped("room_housekeeping_events")


def downgrade() -> None:
    _drop_rls_and_table("room_housekeeping_events")
    op.drop_column("rooms", "housekeeping_priority")
    op.drop_column("rooms", "housekeeping_status")
    op.drop_constraint("uq_guests_tenant_email", "guests", type_="unique")
    op.drop_column("guests", "updated_at")
    op.drop_column("guests", "created_at")
    op.drop_column("guests", "vip_status")
    op.drop_column("guests", "notes")
    op.drop_column("guests", "date_of_birth")
    op.drop_column("guests", "nationality")
