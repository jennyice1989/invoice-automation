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


# Common brand names that appear in this catalog and invoices.
# Aliases (left side) map to a canonical brand (right side) so that
# "Seachem", "seachem labs", "Seachem Laboratories" all collapse to "seachem".
# Order matters — longer aliases checked first.
_BRAND_ALIASES = {
    # Aquariums / livestock
    "aqua zone aquariums": "a2z",
    "a2z": "a2z",
    # Big aquatic brands
    "seachem laboratories": "seachem",
    "seachem labs": "seachem",
    "seachem aquavitro": "seachem",
    "aquavitro": "seachem",
    "seachem": "seachem",
    "carib sea": "caribsea",
    "caribsea": "caribsea",
    "hikari usa": "hikari",
    "hikari": "hikari",
    "fritz aquatics": "fritz",
    "fritz": "fritz",
    "api ": "api",  # trailing space — "api" alone matches too many things
    "aquarium pharmaceuticals": "api",
    "fluval": "fluval",
    "tetra": "tetra",
    "aqueon": "aqueon",
    "marina": "marina",
    "lees aquarium": "lees",
    "lee s aquarium": "lees",
    "lee's aquarium": "lees",
    "ocean nutrition": "ocean nutrition",
    "reef nutrition": "reef nutrition",
    "red sea": "red sea",
    "sicce": "sicce",
    "sera": "sera",
    "aquatop": "aquatop",
    "python": "python",
    "eshopps": "eshopps",
    "flipper": "flipper",
    "novus": "novus",
    "poly filter": "polyfilter",
    "polyfilter": "polyfilter",
    "polybio marine": "polyfilter",
    "lifegard": "lifegard",
    # Reptile/livestock
    "zoo med": "zoomed",
    "zoomed": "zoomed",
    "zml": "zoomed",
    "exo terra": "exoterra",
    "exoterra": "exoterra",
    "komodo": "komodo",
    "oxbow": "oxbow",
    # Plumbing
    "duraplas": "dura",
    "dura plastics": "dura",
    "spears": "spears",
    "nds": "nds",
    "reefh2o": "reefh2o",
    "reef h2o": "reefh2o",
    "jbj": "jbj",
    "xp aqua": "xpaqua",
    "xpaqua": "xpaqua",
    "current usa": "current",
    "current": "current",
}


def _detect_brand(text: str | None) -> str | None:
    """Find the first brand alias that appears in the text. Returns
    the canonical brand key, or None. Case-insensitive."""
    if not text:
        return None
    t = " " + text.lower() + " "
    # Sort by alias length descending so 'seachem aquavitro' wins over 'seachem'
    for alias in sorted(_BRAND_ALIASES, key=len, reverse=True):
        # Match alias as a whole-word substring
        needle = " " + alias.lower().rstrip() + " "
        if needle in t:
            return _BRAND_ALIASES[alias]
        # Also match at start (no leading space possible)
        if t.lstrip().startswith(alias.lower()):
            return _BRAND_ALIASES[alias]
    return None


# Stopwords removed before token-set similarity. These are size/qualifier
# words that match everywhere and add noise. "ml", "oz", "lb" etc. ARE
# kept because the *number* before them carries information.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "with", "of", "in",
    "to", "by", "on", "at", "from", "pack", "size", "new",
    "free", "freight",  # invoice cruft
}


def _tokens(text: str | None) -> set[str]:
    """Return the set of meaningful tokens in text after normalization."""
    norm = _normalize(text)
    if not norm:
        return set()
    return {t for t in norm.split() if t and t not in _STOPWORDS and len(t) > 1}


def _token_set_ratio(a: str, b: str) -> float:
    """Jaccard-style token overlap, with a bonus for shared tokens that
    are numbers/sizes (which carry stronger signal than common words)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    intersection = ta & tb
    union = ta | tb
    if not union:
        return 0.0
    base = len(intersection) / len(union)
    # Bonus: shared tokens that contain digits (like '500ml', '40lbs',
    # 'p55', '3in') are stronger signal than shared words. Add 0.05
    # per shared numeric token, up to +0.20.
    numeric_shared = sum(1 for t in intersection if any(c.isdigit() for c in t))
    return min(1.0, base + min(0.20, 0.05 * numeric_shared))


def _name_similarity(a: str, b: str) -> float:
    """Hybrid similarity — max of token-set overlap and char-level
    ratio. Token-set catches reorderings and partial-word matches
    that the char-level SequenceMatcher misses; char-level still
    helps when one side is a tighter spelling of the other."""
    ts = _token_set_ratio(a, b)
    cs = SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()
    return max(ts, cs)


def _trailing_digits(s: str | None) -> list[str]:
    """Return candidate digit strings from a supplier code, for fuzzy
    cross-distributor matching. Different distributors use different
    alphabetic prefixes and zero-padding for the same physical product:
      ReefH2O 'SC07076'   -> ['07076', '7076']
      Seachem 'ASM7076'   -> ['7076']
      Plain   '7076'      -> ['7076']
    Returns a list (longest first) so the caller can try each.
    """
    if not s:
        return []
    m = re.search(r"(\d+)$", s.strip())
    if not m:
        return []
    digits = m.group(1)
    candidates = [digits]
    stripped = digits.lstrip("0")
    if stripped and stripped != digits and len(stripped) >= 3:
        candidates.append(stripped)
    return candidates


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
        # Pull the full catalog once for exact supplier-code and fuzzy
        # matching. A supplier-filtered first page missed most real products
        # in Lightspeed, which made invoice lines look unrelated.
        catalog_products = await self._load_catalog_products()

        matched: list[MatchedLine] = []
        unmatched: list[UnmatchedLine] = []

        for line in lines:
            result = await self._match_one(
                supplier_id, line, catalog_products
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
        catalog_products: list[dict],
    ) -> MatchedLine | UnmatchedLine:
        # Tier 1: saved mapping (highest signal — human approved this before)
        # Keyed on supplier_code OR barcode, whichever extraction gave us.
        for key in (line.supplier_code, line.barcode):
            if not key:
                continue
            mapping = await find_mapping(
                self.session, supplier_id=supplier_id, supplier_code=key,
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

        # Tier 2: barcode/UPC match (primary signal for catalogs where the
        # Lightspeed SKU field IS the UPC, which is common in pet retail).
        # We try both the extracted barcode AND supplier_code, because
        # extraction sometimes swaps these columns.
        for code in (line.barcode, line.supplier_code):
            if not code:
                continue
            # Try barcode lookup
            try:
                product = await self.ls.find_product_by_barcode(code)
            except LightspeedError as exc:
                logger.warning("Barcode lookup failed: %s", exc)
                product = None
            if product:
                return MatchedLine(
                    raw=line,
                    product_id=product["id"],
                    product_sku=product.get("sku"),
                    product_name=product.get("name"),
                    matched_by="barcode",
                    confidence=1.0,
                )
            # Try SKU lookup (Lightspeed sku field often holds the UPC)
            try:
                product = await self.ls.find_product_by_sku(code)
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

        # Tier 2.5: exact supplier_code match within the full catalog
        # (we already fetched it). This is the typical case
        # for distributors like ReefH2O whose catalog codes don't appear
        # as SKUs/barcodes anywhere — but they ARE stored on the
        # Lightspeed product's `supplier_code` field. Case- and
        # whitespace-insensitive.
        if line.supplier_code and catalog_products:
            target = line.supplier_code.strip().lower()
            for p in catalog_products:
                p_code = (p.get("supplier_code") or "").strip().lower()
                if p_code and p_code == target:
                    return MatchedLine(
                        raw=line,
                        product_id=p["id"],
                        product_sku=p.get("sku"),
                        product_name=p.get("name"),
                        matched_by="supplier_code",
                        confidence=1.0,
                    )

        # Tier 3: fuzzy name match against the full catalog (last resort).
        # We score by name similarity, then BOOST when the supplier_code's
        # numeric tail also appears in the product (in name, sku, or
        # supplier_code). Different distributors use different prefixes
        # (SC07076 vs ASM7076 vs simply 7076), but the digits usually
        # carry the actual model number.
        if line.description and catalog_products:
            code_digits = _trailing_digits(line.supplier_code)
            line_brand = _detect_brand(line.description)
            scored = []
            for p in catalog_products:
                product_name = p.get("name", "") or ""
                product_brand = _detect_brand(
                    " ".join([
                        product_name,
                        p.get("brand_name", "") or "",
                    ])
                )

                sim = _name_similarity(line.description, product_name)

                # Brand-aware adjustment:
                # - If we detected a brand on the invoice line AND the
                #   product has the same brand, +0.10.
                # - If brands are BOTH known but DIFFERENT, heavily penalize
                #   (these are almost certainly the wrong product even if
                #   the names happen to share characters).
                # - If either brand is unknown, no adjustment — let
                #   similarity speak for itself.
                brand_adj = 0.0
                if line_brand and product_brand:
                    if line_brand == product_brand:
                        brand_adj = 0.10
                    else:
                        brand_adj = -0.35

                # Digit-tail boost — see _trailing_digits docs
                boost = 0.0
                digits_matched = False
                if code_digits:
                    haystack = " ".join([
                        product_name,
                        p.get("sku", "") or "",
                        p.get("supplier_code", "") or "",
                    ]).lower()
                    for digit_form in code_digits:
                        if len(digit_form) >= 4 and digit_form in haystack:
                            boost = 0.25
                            digits_matched = True
                            break

                total = max(0.0, sim + brand_adj + boost)
                scored.append((total, sim, boost, digits_matched, p))

            scored.sort(key=lambda x: x[0], reverse=True)
            top_total, top_sim, top_boost, top_digits, top_product = scored[0]

            # Auto-match rules — designed to avoid false matches:
            #   - Confident only if digit-boost fires (cross-distributor signal)
            #   - OR name similarity is very high (0.80+) on its own
            # Plain "the name kind of looks similar" (0.60-0.79) stays uncertain.
            should_auto_match = (
                (top_digits and top_total >= FUZZY_MATCH_THRESHOLD)
                or top_sim >= 0.80
            )
            if should_auto_match:
                return MatchedLine(
                    raw=line,
                    product_id=top_product["id"],
                    product_sku=top_product.get("sku"),
                    product_name=top_product.get("name"),
                    matched_by="fuzzy_name+digits" if top_digits else "fuzzy_name",
                    confidence=top_total,
                )
            candidates = [
                {
                    "product_id": p["id"], "sku": p.get("sku"),
                    "name": p.get("name"), "confidence": round(total, 3),
                }
                for total, _, _, _, p in scored[:3]
            ]
            return UnmatchedLine(
                raw=line, candidates=candidates,
                reason="No code/barcode match; top fuzzy match below threshold",
            )

        return UnmatchedLine(
            raw=line, candidates=[],
            reason="No matching code, barcode, or description found",
        )

    async def _load_catalog_products(self) -> list[dict]:
        """Fetch all live products for matching."""
        try:
            return await self.ls.list_products()
        except LightspeedError as exc:
            logger.warning("Failed to load product catalog: %s", exc)
            return []
