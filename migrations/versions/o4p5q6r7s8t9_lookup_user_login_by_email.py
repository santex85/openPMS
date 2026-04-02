"""SECURITY DEFINER lookup for password login without tenant_id.

RLS on users uses FORCE; resolving tenant by email must bypass tenant session.

Revision ID: o4p5q6r7s8t9
Revises: n3o4p5q6r7s8
Create Date: 2026-04-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "o4p5q6r7s8t9"
down_revision: Union[str, Sequence[str], None] = "n3o4p5q6r7s8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.lookup_active_users_by_email_login(p_email text)
            RETURNS TABLE (tenant_id uuid, user_id uuid)
            LANGUAGE sql
            STABLE
            SECURITY DEFINER
            SET search_path = public
            AS $$
              SELECT u.tenant_id, u.id
              FROM users u
              WHERE lower(trim(both from u.email)) = lower(trim(both from p_email))
                AND u.is_active IS TRUE;
            $$;
            """,
        ),
    )
    op.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.lookup_active_users_by_email_login(text) FROM PUBLIC",
        ),
    )
    op.execute(
        sa.text(
            "GRANT EXECUTE ON FUNCTION public.lookup_active_users_by_email_login(text) TO CURRENT_USER",
        ),
    )
    op.execute(
        sa.text(
            """
            DO $grant_openpms$
            BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'openpms') THEN
                EXECUTE
                  'GRANT EXECUTE ON FUNCTION public.lookup_active_users_by_email_login(text) TO openpms';
              END IF;
            END
            $grant_openpms$;
            """,
        ),
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP FUNCTION IF EXISTS public.lookup_active_users_by_email_login(text)",
        ),
    )
