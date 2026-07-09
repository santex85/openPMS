"""Tenant resolver for inbound Stripe webhooks (SECURITY DEFINER, bypass RLS).

Inbound Stripe webhooks carry no JWT/tenant. The connected-account id in
stripe_connections is encrypted at rest, so we cannot map event.account -> tenant.
Instead we resolve by the PaymentIntent id stored as stripe_charges.stripe_charge_id
(charges store the `pi_...` id). RLS is FORCEd, so the helper turns row security
off in its own body to look up across tenants for a single known charge.

Revision ID: q2r3s4t5u6v7
Revises: p1q2r3s4t5u6
Create Date: 2026-07-09
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "q2r3s4t5u6v7"
down_revision: Union[str, Sequence[str], None] = "p1q2r3s4t5u6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.lookup_stripe_charge_for_webhook(p_pi text)
            RETURNS TABLE (tenant_id uuid, charge_id uuid)
            LANGUAGE sql
            STABLE
            SECURITY DEFINER
            SET search_path = public
            SET row_security = off
            AS $$
              SELECT sc.tenant_id, sc.id
              FROM stripe_charges sc
              WHERE sc.stripe_charge_id = p_pi
              LIMIT 1;
            $$;
            """,
        ),
    )
    op.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.lookup_stripe_charge_for_webhook(text) FROM PUBLIC",
        ),
    )
    op.execute(
        sa.text(
            "GRANT EXECUTE ON FUNCTION public.lookup_stripe_charge_for_webhook(text) TO CURRENT_USER",
        ),
    )
    op.execute(
        sa.text(
            """
            DO $grant_openpms$
            BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'openpms') THEN
                EXECUTE
                  'GRANT EXECUTE ON FUNCTION public.lookup_stripe_charge_for_webhook(text) TO openpms';
              END IF;
            END
            $grant_openpms$;
            """,
        ),
    )


def downgrade() -> None:
    op.execute(
        sa.text("DROP FUNCTION IF EXISTS public.lookup_stripe_charge_for_webhook(text)"),
    )
