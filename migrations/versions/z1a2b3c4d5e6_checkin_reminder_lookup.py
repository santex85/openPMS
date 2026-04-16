"""SECURITY DEFINER lookup for check-in reminder Celery task (bypasses RLS).

Revision ID: z1a2b3c4d5e6
Revises: z0a1b2c3d4e5
Create Date: 2026-04-13

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "z1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "z0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.lookup_checkin_reminder_candidates(
                for_date date
            )
            RETURNS TABLE(tenant_id uuid, booking_id uuid)
            LANGUAGE sql
            STABLE
            SECURITY DEFINER
            SET search_path = public
            AS $$
              SELECT b.tenant_id, b.id
              FROM bookings b
              INNER JOIN guests g
                ON g.tenant_id = b.tenant_id AND g.id = b.guest_id
              WHERE b.status = 'confirmed'
                AND g.email IS NOT NULL
                AND g.email NOT LIKE '%.invalid'
                AND (
                  SELECT min(bl.date)
                  FROM booking_lines bl
                  WHERE bl.tenant_id = b.tenant_id AND bl.booking_id = b.id
                ) = for_date;
            $$;
            """,
        ),
    )
    op.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.lookup_checkin_reminder_candidates(date) "
            "FROM PUBLIC",
        ),
    )
    op.execute(
        sa.text(
            "GRANT EXECUTE ON FUNCTION public.lookup_checkin_reminder_candidates(date) "
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
                  'GRANT EXECUTE ON FUNCTION public.lookup_checkin_reminder_candidates(date) '
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
            "DROP FUNCTION IF EXISTS public.lookup_checkin_reminder_candidates(date)",
        ),
    )
