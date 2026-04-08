"""Add channex_webhook_id to channex_property_links.

Revision ID: s0t1u2v3w4x5
Revises: r8s9t0u1v2w3
Create Date: 2026-04-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "s0t1u2v3w4x5"
down_revision = "r8s9t0u1v2w3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "channex_property_links",
        sa.Column(
            "channex_webhook_id",
            sa.String(length=36),
            nullable=True,
        ),
    )
    # Inbound Channex webhooks have no JWT/tenant; resolve tenant for RLS via SECURITY DEFINER.
    op.execute(
        sa.text(
            """
CREATE OR REPLACE FUNCTION lookup_channex_link_for_webhook(p_cx_property_id text)
RETURNS TABLE(tenant_id uuid, link_id uuid)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
STABLE
AS $$
  SELECT cpl.tenant_id, cpl.id
  FROM channex_property_links cpl
  WHERE cpl.channex_property_id = p_cx_property_id
  LIMIT 1;
$$;
"""
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP FUNCTION IF EXISTS lookup_channex_link_for_webhook(text)"))
    op.drop_column("channex_property_links", "channex_webhook_id")
