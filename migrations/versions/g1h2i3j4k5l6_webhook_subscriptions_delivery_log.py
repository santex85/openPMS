"""Webhook subscriptions and delivery attempt log.

Revision ID: g1h2i3j4k5l6
Revises: f5a6b7c8d9e0
Create Date: 2026-03-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "g1h2i3j4k5l6"
down_revision: Union[str, Sequence[str], None] = "f5a6b7c8d9e0"
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
        "webhook_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("events", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("secret", sa.String(length=512), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_webhook_subscriptions_tenant_id_tenants",
        ),
        sa.PrimaryKeyConstraint("id", name="webhook_subscriptions_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_webhook_subscriptions_tenant_id_id",
        ),
    )
    _apply_rls_tenant_scoped("webhook_subscriptions")

    op.create_table(
        "webhook_delivery_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("webhook_subscription_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("http_status_code", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_webhook_delivery_logs_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "webhook_subscription_id"],
            ["webhook_subscriptions.tenant_id", "webhook_subscriptions.id"],
            name="fk_webhook_delivery_logs_subscription_composite",
        ),
        sa.PrimaryKeyConstraint("id", name="webhook_delivery_logs_pkey"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_webhook_delivery_logs_tenant_id_id",
        ),
    )
    _apply_rls_tenant_scoped("webhook_delivery_logs")


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON webhook_delivery_logs"))
    op.execute(
        sa.text("ALTER TABLE webhook_delivery_logs NO FORCE ROW LEVEL SECURITY"),
    )
    op.execute(sa.text("ALTER TABLE webhook_delivery_logs DISABLE ROW LEVEL SECURITY"))
    op.drop_table("webhook_delivery_logs")

    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON webhook_subscriptions"))
    op.execute(
        sa.text("ALTER TABLE webhook_subscriptions NO FORCE ROW LEVEL SECURITY"),
    )
    op.execute(sa.text("ALTER TABLE webhook_subscriptions DISABLE ROW LEVEL SECURITY"))
    op.drop_table("webhook_subscriptions")
