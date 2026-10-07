from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from reportlab.pdfgen import canvas

LABEL_WIDTH = 162
LABEL_HEIGHT = 90
SAFE_MARGIN = 8
NAME_TARGET_SIZE = 17
PRICE_TARGET_SIZE = 25


def money(value: float | Decimal | None) -> str:
    if value is None:
        return ""
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"${amount:.2f}"


def to_price(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if amount <= 0:
        return None
    return float(amount)


def regular_retail_price(product: dict | None) -> float | None:
    if not product:
        return None
    value = product.get("price_excluding_tax")
    if value is None and isinstance(product.get("prices"), dict):
        value = product["prices"].get("price_excluding_tax")
    return to_price(value)


def product_variant_text(product: dict) -> str | None:
    candidates = [
        product.get("variant_name"),
        product.get("variant_option_one_value"),
        product.get("variant_option_two_value"),
        product.get("variant_option_three_value"),
        product.get("size"),
    ]
    options = product.get("variant_options")
    if isinstance(options, list):
        for option in options:
            if isinstance(option, dict):
                candidates.append(option.get("value") or option.get("name"))
            elif option:
                candidates.append(str(option))
    text = " / ".join(str(c).strip() for c in candidates if str(c or "").strip())
    return text or None


_SIZE_PATTERNS = [
    r"\b\d+(?:\.\d+)?\s*(?:in|inch|inches|\"|cm|mm)\b",
    r"\b(?:x-?small|small|medium|large|x-?large|xl|xs|sm|md|lg)\b",
    r"\b(?:tiny|juvenile|subadult|adult)\b",
    r"\b(?:\d+\s*-\s*\d+|\d+\+?)\s*(?:ct|count)\b",
    r"\b(?:assorted\s+)?(?:size|sized)\b",
]


def printable_fish_name(name: str | None) -> str:
    text = " ".join(str(name or "").replace("_", " ").split())
    text = re.sub(r"\([^)]*(?:inch|inches|cm|mm|small|medium|large|size)[^)]*\)", "", text, flags=re.I)
    text = re.sub(r"\[[^]]*(?:inch|inches|cm|mm|small|medium|large|size)[^]]*\]", "", text, flags=re.I)
    for pattern in _SIZE_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.I)
    text = re.sub(r"\s*[-/]\s*$", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" -/")
    return text or str(name or "").strip()


def label_price(live_price: float | None, recommended_price: float | None, manual_price: float | None) -> tuple[float | None, str]:
    if manual_price is not None:
        return manual_price, "manual_override"
    candidates = [p for p in (live_price, recommended_price) if p is not None]
    if not candidates:
        return None, "missing"
    return max(candidates), "higher_of_live_and_recommended" if recommended_price is not None else "live"


def median_market_price(prices: list[float]) -> float | None:
    valid = sorted(float(p) for p in prices if isinstance(p, (int, float)) and p > 0)
    if not valid:
        return None
    mid = len(valid) // 2
    if len(valid) % 2:
        return round(valid[mid], 2)
    return round((valid[mid - 1] + valid[mid]) / 2, 2)


def market_alignment_status(
    store_price: float | None,
    market_price: float | None,
    *,
    tolerance: float = 0.10,
) -> str:
    if store_price is None or store_price <= 0:
        return "missing_store_price"
    if market_price is None or market_price <= 0:
        return "no_market_data"
    low = market_price * (1 - tolerance)
    high = market_price * (1 + tolerance)
    if store_price < low:
        return "below_market"
    if store_price > high:
        return "above_market"
    return "aligned"


@dataclass
class FishLabel:
    name: str
    price: float
    quantity: int = 1
    unverified: bool = False
    source: str = "lightspeed"


def split_name_lines(c: canvas.Canvas, name: str, font_name: str, font_size: float, max_width: float) -> list[str]:
    words = name.split()
    if len(words) <= 1:
        return [name]
    best: tuple[float, list[str]] | None = None
    for i in range(1, len(words)):
        lines = [" ".join(words[:i]), " ".join(words[i:])]
        widest = max(c.stringWidth(line, font_name, font_size) for line in lines)
        balance = abs(len(lines[0]) - len(lines[1]))
        overflow = max(0, widest - max_width)
        score = overflow * 100 + balance
        if best is None or score < best[0]:
            best = (score, lines)
    return best[1] if best else [name]


def fit_name(c: canvas.Canvas, name: str) -> tuple[list[str], float]:
    font_name = "Helvetica-Bold"
    max_width = LABEL_WIDTH - SAFE_MARGIN * 2
    for size in range(NAME_TARGET_SIZE, 8, -1):
        lines = split_name_lines(c, name, font_name, size, max_width)
        if len(lines) <= 2 and all(c.stringWidth(line, font_name, size) <= max_width for line in lines):
            return lines, float(size)
    return split_name_lines(c, name, font_name, 8, max_width)[:2], 8.0


def draw_label(c: canvas.Canvas, label: FishLabel) -> None:
    name = printable_fish_name(label.name)
    lines, name_size = fit_name(c, name)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", name_size)
    line_height = name_size + 2
    start_y = LABEL_HEIGHT - 18
    if len(lines) == 1:
        start_y = LABEL_HEIGHT - 24
    for idx, line in enumerate(lines):
        c.drawCentredString(LABEL_WIDTH / 2, start_y - idx * line_height, line)

    c.setFont("Helvetica-Bold", PRICE_TARGET_SIZE)
    c.drawCentredString(LABEL_WIDTH / 2, 16, money(label.price))


def generate_fish_label_pdf(labels: list[FishLabel]) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(LABEL_WIDTH, LABEL_HEIGHT))
    expanded: list[FishLabel] = []
    for label in labels:
        qty = min(max(int(label.quantity or 1), 1), 50)
        expanded.extend([label] * qty)
    for idx, label in enumerate(expanded):
        if idx:
            c.showPage()
        draw_label(c, label)
    c.save()
    return buffer.getvalue()


def fetched_at_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
