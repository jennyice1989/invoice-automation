"""Supplier catalog PDF parsing and lookup helpers."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import normalize_text
from app.db import SupplierCatalogItem


UPC_RE = re.compile(r"\b\d{12,13}\b")


@dataclass
class CatalogItem:
    supplier_code: str
    name: str
    barcode: str | None = None
    mfg_part: str | None = None
    unit_cost: float | None = None
    list_price: float | None = None
    source: str | None = None
    page: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)


def _money(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def _clean_lines(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if line in {"ADD TO CART", "Sort By: Title", "Page of 1", "Page of 2"}:
            continue
        if line.startswith(("6/1/26", "Page ", "https://")):
            continue
        if line.startswith("Home >"):
            continue
        lines.append(line)
    return lines


def _clean_product_name(raw: str) -> str:
    lines = _clean_lines(raw)
    kept = []
    skip_prefixes = (
        "In Stock", "Sell Pk:", "Case Qty:", "Pallet Qty:", "Your Price:",
        "List Price", "Members Price:", "Sale Price:", "MSP/MAP/MRP:",
        "(Out of Stock)", "Discontinued by Manufacturer", "No Free Freight",
    )
    for line in lines:
        if line.startswith(skip_prefixes):
            continue
        if re.match(r"^\d+\s+In Stock$", line):
            continue
        kept.append(line)
    name = " ".join(kept)
    name = re.sub(r"\s+", " ", name).strip(" -")
    return name[:500]


def _page_hint(text: str) -> str | None:
    match = re.search(r"Page\s+(\d+)\s+of\s+\d+", text)
    return match.group(1) if match else None


def parse_central_catalog_text(text: str, *, source: str | None = None) -> list[CatalogItem]:
    items: list[CatalogItem] = []
    matches = list(re.finditer(r"Product #:\s*(?P<code>\S+)", text))
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end]
        code = match.group("code").strip()
        mfg = re.search(r"Mfg Part #:\s*(?P<mfg>[^\n]*)", block)
        upc = re.search(r"UPC:\s*(?P<upc>\d{12,13})", block)
        unit = re.search(r"Your Price:\s*\$(?P<price>[\d.,]+)", block)
        list_price = re.search(r"List Price\s*\$?(?P<price>[\d.,]+)", block)
        name_part = block
        if upc:
            name_part = block[upc.end():]
        name_part = re.split(r"Your Price:", name_part, maxsplit=1)[0]
        name = _clean_product_name(name_part)
        if not name:
            continue
        facts = {
            "supplier": "Central Pet / Phillips Pet",
            "mfg_part": (mfg.group("mfg").strip() if mfg else None),
            "sell_pack": _first_match(block, r"Sell Pk:\s*([^|]+)"),
            "case_qty": _first_match(block, r"Case Qty:\s*([^|]+)"),
            "pallet_qty": _first_match(block, r"Pallet Qty:\s*([^\n]+)"),
        }
        items.append(CatalogItem(
            supplier_code=code,
            name=name,
            barcode=upc.group("upc") if upc else None,
            mfg_part=facts["mfg_part"],
            unit_cost=_money(unit.group("price") if unit else None),
            list_price=_money(list_price.group("price") if list_price else None),
            source=source,
            page=_page_hint(block),
            facts={k: v for k, v in facts.items() if v},
        ))
    return items


def parse_reefh2o_catalog_text(text: str, *, source: str | None = None) -> list[CatalogItem]:
    items: list[CatalogItem] = []
    matches = list(re.finditer(r"Product Code:\s*(?P<code>\S+)", text))
    prev_end = 0
    for idx, match in enumerate(matches):
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        add_to_cart = re.search(r"ADD TO CART", text[match.end():next_start])
        block_end = (
            match.end() + add_to_cart.end()
            if add_to_cart else next_start
        )
        prefix = text[prev_end:match.start()]
        block = text[match.start():block_end]
        prev_end = block_end
        code = match.group("code").strip()
        upc = re.search(r"UPC\s*-\s*(?P<upc>\d{12,13})", block)
        list_price = re.search(r"List Price:\s*\$(?P<price>[\d.,]+)", block)
        member_price = re.search(r"Members Price:\s*\$(?P<price>[\d.,]+)", block)
        sale_price = re.search(r"Sale Price:\s*\$(?P<price>[\d.,]+)", block)
        map_price = re.search(r"MSP/MAP/MRP:\s*\$(?P<price>[\d.,]+)", block)
        name = _clean_product_name(prefix)
        if not name:
            continue
        facts = {
            "supplier": "Reef H2O",
            "member_price": _money(member_price.group("price") if member_price else None),
            "sale_price": _money(sale_price.group("price") if sale_price else None),
            "map_price": _money(map_price.group("price") if map_price else None),
            "stock": _first_match(block, r"(\d+\s+In Stock)"),
            "out_of_stock": "(Out of Stock)" in block,
            "no_free_freight": "No Free Freight" in block,
            "discontinued": "Discontinued by Manufacturer" in block,
        }
        items.append(CatalogItem(
            supplier_code=code,
            name=name,
            barcode=upc.group("upc") if upc else None,
            unit_cost=facts["member_price"] or facts["sale_price"] or _money(list_price.group("price") if list_price else None),
            list_price=_money(list_price.group("price") if list_price else None),
            source=source,
            page=_page_hint(prefix + block),
            facts={k: v for k, v in facts.items() if v not in (None, False)},
        ))
    return items


def _first_match(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def parse_supplier_catalog_text(text: str, *, source: str | None = None) -> list[CatalogItem]:
    if "Product #:" in text and "Mfg Part #:" in text:
        return parse_central_catalog_text(text, source=source)
    if "Product Code:" in text and "UPC -" in text:
        return parse_reefh2o_catalog_text(text, source=source)
    return parse_central_catalog_text(text, source=source) + parse_reefh2o_catalog_text(text, source=source)


def extract_pdf_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


async def upsert_supplier_catalog_items(
    session: AsyncSession,
    *,
    supplier_id: str,
    supplier_name: str | None,
    items: list[CatalogItem],
) -> int:
    count = 0
    now = datetime.utcnow()
    for item in items:
        if not item.supplier_code:
            continue
        row = (await session.execute(
            select(SupplierCatalogItem).where(
                SupplierCatalogItem.supplier_id == supplier_id,
                SupplierCatalogItem.supplier_code == item.supplier_code,
            )
        )).scalar_one_or_none()
        if not row:
            row = SupplierCatalogItem(
                supplier_id=supplier_id,
                supplier_name=supplier_name,
                supplier_code=item.supplier_code,
                status="needs_product",
                seen_count=0,
            )
            session.add(row)
        row.supplier_name = supplier_name or row.supplier_name
        row.description = item.name or row.description
        row.barcode = item.barcode or row.barcode
        row.mfg_part = item.mfg_part or row.mfg_part
        row.last_unit_cost = item.unit_cost if item.unit_cost is not None else row.last_unit_cost
        row.list_price = item.list_price if item.list_price is not None else row.list_price
        row.catalog_source = item.source or row.catalog_source
        row.catalog_page = item.page or row.catalog_page
        row.facts = item.facts or row.facts
        row.updated_at = now
        count += 1
    await session.flush()
    return count


def supplier_catalog_facts_text(item: SupplierCatalogItem | None) -> str | None:
    if not item:
        return None
    parts = []
    if item.description:
        parts.append(f"Catalog product name: {item.description}")
    if item.supplier_code:
        parts.append(f"Supplier product code: {item.supplier_code}")
    if item.mfg_part:
        parts.append(f"Manufacturer part number: {item.mfg_part}")
    if item.barcode:
        parts.append(f"UPC: {item.barcode}")
    if item.last_unit_cost is not None:
        parts.append(f"Catalog cost: ${item.last_unit_cost:.2f}")
    if item.list_price is not None:
        parts.append(f"Catalog list price: ${item.list_price:.2f}")
    for key, value in (item.facts or {}).items():
        label = key.replace("_", " ").title()
        parts.append(f"{label}: {value}")
    if item.catalog_source:
        parts.append(f"Catalog source: {item.catalog_source}")
    return "\n".join(parts) if parts else None


async def find_supplier_catalog_item(
    session: AsyncSession,
    *,
    supplier_id: str | None,
    supplier_code: str | None = None,
    barcode: str | None = None,
    product_name: str | None = None,
) -> SupplierCatalogItem | None:
    if not supplier_id:
        return None
    if supplier_code:
        row = (await session.execute(
            select(SupplierCatalogItem).where(
                SupplierCatalogItem.supplier_id == supplier_id,
                func.lower(SupplierCatalogItem.supplier_code) == supplier_code.strip().lower(),
            )
        )).scalar_one_or_none()
        if row:
            return row
    if barcode:
        digits = re.sub(r"\D", "", barcode)
        if UPC_RE.match(digits):
            row = (await session.execute(
                select(SupplierCatalogItem).where(
                    SupplierCatalogItem.supplier_id == supplier_id,
                    SupplierCatalogItem.barcode == digits,
                )
            )).scalar_one_or_none()
            if row:
                return row
    if product_name:
        q = normalize_text(product_name)
        rows = (await session.execute(
            select(SupplierCatalogItem)
            .where(SupplierCatalogItem.supplier_id == supplier_id)
            .where(SupplierCatalogItem.description.is_not(None))
            .limit(300)
        )).scalars().all()
        scored = [
            (SequenceMatcher(None, q, normalize_text(row.description)).ratio(), row)
            for row in rows
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        if scored and scored[0][0] >= 0.74:
            return scored[0][1]
    return None
