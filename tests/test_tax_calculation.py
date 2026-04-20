"""Unit tests for country-agnostic tax engine (no DB)."""

from decimal import Decimal

from app.models.billing.tax_config import TaxMode
from app.services.tax_service import (
    calculate_country_pack_tax_posting,
    calculate_taxes_from_rules,
)


def test_thailand_seven_plus_ten_compound() -> None:
    rules = [
        {
            "code": "VAT",
            "name": "VAT",
            "rate": 0.07,
            "inclusive": False,
            "applies_to": ["all"],
            "compound_after": None,
            "display_on_folio": True,
            "active": True,
        },
        {
            "code": "SERVICE_CHARGE",
            "name": "Service Charge",
            "rate": 0.10,
            "inclusive": False,
            "applies_to": ["room_charge"],
            "compound_after": "VAT",
            "display_on_folio": True,
            "active": True,
        },
    ]
    result = calculate_taxes_from_rules(
        Decimal("1000.00"),
        rules,
        applies_to_category="room_charge",
    )
    assert len(result.lines) == 2
    assert result.lines[0].amount == Decimal("70.00")
    assert result.lines[1].amount == Decimal("107.00")
    assert result.total_with_taxes == Decimal("1177.00")


def test_empty_rules_no_tax() -> None:
    result = calculate_taxes_from_rules(
        Decimal("100.00"),
        [],
        applies_to_category="room_charge",
    )
    assert result.lines == []
    assert result.total_with_taxes == Decimal("100.00")


def test_applies_to_filter() -> None:
    rules = [
        {
            "code": "FOOD",
            "name": "Food tax",
            "rate": 0.05,
            "inclusive": False,
            "applies_to": ["food_beverage"],
            "compound_after": None,
            "active": True,
        },
    ]
    r_room = calculate_taxes_from_rules(
        Decimal("100.00"),
        rules,
        applies_to_category="room_charge",
    )
    assert r_room.lines == []

    r_food = calculate_taxes_from_rules(
        Decimal("100.00"),
        rules,
        applies_to_category="food_beverage",
    )
    assert len(r_food.lines) == 1
    assert r_food.lines[0].amount == Decimal("5.00")


def test_inclusive_extracts_from_gross() -> None:
    rules = [
        {
            "code": "VAT",
            "name": "VAT incl",
            "rate": 0.07,
            "inclusive": True,
            "applies_to": ["all"],
            "active": True,
        },
    ]
    result = calculate_taxes_from_rules(
        Decimal("107.00"),
        rules,
        applies_to_category="room_charge",
    )
    assert len(result.lines) == 1
    assert result.lines[0].code == "VAT"
    assert result.total_with_taxes == Decimal("107.00")


def test_country_pack_posting_inclusive_keeps_gross_total() -> None:
    rules = [
        {
            "code": "VAT",
            "name": "VAT",
            "rate": 0.07,
            "inclusive": False,
            "applies_to": ["all"],
            "compound_after": None,
            "active": True,
        },
        {
            "code": "SERVICE_CHARGE",
            "name": "Service Charge",
            "rate": 0.10,
            "inclusive": False,
            "applies_to": ["room_charge"],
            "compound_after": "VAT",
            "active": True,
        },
    ]
    posting = calculate_country_pack_tax_posting(
        Decimal("30000.00"),
        rules,
        applies_to_category="room_charge",
        mode=TaxMode.inclusive,
    )
    assert [line.code for line in posting.lines] == ["VAT", "SERVICE_CHARGE"]
    assert posting.total_amount == Decimal("30000.00")
    assert posting.room_charge_amount == Decimal("25310.11")
    assert sum((line.amount for line in posting.lines), Decimal("0.00")) == Decimal(
        "4689.89"
    )


def test_country_pack_posting_off_removes_auto_taxes() -> None:
    posting = calculate_country_pack_tax_posting(
        Decimal("30000.00"),
        [
            {
                "code": "VAT",
                "name": "VAT",
                "rate": 0.07,
                "inclusive": False,
                "applies_to": ["all"],
                "active": True,
            },
        ],
        applies_to_category="room_charge",
        mode=TaxMode.off,
    )
    assert posting.lines == []
    assert posting.room_charge_amount == Decimal("30000.00")
    assert posting.total_amount == Decimal("30000.00")
