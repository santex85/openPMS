"""Webhook pending deliveries queue (background worker; RLS + internal worker policy).

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-04-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "q7r8s9t0u1v2"
down_revision: Union[str, Sequence[str], None] = "p6q7r8s9t0u1"
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
        "webhook_pending_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("webhook_subscription_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
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
            name="fk_webhook_pending_deliveries_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "webhook_subscription_id"],
            ["webhook_subscriptions.tenant_id", "webhook_subscriptions.id"],
            name="fk_webhook_pending_deliveries_subscription_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="webhook_pending_deliveries_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_webhook_pending_deliveries_tenant_id_id",
        ),
    )
    op.create_index(
        "ix_webhook_pending_deliveries_due_pending",
        "webhook_pending_deliveries",
        ["next_retry_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    _apply_rls_tenant_scoped("webhook_pending_deliveries")
    op.execute(
        sa.text(
            """
            CREATE POLICY webhook_pending_internal_worker_select
            ON webhook_pending_deliveries
            FOR SELECT
            TO PUBLIC
            USING (current_setting('app.internal_webhook_worker', true) = 'true')
            """
        ),
    )
    op.execute(
        sa.text(
            """
            CREATE POLICY webhook_pending_internal_worker_update
            ON webhook_pending_deliveries
            FOR UPDATE
            TO PUBLIC
            USING (current_setting('app.internal_webhook_worker', true) = 'true')
            WITH CHECK (current_setting('app.internal_webhook_worker', true) = 'true')
            """
        ),
    )
    op.execute(
        sa.text(
            """
            CREATE POLICY webhook_pending_internal_worker_delete
            ON webhook_pending_deliveries
            FOR DELETE
            TO PUBLIC
            USING (current_setting('app.internal_webhook_worker', true) = 'true')
            """
        ),
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP POLICY IF EXISTS webhook_pending_internal_worker_delete "
            "ON webhook_pending_deliveries",
        ),
    )
    op.execute(
        sa.text(
            "DROP POLICY IF EXISTS webhook_pending_internal_worker_update "
            "ON webhook_pending_deliveries",
        ),
    )
    op.execute(
        sa.text(
            "DROP POLICY IF EXISTS webhook_pending_internal_worker_select "
            "ON webhook_pending_deliveries",
        ),
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON webhook_pending_deliveries"))
    op.execute(
        sa.text("ALTER TABLE webhook_pending_deliveries NO FORCE ROW LEVEL SECURITY"),
    )
    op.execute(sa.text("ALTER TABLE webhook_pending_deliveries DISABLE ROW LEVEL SECURITY"))
    op.drop_index(
        "ix_webhook_pending_deliveries_due_pending",
        table_name="webhook_pending_deliveries",
    )
    op.drop_table("webhook_pending_deliveries")
