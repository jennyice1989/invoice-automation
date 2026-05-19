from __future__ import annotations

from app.price_updates import retail_update_decision


def test_retail_update_decision_raises_lower_existing_price():
    should_update, reason = retail_update_decision(9.99, 12.99)

    assert should_update is True
    assert "lower" in reason


def test_retail_update_decision_skips_equal_or_higher_existing_price():
    should_update, reason = retail_update_decision(14.99, 12.99)

    assert should_update is False
    assert "equal or higher" in reason


def test_retail_update_decision_updates_when_existing_price_missing():
    should_update, reason = retail_update_decision(None, 12.99)

    assert should_update is True
    assert "no existing" in reason


def test_retail_update_decision_skips_when_suggestion_missing():
    should_update, reason = retail_update_decision(12.99, None)

    assert should_update is False
    assert "no suggested" in reason
