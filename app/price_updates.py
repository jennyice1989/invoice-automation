from __future__ import annotations


def float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def retail_update_decision(existing_price, suggested_price) -> tuple[bool, str]:
    """Only allow automatic retail updates that raise the current price."""
    suggested = float_or_none(suggested_price)
    existing = float_or_none(existing_price)
    if suggested is None:
        return False, "no suggested retail price"
    if existing is None:
        return True, "no existing retail price"
    if existing < suggested:
        return True, "existing retail price is lower than suggested"
    return False, "existing retail price is already equal or higher"
