from __future__ import annotations

from app.matching import (
    RawInvoiceLine,
    _brand_compatible,
    _has_numeric_conflict,
    _identifier_digits_match,
    _linked_supplier_item_is_safe,
)


def test_identifier_digits_match_finds_supplier_code_inside_upc():
    assert _identifier_digits_match(
        "AT01251",
        {
            "name": "Aquatop Classic Aqua Flow CAF-25 Internal Sponge Filter",
            "sku": "810281012513",
        },
    )


def test_identifier_digits_match_ignores_short_numeric_tails():
    assert not _identifier_digits_match(
        "RF123",
        {"name": "Unrelated Product", "sku": "000000123999"},
    )


def test_brand_compatible_uses_supplier_prefix_when_description_is_generic():
    line = RawInvoiceLine(
        supplier_code="AT01251",
        description="Sponge Filter for up to 25 Gallons",
        barcode=None,
        quantity=1,
        unit_cost=1,
    )

    assert _brand_compatible(
        line,
        {"name": "AQUATOP CAF-25 Internal Sponge Filter"},
    )


def test_numeric_conflict_blocks_size_or_model_variant():
    assert _has_numeric_conflict(
        "Seachem Matrix Carbon 4L",
        "Seachem Matrix Carbon 500mL",
    )
    assert _has_numeric_conflict(
        "Sicce Syncra SDC 7.0 Controllable DC Pump 800-1900gph",
        "Sicce Syncra SDC 6.0 Controllable DC Pump 530-1450gph",
    )


def test_numeric_conflict_allows_shared_size():
    assert not _has_numeric_conflict(
        "Aquatop Sponge Filter for up to 25 Gallons",
        "Aquatop CAF-25 Internal Sponge Filter",
    )


def test_linked_supplier_item_blocks_stale_fuzzy_size_match():
    line = RawInvoiceLine(
        supplier_code="SC01090",
        description="Seachem Matrix Carbon 4L",
        barcode=None,
        quantity=1,
        unit_cost=1,
    )

    assert not _linked_supplier_item_is_safe(
        line,
        {"name": "Seachem Matrix Carbon 500mL", "sku": "116010306"},
    )


def test_linked_supplier_item_allows_identifier_supported_match():
    line = RawInvoiceLine(
        supplier_code="AT01251",
        description="Aquatop Sponge Filter for up to 25 Gallons",
        barcode=None,
        quantity=1,
        unit_cost=1,
    )

    assert _linked_supplier_item_is_safe(
        line,
        {
            "name": "Aquatop Classic Aqua Flow CAF-25 Internal Sponge Filter",
            "sku": "810281012513",
        },
    )
