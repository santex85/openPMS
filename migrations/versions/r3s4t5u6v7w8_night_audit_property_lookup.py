"""SECURITY DEFINER lookup for night-audit Celery fanout (bypasses RLS).

Revision ID: r3s4t5u6v7w8
Revises: q2r3s4t5u6v7
Create Date: 2026-07-11

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "r3s4t5u6v7w8"
down_revision: Union[str, Sequence[str], None] = "q2r3s4t5u6v7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.lookup_all_active_properties_for_worker()
            RETURNS TABLE(tenant_id uuid, property_id uuid, timezone text)
            LANGUAGE sql
            STABLE
            SECURITY DEFINER
            SET search_path = public
            SET row_security = off
            AS $$
              SELECT p.tenant_id, p.id, p.timezone
              FROM properties p
              INNER JOIN tenants t ON t.id = p.tenant_id
              WHERE t.status = 'active';
            $$;
            """,
        ),
    )
    op.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.lookup_all_active_properties_for_worker() "
            "FROM PUBLIC",
        ),
    )
    op.execute(
        sa.text(
            "GRANT EXECUTE ON FUNCTION public.lookup_all_active_properties_for_worker() "
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
                  'GRANT EXECUTE ON FUNCTION public.lookup_all_active_properties_for_worker() '
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
            "DROP FUNCTION IF EXISTS public.lookup_all_active_properties_for_worker()",
        ),
    )
