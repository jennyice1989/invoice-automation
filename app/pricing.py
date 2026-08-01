"""
Pricing engine.

Strategy: gather pricing signals and recommend the highest safe value.
  1. Rules engine: cost * markup, rounded
  2. MSRP from supplier price list (if uploaded)
  3. First-party retailer comparison from Chewy/Petco/PetSmart (DISABLED by
     default because these sites block cloud IPs aggressively. Set
     ENABLE_SCRAPING=1 to try.)

Returns the price and a source tag so the UI can show how it was derived
and let the user override.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import urllib.parse
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import PricingRule, find_msrp

logger = logging.getLogger(__name__)

ENABLE_SCRAPING = os.environ.get("ENABLE_SCRAPING", "").lower() in ("1", "true", "yes")


@dataclass
class PricingResult:
    price: float | None
    source: str  # 'msrp' | 'scrape:*' | 'rule' | 'none'
    rule_name: str | None = None
    notes: str | None = None
    scraped_data: dict | None = None
    msrp: float | None = None
    target_price: float | None = None
    current_retail_price: float | None = None


# --------------------------------------------------------------------- #
# Rules engine                                                          #
# --------------------------------------------------------------------- #

def _round(price: float, mode: str) -> float:
    if price <= 0:
        return price
    if mode == "none":
        return round(price, 2)
    if mode == "cents_99":
        return math.floor(price) + 0.99
    if mode == "charm":
        return _round_49_99(price)
    if mode in ("cents_49_99", "nearest_49_99"):
        return _round_49_99(price)
    return round(price, 2)


def _round_49_99(price: float) -> float:
    """Round up to the next .49 or .99 ending."""
    dollars = math.floor(price)
    cents = round(price - dollars, 2)
    if cents <= 0.49:
        return round(dollars + 0.49, 2)
    if cents <= 0.99:
        return round(dollars + 0.99, 2)
    return round(dollars + 1.49, 2)


async def _apply_rules(
    session: AsyncSession,
    cost: float,
    description: str | None,
) -> PricingResult:
    rules = (await session.execute(
        select(PricingRule).where(PricingRule.enabled == True)
        .order_by(PricingRule.priority.asc())
    )).scalars().all()

    desc = (description or "").lower()
    for rule in rules:
        if rule.keywords:
            tokens = [t.strip().lower() for t in rule.keywords.split(",") if t.strip()]
            if not any(tok in desc for tok in tokens):
                continue
        price = cost * rule.multiplier
        price = _round(price, rule.rounding)
        return PricingResult(
            price=price, source="rule", rule_name=rule.name, target_price=price,
            notes=f"{rule.multiplier}x cost, rounded ({rule.rounding})",
        )
    return PricingResult(price=None, source="none")


# --------------------------------------------------------------------- #
# Scraping                                                              #
# --------------------------------------------------------------------- #
#
# Honest disclaimer: this is best-effort. Chewy/Petco/PetSmart use
# Cloudflare and will often block requests from cloud IPs. When that
# happens we return None and the pricing falls back to rules. No retries
# with exotic headers — that's a rabbit hole. If reliable retail-price
# data is essential, plug in a third-party service in fetch_retail_price.

SCRAPE_TIMEOUT = 8.0
SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/16.5 Safari/605.1.15"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


async def _scrape_chewy(client: httpx.AsyncClient, query: str) -> float | None:
    """Search Chewy and extract first non-sale price."""
    try:
        url = f"https://www.chewy.com/s?query={urllib.parse.quote(query)}"
        resp = await client.get(url, headers=SCRAPE_HEADERS, timeout=SCRAPE_TIMEOUT)
        if resp.status_code != 200:
            return None
        html = resp.text
        # Chewy embeds prices in <span data-testid="price">$12.34</span>
        # and strikethrough sale prices nearby. Look for first regular price.
        m = re.search(r'data-testid="price"[^>]*>\s*\$([\d,]+\.\d{2})', html)
        if m:
            return float(m.group(1).replace(",", ""))
    except Exception as exc:
        logger.debug("Chewy scrape failed: %s", exc)
    return None


async def _scrape_petco(client: httpx.AsyncClient, query: str) -> float | None:
    try:
        url = f"https://www.petco.com/shop/PetcoSearchCmd?searchKeyword={urllib.parse.quote(query)}"
        resp = await client.get(url, headers=SCRAPE_HEADERS, timeout=SCRAPE_TIMEOUT,
                                 follow_redirects=True)
        if resp.status_code != 200:
            return None
        html = resp.text
        # Petco product cards have prices in formats like:
        #   "price":"19.99"  or  $19.99 in nearby markup.
        m = re.search(r'"price":\s*"?([\d.]+)"?', html)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    except Exception as exc:
        logger.debug("Petco scrape failed: %s", exc)
    return None


async def _scrape_petsmart(client: httpx.AsyncClient, query: str) -> float | None:
    try:
        url = f"https://www.petsmart.com/search/?q={urllib.parse.quote(query)}"
        resp = await client.get(url, headers=SCRAPE_HEADERS, timeout=SCRAPE_TIMEOUT,
                                 follow_redirects=True)
        if resp.status_code != 200:
            return None
        html = resp.text
        m = re.search(r'"price":\s*"?([\d.]+)"?', html)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    except Exception as exc:
        logger.debug("PetSmart scrape failed: %s", exc)
    return None


async def _try_scrape(query: str) -> tuple[str | None, float | None, dict]:
    """Try all three retailers in parallel. Return first hit and full data."""
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            _scrape_chewy(client, query),
            _scrape_petco(client, query),
            _scrape_petsmart(client, query),
            return_exceptions=True,
        )
    data = {
        "chewy": results[0] if not isinstance(results[0], Exception) else None,
        "petco": results[1] if not isinstance(results[1], Exception) else None,
        "petsmart": results[2] if not isinstance(results[2], Exception) else None,
    }
    for name, price in data.items():
        if isinstance(price, (int, float)) and price > 0:
            return f"scrape:{name}", float(price), data
    return None, None, data


def _competitor_alignment_price(prices: list[float]) -> float | None:
    """Use the median first-party retail price as the market alignment point."""
    valid = sorted(float(p) for p in prices if isinstance(p, (int, float)) and p > 0)
    if not valid:
        return None
    mid = len(valid) // 2
    if len(valid) % 2:
        return round(valid[mid], 2)
    return round((valid[mid - 1] + valid[mid]) / 2, 2)


# --------------------------------------------------------------------- #
# Public                                                                #
# --------------------------------------------------------------------- #

async def price_line(
    session: AsyncSession,
    *,
    supplier_id: str | None,
    supplier_code: str | None,
    barcode: str | None,
    description: str | None,
    cost: float,
    current_retail_price: float | None = None,
    try_scrape: bool = True,
) -> PricingResult:
    """Resolve a retail price for one invoice line.

    Returns the best available price plus its source. Always returns a
    PricingResult; price may be None if nothing matched.
    """
    # 1. Target margin rule. This is the recommendation baseline.
    rule_result = await _apply_rules(session, cost, description)
    target_price = rule_result.price
    notes = [rule_result.notes] if rule_result.notes else []

    # 2. MSRP from uploaded price list. MSRP is a comparison point, not an
    # automatic override, so the user can approve the final recommendation.
    msrp_value: float | None = None
    if supplier_id:
        msrp = await find_msrp(
            session, supplier_id=supplier_id,
            supplier_code=supplier_code, barcode=barcode,
        )
        if msrp:
            msrp_value = msrp.msrp
            notes.append(f"MSRP ${msrp.msrp:.2f}" + (f": {msrp.notes}" if msrp.notes else ""))

    # 3. Retail competitor review — only first-party retailer pages, not
    # marketplaces. These are alignment signals; they do not force a lower
    # price.
    scraped_data: dict | None = None
    competitor_prices: list[float] = []
    competitor_price: float | None = None
    if try_scrape and ENABLE_SCRAPING:
        scrape_queries = []
        for query in (barcode, description):
            query = (query or "").strip()
            if query and query not in scrape_queries:
                scrape_queries.append(query)

        for query in scrape_queries:
            source, price, data = await _try_scrape(query)
            scraped_data = {"query": query, "prices": data}
            if source and price:
                competitor_prices = [
                    float(v) for v in data.values() if isinstance(v, (int, float)) and v > 0
                ]
                competitor_price = _competitor_alignment_price(competitor_prices)
                low = min(competitor_prices)
                high = max(competitor_prices)
                notes.append(
                    f"Retailer comparison ${low:.2f}-${high:.2f}; "
                    f"market-aligned ${competitor_price:.2f}; marketplace sellers excluded"
                )
                break

    candidates = [
        p for p in (target_price, msrp_value, competitor_price) if p is not None and p > 0
    ]
    recommended = max(candidates) if candidates else None

    if current_retail_price is not None and recommended is not None:
        if recommended < current_retail_price:
            notes.append(
                f"Recommendation held at current retail ${current_retail_price:.2f}; app will not recommend a lower price"
            )
            recommended = current_retail_price

    if recommended is not None:
        if competitor_price is not None and recommended == competitor_price:
            source = "scrape:retailer-comparison"
        elif msrp_value is not None and recommended == msrp_value:
            source = "msrp"
        else:
            source = rule_result.source
        return PricingResult(
            price=recommended,
            source=source or "rule",
            rule_name=rule_result.rule_name,
            notes="; ".join(notes) if notes else None,
            scraped_data=scraped_data,
            msrp=msrp_value,
            target_price=target_price,
            current_retail_price=current_retail_price,
        )

    # Nothing worked.
    return PricingResult(price=None, source="none", notes="No pricing source available")
