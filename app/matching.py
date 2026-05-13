"""
Product matching service.

Takes raw invoice line items (supplier code + name + barcode if present)
and resolves them to Lightspeed product IDs using a tiered strategy:

  1. Saved mapping (this supplier has seen this code before)
  2. Exact SKU match in Lightspeed
  3. Barcode match in Lightspeed
  4. Fuzzy name match against products from this supplier
  5. Unresolved — goes to the human review queue

Tier 1 is what makes this work over time: every manual resolution saves
to the mappings table, so the same invoice next month is fully automatic.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import find_mapping
from app.lightspeed import LightspeedClient, LightspeedError

logger = logging.getLogger(__name__)


# Confidence threshold for fuzzy name match. Below this, we send it to
# review rather than auto-applying. 0.85 is empirically a reasonable
# starting point — tune based on how clean your invoices are.
FUZZY_MATCH_THRESHOLD = 0.85


@dataclass
class RawInvoiceLine:
    """An invoice line as extracted, before matching to Lightspeed."""

    supplier_code: str | None
    description: str | None
    barcode: str | None
    quantity: float
    unit_cost: float


@dataclass
class MatchedLine:
    """A successfully matched line, ready to send to import_invoice."""

    raw: RawInvoiceLine
    product_id: str
    product_sku: str | None
    product_name: str | None
    matched_by: str  # 'mapping' | 'sku' | 'barcode' | 'fuzzy_name'
    confidence: float  # 0.0–1.0; 1.0 for exact matches


@dataclass
class UnmatchedLine:
    """A line that couldn't be auto-resolved — needs human review.
    `candidates` are the top-3 fuzzy matches if any, so the UI can
    suggest options."""

    raw: RawInvoiceLine
    candidates: list[dict]
    reason: str


@dataclass
class MatchResult:
    matched: list[MatchedLine]
    unmatched: list[UnmatchedLine]


def _normalize(s: str | None) -> str:
    """Lowercase, strip, collapse whitespace, drop punctuation."""
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


class MatchingService:
    """Resolves invoice lines to Lightspeed products."""

    def __init__(self, lightspeed: LightspeedClient, session: AsyncSession):
        self.ls = lightspeed
        self.session = session

    async def match_invoice(
        self,
        supplier_id: str,
        lines: list[RawInvoiceLine],
    ) -> MatchResult:
        # Pull the supplier's product catalog once for fuzzy matching.
        # This is the expensive call — better to do it once and reuse.
        supplier_products = await self._load_supplier_products(supplier_id)

        matched: list[MatchedLine] = []
        unmatched: list[UnmatchedLine] = []

        for line in lines:
            result = await self._match_one(
                supplier_id, line, supplier_products
            )
            if isinstance(result, MatchedLine):
                matched.append(result)
            else:
                unmatched.append(result)

        return MatchResult(matched=matched, unmatched=unmatched)

    async def _match_one(
        self,
        supplier_id: str,
        line: RawInvoiceLine,
        supplier_products: list[dict],
    ) -> MatchedLine | UnmatchedLine:
        # Tier 1: saved mapping
        if line.supplier_code:
            mapping = await find_mapping(
                self.session,
                supplier_id=supplier_id,
                supplier_code=line.supplier_code,
            )
            if mapping:
                return MatchedLine(
                    raw=line,
                    product_id=mapping.lightspeed_product_id,
                    product_sku=mapping.lightspeed_sku,
                    product_name=mapping.product_name,
                    matched_by="mapping",
                    confidence=1.0,
                )

        # Tier 2: exact SKU match
        if line.supplier_code:
            try:
                product = await self.ls.find_product_by_sku(line.supplier_code)
            except LightspeedError as exc:
                logger.warning("SKU lookup failed: %s", exc)
                product = None
            if product:
                return MatchedLine(
                    raw=line,
                    product_id=product["id"],
                    product_sku=product.get("sku"),
                    product_name=product.get("name"),
                    matched_by="sku",
                    confidence=1.0,
                )

        # Tier 3: barcode match. We scan the supplier's product list
        # locally rather than hitting the API — much faster.
        if line.barcode:
            for p in supplier_products:
                # Lightspeed's barcode field is sometimes a list, sometimes
                # a string; handle both shapes defensively.
                pb = p.get("barcode") or ""
                if isinstance(pb, list):
                    if line.barcode in pb:
                        return MatchedLine(
                            raw=line,
                            product_id=p["id"],
                            product_sku=p.get("sku"),
                            product_name=p.get("name"),
                            matched_by="barcode",
                            confidence=1.0,
                        )
                elif pb == line.barcode:
                    return MatchedLine(
                        raw=line,
                        product_id=p["id"],
                        product_sku=p.get("sku"),
                        product_name=p.get("name"),
                        matched_by="barcode",
                        confidence=1.0,
                    )

        # Tier 4: fuzzy name match against supplier's catalog
        if line.description and supplier_products:
            scored = sorted(
                (
                    (_name_similarity(line.description, p.get("name", "")), p)
                    for p in supplier_products
                ),
                key=lambda x: x[0],
                reverse=True,
            )
            top_score, top_product = scored[0]
            if top_score >= FUZZY_MATCH_THRESHOLD:
                return MatchedLine(
                    raw=line,
                    product_id=top_product["id"],
                    product_sku=top_product.get("sku"),
                    product_name=top_product.get("name"),
                    matched_by="fuzzy_name",
                    confidence=top_score,
                )
            # Below threshold — surface top candidates for human review.
            candidates = [
                {
                    "product_id": p["id"],
                    "sku": p.get("sku"),
                    "name": p.get("name"),
                    "confidence": round(score, 3),
                }
                for score, p in scored[:3]
            ]
            return UnmatchedLine(
                raw=line,
                candidates=candidates,
                reason="No exact match; top fuzzy match below threshold",
            )

        return UnmatchedLine(
            raw=line,
            candidates=[],
            reason="No supplier_code, barcode, or description to match on",
        )

    async def _load_supplier_products(self, supplier_id: str) -> list[dict]:
        """Fetch all products for a given supplier. For typical suppliers
        this is a few dozen to a few hundred — fine to load and cache in
        memory per request."""
        try:
            data = await self.ls._request(
                "GET",
                "/products",
                params={"supplier_id": supplier_id, "page_size": 500},
            )
        except LightspeedError as exc:
            logger.warning("Failed to load supplier products: %s", exc)
            return []
        return data.get("data", [])
