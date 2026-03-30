"""Align api_keys RLS with FORCE; SECURITY DEFINER lookup for middleware.

X-API-Key auth must resolve tenant before app.tenant_id is set. A NO FORCE table
let the table owner bypass policies; FORCE aligns with other tenant tables.
Lookup uses a narrow SECURITY DEFINER function (grants EXECUTE to app role).

Revision ID: i9j0k1l2m3n4
Revises: h7i8j9k0l1m2
Create Date: 2026-03-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i9j0k1l2m3n4"
down_revision: Union[str, Sequence[str], None] = "h7i8j9k0l1m2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION public.lookup_api_key_by_hash(p_key_hash text)
            RETURNS TABLE (tenant_id uuid, key_id uuid, scopes text[])
            LANGUAGE sql
            STABLE
            SECURITY DEFINER
            SET search_path = public
            AS $$
              SELECT ak.tenant_id, ak.id, ak.scopes
              FROM api_keys ak
              WHERE ak.key_hash = p_key_hash
                AND ak.is_active IS TRUE
                AND (ak.expires_at IS NULL OR ak.expires_at > now());
            $$;
            """
        ),
    )
    op.execute(sa.text("REVOKE ALL ON FUNCTION public.lookup_api_key_by_hash(text) FROM PUBLIC"))
    op.execute(sa.text("GRANT EXECUTE ON FUNCTION public.lookup_api_key_by_hash(text) TO CURRENT_USER"))
    op.execute(
        sa.text(
            """
            DO $grant_openpms$
            BEGIN
              IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'openpms') THEN
                EXECUTE 'GRANT EXECUTE ON FUNCTION public.lookup_api_key_by_hash(text) TO openpms';
              END IF;
            END
            $grant_openpms$;
            """
        ),
    )
    op.execute(sa.text("ALTER TABLE api_keys FORCE ROW LEVEL SECURITY"))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE api_keys NO FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.lookup_api_key_by_hash(text)"))
