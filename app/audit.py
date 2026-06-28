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
CUSTOM_SKU_PREFIX = "CUSTOM"


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


def _looks_like_image_key(key: str) -> bool:
    key = key.lower()
    return any(token in key for token in ("image", "photo", "media", "thumbnail"))


def _image_value_present(value) -> bool:
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, str):
        text = value.strip()
        return bool(text and text.lower() not in {"none", "null"})
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, list):
        return any(_image_value_present(item) for item in value)
    if isinstance(value, dict):
        for key, child in value.items():
            if _looks_like_image_key(str(key)) or str(key).lower() in {
                "id", "url", "src", "href", "original", "standard", "thumb",
                "thumbnail", "filename", "file_name",
            }:
                if _image_value_present(child):
                    return True
        return False
    return False


def _raw_has_image(raw: dict | None) -> bool:
    if not raw:
        return False
    for key, value in raw.items():
        if _looks_like_image_key(str(key)) and _image_value_present(value):
            return True
    return False


def target_price_for_cost(cost: float | None) -> float | None:
    if cost is None or cost <= 0:
        return None
    return _round(cost * TARGET_MARGIN_MULTIPLIER, TARGET_ROUNDING)


def custom_sku_for_product(product: CatalogProduct) -> str:
    source = product.lightspeed_product_id or product.name or "PRODUCT"
    token = re.sub(r"[^A-Za-z0-9]", "", source).upper()[:10]
    return f"{CUSTOM_SKU_PREFIX}-{token or 'PRODUCT'}"


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
    if not product.barcode:
        issues.append(AuditIssue(
            "missing_barcode", "Missing barcode", "medium",
        ))
    if not product.sku:
        issues.append(AuditIssue(
            "missing_sku", "Missing SKU", "medium",
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
        "suggested_custom_sku": custom_sku_for_product(product) if not product.sku else None,
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


def audit_item_matches_filter(
    item: dict[str, Any],
    *,
    issue: str | None = None,
    query: str | None = None,
) -> bool:
    codes = {i["code"] for i in item.get("issues", [])}
    if issue and issue != "all" and issue not in codes:
        return False
    q = (query or "").strip().lower()
    if q:
        haystack = " ".join(
            str(v or "") for v in (
                item.get("name"), item.get("sku"), item.get("barcode"),
                item.get("supplier_code"), item.get("brand_name"),
            )
        ).lower()
        if q not in haystack:
            return False
    return True


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
        "missing_barcode": 0,
        "missing_sku": 0,
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

        if not audit_item_matches_filter(item, issue=issue, query=query):
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
