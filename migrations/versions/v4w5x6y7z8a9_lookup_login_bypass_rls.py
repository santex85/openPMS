"""Login-by-email helper must bypass RLS when invoked as app role.

SECURITY DEFINER alone is not enough: queries in the function still
enforce row security for the definer when RLS is FORCE. Set row_security
off for the function body so email login without tenant_id can resolve.

Revision ID: v4w5x6y7z8a9
Revises: u3v4w5x6y7z8
Create Date: 2026-04-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v4w5x6y7z8a9"
down_revision: Union[str, Sequence[str], None] = "u3v4w5x6y7z8"
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
            SET row_security = off
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
