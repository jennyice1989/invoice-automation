"""Local catalog cache and supplier-item memory.

Lightspeed is still the source of truth, but invoice matching should not
depend on live API search semantics. This module syncs products into local
tables and provides deterministic lookup/search helpers.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.lightspeed import LightspeedClient

logger = logging.getLogger(__name__)


def normalize_text(s: str | None) -> str:
    if not s:
        return ""
    s = str(s).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_code(s: str | None) -> str:
    return (s or "").strip().lower()


def _barcode_value(value) -> str | None:
    if isinstance(value, list):
        return str(value[0]).strip() if value else None
    return str(value).strip() if value else None


def product_to_cache_fields(product: dict, synced_at: datetime) -> dict:
    brand = product.get("brand")
    brand_name = product.get("brand_name")
    if not brand_name and isinstance(brand, dict):
        brand_name = brand.get("name")
    return {
        "lightspeed_product_id": product["id"],
        "name": product.get("name"),
        "normalized_name": normalize_text(product.get("name")),
        "sku": product.get("sku"),
        "barcode": _barcode_value(product.get("barcode")),
        "supplier_code": product.get("supplier_code"),
        "supplier_id": product.get("supplier_id"),
        "brand_name": brand_name,
        "category_name": product.get("product_category_name")
        or product.get("category_name"),
        "supply_price": product.get("supply_price"),
        "retail_price": product.get("price_excluding_tax"),
        "active": product.get("active") is not False,
        "deleted_at": product.get("deleted_at"),
        "raw": product,
        "synced_at": synced_at,
        "updated_at": synced_at,
    }


def cache_product_to_dict(product: Any) -> dict:
    return {
        "id": product.lightspeed_product_id,
        "name": product.name,
        "sku": product.sku,
        "barcode": product.barcode,
        "supplier_code": product.supplier_code,
        "supplier_id": product.supplier_id,
        "brand_name": product.brand_name,
        "supply_price": product.supply_price,
        "price_excluding_tax": product.retail_price,
        "active": product.active,
        "deleted_at": product.deleted_at,
    }


def search_score(query: str, product: Any) -> float:
    q = normalize_text(query)
    if not q:
        return 0.0
    if not isinstance(product, dict):
        fields = [
            product.normalized_name or normalize_text(product.name),
            normalize_text(product.sku),
            normalize_text(product.supplier_code),
            normalize_text(product.barcode),
        ]
    else:
        fields = [
            normalize_text(product.get("name")),
            normalize_text(product.get("sku")),
            normalize_text(product.get("supplier_code")),
            normalize_text(product.get("barcode")),
        ]

    best = 0.0
    q_tokens = set(q.split())
    for field in fields:
        if not field:
            continue
        if field == q:
            best = max(best, 1.0)
        elif q in field:
            best = max(best, 0.92)
        elif field in q:
            best = max(best, 0.86)

        f_tokens = set(field.split())
        if q_tokens and f_tokens:
            best = max(best, len(q_tokens & f_tokens) / len(q_tokens | f_tokens))
        best = max(best, SequenceMatcher(None, q, field).ratio())
    return best


@dataclass
class CatalogSyncResult:
    total: int
    upserted: int
    synced_at: datetime
    deactivated: int = 0


def deactivate_missing_catalog_products(
    cached_products: list[Any],
    seen_ids: set[str],
    synced_at: datetime,
) -> int:
    """Mark cached products inactive when they disappeared from latest sync."""
    deactivated = 0
    for row in cached_products:
        if row.lightspeed_product_id in seen_ids:
            continue
        row.active = False
        row.deleted_at = row.deleted_at or "missing_from_latest_sync"
        row.synced_at = synced_at
        row.updated_at = synced_at
        deactivated += 1
    return deactivated


async def sync_lightspeed_catalog(
    session: AsyncSession,
    client: LightspeedClient,
) -> CatalogSyncResult:
    """Fetch all live Lightspeed products and upsert the local cache."""
    from app.db import CatalogProduct

    synced_at = datetime.utcnow()
    products = await client.list_products()
    upserted = 0
    seen_ids: set[str] = set()

    for product in products:
        if not product.get("id"):
            continue
        fields = product_to_cache_fields(product, synced_at)
        seen_ids.add(fields["lightspeed_product_id"])
        existing = (await session.execute(
            select(CatalogProduct).where(
                CatalogProduct.lightspeed_product_id == fields["lightspeed_product_id"]
            )
        )).scalar_one_or_none()
        if existing:
            for key, value in fields.items():
                setattr(existing, key, value)
        else:
            session.add(CatalogProduct(**fields))
        upserted += 1

    deactivated = 0
    if seen_ids:
        cached_products = (await session.execute(
            select(CatalogProduct).where(CatalogProduct.active.is_(True))
        )).scalars().all()
        deactivated = deactivate_missing_catalog_products(
            list(cached_products),
            seen_ids,
            synced_at,
        )

    await session.flush()
    return CatalogSyncResult(
        total=len(products),
        upserted=upserted,
        synced_at=synced_at,
        deactivated=deactivated,
    )


async def upsert_cached_product(
    session: AsyncSession,
    product: dict,
):
    from app.db import CatalogProduct

    if not product.get("id"):
        return None
    synced_at = datetime.utcnow()
    fields = product_to_cache_fields(product, synced_at)
    existing = (await session.execute(
        select(CatalogProduct).where(
            CatalogProduct.lightspeed_product_id == fields["lightspeed_product_id"]
        )
    )).scalar_one_or_none()
    if existing:
        for key, value in fields.items():
            setattr(existing, key, value)
        return existing
    row = CatalogProduct(**fields)
    session.add(row)
    return row


async def catalog_status(session: AsyncSession) -> dict:
    from app.db import CatalogProduct

    row = (await session.execute(
        select(func.count(CatalogProduct.id), func.max(CatalogProduct.synced_at))
    )).one()
    return {"product_count": row[0] or 0, "last_synced_at": row[1].isoformat() if row[1] else None}


async def get_cached_products(session: AsyncSession) -> list[dict]:
    from app.db import CatalogProduct

    rows = (await session.execute(
        select(CatalogProduct).where(CatalogProduct.active.is_(True))
    )).scalars().all()
    return [cache_product_to_dict(row) for row in rows]


async def find_cached_product_by_code(
    session: AsyncSession,
    code: str | None,
    *,
    fields: tuple[str, ...] = ("barcode", "sku", "supplier_code"),
) -> dict | None:
    from app.db import CatalogProduct

    needle = _norm_code(code)
    if not needle:
        return None

    clauses = []
    if "barcode" in fields:
        clauses.append(func.lower(CatalogProduct.barcode) == needle)
    if "sku" in fields:
        clauses.append(func.lower(CatalogProduct.sku) == needle)
    if "supplier_code" in fields:
        clauses.append(func.lower(CatalogProduct.supplier_code) == needle)
    if not clauses:
        return None

    product = (await session.execute(
        select(CatalogProduct)
        .where(CatalogProduct.active.is_(True))
        .where(or_(*clauses))
        .limit(1)
    )).scalar_one_or_none()
    return cache_product_to_dict(product) if product else None


async def find_cached_product_by_id(
    session: AsyncSession,
    lightspeed_product_id: str | None,
) -> dict | None:
    from app.db import CatalogProduct

    if not lightspeed_product_id:
        return None
    product = (await session.execute(
        select(CatalogProduct).where(
            CatalogProduct.lightspeed_product_id == lightspeed_product_id
        )
    )).scalar_one_or_none()
    return cache_product_to_dict(product) if product else None


async def search_cached_products(
    session: AsyncSession,
    query: str,
    *,
    limit: int = 20,
) -> list[dict]:
    from app.db import CatalogProduct

    q = (query or "").strip()
    if not q:
        return []
    rows = (await session.execute(
        select(CatalogProduct).where(CatalogProduct.active.is_(True))
    )).scalars().all()
    scored = [(search_score(q, row), row) for row in rows]
    scored = [(score, row) for score, row in scored if score >= 0.25]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [cache_product_to_dict(row) for _, row in scored[:limit]]


async def remember_supplier_item(
    session: AsyncSession,
    *,
    supplier_id: str | None,
    supplier_name: str | None,
    supplier_code: str | None,
    description: str | None,
    barcode: str | None,
    unit_cost: float | None,
    lightspeed_product_id: str | None = None,
    status: str | None = None,
):
    from app.db import SupplierCatalogItem

    if not supplier_id or not supplier_code:
        return None
    now = datetime.utcnow()
    row = (await session.execute(
        select(SupplierCatalogItem).where(
            SupplierCatalogItem.supplier_id == supplier_id,
            SupplierCatalogItem.supplier_code == supplier_code,
        )
    )).scalar_one_or_none()
    resolved_status = status or ("linked" if lightspeed_product_id else "needs_product")
    if row:
        row.supplier_name = supplier_name or row.supplier_name
        row.description = description or row.description
        row.barcode = barcode or row.barcode
        row.last_unit_cost = unit_cost if unit_cost is not None else row.last_unit_cost
        row.lightspeed_product_id = lightspeed_product_id or row.lightspeed_product_id
        row.status = resolved_status if lightspeed_product_id or status else row.status
        row.seen_count = (row.seen_count or 0) + 1
        row.last_seen_at = now
        row.updated_at = now
        return row

    row = SupplierCatalogItem(
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        supplier_code=supplier_code,
        description=description,
        barcode=barcode,
        lightspeed_product_id=lightspeed_product_id,
        status=resolved_status,
        last_unit_cost=unit_cost,
        seen_count=1,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    return row


async def find_linked_supplier_item(
    session: AsyncSession,
    *,
    supplier_id: str,
    supplier_code: str | None,
):
    from app.db import SupplierCatalogItem

    if not supplier_code:
        return None
    return (await session.execute(
        select(SupplierCatalogItem).where(
            SupplierCatalogItem.supplier_id == supplier_id,
            SupplierCatalogItem.supplier_code == supplier_code,
            SupplierCatalogItem.lightspeed_product_id.is_not(None),
        )
    )).scalar_one_or_none()
