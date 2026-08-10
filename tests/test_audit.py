from __future__ import annotations

from app.audit import (
    audit_item_matches_filter,
    audit_product,
    custom_sku_for_product,
    is_generated_sku,
    sku_looks_like_barcode,
    target_price_for_cost,
)
from app.db import CatalogProduct


def _product(**kwargs) -> CatalogProduct:
    defaults = {
        "lightspeed_product_id": "prod-1",
        "name": "Test Product",
        "sku": "SKU1",
        "barcode": "123",
        "supplier_code": "SUP1",
        "brand_name": "Brand",
        "category_name": "Category",
        "supply_price": 10.00,
        "retail_price": 12.99,
        "has_inventory": True,
        "active": True,
        "raw": {},
    }
    defaults.update(kwargs)
    return CatalogProduct(**defaults)


def test_target_price_uses_1_5x_and_49_99_rounding():
    assert target_price_for_cost(10.00) == 15.49
    assert target_price_for_cost(10.50) == 15.99


def test_audit_flags_missing_content_and_price_below_target():
    result = audit_product(_product(raw={}, retail_price=12.99))
    codes = {issue["code"] for issue in result["issues"]}

    assert "missing_description" in codes
    assert "missing_photo" in codes
    assert "below_target_margin" in codes
    assert result["target_price"] == 15.49


def test_audit_flags_inventory_tracking_off():
    result = audit_product(_product(has_inventory=False, retail_price=19.99))
    codes = {issue["code"] for issue in result["issues"]}

    assert "inventory_tracking_off" in codes
    assert result["has_inventory"] is False
    assert audit_item_matches_filter(result, issue="inventory_tracking_off")


def test_audit_accepts_description_and_image_fields():
    result = audit_product(_product(
        retail_price=19.99,
        raw={
            "description": (
                "<p>This is a complete product description with enough useful "
                "detail for a product page. It explains what the item is, "
                "who it is for, and why a customer would choose it.</p>"
            ),
            "images": [{"url": "https://example.test/image.jpg"}],
        },
    ))
    codes = {issue["code"] for issue in result["issues"]}

    assert "missing_description" not in codes
    assert "weak_description" not in codes
    assert "missing_photo" not in codes
    assert "below_target_margin" not in codes


def test_audit_flags_empty_image_placeholders_as_missing_photo():
    result = audit_product(_product(
        retail_price=19.99,
        raw={
            "description": (
                "<p>This is a complete product description with enough useful "
                "detail for a product page. It explains what the item is, "
                "who it is for, and why a customer would choose it.</p>"
            ),
            "image": {"url": None, "thumbnail": ""},
            "images": [],
        },
    ))
    codes = {issue["code"] for issue in result["issues"]}

    assert result["has_image"] is False
    assert "missing_photo" in codes


def test_audit_flags_missing_sku_and_suggests_custom_sku():
    product = _product(lightspeed_product_id="6d3e8ae2-bbb8", sku=None)
    result = audit_product(product)
    codes = {issue["code"] for issue in result["issues"]}

    assert "missing_sku" in codes
    assert result["suggested_custom_sku"] == "CUSTOM-6D3E8AE2BB"
    assert custom_sku_for_product(product) == "CUSTOM-6D3E8AE2BB"


def test_audit_flags_missing_barcode_when_sku_exists():
    result = audit_product(_product(sku="SKU1", barcode=None))
    codes = {issue["code"] for issue in result["issues"]}

    assert "missing_barcode" in codes
    assert "missing_sku" not in codes
    assert "missing_barcode_sku" not in codes
    assert audit_item_matches_filter(result, issue="missing_barcode")


def test_audit_treats_upc_sku_as_barcode():
    result = audit_product(_product(sku="000116768702", barcode=None))
    codes = {issue["code"] for issue in result["issues"]}

    assert result["barcode"] == "000116768702"
    assert result["is_generated_sku"] is False
    assert "missing_barcode" not in codes
    assert "missing_barcode_sku" not in codes
    assert "generated_sku" not in codes


def test_audit_flags_short_numeric_sku_as_generated():
    result = audit_product(_product(sku="10558", barcode=None))
    codes = {issue["code"] for issue in result["issues"]}

    assert "generated_sku" in codes
    assert result["is_generated_sku"] is True
    assert audit_item_matches_filter(result, issue="generated_sku")


def test_sku_looks_like_barcode_for_real_barcode_lengths():
    assert sku_looks_like_barcode("000116768702")
    assert is_generated_sku("10558") is True
    assert is_generated_sku("000116768702") is False


def test_audit_item_matches_issue_and_search_filters():
    missing_photo = audit_product(_product(
        name="Aquatop Magnet Cleaner",
        raw={"description": "A detailed aquarium cleaning magnet description." * 4},
        retail_price=19.99,
    ))
    missing_description = audit_product(_product(
        name="Seachem Tube",
        raw={"images": [{"url": "https://example.test/image.jpg"}]},
        retail_price=19.99,
    ))

    assert audit_item_matches_filter(missing_photo, issue="missing_photo")
    assert not audit_item_matches_filter(missing_description, issue="missing_photo")
    assert audit_item_matches_filter(missing_photo, query="aquatop")
    assert not audit_item_matches_filter(missing_photo, query="seachem")
