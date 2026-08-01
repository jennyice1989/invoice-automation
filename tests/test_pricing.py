from __future__ import annotations

import pytest

from app import pricing
from app.pricing import PricingResult


def test_round_49_99_rounds_up_to_allowed_endings():
    assert pricing._round(10.01, "cents_49_99") == 10.49
    assert pricing._round(10.50, "cents_49_99") == 10.99
    assert pricing._round(10.99, "cents_49_99") == 10.99


def test_competitor_alignment_uses_median_price():
    assert pricing._competitor_alignment_price([24.99, 19.99, 21.99]) == 21.99
    assert pricing._competitor_alignment_price([19.99, 21.99]) == 20.99
    assert pricing._competitor_alignment_price([]) is None


@pytest.mark.asyncio
async def test_price_line_does_not_recommend_lower_than_current_retail(monkeypatch):
    async def fake_apply_rules(session, cost, description):
        return PricingResult(
            price=10.49,
            source="rule",
            rule_name="Default target margin",
            notes="1.5x cost, rounded (cents_49_99)",
            target_price=10.49,
        )

    async def fake_find_msrp(*args, **kwargs):
        return None

    monkeypatch.setattr(pricing, "_apply_rules", fake_apply_rules)
    monkeypatch.setattr(pricing, "find_msrp", fake_find_msrp)

    result = await pricing.price_line(
        None,
        supplier_id="sup-1",
        supplier_code="abc",
        barcode=None,
        description="Test product",
        cost=6.99,
        current_retail_price=12.99,
        try_scrape=False,
    )

    assert result.price == 12.99
    assert "will not recommend a lower price" in (result.notes or "")


@pytest.mark.asyncio
async def test_price_line_uses_competitor_alignment_when_higher(monkeypatch):
    async def fake_apply_rules(session, cost, description):
        return PricingResult(
            price=16.99,
            source="rule",
            rule_name="Default target margin",
            notes="1.5x cost, rounded (cents_49_99)",
            target_price=16.99,
        )

    async def fake_find_msrp(*args, **kwargs):
        return None

    async def fake_try_scrape(query):
        return "scrape:chewy", 24.99, {
            "chewy": 24.99,
            "petco": 21.99,
            "petsmart": 19.99,
        }

    monkeypatch.setattr(pricing, "_apply_rules", fake_apply_rules)
    monkeypatch.setattr(pricing, "find_msrp", fake_find_msrp)
    monkeypatch.setattr(pricing, "_try_scrape", fake_try_scrape)
    monkeypatch.setattr(pricing, "ENABLE_SCRAPING", True)

    result = await pricing.price_line(
        None,
        supplier_id="sup-1",
        supplier_code="abc",
        barcode=None,
        description="Test product",
        cost=10.00,
        current_retail_price=None,
        try_scrape=True,
    )

    assert result.price == 21.99
    assert result.source == "scrape:retailer-comparison"
    assert "market-aligned $21.99" in (result.notes or "")


@pytest.mark.asyncio
async def test_price_line_keeps_margin_when_competitors_are_lower(monkeypatch):
    async def fake_apply_rules(session, cost, description):
        return PricingResult(
            price=29.99,
            source="rule",
            rule_name="Default target margin",
            notes="1.5x cost, rounded (cents_49_99)",
            target_price=29.99,
        )

    async def fake_find_msrp(*args, **kwargs):
        return None

    async def fake_try_scrape(query):
        return "scrape:chewy", 19.99, {
            "chewy": 19.99,
            "petco": 21.99,
            "petsmart": 17.99,
        }

    monkeypatch.setattr(pricing, "_apply_rules", fake_apply_rules)
    monkeypatch.setattr(pricing, "find_msrp", fake_find_msrp)
    monkeypatch.setattr(pricing, "_try_scrape", fake_try_scrape)
    monkeypatch.setattr(pricing, "ENABLE_SCRAPING", True)

    result = await pricing.price_line(
        None,
        supplier_id="sup-1",
        supplier_code="abc",
        barcode=None,
        description="Test product",
        cost=10.00,
        current_retail_price=None,
        try_scrape=True,
    )

    assert result.price == 29.99
    assert result.source == "rule"
    assert "market-aligned $19.99" in (result.notes or "")


@pytest.mark.asyncio
async def test_price_line_searches_barcode_before_description(monkeypatch):
    calls = []

    async def fake_apply_rules(session, cost, description):
        return PricingResult(
            price=16.99,
            source="rule",
            rule_name="Default target margin",
            notes="1.5x cost, rounded (cents_49_99)",
            target_price=16.99,
        )

    async def fake_find_msrp(*args, **kwargs):
        return None

    async def fake_try_scrape(query):
        calls.append(query)
        if query == "000116070782":
            return None, None, {"chewy": None, "petco": None, "petsmart": None}
        return "scrape:petco", 22.99, {
            "chewy": None,
            "petco": 22.99,
            "petsmart": None,
        }

    monkeypatch.setattr(pricing, "_apply_rules", fake_apply_rules)
    monkeypatch.setattr(pricing, "find_msrp", fake_find_msrp)
    monkeypatch.setattr(pricing, "_try_scrape", fake_try_scrape)
    monkeypatch.setattr(pricing, "ENABLE_SCRAPING", True)

    result = await pricing.price_line(
        None,
        supplier_id="sup-1",
        supplier_code="abc",
        barcode="000116070782",
        description="Seachem Prime 500ml",
        cost=10.00,
        current_retail_price=None,
        try_scrape=True,
    )

    assert calls == ["000116070782", "Seachem Prime 500ml"]
    assert result.price == 22.99
    assert result.scraped_data == {
        "query": "Seachem Prime 500ml",
        "prices": {"chewy": None, "petco": 22.99, "petsmart": None},
    }
