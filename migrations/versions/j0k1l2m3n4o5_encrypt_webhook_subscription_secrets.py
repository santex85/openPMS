"""Encrypt webhook_subscriptions.secret at rest (Fernet).

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-03-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j0k1l2m3n4o5"
down_revision: Union[str, Sequence[str], None] = "i9j0k1l2m3n4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.core.config import get_settings
    from app.core.webhook_secrets import encrypt_webhook_secret

    connection = op.get_bind()
    settings = get_settings()
    rows = connection.execute(
        sa.text(
            "SELECT tenant_id, id, secret FROM webhook_subscriptions",
        ),
    ).fetchall()
    for tenant_id, sid, secret in rows:
        if secret is None:
            continue
        s = str(secret)
        if s.startswith("gAAAAA"):
            continue
        enc = encrypt_webhook_secret(settings, s)
        connection.execute(
            sa.text(
                "UPDATE webhook_subscriptions SET secret = :enc "
                "WHERE tenant_id = :tid AND id = :sid",
            ),
            {"enc": enc, "tid": tenant_id, "sid": sid},
        )


def downgrade() -> None:
    # Cannot restore plaintext without application key; leave ciphertext in place.
    pass
