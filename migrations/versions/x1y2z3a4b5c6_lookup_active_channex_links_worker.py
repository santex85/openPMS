"""SECURITY DEFINER lookup for nightly Channex ARI fanout (bypasses RLS).

Celery beat has no JWT / app.tenant_id; list active property links for fanout.

Revision ID: x1y2z3a4b5c6
Revises: w0x1y2z3a4b5
Create Date: 2026-04-12

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "x1y2z3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "w0x1y2z3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.lookup_active_channex_property_links_for_worker()
            RETURNS TABLE(tenant_id uuid, property_id uuid)
            LANGUAGE sql
            STABLE
            SECURITY DEFINER
            SET search_path = public
            AS $$
              SELECT cpl.tenant_id, cpl.property_id
              FROM channex_property_links cpl
              WHERE cpl.status = 'active';
            $$;
            """
        ),
    )
    op.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.lookup_active_channex_property_links_for_worker() "
            "FROM PUBLIC",
        ),
    )
    op.execute(
        sa.text(
            "GRANT EXECUTE ON FUNCTION public.lookup_active_channex_property_links_for_worker() "
            "TO CURRENT_USER",
        ),
    )
    op.execute(
        sa.text(
            """
            DO $grant_openpms$
            BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'openpms') THEN
                EXECUTE
                  'GRANT EXECUTE ON FUNCTION public.lookup_active_channex_property_links_for_worker() '
                  'TO openpms';
              END IF;
            END
            $grant_openpms$;
            """,
        ),
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP FUNCTION IF EXISTS public.lookup_active_channex_property_links_for_worker()",
        ),
    )
