"""
Pricing engine.

Strategy: try sources in order, first non-null wins.
  1. MSRP from supplier price list (if uploaded)
  2. Web scrape Chewy/Petco/PetSmart non-sale price (best effort)
  3. Rules engine: cost * markup, rounded

Returns the price and a source tag so the UI can show how it was derived
and let the user override.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import urllib.parse
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import PricingRule, find_msrp

logger = logging.getLogger(__name__)


@dataclass
class PricingResult:
    price: float | None
    source: str  # 'msrp' | 'scrape:chewy' | 'scrape:petco' | 'scrape:petsmart' | 'rule' | 'none'
    rule_name: str | None = None
    notes: str | None = None
    scraped_data: dict | None = None


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
        # .99 for <$10, .99 for $10–$50, .99 for higher too.
        # (Pet retail almost always uses .99 endings regardless of band.)
        return math.floor(price) + 0.99
    return round(price, 2)


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
            price=price, source="rule", rule_name=rule.name,
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
    try_scrape: bool = True,
) -> PricingResult:
    """Resolve a retail price for one invoice line.

    Returns the best available price plus its source. Always returns a
    PricingResult; price may be None if nothing matched.
    """
    # 1. MSRP from uploaded price list
    if supplier_id:
        msrp = await find_msrp(
            session, supplier_id=supplier_id,
            supplier_code=supplier_code, barcode=barcode,
        )
        if msrp:
            return PricingResult(
                price=msrp.msrp, source="msrp",
                notes=msrp.notes or "From uploaded MSRP list",
            )

    # 2. Scrape (best effort)
    if try_scrape and description:
        source, price, data = await _try_scrape(description)
        if source and price:
            return PricingResult(
                price=price, source=source, scraped_data=data,
                notes="Scraped from public listing",
            )

    # 3. Rules engine
    rule_result = await _apply_rules(session, cost, description)
    if rule_result.price is not None:
        return rule_result

    # Nothing worked.
    return PricingResult(price=None, source="none", notes="No pricing source available")
