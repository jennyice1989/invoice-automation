"""Deterministic UPC lookup for supplier-backed product drafts.

UPC values are inventory identifiers, so this module only returns codes
that already appear in trusted data we can inspect. It does not ask the
LLM to infer or invent a barcode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import normalize_text, search_cached_products


UPC_RE = re.compile(r"^\d{12,13}$")
TARGET_SUPPLIER_TERMS = (
    "central pet",
    "central garden pet",
    "phillips pet",
    "phillips feed",
    "reef h2o",
    "reefh2o",
)


@dataclass
class UpcLookupResult:
    upc: str
    source: str
    confidence: float
    product_id: str | None = None
    product_name: str | None = None
    notes: str | None = None


def normalize_supplier_name(name: str | None) -> str:
    return normalize_text(name).replace("h 2 o", "h2o")


def supplier_supports_upc_lookup(name: str | None) -> bool:
    normalized = normalize_supplier_name(name)
    if not normalized:
        return False
    compact = normalized.replace(" ", "")
    return any(term in normalized or term.replace(" ", "") in compact for term in TARGET_SUPPLIER_TERMS)


def looks_like_upc(value: Any) -> bool:
    return bool(UPC_RE.match(re.sub(r"\D", "", str(value or ""))))


def extract_upc(product: dict | None) -> str | None:
    if not product:
        return None
    values: list[Any] = []
    for key in ("barcode", "sku", "upc", "ean"):
        value = product.get(key)
        if isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)
    raw = product.get("raw")
    if isinstance(raw, dict):
        for key in ("barcode", "sku", "upc", "ean"):
            value = raw.get(key)
            if isinstance(value, list):
                values.extend(value)
            else:
                values.append(value)

    for value in values:
        digits = re.sub(r"\D", "", str(value or ""))
        if UPC_RE.match(digits):
            return digits
    return None


def _supplier_code_digits(supplier_code: str | None) -> str:
    return re.sub(r"\D", "", supplier_code or "")


def _name_ratio(query: str | None, candidate_name: str | None) -> float:
    q = normalize_text(query)
    n = normalize_text(candidate_name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    if q in n or n in q:
        return 0.92
    q_tokens = set(q.split())
    n_tokens = set(n.split())
    token_score = len(q_tokens & n_tokens) / len(q_tokens | n_tokens) if q_tokens and n_tokens else 0.0
    return max(token_score, SequenceMatcher(None, q, n).ratio())


def score_upc_candidate(
    product: dict,
    *,
    product_name: str | None,
    supplier_code: str | None,
) -> float:
    upc = extract_upc(product)
    if not upc:
        return 0.0

    score = _name_ratio(product_name, product.get("name"))
    code_digits = _supplier_code_digits(supplier_code)
    if code_digits and len(code_digits) >= 4:
        fields = [
            re.sub(r"\D", "", str(product.get("sku") or "")),
            re.sub(r"\D", "", str(product.get("barcode") or "")),
            upc,
        ]
        if any(code_digits in field for field in fields if field):
            score = max(score, 0.94)

    return score


async def _supplier_name_for_id(client: Any, supplier_id: str | None) -> str | None:
    if not client or not supplier_id:
        return None
    suppliers = await client.list_suppliers()
    for supplier in suppliers:
        if supplier.get("id") == supplier_id:
            return supplier.get("name")
    return None


async def lookup_upc_for_product(
    session: AsyncSession,
    *,
    client: Any = None,
    supplier_id: str | None = None,
    supplier_name: str | None = None,
    supplier_code: str | None = None,
    product_name: str | None = None,
) -> UpcLookupResult | None:
    """Return a trusted UPC candidate for supported suppliers, if found."""
    resolved_supplier_name = supplier_name
    if not resolved_supplier_name and supplier_id:
        try:
            resolved_supplier_name = await _supplier_name_for_id(client, supplier_id)
        except Exception:
            resolved_supplier_name = None

    if not supplier_supports_upc_lookup(resolved_supplier_name):
        return None

    code = (supplier_code or "").strip()
    if supplier_id and code:
        from app.db import SupplierCatalogItem, SupplierMsrp

        remembered = (await session.execute(
            select(SupplierCatalogItem).where(
                SupplierCatalogItem.supplier_id == supplier_id,
                SupplierCatalogItem.supplier_code == code,
            )
        )).scalar_one_or_none()
        remembered_upc = extract_upc({"barcode": getattr(remembered, "barcode", None)})
        if remembered_upc:
            return UpcLookupResult(
                upc=remembered_upc,
                source="saved supplier item memory",
                confidence=0.99,
                notes=f"Matched supplier code {code}",
            )

        msrp = (await session.execute(
            select(SupplierMsrp).where(
                SupplierMsrp.supplier_id == supplier_id,
                SupplierMsrp.supplier_code == code,
            )
        )).scalar_one_or_none()
        msrp_upc = extract_upc({"barcode": getattr(msrp, "barcode", None)})
        if msrp_upc:
            return UpcLookupResult(
                upc=msrp_upc,
                source="supplier MSRP file",
                confidence=0.98,
                notes=f"Matched supplier code {code}",
            )

    queries = []
    for value in (supplier_code, product_name, f"{product_name or ''} {supplier_code or ''}".strip()):
        value = (value or "").strip()
        if value and value not in queries:
            queries.append(value)

    candidates: dict[str, dict] = {}
    for query in queries:
        for product in await search_cached_products(session, query, limit=30):
            key = str(product.get("id") or product.get("sku") or product.get("barcode") or product.get("name"))
            candidates[key] = product

    scored = [
        (score_upc_candidate(product, product_name=product_name, supplier_code=supplier_code), product)
        for product in candidates.values()
    ]
    scored = [(score, product) for score, product in scored if score >= 0.70]
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)

    score, product = scored[0]
    upc = extract_upc(product)
    if not upc:
        return None
    return UpcLookupResult(
        upc=upc,
        source="Lightspeed catalog cache",
        confidence=round(float(score), 3),
        product_id=product.get("id"),
        product_name=product.get("name"),
        notes=f"Matched supported supplier {resolved_supplier_name or supplier_id}",
    )
