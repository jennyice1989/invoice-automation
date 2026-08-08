"""Market retail price providers.

The app uses this module to fetch structured shopping offers from a pricing
API. Direct retailer scraping remains a fallback in app.pricing; this module is
for providers that return normalized result data.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
SERPAPI_TIMEOUT = 12.0
SERPAPI_API_KEY = os.environ.get("SERPAPI_API_KEY")
PRICING_PROVIDER = os.environ.get("PRICING_PROVIDER", "").strip().lower()

MARKETPLACE_SOURCES = {
    "amazon",
    "amazon.com",
    "ebay",
    "ebay.com",
    "walmart",
    "walmart.com",
    "google shopping",
    "shopping.google.com",
}
SALE_MARKERS = {"sale", "clearance", "coupon", "promo", "discount"}


@dataclass(frozen=True)
class RetailOffer:
    seller: str
    title: str
    price: float
    url: str | None = None
    position: int | None = None


@dataclass(frozen=True)
class MarketPriceResult:
    provider: str
    query: str
    offers: list[RetailOffer]
    raw_count: int = 0


def configured_provider() -> str | None:
    if PRICING_PROVIDER:
        return PRICING_PROVIDER
    if SERPAPI_API_KEY:
        return "serpapi"
    return None


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_marketplace(source: str | None) -> bool:
    normalized = _norm(source)
    return normalized in MARKETPLACE_SOURCES


def _has_sale_marker(result: dict[str, Any]) -> bool:
    if result.get("old_price") or result.get("extracted_old_price"):
        return True
    values: list[Any] = [
        result.get("tag"),
        result.get("badge"),
        result.get("snippet"),
    ]
    extensions = result.get("extensions")
    if isinstance(extensions, list):
        values.extend(extensions)
    text = " ".join(_norm(v) for v in values)
    return any(marker in text for marker in SALE_MARKERS)


def _offer_from_serpapi_result(result: dict[str, Any]) -> RetailOffer | None:
    price = result.get("extracted_price")
    if not isinstance(price, (int, float)) or price <= 0:
        return None
    if result.get("second_hand_condition"):
        return None

    seller = str(result.get("source") or "").strip()
    title = str(result.get("title") or "").strip()
    if not seller or not title:
        return None
    if _is_marketplace(seller) or _has_sale_marker(result):
        return None

    return RetailOffer(
        seller=seller,
        title=title,
        price=round(float(price), 2),
        url=result.get("product_link") or result.get("link"),
        position=result.get("position") if isinstance(result.get("position"), int) else None,
    )


def _dedupe_offers(offers: list[RetailOffer], *, limit: int = 8) -> list[RetailOffer]:
    seen: set[tuple[str, float]] = set()
    deduped: list[RetailOffer] = []
    for offer in sorted(offers, key=lambda o: o.position or 9999):
        key = (_norm(offer.seller), offer.price)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(offer)
        if len(deduped) >= limit:
            break
    return deduped


async def fetch_serpapi_market_prices(
    query: str,
    *,
    api_key: str | None = None,
    location: str | None = "United States",
) -> MarketPriceResult:
    """Fetch normalized Google Shopping prices from SerpApi."""
    key = api_key or SERPAPI_API_KEY
    if not key:
        return MarketPriceResult(provider="serpapi", query=query, offers=[], raw_count=0)

    params = {
        "engine": "google_shopping",
        "q": query,
        "api_key": key,
        "gl": "us",
        "hl": "en",
    }
    if location:
        params["location"] = location

    try:
        async with httpx.AsyncClient(timeout=SERPAPI_TIMEOUT) as client:
            resp = await client.get(SERPAPI_ENDPOINT, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.info("SerpApi price lookup failed for %r: %s", query, exc)
        return MarketPriceResult(provider="serpapi", query=query, offers=[], raw_count=0)

    raw_results = data.get("shopping_results") or []
    if not isinstance(raw_results, list):
        raw_results = []
    offers = [
        offer for item in raw_results
        if isinstance(item, dict)
        for offer in [_offer_from_serpapi_result(item)]
        if offer is not None
    ]
    return MarketPriceResult(
        provider="serpapi",
        query=query,
        offers=_dedupe_offers(offers),
        raw_count=len(raw_results),
    )


async def fetch_market_prices(query: str) -> MarketPriceResult:
    provider = configured_provider()
    if provider == "serpapi":
        return await fetch_serpapi_market_prices(query)
    return MarketPriceResult(provider=provider or "none", query=query, offers=[], raw_count=0)
