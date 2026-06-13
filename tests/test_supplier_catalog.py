from __future__ import annotations

from app.supplier_catalog import (
    parse_central_catalog_text,
    parse_reefh2o_catalog_text,
)


def test_parse_central_catalog_text_extracts_product_facts():
    text = """
Product #: 73802520
Mfg Part #: 2520
UPC: 842982025202
In Stock
Sell Pk: 1 | Case Qty: 24 |
Pallet Qty: 1200
Acurel Cut to Fit
Infused Media PadNitrate Reducing, Grey,
1ea/18 In X 10 in
Your Price: $7.76
List Price $8.17
"""

    items = parse_central_catalog_text(text, source="Central Aquatics.pdf")

    assert len(items) == 1
    item = items[0]
    assert item.supplier_code == "73802520"
    assert item.mfg_part == "2520"
    assert item.barcode == "842982025202"
    assert item.unit_cost == 7.76
    assert item.list_price == 8.17
    assert "Acurel Cut to Fit" in item.name
    assert item.facts["case_qty"] == "24"


def test_parse_reefh2o_catalog_text_extracts_product_facts():
    text = """
Home > Food
Aquatop Coral & Fish Target Feeder - 20.5"
Product Code: AT75122
UPC - 810146751229
List Price: $5.35
23 In Stock
ADD TO CART
Bay Brand Betta Food 1g Vial (Bloodworms)
Product Code: BAY71401
UPC - 000945714017
List Price: $1.75
69 In Stock
ADD TO CART
"""

    items = parse_reefh2o_catalog_text(text, source="Food_page1.pdf")

    assert len(items) == 2
    assert items[0].supplier_code == "AT75122"
    assert items[0].barcode == "810146751229"
    assert items[0].list_price == 5.35
    assert items[0].name == 'Aquatop Coral & Fish Target Feeder - 20.5"'
    assert items[1].supplier_code == "BAY71401"
    assert items[1].facts["stock"] == "69 In Stock"
