"""Webhook delivery logs: CASCADE delete when subscription removed.

Revision ID: k0l1m2n3o4p5
Revises: j0k1l2m3n4o5
Create Date: 2026-03-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "k0l1m2n3o4p5"
down_revision: Union[str, Sequence[str], None] = "j0k1l2m3n4o5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_webhook_delivery_logs_subscription_composite",
        "webhook_delivery_logs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_webhook_delivery_logs_subscription_composite",
        "webhook_delivery_logs",
        "webhook_subscriptions",
        ["tenant_id", "webhook_subscription_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_webhook_delivery_logs_subscription_composite",
        "webhook_delivery_logs",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_webhook_delivery_logs_subscription_composite",
        "webhook_delivery_logs",
        "webhook_subscriptions",
        ["tenant_id", "webhook_subscription_id"],
        ["tenant_id", "id"],
    )
