from __future__ import annotations

import pytest

from app import pricing
from app.pricing import PricingResult


def test_round_49_99_rounds_up_to_allowed_endings():
    assert pricing._round(10.01, "cents_49_99") == 10.49
    assert pricing._round(10.50, "cents_49_99") == 10.99
    assert pricing._round(10.99, "cents_49_99") == 10.99


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
