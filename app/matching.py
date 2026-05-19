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

from app.catalog import (
    find_cached_product_by_id,
    find_cached_product_by_code,
    find_linked_supplier_item,
    get_cached_products,
)
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

_SUPPLIER_CODE_BRANDS = {
    "API": "api",
    "AT": "aquatop",
    "CAR": "caribsea",
    "FM": "fritz",
    "HI": "hikari",
    "SC": "seachem",
    "SER": "sera",
    "SI": "sicce",
    "ZM": "zoomed",
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


def _brand_from_supplier_code(code: str | None) -> str | None:
    if not code:
        return None
    code = code.strip().upper()
    for prefix in sorted(_SUPPLIER_CODE_BRANDS, key=len, reverse=True):
        if code.startswith(prefix):
            return _SUPPLIER_CODE_BRANDS[prefix]
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


def _numbers(text: str | None) -> set[str]:
    """Extract significant numeric tokens from a product description/name."""
    if not text:
        return set()
    values = set()
    for raw in re.findall(r"\d+(?:\.\d+)?", text.lower()):
        value = raw.lstrip("0") or "0"
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        values.add(value)
    return values


def _has_numeric_conflict(invoice_text: str | None, product_text: str | None) -> bool:
    """True when both sides mention numbers but none agree.

    This keeps fuzzy matching from auto-accepting size/model variants like
    "4L" vs "500mL" or "SDC 7.0" vs "SDC 6.0".
    """
    invoice_numbers = _numbers(invoice_text)
    product_numbers = _numbers(product_text)
    return bool(invoice_numbers and product_numbers and not invoice_numbers & product_numbers)


def _identifier_digits_match(supplier_code: str | None, product: dict) -> bool:
    """Check if a supplier code's numeric tail appears in SKU/barcode fields.

    Many UPCs embed the distributor item number plus a check digit:
    AT01251 -> 810281012513. This is much stronger than a fuzzy name match,
    but only useful for reasonably long numeric tails.
    """
    digit_forms = [d for d in _trailing_digits(supplier_code) if len(d) >= 5]
    if not digit_forms:
        return False
    haystack = "".join([
        re.sub(r"\D", "", str(product.get("sku") or "")),
        " ",
        re.sub(r"\D", "", str(product.get("barcode") or "")),
    ])
    return any(d in haystack for d in digit_forms)


def _brand_compatible(line: RawInvoiceLine, product: dict) -> bool:
    line_brand = (
        _detect_brand(line.description)
        or _brand_from_supplier_code(line.supplier_code)
    )
    product_brand = _detect_brand(
        " ".join([
            product.get("name", "") or "",
            product.get("brand_name", "") or "",
        ])
    )
    return bool(line_brand and product_brand and line_brand == product_brand)


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
        # Prefer the local catalog cache. If it has not been synced yet,
        # fall back to a live pull so uploads still work on a fresh deploy.
        catalog_products = await get_cached_products(self.session)
        if not catalog_products:
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

        # Tier 1.5: supplier item memory. This is the "catalog-first" layer:
        # once a missing supplier item is created and linked, future invoices
        # match before any fuzzy search.
        if line.supplier_code:
            supplier_item = await find_linked_supplier_item(
                self.session,
                supplier_id=supplier_id,
                supplier_code=line.supplier_code,
            )
            if supplier_item and supplier_item.lightspeed_product_id:
                product = await find_cached_product_by_id(
                    self.session,
                    supplier_item.lightspeed_product_id,
                )
                return MatchedLine(
                    raw=line,
                    product_id=supplier_item.lightspeed_product_id,
                    product_sku=product.get("sku") if product else None,
                    product_name=(
                        product.get("name") if product else supplier_item.description
                    ),
                    matched_by="supplier_item",
                    confidence=1.0,
                )

        # Tier 2: barcode/UPC match (primary signal for catalogs where the
        # Lightspeed SKU field IS the UPC, which is common in pet retail).
        # We try both the extracted barcode AND supplier_code, because
        # extraction sometimes swaps these columns.
        for code in (line.barcode, line.supplier_code):
            if not code:
                continue
            product = await find_cached_product_by_code(
                self.session, code, fields=("barcode", "sku")
            )
            if product:
                return MatchedLine(
                    raw=line,
                    product_id=product["id"],
                    product_sku=product.get("sku"),
                    product_name=product.get("name"),
                    matched_by="cached_barcode_sku",
                    confidence=1.0,
                )
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

        # Tier 2.55: distributor code embedded in SKU/barcode. UPCs commonly
        # contain the supplier's numeric item code with a check digit. Require
        # brand compatibility so generic short item numbers don't cross-match.
        if line.supplier_code and catalog_products:
            for p in catalog_products:
                if (
                    _identifier_digits_match(line.supplier_code, p)
                    and _brand_compatible(line, p)
                ):
                    return MatchedLine(
                        raw=line,
                        product_id=p["id"],
                        product_sku=p.get("sku"),
                        product_name=p.get("name"),
                        matched_by="catalog_identifier_digits",
                        confidence=1.0,
                    )

        # Tier 2.6: exact supplier_code lookup in Lightspeed. The local
        # catalog list endpoint may omit supplier_code on some accounts, but
        # Lightspeed can still filter by it. This is a high-confidence signal
        # and should run before any fuzzy-name matching.
        if line.supplier_code:
            try:
                product = await self.ls.find_product_by_supplier_code(
                    line.supplier_code
                )
            except LightspeedError as exc:
                logger.warning("Supplier-code lookup failed: %s", exc)
                product = None
            if product:
                return MatchedLine(
                    raw=line,
                    product_id=product["id"],
                    product_sku=product.get("sku"),
                    product_name=product.get("name"),
                    matched_by="supplier_code_live",
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
            numeric_conflict = _has_numeric_conflict(
                line.description,
                top_product.get("name", ""),
            )

            # Auto-match rules — designed to avoid false matches:
            #   - Confident only if digit-boost fires (cross-distributor signal)
            #   - OR name similarity is very high (0.80+) on its own
            # Plain "the name kind of looks similar" (0.60-0.79) stays uncertain.
            should_auto_match = (
                not numeric_conflict
                and (
                    (top_digits and top_total >= FUZZY_MATCH_THRESHOLD)
                    or top_sim >= 0.80
                )
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
