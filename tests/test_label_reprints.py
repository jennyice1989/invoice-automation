from __future__ import annotations

from app.db import CatalogProduct
from app.main import _is_generated_sku, _label_reprint_from_price_change


def test_label_reprint_is_created_for_changed_price():
    product = CatalogProduct(
        lightspeed_product_id="prod-1",
        name="Test Product",
        sku="SKU1",
        barcode="123",
        supplier_code="SUP1",
        retail_price=12.99,
    )

    row = _label_reprint_from_price_change(
        product,
        product_id="prod-1",
        old_price=12.99,
        new_price=15.49,
    )

    assert row is not None
    assert row.product_name == "Test Product"
    assert row.old_price == 12.99
    assert row.new_price == 15.49
    assert row.status is None or row.status == "pending"


def test_label_reprint_is_skipped_for_same_price():
    product = CatalogProduct(lightspeed_product_id="prod-1", name="Test Product")

    row = _label_reprint_from_price_change(
        product,
        product_id="prod-1",
        old_price=12.99,
        new_price=12.99,
    )

    assert row is None


def test_is_generated_sku_detects_custom_prefix_only():
    assert _is_generated_sku("CUSTOM-ABC123")
    assert _is_generated_sku("custom-abc123")
    assert _is_generated_sku("10558")
    assert not _is_generated_sku("000116768702")
    assert not _is_generated_sku(None)
