"""
Product enrichment via Claude.

Two paths:
  - Dry goods: draft a retail-quality product description from the name
    + brand knowledge.
  - Live fish: draft a structured care profile (water params, tankmates,
    diet, etc.) with explicit uncertainty flags.

The classifier decides which path a product takes; the user can override.

Nothing factual that Claude can't reliably know (UPC, exact photo) is
invented here — those fields stay empty for the user to fill.
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


ProductKind = Literal["dry_good", "live_fish", "unknown"]


@dataclass
class FishProfile:
    """Structured care profile for live aquatic livestock."""
    common_name: str | None = None
    scientific_name: str | None = None
    adult_size: str | None = None
    min_tank_size: str | None = None
    temperature_range: str | None = None
    ph_range: str | None = None
    hardness: str | None = None
    temperament: str | None = None
    care_level: str | None = None
    lifespan: str | None = None
    compatible_with: str | None = None
    avoid_with: str | None = None
    diet: str | None = None
    species_notes: str | None = None
    # Fields Claude flagged as uncertain — surfaced in the review UI.
    uncertain_fields: list[str] = field(default_factory=list)

    def to_description(self) -> str:
        """Render the profile as a formatted description for Lightspeed."""
        lines = []
        if self.scientific_name:
            lines.append(f"Scientific name: {self.scientific_name}")
        if self.adult_size:
            lines.append(f"Adult size: {self.adult_size}")
        if self.min_tank_size:
            lines.append(f"Minimum tank size: {self.min_tank_size}")
        params = []
        if self.temperature_range:
            params.append(f"temp {self.temperature_range}")
        if self.ph_range:
            params.append(f"pH {self.ph_range}")
        if self.hardness:
            params.append(f"hardness {self.hardness}")
        if params:
            lines.append("Water parameters: " + ", ".join(params))
        if self.temperament:
            lines.append(f"Temperament: {self.temperament}")
        if self.care_level:
            lines.append(f"Care level: {self.care_level}")
        if self.lifespan:
            lines.append(f"Lifespan: {self.lifespan}")
        if self.compatible_with:
            lines.append(f"Compatible with: {self.compatible_with}")
        if self.avoid_with:
            lines.append(f"Avoid keeping with: {self.avoid_with}")
        if self.diet:
            lines.append(f"Diet: {self.diet}")
        if self.species_notes:
            lines.append("")
            lines.append(self.species_notes)
        return "\n".join(lines)


@dataclass
class EnrichmentResult:
    """Output of enriching one product."""
    input_name: str
    kind: ProductKind
    description: str | None = None
    fish_profile: FishProfile | None = None
    # Brand Claude inferred, if any (helps with manual photo sourcing).
    detected_brand: str | None = None
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- #
# Prompts                                                               #
# --------------------------------------------------------------------- #

_CLASSIFY_AND_ENRICH_PROMPT = """You are helping a specialty aquarium and pet store enrich its product catalog.

You will be given a product name (and possibly a supplier name). Do two things:

1. CLASSIFY the product as one of:
   - "live_fish": a living aquatic animal or plant sold as livestock
     (fish, shrimp, snails, corals, live aquatic plants)
   - "dry_good": any non-living product (food, equipment, treatments,
     decor, filters, heaters, supplements, etc.)
   - "unknown": you genuinely cannot tell from the name

2. ENRICH based on the classification:

FOR dry_good — write a retail product description:
   - 2-4 sentences, accurate and useful to a customer
   - Describe what the product is and its main use/benefit
   - Mention the brand if identifiable from the name
   - Do NOT invent specifications you don't know (exact dimensions,
     exact ingredient lists, wattage unless it's in the name)
   - Do NOT invent a UPC or barcode
   - Natural retail copy, not a spec sheet

FOR live_fish — produce a structured care profile. For EACH field, only
fill it if you are reasonably confident. If you are uncertain about a
field, still provide your best estimate but add the field name to
"uncertain_fields". Care information for common aquarium species is
well-documented; for unusual species, be honest about uncertainty.

Return ONLY a JSON object, no other text:

{
  "kind": "dry_good" | "live_fish" | "unknown",
  "detected_brand": "brand name if identifiable, else null",
  "description": "retail description (for dry_good; null for live_fish)",
  "fish_profile": {
    "common_name": "...",
    "scientific_name": "...",
    "adult_size": "e.g. '3 inches (7.5 cm)'",
    "min_tank_size": "e.g. '20 gallons'",
    "temperature_range": "e.g. '72-82°F (22-28°C)'",
    "ph_range": "e.g. '6.5-7.5'",
    "hardness": "e.g. '5-15 dGH'",
    "temperament": "Peaceful | Semi-aggressive | Aggressive",
    "care_level": "Beginner | Intermediate | Advanced",
    "lifespan": "e.g. '5-8 years'",
    "compatible_with": "brief list of good tankmates",
    "avoid_with": "brief list of incompatible species",
    "diet": "what it eats + feeding notes",
    "species_notes": "1-3 sentences: breeding, sexing, common health issues, or other notable info"
  },
  "uncertain_fields": ["list of fish_profile field names you're not confident about"]
}

For dry_good, set fish_profile to null and uncertain_fields to [].
For live_fish, set description to null.
Return ONLY the JSON object."""


# --------------------------------------------------------------------- #
# API call                                                              #
# --------------------------------------------------------------------- #

async def enrich_product(
    product_name: str,
    supplier_name: str | None = None,
    kind_hint: ProductKind | None = None,
) -> EnrichmentResult:
    """Enrich a single product. kind_hint, if given, forces the classification
    (used when the user has already tagged the product type)."""
    if not ANTHROPIC_API_KEY:
        raise EnrichmentError("ANTHROPIC_API_KEY not configured")
    if not product_name or not product_name.strip():
        raise EnrichmentError("Empty product name")

    user_content = f"Product name: {product_name.strip()}"
    if supplier_name:
        user_content += f"\nSupplier: {supplier_name.strip()}"
    if kind_hint and kind_hint != "unknown":
        user_content += (
            f"\n\nThe user has already classified this as: {kind_hint}. "
            f"Use that classification."
        )

    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 2000,
        "messages": [
            {
                "role": "user",
                "content": f"{_CLASSIFY_AND_ENRICH_PROMPT}\n\n---\n\n{user_content}",
            }
        ],
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
    return _to_result(product_name, parsed)


async def enrich_batch(
    products: list[dict],
) -> list[EnrichmentResult]:
    """Enrich a list of products. Each dict: {name, supplier_name?, kind_hint?}.

    Runs sequentially — a few dozen products is fine, and sequential keeps
    us well clear of API rate limits without added complexity.
    """
    results: list[EnrichmentResult] = []
    for p in products:
        try:
            result = await enrich_product(
                p["name"],
                supplier_name=p.get("supplier_name"),
                kind_hint=p.get("kind_hint"),
            )
        except EnrichmentError as exc:
            result = EnrichmentResult(
                input_name=p.get("name", "?"),
                kind="unknown",
                warnings=[f"Enrichment failed: {exc}"],
            )
        results.append(result)
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


def _to_result(input_name: str, parsed: dict) -> EnrichmentResult:
    kind = parsed.get("kind", "unknown")
    if kind not in ("dry_good", "live_fish", "unknown"):
        kind = "unknown"

    warnings: list[str] = []
    fish_profile = None

    if kind == "live_fish":
        fp = parsed.get("fish_profile") or {}
        fish_profile = FishProfile(
            common_name=_s(fp.get("common_name")) or input_name,
            scientific_name=_s(fp.get("scientific_name")),
            adult_size=_s(fp.get("adult_size")),
            min_tank_size=_s(fp.get("min_tank_size")),
            temperature_range=_s(fp.get("temperature_range")),
            ph_range=_s(fp.get("ph_range")),
            hardness=_s(fp.get("hardness")),
            temperament=_s(fp.get("temperament")),
            care_level=_s(fp.get("care_level")),
            lifespan=_s(fp.get("lifespan")),
            compatible_with=_s(fp.get("compatible_with")),
            avoid_with=_s(fp.get("avoid_with")),
            diet=_s(fp.get("diet")),
            species_notes=_s(fp.get("species_notes")),
            uncertain_fields=parsed.get("uncertain_fields") or [],
        )
        if fish_profile.uncertain_fields:
            warnings.append(
                "Claude flagged uncertainty on: "
                + ", ".join(fish_profile.uncertain_fields)
                + ". Verify these before publishing."
            )
        if not fish_profile.scientific_name:
            warnings.append("No scientific name identified — double-check the species.")

    description = None
    if kind == "dry_good":
        description = _s(parsed.get("description"))
        if not description:
            warnings.append("No description was generated.")
    elif kind == "unknown":
        warnings.append(
            "Could not classify this product as dry good or live fish. "
            "Tag it manually and re-enrich."
        )

    return EnrichmentResult(
        input_name=input_name,
        kind=kind,
        description=description,
        fish_profile=fish_profile,
        detected_brand=_s(parsed.get("detected_brand")),
        warnings=warnings,
    )
