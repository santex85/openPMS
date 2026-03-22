"""mt_rls_smoke table + tenant RLS

Temporary table to verify PostgreSQL RLS with app.tenant_id (SET LOCAL / set_config).
Replace when domain models ship.

Revision ID: c4f8a1b2d3e0
Revises: 22d827ba2db9
Create Date: 2026-03-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4f8a1b2d3e0"
down_revision: Union[str, Sequence[str], None] = "22d827ba2db9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS mt_rls_smoke (
                id UUID NOT NULL,
                tenant_id UUID NOT NULL,
                note TEXT,
                CONSTRAINT mt_rls_smoke_pkey PRIMARY KEY (id)
            )
            """
        )
    )
    op.execute(sa.text("ALTER TABLE mt_rls_smoke ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE mt_rls_smoke FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON mt_rls_smoke"))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON mt_rls_smoke
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


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON mt_rls_smoke"))
    op.execute(sa.text("ALTER TABLE mt_rls_smoke NO FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE mt_rls_smoke DISABLE ROW LEVEL SECURITY"))
    op.drop_table("mt_rls_smoke")
