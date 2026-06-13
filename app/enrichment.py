"""
Product enrichment via Claude.

Drafts a retail-quality HTML product description in the style of A2Z
Aquariums' existing catalog (h3 heading, paragraph structure, brand
mentions). For live fish, care details are woven into the prose
naturally rather than living in a separate structured template.

What Claude DRAFTS:
  - name (cleaned up if the input is a supplier abbreviation)
  - description (HTML)
  - product_category (picked from the real list provided)
  - brand_name (inferred from product name)
  - tags (suggested)

What stays manual (Claude never invents):
  - UPC / barcode
  - product photo (still manual)
  - exact retail price (rules engine handles this elsewhere)
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-7")
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1/messages"


class EnrichmentError(Exception):
    pass


ProductKind = Literal["dry_good", "live_fish", "live_invert", "live_plant", "live_coral", "unknown"]


@dataclass
class EnrichmentResult:
    """What Claude drafts for one product."""
    input_name: str
    cleaned_name: str | None = None
    kind: ProductKind = "unknown"
    description_html: str | None = None
    product_category: str | None = None  # exact match from provided list
    brand_name: str | None = None
    suggested_tags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- #
# Prompt                                                                #
# --------------------------------------------------------------------- #

_STYLE_GUIDE = """STYLE GUIDE (match this voice exactly):

You are writing retail descriptions for A2Z Aquariums, a specialty aquarium
and pet store. Descriptions appear on the product page of their online store
and should help customers decide whether the product is right for them.

FORMAT (mandatory):
- HTML using <h3>, <p>, <strong>, and <em> tags only
- Open with a single <h3> heading: "<Product Name> – <Hook>"
- Then 2-3 <p> paragraphs
- Total length: 300-550 characters of visible text (not counting HTML)
- End with brand attribution: "Available at <strong>A2Z Aquariums</strong>"

TONE:
- Confident, specific, and plainspoken; no salesy filler
- NEVER use these clichés: "transform your aquarium", "elevate your",
  "underwater paradise", "hand-selected", "must-have", "whether you're
  a seasoned aquarist or just starting", "captivating", "mesmerizing"
- DO mention specific facts: dimensions, capacity, materials, species
  characteristics, care needs, brand reputation
- If a fact is not knowable from the product name, avoid making it up.
  Prefer concrete product-type context over invented specs.
- For dry goods: explain what it is, what it does, who it's for, and
  one specific detail (dosing, compatibility, capacity, etc.)
- For live fish/inverts/corals/plants: weave care info into the prose
  naturally — adult size, minimum tank, water parameters, temperament,
  compatibility, diet — as part of the description, not as a list

EXAMPLE OF THE TARGET STYLE (live fish):

<h3>Electric Blue Acara – Brilliant Color, Easygoing Personality</h3>
<p>The <strong>Electric Blue Acara</strong> (<em>Andinoacara pulcher</em>)
is one of the most striking cichlids you can keep, with shimmering
metallic-blue scales and a peaceful temperament that makes it a standout
centerpiece for community tanks. Reaching 6–7 inches at maturity, this
hardy South American native is one of the few cichlids genuinely suited
to mixed setups.</p>
<p><strong>Tank requirements:</strong> 30 gallons minimum, soft to
moderately hard water (pH 6.5–7.5, 72–82°F). They pair well with larger
tetras, plecos, corydoras, and other peaceful cichlids. Avoid keeping
with very small fish or long-finned species.</p>
<p>Hand-picked for color and health. Available at
<strong>A2Z Aquariums</strong>.</p>

EXAMPLE OF THE TARGET STYLE (dry good):

<h3>Seachem Prime 500ml – Water Conditioner for Fresh & Saltwater</h3>
<p>Seachem <strong>Prime</strong> is the industry-standard water
conditioner, removing chlorine and chloramine while neutralizing ammonia
and nitrite. A single 500ml bottle treats 5,000 gallons, making it one
of the most cost-effective conditioners on the market.</p>
<p>Use 5ml (one capful) per 50 gallons during water changes, or as an
emergency detoxifier if ammonia or nitrite spike. Safe for fresh and
saltwater, planted tanks, and reef systems.</p>
<p>Available at <strong>A2Z Aquariums</strong>.</p>
"""


def _build_prompt(
    product_name: str,
    supplier_name: str | None,
    supplier_code: str | None,
    barcode: str | None,
    supply_price: float | None,
    kind_hint: ProductKind | None,
    available_categories: list[str],
    available_brands: list[str],
    product_facts: str | None = None,
) -> str:
    cat_list = "\n".join(f"  - {c}" for c in available_categories) or "  (none provided)"
    brand_list = ", ".join(available_brands[:80]) if available_brands else "(none provided)"

    extra = []
    if supplier_name:
        extra.append(f"Supplier: {supplier_name}")
    if supplier_code:
        extra.append(f"Supplier item code: {supplier_code}")
    if barcode:
        extra.append(f"Barcode/UPC: {barcode}")
    if supply_price is not None:
        extra.append(f"Wholesale cost: ${supply_price:.2f}")
    if kind_hint and kind_hint != "unknown":
        extra.append(f"Product type (already classified): {kind_hint}")
    if product_facts:
        extra.append(
            "Trusted supplier/catalog facts. Use these facts when writing the "
            f"name and description; do not contradict them:\n{product_facts}"
        )
    extras = ("\n" + "\n".join(extra)) if extra else ""

    return f"""You are enriching a product for a specialty aquarium retailer's online catalog.

PRODUCT TO ENRICH:
Name (as it appears on the invoice or list, may be abbreviated): {product_name}{extras}

TASK — return a JSON object with these fields:

1. "cleaned_name": If the input name is a supplier abbreviation (e.g.
   "AQE TNK BK TRIMSIL 125G" or "SLI COND PRIME 50ML"), expand it to a
   proper retail name ("Aqueon Tank Background Trimsil 125g", "Seachem
   Prime Water Conditioner 50ml"). If the input is already a clean name,
   echo it back unchanged. Use the supplier context above to help guess
   the brand.

2. "kind": one of "dry_good", "live_fish", "live_invert" (shrimp, snails,
   sea stars, etc.), "live_plant", "live_coral", or "unknown".

3. "product_category": pick the SINGLE BEST match from this exact list of
   the retailer's existing categories. You MUST return one of these
   strings verbatim, or null if truly nothing fits. Do not invent new
   categories.
{cat_list}

4. "brand_name": the manufacturer brand if identifiable from the name
   (Aqueon, Seachem, API, Fluval, Hikari, Tetra, CaribSea, etc.). If
   the name doesn't contain a recognizable brand, use null. If you
   recognize a brand that appears in this list, use the exact spelling
   from the list:
{brand_list}

5. "description_html": A retail HTML description following the style guide
   below. This is the main output.

6. "suggested_tags": Up to 5 short keywords useful for filtering/search
   (e.g. ["freshwater", "beginner", "peaceful"] for a fish, or
   ["water-care", "freshwater", "saltwater"] for a conditioner). Lowercase.

{_STYLE_GUIDE}

Return ONLY the JSON object — no markdown fences, no prose before or after."""


# --------------------------------------------------------------------- #
# API call                                                              #
# --------------------------------------------------------------------- #

async def enrich_product(
    product_name: str,
    *,
    supplier_name: str | None = None,
    supplier_code: str | None = None,
    barcode: str | None = None,
    supply_price: float | None = None,
    kind_hint: ProductKind | None = None,
    available_categories: list[str] | None = None,
    available_brands: list[str] | None = None,
    product_facts: str | None = None,
) -> EnrichmentResult:
    """Enrich a single product."""
    if not ANTHROPIC_API_KEY:
        raise EnrichmentError("ANTHROPIC_API_KEY not configured")
    if not product_name or not product_name.strip():
        raise EnrichmentError("Empty product name")

    prompt = _build_prompt(
        product_name=product_name.strip(),
        supplier_name=supplier_name,
        supplier_code=supplier_code,
        barcode=barcode,
        supply_price=supply_price,
        kind_hint=kind_hint,
        available_categories=available_categories or [],
        available_brands=available_brands or [],
        product_facts=product_facts,
    )

    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
    }

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            ANTHROPIC_BASE_URL,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )

    if not resp.is_success:
        raise EnrichmentError(
            f"Anthropic API error {resp.status_code}: {resp.text[:300]}"
        )

    data = resp.json()
    text = "\n".join(
        b.get("text", "") for b in data.get("content", [])
        if b.get("type") == "text"
    ).strip()
    parsed = _parse_json(text)
    return _to_result(product_name, parsed, available_categories or [])


async def enrich_batch(
    products: list[dict],
    *,
    available_categories: list[str] | None = None,
    available_brands: list[str] | None = None,
) -> list[EnrichmentResult]:
    """Enrich a list of products sequentially.

    Each dict: {name, supplier_name?, barcode?, kind_hint?}
    """
    results: list[EnrichmentResult] = []
    for p in products:
        try:
            r = await enrich_product(
                p["name"],
                supplier_name=p.get("supplier_name"),
                supplier_code=p.get("supplier_code"),
                barcode=p.get("barcode"),
                supply_price=p.get("supply_price"),
                kind_hint=p.get("kind_hint"),
                available_categories=available_categories,
                available_brands=available_brands,
                product_facts=p.get("product_facts"),
            )
        except EnrichmentError as exc:
            r = EnrichmentResult(
                input_name=p.get("name", "?"),
                kind="unknown",
                warnings=[f"Enrichment failed: {exc}"],
            )
        results.append(r)
    return results


# --------------------------------------------------------------------- #
# Parsing                                                               #
# --------------------------------------------------------------------- #

def _parse_json(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise EnrichmentError(f"Could not parse model response: {text[:300]}")


def _s(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _to_result(
    input_name: str, parsed: dict, available_categories: list[str],
) -> EnrichmentResult:
    kind = parsed.get("kind", "unknown")
    valid_kinds = ("dry_good", "live_fish", "live_invert", "live_plant", "live_coral", "unknown")
    if kind not in valid_kinds:
        kind = "unknown"

    warnings: list[str] = []

    category = _s(parsed.get("product_category"))
    if category and available_categories and category not in available_categories:
        # Claude returned a category not in the list — flag it and clear.
        warnings.append(
            f"Suggested category '{category}' is not in your category list. "
            f"Pick one manually."
        )
        category = None

    description = _s(parsed.get("description_html"))
    if not description:
        warnings.append("No description was generated.")

    suggested_tags = parsed.get("suggested_tags") or []
    if not isinstance(suggested_tags, list):
        suggested_tags = []
    suggested_tags = [str(t).strip().lower() for t in suggested_tags if t][:5]

    return EnrichmentResult(
        input_name=input_name,
        cleaned_name=_s(parsed.get("cleaned_name")) or input_name,
        kind=kind,
        description_html=description,
        product_category=category,
        brand_name=_s(parsed.get("brand_name")),
        suggested_tags=suggested_tags,
        warnings=warnings,
    )
