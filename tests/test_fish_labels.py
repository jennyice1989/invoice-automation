from __future__ import annotations

import io

import pytest
from pypdf import PdfReader

from app.fish_labels import (
    FishLabel,
    LABEL_HEIGHT,
    LABEL_WIDTH,
    generate_fish_label_pdf,
    label_price,
    printable_fish_name,
    regular_retail_price,
)
from app.main import (
    FishLabelItemIn,
    FishLabelPreviewRequest,
    _review_fish_label_items,
    search_fish_labels,
)
from app.lightspeed import LightspeedError


def test_printable_fish_name_removes_sizes_but_keeps_sex_and_variety():
    assert printable_fish_name("Fancy Guppy Male 1.5 inch Medium") == "Fancy Guppy Male"
    assert printable_fish_name("Captive Bred Yellow Tang Large") == "Captive Bred Yellow Tang"
    assert printable_fish_name("Betta Female - Small") == "Betta Female"


def test_regular_retail_price_uses_tax_exclusive_product_price_not_supply_cost():
    product = {
        "price_excluding_tax": "39.99",
        "supply_price": "12.00",
        "price_including_tax": "43.50",
    }
    assert regular_retail_price(product) == 39.99
    assert regular_retail_price({"prices": {"price_excluding_tax": "14.49"}}) == 14.49
    assert regular_retail_price({"price_excluding_tax": "0"}) is None


def test_label_price_preserves_higher_existing_price_and_allows_lower_override():
    assert label_price(19.99, 14.99, None) == (19.99, "higher_of_live_and_recommended")
    assert label_price(19.99, 24.99, None) == (24.99, "higher_of_live_and_recommended")
    assert label_price(19.99, 24.99, 15.99) == (15.99, "manual_override")


def test_pdf_dimensions_label_count_and_prices():
    pdf = generate_fish_label_pdf([
        FishLabel("Very Long Blue Mosaic Guppy Male 1.5 inch", 39.99, quantity=2),
        FishLabel("Yellow Tang Medium", 149.49, quantity=1),
    ])
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 3
    first = reader.pages[0].mediabox
    assert float(first.width) == LABEL_WIDTH
    assert float(first.height) == LABEL_HEIGHT
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "$39.99" in text
    assert "$149.49" in text
    assert "inch" not in text.lower()


@pytest.mark.asyncio
async def test_search_returns_ambiguous_matches_for_user_selection(monkeypatch):
    class FakeClient:
        async def search_products(self, q, limit=20):
            assert q == "guppy male"
            return [
                {"id": "g1", "name": "Blue Mosaic Guppy Male", "variant_name": "Small", "sku": "GUP-BL-M-S", "price_excluding_tax": "9.99"},
                {"id": "g2", "name": "Blue Mosaic Guppy Male", "variant_name": "Large", "sku": "GUP-BL-M-L", "price_excluding_tax": "12.99"},
            ]

    monkeypatch.setattr("app.main._client", lambda: FakeClient())
    result = await search_fish_labels("guppy male")
    assert [row["id"] for row in result["data"]] == ["g1", "g2"]
    assert result["data"][0]["regular_retail_price"] == 9.99
    assert result["data"][0]["variant"] == "Small"


@pytest.mark.asyncio
async def test_preview_rechecks_price_and_reports_changes(monkeypatch):
    class FakeClient:
        async def get_product(self, product_id):
            assert product_id == "fish-1"
            return {
                "id": "fish-1",
                "name": "Halfmoon Betta Male Medium",
                "sku": "BETTA-M",
                "price_excluding_tax": "24.99",
            }

    monkeypatch.setattr("app.main._client", lambda: FakeClient())
    result = await _review_fish_label_items(FishLabelPreviewRequest(items=[
        FishLabelItemIn(
            product_id="fish-1",
            selected_live_price=19.99,
            selected_fetched_at="2026-10-06T12:00:00Z",
            quantity=1,
        )
    ]))
    assert result["changes"] == [{
        "index": 0,
        "product_id": "fish-1",
        "display_name": "Halfmoon Betta Male",
        "old_price": 19.99,
        "new_price": 24.99,
    }]
    assert result["items"][0]["final_price"] == 24.99
    assert result["items"][0]["price_changed"] is True


@pytest.mark.asyncio
async def test_preview_flags_lower_manual_override_without_blocking(monkeypatch):
    class FakeClient:
        async def get_product(self, product_id):
            return {
                "id": product_id,
                "name": "Koi Guppy Female Small",
                "price_excluding_tax": "12.99",
            }

    monkeypatch.setattr("app.main._client", lambda: FakeClient())
    result = await _review_fish_label_items(FishLabelPreviewRequest(items=[
        FishLabelItemIn(product_id="fish-2", selected_live_price=12.99, manual_price=9.99)
    ]))
    assert result["items"][0]["final_price"] == 9.99
    assert result["items"][0]["override_below_live"] is True
    assert result["labels"][0].price == 9.99


@pytest.mark.asyncio
async def test_preview_reports_missing_manual_price_and_api_failures(monkeypatch):
    class FakeClient:
        async def get_product(self, product_id):
            raise LightspeedError("timeout")

    monkeypatch.setattr("app.main._client", lambda: FakeClient())
    result = await _review_fish_label_items(FishLabelPreviewRequest(items=[
        FishLabelItemIn(display_name="Manual Shrimp"),
        FishLabelItemIn(product_id="fish-3", selected_live_price=10.00),
    ]))
    assert "Enter a price" in result["items"][0]["errors"][0]
    assert "Lightspeed price check failed" in result["items"][1]["error"]
