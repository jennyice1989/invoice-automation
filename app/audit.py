"""Catalog audit helpers for existing Lightspeed products."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import CatalogProduct
from app.pricing import _round


TARGET_MARGIN_MULTIPLIER = 1.5
TARGET_ROUNDING = "cents_49_99"


@dataclass
class AuditIssue:
    code: str
    label: str
    severity: str


def _plain_text(html: str | None) -> str:
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(html))
    return re.sub(r"\s+", " ", text).strip()


def _raw_description(raw: dict | None) -> str | None:
    if not raw:
        return None
    for key in ("description", "description_html", "short_description"):
        value = raw.get(key)
        if value:
            return str(value)
    return None


def _raw_has_image(raw: dict | None) -> bool:
    if not raw:
        return False
    for key in ("image_url", "image_thumbnail_url", "thumbnail_url"):
        if raw.get(key):
            return True
    for key in ("images", "image"):
        value = raw.get(key)
        if isinstance(value, list) and value:
            return True
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def target_price_for_cost(cost: float | None) -> float | None:
    if cost is None or cost <= 0:
        return None
    return _round(cost * TARGET_MARGIN_MULTIPLIER, TARGET_ROUNDING)


def audit_product(product: CatalogProduct) -> dict[str, Any]:
    raw = product.raw or {}
    description = _raw_description(raw)
    description_text = _plain_text(description)
    current_price = product.retail_price
    target_price = target_price_for_cost(product.supply_price)
    issues: list[AuditIssue] = []

    if not description_text:
        issues.append(AuditIssue(
            "missing_description", "Missing description", "high",
        ))
    elif len(description_text) < 120:
        issues.append(AuditIssue(
            "weak_description", "Short or weak description", "medium",
        ))

    if not _raw_has_image(raw):
        issues.append(AuditIssue(
            "missing_photo", "Missing product photo", "medium",
        ))

    if target_price is not None:
        if current_price is None:
            issues.append(AuditIssue(
                "missing_price", "Missing retail price", "high",
            ))
        elif current_price + 0.005 < target_price:
            issues.append(AuditIssue(
                "below_target_margin", "Retail below 1.5x cost target", "high",
            ))

    if not product.barcode and not product.sku:
        issues.append(AuditIssue(
            "missing_barcode_sku", "Missing barcode/SKU", "medium",
        ))
    if not product.brand_name:
        issues.append(AuditIssue(
            "missing_brand", "Missing brand", "low",
        ))
    if not product.category_name:
        issues.append(AuditIssue(
            "missing_category", "Missing category", "low",
        ))

    severity_order = {"high": 3, "medium": 2, "low": 1}
    issues.sort(key=lambda issue: severity_order.get(issue.severity, 0), reverse=True)

    return {
        "id": product.lightspeed_product_id,
        "name": product.name,
        "sku": product.sku,
        "barcode": product.barcode,
        "supplier_code": product.supplier_code,
        "brand_name": product.brand_name,
        "category_name": product.category_name,
        "supply_price": product.supply_price,
        "retail_price": product.retail_price,
        "target_price": target_price,
        "description": description,
        "description_text_length": len(description_text),
        "has_image": _raw_has_image(raw),
        "issues": [issue.__dict__ for issue in issues],
        "issue_count": len(issues),
    }


async def audit_catalog(
    session: AsyncSession,
    *,
    issue: str | None = None,
    query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    rows = (await session.execute(
        select(CatalogProduct)
        .where(CatalogProduct.active.is_(True))
        .order_by(CatalogProduct.name.asc())
    )).scalars().all()

    q = (query or "").strip().lower()
    audited = []
    summary = {
        "products": 0,
        "with_issues": 0,
        "missing_description": 0,
        "weak_description": 0,
        "missing_photo": 0,
        "below_target_margin": 0,
        "missing_price": 0,
        "missing_barcode_sku": 0,
        "missing_brand": 0,
        "missing_category": 0,
    }

    for row in rows:
        item = audit_product(row)
        summary["products"] += 1
        codes = {i["code"] for i in item["issues"]}
        for code in codes:
            if code in summary:
                summary[code] += 1
        if item["issues"]:
            summary["with_issues"] += 1

        if issue and issue != "all" and issue not in codes:
            continue
        if q:
            haystack = " ".join(
                str(v or "") for v in (
                    item["name"], item["sku"], item["barcode"],
                    item["supplier_code"], item["brand_name"],
                )
            ).lower()
            if q not in haystack:
                continue
        audited.append(item)

    audited.sort(key=lambda item: (-item["issue_count"], item["name"] or ""))
    return {
        "summary": summary,
        "total": len(audited),
        "data": audited[offset:offset + limit],
        "limit": limit,
        "offset": offset,
    }
