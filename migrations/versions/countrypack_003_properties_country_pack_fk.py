"""Add properties.country_pack_code FK to country_packs (nullable).

Revision ID: countrypack_003
Revises: countrypack_002
Create Date: 2026-04-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "countrypack_003"
down_revision: Union[str, Sequence[str], None] = "countrypack_002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "properties",
        sa.Column("country_pack_code", sa.String(length=32), nullable=True),
    )
    op.create_foreign_key(
        "fk_properties_country_pack_code_country_packs",
        "properties",
        "country_packs",
        ["country_pack_code"],
        ["code"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_properties_country_pack_code_country_packs",
        "properties",
        type_="foreignkey",
    )
    op.drop_column("properties", "country_pack_code")
