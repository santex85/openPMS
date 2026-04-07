"""Seed builtin Thailand and Generic country packs.

Revision ID: countrypack_002
Revises: countrypack_001
Create Date: 2026-04-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "countrypack_002"
down_revision: Union[str, Sequence[str], None] = "countrypack_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TH_TAXES = r"""[
  {"code": "VAT", "name": "VAT", "rate": 0.07, "inclusive": false, "applies_to": ["all"], "compound_after": null, "display_on_folio": true, "active": true},
  {"code": "SERVICE_CHARGE", "name": "Service Charge", "rate": 0.10, "inclusive": false, "applies_to": ["room_charge", "food", "beverage", "services", "food_beverage"], "compound_after": "VAT", "display_on_folio": true, "active": true}
]"""


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO country_packs (
                code, tenant_id, name, currency_code, currency_symbol,
                currency_symbol_position, currency_decimal_places, timezone,
                date_format, locale, default_checkin_time, default_checkout_time,
                taxes, payment_methods, fiscal_year_start, is_builtin
            ) VALUES (
                'TH', NULL, 'Thailand', 'THB', :thb_sym,
                'before', 2, 'Asia/Bangkok',
                'DD/MM/YYYY', 'th-TH', TIME '14:00:00', TIME '12:00:00',
                CAST(:th_taxes AS jsonb),
                CAST(:th_pm AS jsonb),
                '01-01', true
            )
            """
        ).bindparams(
            thb_sym="฿",
            th_taxes=_TH_TAXES,
            th_pm='["cash", "credit_card", "promptpay", "qr_code", "bank_transfer"]',
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO country_packs (
                code, tenant_id, name, currency_code, currency_symbol,
                currency_symbol_position, currency_decimal_places, timezone,
                date_format, locale, default_checkin_time, default_checkout_time,
                taxes, payment_methods, fiscal_year_start, is_builtin
            ) VALUES (
                'XX', NULL, 'Generic / Unspecified', 'USD', '$',
                'before', 2, 'UTC',
                'YYYY-MM-DD', 'en-US', TIME '14:00:00', TIME '11:00:00',
                '[]'::jsonb,
                '["cash", "credit_card", "bank_transfer"]'::jsonb,
                NULL, true
            )
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM country_packs WHERE code IN ('TH', 'XX')"))
