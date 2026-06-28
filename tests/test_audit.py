from __future__ import annotations

from app.audit import audit_product, target_price_for_cost
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
