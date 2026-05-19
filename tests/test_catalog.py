from __future__ import annotations

from datetime import datetime

from app.catalog import product_to_cache_fields, search_score


def test_product_to_cache_fields_normalizes_common_values():
    fields = product_to_cache_fields(
        {
            "id": "prod-1",
            "name": "Seachem Shrimp Accessories - Tube ASM7078",
            "sku": "000116070782",
            "barcode": ["000116070782"],
            "brand": {"name": "Seachem"},
            "supply_price": 3.05,
            "price_excluding_tax": 6.99,
        },
        datetime(2026, 1, 1),
    )

    assert fields["lightspeed_product_id"] == "prod-1"
    assert fields["normalized_name"] == "seachem shrimp accessories tube asm7078"
    assert fields["barcode"] == "000116070782"
    assert fields["brand_name"] == "Seachem"
    assert fields["active"] is True


def test_search_score_prefers_specific_match():
    query = "Seachem Aquavitro Shrimp Tube"
    good = {
        "name": "Seachem Shrimp Accessories - Tube ASM7078",
        "sku": "000116070782",
    }
    weak = {"name": "Seachem Curved Fine Tip Forceps", "sku": "000116768702"}

    assert search_score(query, good) > search_score(query, weak)
