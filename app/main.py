"""
FastAPI service: invoice upload -> extract -> match -> price -> review -> push.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, AsyncIterator

from fastapi import (
    BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request,
    UploadFile, status,
)
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    APP_PASSWORD, COOKIE_MAX_AGE, COOKIE_NAME, check_password,
    make_token, require_auth, require_auth_html,
)
from app.audit import (
    CUSTOM_SKU_PREFIX, audit_catalog, audit_product, custom_sku_for_product,
    target_price_for_cost,
)
from app.catalog import (
    catalog_status,
    find_cached_product_by_id,
    remember_supplier_item,
    search_cached_products,
    sync_lightspeed_catalog,
    upsert_cached_product,
)
from app.db import (
    CatalogProduct, EnrichmentDraft, Invoice, InvoiceLine, LabelReprintQueue,
    PricingRule,
    SupplierCatalogItem, SupplierMsrp, SupplierSkuMapping,
    find_existing_invoice, init_db, session_scope, upsert_mapping,
)
from app.enrichment import (
    EnrichmentError, enrich_batch, enrich_product,
)
from app.extraction import ExtractionError, extract_invoice_from_pdf
from app.lightspeed import (
    LightspeedAuthError, LightspeedClient, LightspeedError,
    LightspeedNotFoundError, MatchedLineItem,
)
from app.matching import MatchingService, RawInvoiceLine
from app.price_updates import float_or_none, retail_update_decision
from app.pricing import PricingResult, price_line
from app.supplier_catalog import (
    extract_pdf_text,
    find_supplier_catalog_item,
    parse_supplier_catalog_text,
    supplier_catalog_facts_text,
    upsert_supplier_catalog_items,
)
from app.ui import (
    LOGIN_HTML, INDEX_HTML, HISTORY_HTML, REVIEW_HTML, SETTINGS_HTML,
    ENRICH_HTML, ENRICH_REVIEW_HTML, ADMIN_HTML, AUDIT_HTML,
)
from app.upc_lookup import UpcLookupResult, lookup_upc_for_product

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LIGHTSPEED_DOMAIN_PREFIX = os.environ.get("LIGHTSPEED_DOMAIN_PREFIX", "")
LIGHTSPEED_TOKEN = os.environ.get("LIGHTSPEED_PERSONAL_TOKEN", "")
DEFAULT_OUTLET_ID = os.environ.get("LIGHTSPEED_DEFAULT_OUTLET_ID", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if LIGHTSPEED_DOMAIN_PREFIX and LIGHTSPEED_TOKEN:
        client = LightspeedClient(LIGHTSPEED_DOMAIN_PREFIX, LIGHTSPEED_TOKEN)
        app.state.lightspeed = client
    else:
        logger.warning("Lightspeed credentials missing")
        app.state.lightspeed = None

    if DATABASE_URL:
        try:
            await init_db()
            logger.info("Database initialized")
        except Exception as exc:
            logger.error("DB init failed: %s", exc)
    else:
        logger.warning("DATABASE_URL not set")

    try:
        yield
    finally:
        if app.state.lightspeed:
            await app.state.lightspeed.close()


app = FastAPI(title="Invoice Importer", version="1.0.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def _log_unhandled(request: Request, exc: Exception):
    """Ensure every unhandled exception is logged with a full traceback.
    FastAPI's default logging can swallow these on some configurations."""
    import traceback
    from fastapi.responses import JSONResponse
    tb = traceback.format_exc()
    logger.error(
        "Unhandled exception on %s %s:\n%s",
        request.method, request.url.path, tb,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"{type(exc).__name__}: {exc}",
            "path": str(request.url.path),
        },
    )


def _client() -> LightspeedClient:
    client = getattr(app.state, "lightspeed", None)
    if client is None:
        raise HTTPException(503, "Lightspeed not configured")
    return client


async def _session() -> AsyncIterator[AsyncSession]:
    async with session_scope() as session:
        yield session


# --------------------------------------------------------------------- #
# Auth                                                                  #
# --------------------------------------------------------------------- #

@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str | None = None) -> str:
    return LOGIN_HTML.replace("{{ERROR}}", error or "")


@app.post("/login")
async def login_post(password: str = Form(...)):
    if not check_password(password):
        return RedirectResponse(url="/login?error=Incorrect+password", status_code=303)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        COOKIE_NAME, make_token(),
        max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax",
        secure=True,
    )
    return response


@app.post("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# --------------------------------------------------------------------- #
# Health (unauthenticated)                                              #
# --------------------------------------------------------------------- #

@app.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "lightspeed_configured": bool(LIGHTSPEED_DOMAIN_PREFIX),
        "db_configured": bool(DATABASE_URL),
        "auth_configured": bool(APP_PASSWORD),
    }


# --------------------------------------------------------------------- #
# HTML pages                                                            #
# --------------------------------------------------------------------- #

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if redirect := require_auth_html(request.cookies.get(COOKIE_NAME)):
        return redirect
    return HTMLResponse(INDEX_HTML)


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    if redirect := require_auth_html(request.cookies.get(COOKIE_NAME)):
        return redirect
    return HTMLResponse(HISTORY_HTML)


@app.get("/review/{invoice_id}", response_class=HTMLResponse)
async def review_page(invoice_id: int, request: Request):
    if redirect := require_auth_html(request.cookies.get(COOKIE_NAME)):
        return redirect
    return HTMLResponse(REVIEW_HTML.replace("{{INVOICE_ID}}", str(invoice_id)))


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if redirect := require_auth_html(request.cookies.get(COOKIE_NAME)):
        return redirect
    return HTMLResponse(SETTINGS_HTML)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if redirect := require_auth_html(request.cookies.get(COOKIE_NAME)):
        return redirect
    return HTMLResponse(ADMIN_HTML)


@app.get("/enrich", response_class=HTMLResponse)
async def enrich_page(request: Request):
    if redirect := require_auth_html(request.cookies.get(COOKIE_NAME)):
        return redirect
    return HTMLResponse(ENRICH_HTML)


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request):
    if redirect := require_auth_html(request.cookies.get(COOKIE_NAME)):
        return redirect
    return HTMLResponse(AUDIT_HTML)


@app.get("/enrich/review/{batch_id}", response_class=HTMLResponse)
async def enrich_review_page(batch_id: str, request: Request):
    if redirect := require_auth_html(request.cookies.get(COOKIE_NAME)):
        return redirect
    return HTMLResponse(ENRICH_REVIEW_HTML.replace("{{BATCH_ID}}", batch_id))


# --------------------------------------------------------------------- #
# Discovery                                                             #
# --------------------------------------------------------------------- #

@app.get("/outlets", dependencies=[Depends(require_auth)])
async def list_outlets():
    try:
        outlets = await _client().list_outlets()
    except LightspeedError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"data": [{"id": o["id"], "name": o.get("name")} for o in outlets]}


@app.get("/suppliers", dependencies=[Depends(require_auth)])
async def list_suppliers():
    try:
        suppliers = await _client().list_suppliers()
    except LightspeedError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"data": [{"id": s["id"], "name": s.get("name")} for s in suppliers]}


@app.get("/categories", dependencies=[Depends(require_auth)])
async def list_categories_endpoint():
    """List product categories as full-path leaf names like
    'Freshwater Fish / Cichlids'. Only leaf categories are returned —
    products shouldn't be assigned to parent categories."""
    try:
        cats = await _client().list_categories()
    except LightspeedError as exc:
        raise HTTPException(502, str(exc)) from exc
    items = []
    for c in cats:
        if not isinstance(c, dict):
            logger.warning("Skipping non-dict category: %r", c)
            continue
        if c.get("leaf_category") is False:
            continue
        path = c.get("category_path") or []
        if isinstance(path, list):
            full = " / ".join(
                p.get("name", "") for p in path
                if isinstance(p, dict) and p.get("name")
            )
        else:
            full = ""
        if not full:
            full = c.get("name") or ""
        items.append({"id": c["id"], "name": c.get("name"), "full_name": full})
    items.sort(key=lambda x: x["full_name"].lower())
    return {"data": items}


@app.get("/brands", dependencies=[Depends(require_auth)])
async def list_brands_endpoint():
    try:
        brands = await _client().list_brands()
    except LightspeedError as exc:
        raise HTTPException(502, str(exc)) from exc
    items = [{"id": b["id"], "name": b.get("name")} for b in brands if b.get("name")]
    items.sort(key=lambda x: (x["name"] or "").lower())
    return {"data": items}


def _category_full_name(c: dict) -> str:
    path = c.get("category_path") or []
    if isinstance(path, list) and path:
        full = " / ".join(
            p.get("name", "") for p in path
            if isinstance(p, dict) and p.get("name")
        )
        if full:
            return full
    return c.get("name") or ""


@app.get("/catalog/status", dependencies=[Depends(require_auth)])
async def get_catalog_status(session: AsyncSession = Depends(_session)):
    return await catalog_status(session)


@app.post("/catalog/sync", dependencies=[Depends(require_auth)])
async def sync_catalog(session: AsyncSession = Depends(_session)):
    try:
        result = await sync_lightspeed_catalog(session, _client())
    except LightspeedError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {
        "ok": True,
        "product_count": result.total,
        "upserted": result.upserted,
        "synced_at": result.synced_at.isoformat(),
    }


# --------------------------------------------------------------------- #
# Admin tools                                                           #
# --------------------------------------------------------------------- #

@app.get("/admin/status", dependencies=[Depends(require_auth)])
async def admin_status(session: AsyncSession = Depends(_session)):
    catalog = await catalog_status(session)
    supplier_item_count = (await session.execute(
        select(func.count(SupplierCatalogItem.id))
    )).scalar_one()
    mapping_count = (await session.execute(
        select(func.count(SupplierSkuMapping.id))
    )).scalar_one()
    failed_invoice_count = (await session.execute(
        select(func.count(Invoice.id)).where(
            or_(Invoice.status == "FAILED", Invoice.error.is_not(None))
        )
    )).scalar_one()
    return {
        "catalog": catalog,
        "supplier_item_count": supplier_item_count or 0,
        "mapping_count": mapping_count or 0,
        "failed_invoice_count": failed_invoice_count or 0,
    }


@app.get("/admin/errors", dependencies=[Depends(require_auth)])
async def admin_errors(session: AsyncSession = Depends(_session), limit: int = 20):
    rows = (await session.execute(
        select(Invoice)
        .where(or_(Invoice.status == "FAILED", Invoice.error.is_not(None)))
        .order_by(Invoice.created_at.desc())
        .limit(min(max(limit, 1), 100))
    )).scalars().all()
    return {"data": [{
        "id": r.id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "supplier_name": r.supplier_name,
        "supplier_invoice_number": r.supplier_invoice_number,
        "status": r.status,
        "error": r.error,
    } for r in rows]}


@app.get("/admin/label-reprints", dependencies=[Depends(require_auth)])
async def admin_label_reprints(
    status: str = "pending",
    limit: int = 100,
    session: AsyncSession = Depends(_session),
):
    limit = min(max(limit, 1), 500)
    query = select(LabelReprintQueue).order_by(LabelReprintQueue.created_at.desc())
    if status != "all":
        query = query.where(LabelReprintQueue.status == status)
    rows = (await session.execute(query.limit(limit))).scalars().all()
    return {"data": [_label_reprint_to_dict(row) for row in rows]}


@app.get("/admin/label-reprints.csv", dependencies=[Depends(require_auth)])
async def export_label_reprints_csv(
    status: str = "pending",
    session: AsyncSession = Depends(_session),
):
    query = select(LabelReprintQueue).order_by(LabelReprintQueue.created_at.desc())
    if status != "all":
        query = query.where(LabelReprintQueue.status == status)
    rows = (await session.execute(query)).scalars().all()

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "created_at", "product_name", "sku", "barcode", "supplier_code",
        "old_price", "new_price", "lightspeed_product_id", "status",
    ])
    for row in rows:
        writer.writerow([
            row.created_at.isoformat() if row.created_at else "",
            row.product_name or "",
            row.sku or "",
            row.barcode or "",
            row.supplier_code or "",
            "" if row.old_price is None else f"{row.old_price:.2f}",
            f"{row.new_price:.2f}",
            row.lightspeed_product_id,
            row.status,
        ])
    return Response(
        content=out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="label-reprints.csv"'},
    )


@app.post("/admin/label-reprints/mark-printed", dependencies=[Depends(require_auth)])
async def mark_label_reprints_printed(
    body: dict,
    session: AsyncSession = Depends(_session),
):
    ids = [int(i) for i in body.get("ids", []) if str(i).isdigit()]
    if not ids:
        raise HTTPException(400, "Select at least one label row")
    rows = (await session.execute(
        select(LabelReprintQueue).where(LabelReprintQueue.id.in_(ids))
    )).scalars().all()
    now = datetime.utcnow()
    for row in rows:
        row.status = "printed"
        row.printed_at = now
    await session.flush()
    return {"ok": True, "updated": len(rows)}


def _label_reprint_to_dict(row: LabelReprintQueue) -> dict:
    return {
        "id": row.id,
        "lightspeed_product_id": row.lightspeed_product_id,
        "product_name": row.product_name,
        "sku": row.sku,
        "barcode": row.barcode,
        "supplier_code": row.supplier_code,
        "old_price": row.old_price,
        "new_price": row.new_price,
        "source": row.source,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "printed_at": row.printed_at.isoformat() if row.printed_at else None,
    }


def _is_generated_sku(sku: str | None) -> bool:
    return bool((sku or "").strip().upper().startswith(f"{CUSTOM_SKU_PREFIX}-"))


def _generated_sku_to_dict(row: CatalogProduct) -> dict:
    return {
        "id": row.lightspeed_product_id,
        "name": row.name,
        "sku": row.sku,
        "barcode": row.barcode,
        "supplier_code": row.supplier_code,
        "brand_name": row.brand_name,
        "category_name": row.category_name,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@app.get("/admin/generated-skus", dependencies=[Depends(require_auth)])
async def admin_generated_skus(
    q: str = "",
    limit: int = 100,
    session: AsyncSession = Depends(_session),
):
    limit = min(max(limit, 1), 500)
    query = (
        select(CatalogProduct)
        .where(CatalogProduct.active.is_(True))
        .where(func.lower(CatalogProduct.sku).like(f"{CUSTOM_SKU_PREFIX.lower()}-%"))
        .order_by(CatalogProduct.name.asc())
    )
    needle = q.strip().lower()
    if needle:
        like = f"%{needle}%"
        query = query.where(or_(
            func.lower(CatalogProduct.name).like(like),
            func.lower(CatalogProduct.sku).like(like),
            func.lower(CatalogProduct.barcode).like(like),
            func.lower(CatalogProduct.supplier_code).like(like),
        ))
    rows = (await session.execute(query.limit(limit))).scalars().all()
    return {"data": [_generated_sku_to_dict(row) for row in rows]}


@app.get("/admin/generated-skus.csv", dependencies=[Depends(require_auth)])
async def export_generated_skus_csv(
    session: AsyncSession = Depends(_session),
):
    rows = (await session.execute(
        select(CatalogProduct)
        .where(CatalogProduct.active.is_(True))
        .where(func.lower(CatalogProduct.sku).like(f"{CUSTOM_SKU_PREFIX.lower()}-%"))
        .order_by(CatalogProduct.name.asc())
    )).scalars().all()

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "product_name", "generated_sku", "current_barcode", "supplier_code",
        "brand", "category", "lightspeed_product_id",
    ])
    for row in rows:
        writer.writerow([
            row.name or "",
            row.sku or "",
            row.barcode or "",
            row.supplier_code or "",
            row.brand_name or "",
            row.category_name or "",
            row.lightspeed_product_id,
        ])
    return Response(
        content=out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="generated-skus.csv"'},
    )


@app.post("/admin/generated-skus/{product_id}/update", dependencies=[Depends(require_auth)])
async def update_generated_sku(
    product_id: str,
    body: dict,
    session: AsyncSession = Depends(_session),
):
    real_sku = str(body.get("sku") or "").strip()
    if not real_sku:
        raise HTTPException(400, "Enter the real barcode/SKU")
    product = (await session.execute(
        select(CatalogProduct).where(CatalogProduct.lightspeed_product_id == product_id)
    )).scalar_one_or_none()
    if not product:
        raise HTTPException(404, "Product not found in local catalog cache")
    if not _is_generated_sku(product.sku):
        raise HTTPException(400, "Product does not have a generated CUSTOM- SKU")

    try:
        updated = await _client().update_product(
            product_id,
            sku=real_sku,
            barcode=real_sku,
        )
    except LightspeedError as exc:
        raise HTTPException(502, f"Lightspeed update failed: {exc}") from exc
    if not updated:
        raise HTTPException(404, "Lightspeed could not find this product for updates")
    partial_update = updated.get("_partial_update") if isinstance(updated, dict) else None
    if partial_update:
        product.sku = real_sku
        product.barcode = real_sku
        raw = dict(product.raw or {})
        raw.update({"sku": real_sku, "barcode": real_sku})
        product.raw = raw
        product.updated_at = datetime.utcnow()
    else:
        await upsert_cached_product(session, updated)
    refreshed = (await session.execute(
        select(CatalogProduct).where(CatalogProduct.lightspeed_product_id == product_id)
    )).scalar_one_or_none()
    return {"ok": True, "product": _generated_sku_to_dict(refreshed or product)}


@app.get("/admin/supplier-items", dependencies=[Depends(require_auth)])
async def admin_supplier_items(
    q: str = "",
    session: AsyncSession = Depends(_session),
    limit: int = 50,
):
    stmt = select(SupplierCatalogItem).order_by(
        SupplierCatalogItem.updated_at.desc()
    )
    q = (q or "").strip()
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(
            func.lower(SupplierCatalogItem.supplier_name).like(like),
            func.lower(SupplierCatalogItem.supplier_code).like(like),
            func.lower(SupplierCatalogItem.description).like(like),
            func.lower(SupplierCatalogItem.lightspeed_product_id).like(like),
            func.lower(SupplierCatalogItem.status).like(like),
        ))
    rows = (await session.execute(
        stmt.limit(min(max(limit, 1), 200))
    )).scalars().all()
    return {"data": [{
        "id": r.id,
        "supplier_id": r.supplier_id,
        "supplier_name": r.supplier_name,
        "supplier_code": r.supplier_code,
        "description": r.description,
        "barcode": r.barcode,
        "mfg_part": r.mfg_part,
        "list_price": r.list_price,
        "catalog_source": r.catalog_source,
        "catalog_page": r.catalog_page,
        "facts": r.facts or {},
        "lightspeed_product_id": r.lightspeed_product_id,
        "status": r.status,
        "last_unit_cost": r.last_unit_cost,
        "seen_count": r.seen_count,
        "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    } for r in rows]}


@app.post("/admin/supplier-items/{item_id}/unlink", dependencies=[Depends(require_auth)])
async def admin_unlink_supplier_item(
    item_id: int,
    session: AsyncSession = Depends(_session),
):
    row = (await session.execute(
        select(SupplierCatalogItem).where(SupplierCatalogItem.id == item_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Supplier item not found")
    row.lightspeed_product_id = None
    row.status = "needs_product"
    row.updated_at = datetime.utcnow()
    return {"ok": True}


@app.post("/admin/catalog/upload", dependencies=[Depends(require_auth)])
async def admin_upload_supplier_catalog(
    supplier_id: Annotated[str, Form(...)],
    files: Annotated[list[UploadFile], File(...)],
    session: AsyncSession = Depends(_session),
):
    if not files:
        raise HTTPException(400, "Upload at least one PDF")
    supplier_name = None
    try:
        suppliers = await _client().list_suppliers()
        supplier = next((s for s in suppliers if s.get("id") == supplier_id), None)
        supplier_name = (supplier or {}).get("name")
    except Exception as exc:
        logger.warning("Could not resolve supplier name for catalog import: %s", exc)

    imported = 0
    parsed_files = []
    errors = []
    for file in files:
        filename = file.filename or "catalog.pdf"
        if not filename.lower().endswith(".pdf"):
            errors.append(f"{filename}: skipped because it is not a PDF")
            continue
        try:
            content = await file.read()
            text = extract_pdf_text(content)
            items = parse_supplier_catalog_text(text, source=filename)
            added = await upsert_supplier_catalog_items(
                session,
                supplier_id=supplier_id,
                supplier_name=supplier_name,
                items=items,
            )
            imported += added
            parsed_files.append({"filename": filename, "items": added})
        except Exception as exc:
            logger.exception("Supplier catalog import failed for %s", filename)
            errors.append(f"{filename}: {exc}")

    return {
        "ok": True,
        "imported": imported,
        "files": parsed_files,
        "errors": errors,
    }


@app.get("/admin/mappings", dependencies=[Depends(require_auth)])
async def admin_mappings(
    q: str = "",
    session: AsyncSession = Depends(_session),
    limit: int = 50,
):
    stmt = select(SupplierSkuMapping).order_by(
        SupplierSkuMapping.updated_at.desc()
    )
    q = (q or "").strip()
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(
            func.lower(SupplierSkuMapping.supplier_id).like(like),
            func.lower(SupplierSkuMapping.supplier_code).like(like),
            func.lower(SupplierSkuMapping.lightspeed_product_id).like(like),
            func.lower(SupplierSkuMapping.lightspeed_sku).like(like),
            func.lower(SupplierSkuMapping.product_name).like(like),
        ))
    rows = (await session.execute(
        stmt.limit(min(max(limit, 1), 200))
    )).scalars().all()
    return {"data": [{
        "id": r.id,
        "supplier_id": r.supplier_id,
        "supplier_code": r.supplier_code,
        "lightspeed_product_id": r.lightspeed_product_id,
        "lightspeed_sku": r.lightspeed_sku,
        "product_name": r.product_name,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    } for r in rows]}


@app.delete("/admin/mappings/{mapping_id}", dependencies=[Depends(require_auth)])
async def admin_delete_mapping(
    mapping_id: int,
    session: AsyncSession = Depends(_session),
):
    row = (await session.execute(
        select(SupplierSkuMapping).where(SupplierSkuMapping.id == mapping_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Mapping not found")
    await session.delete(row)
    return {"ok": True}


# --------------------------------------------------------------------- #
# Catalog audit                                                         #
# --------------------------------------------------------------------- #

@app.get("/audit/products", dependencies=[Depends(require_auth)])
async def list_audit_products(
    issue: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(_session),
):
    limit = max(1, min(limit, 250))
    offset = max(0, offset)
    status_info = await catalog_status(session)
    if not status_info["product_count"]:
        try:
            await sync_lightspeed_catalog(session, _client())
        except LightspeedError as exc:
            raise HTTPException(502, str(exc)) from exc
    return await audit_catalog(
        session, issue=issue, query=q, limit=limit, offset=offset,
    )


@app.post("/audit/sync", dependencies=[Depends(require_auth)])
async def sync_audit_catalog(session: AsyncSession = Depends(_session)):
    try:
        result = await sync_lightspeed_catalog(session, _client())
    except LightspeedError as exc:
        raise HTTPException(502, str(exc)) from exc
    audit = await audit_catalog(session, limit=1)
    return {
        "ok": True,
        "product_count": result.total,
        "upserted": result.upserted,
        "synced_at": result.synced_at.isoformat(),
        "summary": audit["summary"],
    }


class AuditApplyRequest(BaseModel):
    approve_price: bool = False
    retail_price: float | None = None
    approve_description: bool = False
    description: str | None = None
    approve_sku: bool = False
    custom_sku: str | None = None
    approve_barcode_sku: bool = False
    barcode_sku: str | None = None


class AuditBulkDraftRequest(BaseModel):
    product_ids: list[str] = Field(default_factory=list)


class AuditBulkApplyItem(AuditApplyRequest):
    product_id: str


class AuditBulkApplyRequest(BaseModel):
    updates: list[AuditBulkApplyItem] = Field(default_factory=list)


def _label_reprint_from_price_change(
    product: CatalogProduct,
    *,
    product_id: str,
    old_price: float | None,
    new_price: float | None,
) -> LabelReprintQueue | None:
    if new_price is None:
        return None
    if old_price is not None and abs(float(old_price) - float(new_price)) < 0.005:
        return None
    return LabelReprintQueue(
        lightspeed_product_id=product_id,
        product_name=product.name,
        sku=product.sku,
        barcode=product.barcode,
        supplier_code=product.supplier_code,
        old_price=old_price,
        new_price=float(new_price),
        source="audit_price_approval",
    )


async def _find_live_audit_product(
    client: LightspeedClient,
    session: AsyncSession,
    product: CatalogProduct,
) -> CatalogProduct | None:
    """Find the current live product when the cached id no longer updates."""
    candidates: list[dict | None] = []
    if product.barcode:
        candidates.append(await client.find_product_by_barcode(product.barcode))
    if product.sku:
        candidates.append(await client.find_product_by_sku(product.sku))
    if product.supplier_code:
        candidates.append(await client.find_product_by_supplier_code(product.supplier_code))
    if product.name:
        matches = await client.search_products(product.name, limit=10)
        normalized_name = (product.name or "").strip().lower()
        candidates.extend(
            m for m in matches
            if (m.get("name") or "").strip().lower() == normalized_name
        )

    for candidate in candidates:
        if not candidate or not candidate.get("id"):
            continue
        await upsert_cached_product(session, candidate)
        row = (await session.execute(
            select(CatalogProduct).where(
                CatalogProduct.lightspeed_product_id == candidate["id"]
            )
        )).scalar_one_or_none()
        if row:
            return row
    return None


async def _apply_audit_product_update(
    product_id: str,
    body: AuditApplyRequest,
    session: AsyncSession,
) -> dict:
    product = (await session.execute(
        select(CatalogProduct).where(CatalogProduct.lightspeed_product_id == product_id)
    )).scalar_one_or_none()
    if not product:
        raise HTTPException(404, "Product not found in local catalog cache")

    update: dict = {}
    if body.approve_price:
        price = body.retail_price
        if price is None:
            price = target_price_for_cost(product.supply_price)
        if price is None:
            raise HTTPException(400, "No approved retail price was provided")
        if product.retail_price is not None and price < product.retail_price:
            raise HTTPException(
                400,
                "Approved retail price is below current retail; this app will not lower prices",
            )
        update["retail_price"] = float(price)

    if body.approve_description:
        description = (body.description or "").strip()
        if not description:
            raise HTTPException(400, "Approved description is empty")
        update["description"] = description

    if body.approve_sku:
        custom_sku = (body.custom_sku or "").strip() or custom_sku_for_product(product)
        if not custom_sku:
            raise HTTPException(400, "Approved SKU is empty")
        if product.sku and product.sku.strip():
            raise HTTPException(400, "Product already has a SKU")
        update["sku"] = custom_sku

    if body.approve_barcode_sku:
        barcode_sku = (body.barcode_sku or "").strip()
        if not barcode_sku:
            raise HTTPException(400, "Approved barcode/SKU is empty")
        update["sku"] = barcode_sku
        update["barcode"] = barcode_sku

    if not update:
        raise HTTPException(400, "Nothing approved to update")

    old_retail_price = product.retail_price
    approved_retail_price = update.get("retail_price")
    try:
        client = _client()
        updated = await client.update_product(product_id, **update)
        if not updated:
            live_product = await _find_live_audit_product(client, session, product)
            if live_product and live_product.lightspeed_product_id != product_id:
                product = live_product
                product_id = live_product.lightspeed_product_id
                updated = await client.update_product(product_id, **update)
    except LightspeedError as exc:
        raise HTTPException(502, f"Lightspeed update failed: {exc}") from exc
    if not updated:
        product.active = False
        product.deleted_at = product.deleted_at or "update_not_found"
        product.updated_at = datetime.utcnow()
        await session.flush()
        return {
            "ok": True,
            "retired": True,
            "detail": (
                "Lightspeed could not find this product for updates, so it "
                "was removed from the local audit queue. It may be archived, "
                "deleted, or unavailable to the API token. Sync the catalog "
                "to refresh the audit list."
            ),
            "product_id": product_id,
            "product_name": product.name,
        }

    partial_update = updated.get("_partial_update") if isinstance(updated, dict) else None
    if partial_update:
        raw = dict(product.raw or {})
        raw.update(partial_update)
        product.raw = raw
        if "price_excluding_tax" in partial_update:
            product.retail_price = partial_update["price_excluding_tax"]
        if "supply_price" in partial_update:
            product.supply_price = partial_update["supply_price"]
        if "supplier_code" in partial_update:
            product.supplier_code = partial_update["supplier_code"]
        if "sku" in partial_update:
            product.sku = partial_update["sku"]
        if "barcode" in partial_update:
            product.barcode = partial_update["barcode"]
        product.updated_at = datetime.utcnow()
    else:
        await upsert_cached_product(session, updated)
    label_row = _label_reprint_from_price_change(
        product,
        product_id=product_id,
        old_price=old_retail_price,
        new_price=approved_retail_price,
    )
    if label_row:
        session.add(label_row)
    refreshed = (await session.execute(
        select(CatalogProduct).where(CatalogProduct.lightspeed_product_id == product_id)
    )).scalar_one_or_none()
    return {
        "ok": True,
        "product": audit_product(refreshed or product),
    }


@app.post("/audit/products/{product_id}/apply", dependencies=[Depends(require_auth)])
async def apply_audit_product_update(
    product_id: str,
    body: AuditApplyRequest,
    session: AsyncSession = Depends(_session),
):
    return await _apply_audit_product_update(product_id, body, session)


async def _draft_audit_product_description(
    product_id: str,
    session: AsyncSession,
) -> dict:
    product = (await session.execute(
        select(CatalogProduct).where(CatalogProduct.lightspeed_product_id == product_id)
    )).scalar_one_or_none()
    if not product:
        raise HTTPException(404, "Product not found in local catalog cache")

    try:
        result = await enrich_product(
            product.name or product.sku or product.lightspeed_product_id,
            supplier_code=product.supplier_code,
            barcode=product.barcode,
            supply_price=product.supply_price,
            available_categories=[],
            available_brands=[],
        )
    except EnrichmentError as exc:
        if "OPENAI_API_KEY" in str(exc):
            raise HTTPException(
                503,
                "OpenAI is not configured. Add OPENAI_API_KEY to the Render "
                "service environment variables, then redeploy or restart the service.",
            ) from exc
        raise HTTPException(502, f"OpenAI description draft failed: {exc}") from exc
    return {
        "ok": True,
        "product_id": product_id,
        "description": result.description_html,
        "cleaned_name": result.cleaned_name,
        "warnings": result.warnings,
    }


@app.post("/audit/products/{product_id}/draft-description", dependencies=[Depends(require_auth)])
async def draft_audit_description(
    product_id: str,
    session: AsyncSession = Depends(_session),
):
    return await _draft_audit_product_description(product_id, session)


@app.post("/audit/bulk/draft-descriptions", dependencies=[Depends(require_auth)])
async def bulk_draft_audit_descriptions(
    body: AuditBulkDraftRequest,
    session: AsyncSession = Depends(_session),
):
    product_ids = [pid for pid in dict.fromkeys(body.product_ids) if pid]
    if not product_ids:
        raise HTTPException(400, "Select at least one product")
    if len(product_ids) > 50:
        raise HTTPException(400, "Max 50 products per bulk draft")

    results = []
    for product_id in product_ids:
        try:
            result = await _draft_audit_product_description(product_id, session)
            results.append(result)
        except HTTPException as exc:
            results.append({
                "ok": False,
                "product_id": product_id,
                "error": exc.detail,
                "status_code": exc.status_code,
            })
    return {
        "ok": True,
        "requested": len(product_ids),
        "succeeded": sum(1 for item in results if item.get("ok")),
        "failed": sum(1 for item in results if not item.get("ok")),
        "results": results,
    }


@app.post("/audit/bulk/apply", dependencies=[Depends(require_auth)])
async def bulk_apply_audit_updates(
    body: AuditBulkApplyRequest,
    session: AsyncSession = Depends(_session),
):
    if not body.updates:
        raise HTTPException(400, "Select at least one product")
    if len(body.updates) > 50:
        raise HTTPException(400, "Max 50 products per bulk update")

    results = []
    seen_ids: set[str] = set()
    for item in body.updates:
        if not item.product_id or item.product_id in seen_ids:
            continue
        seen_ids.add(item.product_id)
        try:
            result = await _apply_audit_product_update(item.product_id, item, session)
            results.append({
                "ok": True,
                "product_id": item.product_id,
                "retired": bool(result.get("retired")),
                "detail": result.get("detail"),
            })
        except HTTPException as exc:
            results.append({
                "ok": False,
                "product_id": item.product_id,
                "error": exc.detail,
                "status_code": exc.status_code,
            })
    return {
        "ok": True,
        "requested": len(seen_ids),
        "succeeded": sum(1 for item in results if item.get("ok")),
        "failed": sum(1 for item in results if not item.get("ok")),
        "retired": sum(1 for item in results if item.get("retired")),
        "results": results,
    }


@app.post("/audit/products/{product_id}/image", dependencies=[Depends(require_auth)])
async def upload_audit_product_image(
    product_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(_session),
):
    product = (await session.execute(
        select(CatalogProduct).where(CatalogProduct.lightspeed_product_id == product_id)
    )).scalar_one_or_none()
    if not product:
        raise HTTPException(404, "Product not found in local catalog cache")

    content_type = file.content_type or ""
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(400, "Use a JPG, PNG, or WebP image")
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(400, "Empty image")
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(400, "Image is too large (max 10 MB)")

    try:
        await _client().upload_product_image(
            product_id,
            image_bytes=image_bytes,
            filename=file.filename or "product-image",
            content_type=content_type,
        )
    except LightspeedError as exc:
        raise HTTPException(502, f"Lightspeed image upload failed: {exc}") from exc

    try:
        products = await _client().search_products(product.name or product_id, limit=20)
        refreshed = next((p for p in products if p.get("id") == product_id), None)
        if refreshed:
            await upsert_cached_product(session, refreshed)
    except LightspeedError:
        pass
    return {"ok": True}


# --------------------------------------------------------------------- #
# Upload / process                                                      #
# --------------------------------------------------------------------- #

async def _run_pipeline(
    pdf_bytes: bytes,
    filename: str | None,
    session: AsyncSession,
    *,
    allow_duplicate: bool = False,
) -> dict:
    """Core pipeline: extract -> resolve supplier -> dedupe -> match ->
    price -> persist. Used by both fresh upload and re-process.

    If allow_duplicate is True, the dedupe check is skipped (re-process
    has already deleted the old row, so the "duplicate" would be itself).
    """
    try:
        extracted = await extract_invoice_from_pdf(pdf_bytes)
    except ExtractionError as exc:
        raise HTTPException(502, f"Extraction failed: {exc}")

    client = _client()
    supplier_id: str | None = None
    supplier_name = extracted.supplier_name
    if supplier_name:
        try:
            sup = await client.find_supplier_by_name(supplier_name)
            if not sup:
                fuzzy = await client.search_suppliers(supplier_name)
                if len(fuzzy) == 1:
                    sup = fuzzy[0]
                elif len(fuzzy) > 1:
                    extracted.warnings.append(
                        f"Supplier '{supplier_name}' matches {len(fuzzy)} "
                        f"suppliers; pick one manually."
                    )
            if sup:
                supplier_id = sup["id"]
        except LightspeedError as exc:
            extracted.warnings.append(f"Supplier lookup failed: {exc}")

    # Dedupe check
    if not allow_duplicate and supplier_id and extracted.invoice_number:
        existing = await find_existing_invoice(
            session, supplier_id=supplier_id,
            supplier_invoice_number=extracted.invoice_number,
        )
        if existing:
            return {
                "duplicate": True,
                "existing_invoice_id": existing.id,
                "existing_status": existing.status,
                "consignment_id": existing.consignment_id,
                "message": (
                    f"This invoice (#{extracted.invoice_number} from "
                    f"{supplier_name}) was already processed."
                ),
            }

    invoice = Invoice(
        filename=filename,
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        supplier_invoice_number=extracted.invoice_number,
        invoice_date=extracted.invoice_date,
        subtotal=extracted.subtotal,
        tax=extracted.tax,
        total=extracted.total,
        page_count=extracted.page_count,
        status="EXTRACTED",
        pdf_bytes=pdf_bytes,
    )
    session.add(invoice)
    await session.flush()

    matched_results: list = []
    unmatched_results: list = []
    if supplier_id and extracted.lines:
        status_info = await catalog_status(session)
        if not status_info["product_count"]:
            try:
                await sync_lightspeed_catalog(session, client)
            except LightspeedError as exc:
                extracted.warnings.append(f"Catalog sync failed; matching live: {exc}")
        service = MatchingService(client, session)
        raw = [
            RawInvoiceLine(
                supplier_code=l.supplier_code, description=l.description,
                barcode=l.barcode, quantity=l.quantity, unit_cost=l.unit_cost,
            )
            for l in extracted.lines
        ]
        try:
            mr = await service.match_invoice(supplier_id, raw)
            matched_results = mr.matched
            unmatched_results = mr.unmatched
        except LightspeedError as exc:
            extracted.warnings.append(f"Matching failed: {exc}")
    elif not supplier_id:
        extracted.warnings.append(
            f"Supplier '{supplier_name}' not in Lightspeed; "
            f"every line will need review."
        )

    async def _price(supplier_code, barcode, description, cost, current_retail_price=None):
        try:
            return await price_line(
                session, supplier_id=supplier_id,
                supplier_code=supplier_code, barcode=barcode,
                description=description, cost=cost,
                current_retail_price=current_retail_price,
            )
        except Exception as exc:
            logger.warning("Pricing failed: %s", exc)
            return PricingResult(price=None, source="none", notes=str(exc))

    invoice_lines_for_db: list[InvoiceLine] = []
    matched_payload: list[dict] = []
    new_payload: list[dict] = []
    uncertain_payload: list[dict] = []

    for m in matched_results:
        await remember_supplier_item(
            session,
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            supplier_code=m.raw.supplier_code,
            description=m.raw.description,
            barcode=m.raw.barcode,
            unit_cost=m.raw.unit_cost,
            lightspeed_product_id=m.product_id,
            status="linked",
        )
        pr = await _price(
            m.raw.supplier_code, m.raw.barcode, m.raw.description, m.raw.unit_cost,
            m.current_retail_price,
        )
        meta = {
            "matched_by": m.matched_by, "confidence": m.confidence,
            "product_sku": m.product_sku, "product_name": m.product_name,
            "current_retail_price": m.current_retail_price,
        }
        invoice_lines_for_db.append(InvoiceLine(
            invoice_id=invoice.id,
            supplier_code=m.raw.supplier_code, description=m.raw.description,
            barcode=m.raw.barcode, quantity=m.raw.quantity,
            unit_cost=m.raw.unit_cost, bucket="match",
            lightspeed_product_id=m.product_id,
            suggested_retail_price=pr.price,
            pricing_source=pr.source, match_meta=meta,
        ))
        matched_payload.append({
            "supplier_code": m.raw.supplier_code, "description": m.raw.description,
            "barcode": m.raw.barcode, "quantity": m.raw.quantity,
            "unit_cost": m.raw.unit_cost, "product_id": m.product_id,
            "product_sku": m.product_sku, "product_name": m.product_name,
            "current_retail_price": m.current_retail_price,
            "matched_by": m.matched_by, "confidence": m.confidence,
            "suggested_retail_price": pr.price, "pricing_source": pr.source,
            "pricing_notes": pr.notes, "msrp": pr.msrp,
            "target_margin_price": pr.target_price,
            "competitor_prices": pr.scraped_data,
        })

    for u in unmatched_results:
        await remember_supplier_item(
            session,
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            supplier_code=u.raw.supplier_code,
            description=u.raw.description,
            barcode=u.raw.barcode,
            unit_cost=u.raw.unit_cost,
            status="needs_product",
        )
        pr = await _price(
            u.raw.supplier_code, u.raw.barcode, u.raw.description, u.raw.unit_cost,
        )
        meta = {"candidates": u.candidates, "reason": u.reason}
        invoice_lines_for_db.append(InvoiceLine(
            invoice_id=invoice.id,
            supplier_code=u.raw.supplier_code, description=u.raw.description,
            barcode=u.raw.barcode, quantity=u.raw.quantity,
            unit_cost=u.raw.unit_cost, bucket="uncertain",
            suggested_retail_price=pr.price,
            pricing_source=pr.source, match_meta=meta,
        ))
        uncertain_payload.append({
            "supplier_code": u.raw.supplier_code, "description": u.raw.description,
            "barcode": u.raw.barcode, "quantity": u.raw.quantity,
            "unit_cost": u.raw.unit_cost, "candidates": u.candidates,
            "reason": u.reason, "suggested_retail_price": pr.price,
            "pricing_source": pr.source, "pricing_notes": pr.notes,
            "msrp": pr.msrp, "target_margin_price": pr.target_price,
            "competitor_prices": pr.scraped_data,
        })

    session.add_all(invoice_lines_for_db)

    invoice.extraction_json = {
        "invoice": {
            "supplier_name": supplier_name, "supplier_id": supplier_id,
            "invoice_number": extracted.invoice_number,
            "invoice_date": extracted.invoice_date,
            "currency": extracted.currency, "subtotal": extracted.subtotal,
            "tax": extracted.tax, "total": extracted.total,
            "page_count": extracted.page_count,
        },
        "matched": matched_payload,
        "new": new_payload,
        "uncertain": uncertain_payload,
        "warnings": extracted.warnings,
    }

    return {
        "duplicate": False,
        "invoice_id": invoice.id,
        "summary": {
            "lines": len(extracted.lines),
            "matched": len(matched_payload),
            "uncertain": len(uncertain_payload),
        },
        "redirect": f"/review/{invoice.id}",
    }


@app.post("/invoices/process", dependencies=[Depends(require_auth)])
async def process_invoice(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(_session),
):
    """Upload PDF, extract, dedupe, match, price, store. Returns invoice_id."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(400, "Empty file")
    if len(pdf_bytes) > 30 * 1024 * 1024:
        raise HTTPException(400, "PDF too large (max 30 MB)")

    return await _run_pipeline(pdf_bytes, file.filename, session)


@app.delete("/invoices/{invoice_id}", dependencies=[Depends(require_auth)])
async def delete_invoice(
    invoice_id: int, session: AsyncSession = Depends(_session),
):
    """Delete an invoice and its lines. Does NOT touch anything already
    pushed to Lightspeed — if a consignment was created, it stays in
    Lightspeed; only the local record is removed."""
    invoice = (await session.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    )).scalar_one_or_none()
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    had_consignment = invoice.consignment_id
    # invoice_lines cascade-delete via the relationship's cascade setting
    await session.delete(invoice)

    return {
        "ok": True,
        "deleted_invoice_id": invoice_id,
        "warning": (
            f"A consignment ({had_consignment}) was already created in "
            f"Lightspeed for this invoice; it was NOT deleted. Remove it "
            f"in Lightspeed if needed."
            if had_consignment else None
        ),
    }


@app.post("/invoices/{invoice_id}/reprocess", dependencies=[Depends(require_auth)])
async def reprocess_invoice(
    invoice_id: int, session: AsyncSession = Depends(_session),
):
    """Re-run the original PDF through the current pipeline.

    Deletes the old invoice record and creates a fresh one from the stored
    PDF bytes. Useful after pipeline improvements. Refuses if the invoice
    was already imported (don't want to silently orphan a consignment).
    """
    invoice = (await session.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    )).scalar_one_or_none()
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    if invoice.status == "IMPORTED":
        raise HTTPException(
            409,
            "This invoice was already imported to Lightspeed. Delete the "
            "consignment in Lightspeed first if you really want to redo it, "
            "then delete this record and re-upload the PDF.",
        )
    if not invoice.pdf_bytes:
        raise HTTPException(
            400,
            "The original PDF for this invoice wasn't stored (it was "
            "processed before PDF storage was added). Delete this record "
            "and re-upload the PDF instead.",
        )

    pdf_bytes = invoice.pdf_bytes
    filename = invoice.filename

    # Delete the old record (and its lines) so the dedupe check doesn't
    # flag the re-process as a duplicate of itself.
    await session.delete(invoice)
    await session.flush()

    return await _run_pipeline(
        pdf_bytes, filename, session, allow_duplicate=True,
    )


# --------------------------------------------------------------------- #
# Review data + decisions                                               #
# --------------------------------------------------------------------- #

@app.get("/invoices/{invoice_id}", dependencies=[Depends(require_auth)])
async def get_invoice(
    invoice_id: int, session: AsyncSession = Depends(_session),
):
    invoice = (await session.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    )).scalar_one_or_none()
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    return {
        "id": invoice.id, "status": invoice.status,
        "filename": invoice.filename, "consignment_id": invoice.consignment_id,
        "created_at": invoice.created_at.isoformat(),
        "data": invoice.extraction_json,
        "error": invoice.error,
    }


class LineDecision(BaseModel):
    """One per uncertain or new line in the review screen."""
    supplier_code: str | None = None
    description: str | None = None
    barcode: str | None = None
    quantity: float
    unit_cost: float
    decision: str  # 'match_existing' | 'create_new' | 'queue_enrich' | 'skip'
    # For match_existing:
    lightspeed_product_id: str | None = None
    # For create_new (legacy inline path):
    new_product_name: str | None = None
    new_product_sku: str | None = None
    new_retail_price: float | None = None
    # For queue_enrich (the integrated path — only kind_hint is needed):
    kind_hint: str | None = None  # 'dry_good' | 'live_fish' | None
    # Always:
    retail_price_override: float | None = None  # for updating existing products


class OrderCostIn(BaseModel):
    label: str = "Additional cost"
    amount: float


class FinalizeRequest(BaseModel):
    invoice_id: int
    receive_immediately: bool = False
    update_costs_for_existing: bool = True
    additional_costs: list[OrderCostIn] = Field(default_factory=list)
    # Decisions for uncertain/new lines, keyed by index into the uncertain list
    decisions: list[LineDecision] = Field(default_factory=list)
    # Per-matched-line retail price overrides, keyed by index into matched list
    matched_overrides: dict[int, float] = Field(default_factory=dict)


@app.post("/invoices/finalize", dependencies=[Depends(require_auth)])
async def finalize_invoice(
    body: FinalizeRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(_session),
) -> dict:
    """Apply decisions, create/update products, push consignment to Lightspeed."""
    invoice = (await session.execute(
        select(Invoice).where(Invoice.id == body.invoice_id)
    )).scalar_one_or_none()
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    if invoice.status == "IMPORTED":
        raise HTTPException(409, "Invoice already imported")
    if not invoice.supplier_id:
        raise HTTPException(400, "Invoice has no supplier_id")
    if not invoice.supplier_invoice_number:
        raise HTTPException(400, "Invoice has no invoice number")

    client = _client()
    outlet_id = DEFAULT_OUTLET_ID
    if not outlet_id:
        outlets = await client.list_outlets()
        if outlets:
            outlet_id = outlets[0]["id"]
    if not outlet_id:
        raise HTTPException(400, "No outlet_id available")

    data = invoice.extraction_json or {}
    matched: list[dict] = list(data.get("matched", []))
    uncertain: list[dict] = list(data.get("uncertain", []))
    additional_cost_total = sum(
        max(0.0, float(c.amount)) for c in body.additional_costs
        if c.amount is not None
    )
    base_item_total = 0.0
    for m in matched:
        base_item_total += float(m.get("quantity") or 0) * float(m.get("unit_cost") or 0)
    for dec in body.decisions:
        if dec.decision in {"match_existing", "create_new"}:
            base_item_total += float(dec.quantity or 0) * float(dec.unit_cost or 0)

    def _landed_unit_cost(quantity, unit_cost) -> float:
        unit = float(unit_cost or 0)
        qty = float(quantity or 0)
        if not additional_cost_total or not base_item_total or not qty:
            return unit
        line_total = qty * unit
        allocated = additional_cost_total * (line_total / base_item_total)
        return round(unit + (allocated / qty), 4)

    items_for_lightspeed: list[MatchedLineItem] = []
    products_created: list[dict] = []
    products_updated: list[dict] = []
    retail_price_report: list[dict] = []
    skipped: list[dict] = []
    errors: list[str] = []
    queued_for_enrichment: list[dict] = []
    enrichment_batch_id: str | None = None
    product_cache: dict[str, dict] = {}

    async def _get_existing_product(product_id: str | None) -> dict | None:
        if not product_id:
            return None
        if product_id in product_cache:
            return product_cache[product_id]
        product = await find_cached_product_by_id(session, product_id)
        try:
            live_product = await client.get_product(product_id)
            if live_product:
                product = live_product
        except LightspeedError as exc:
            logger.warning(
                "Could not fetch product %s before price update: %s",
                product_id, exc,
            )
        if product:
            product_cache[product_id] = product
        return product

    def _existing_retail(product: dict | None) -> float | None:
        if not product:
            return None
        return float_or_none(
            product.get("price_excluding_tax")
            if product.get("price_excluding_tax") is not None
            else product.get("retail_price")
        )

    def _report_retail_decision(
        *,
        product_id: str | None,
        name: str | None,
        sku: str | None,
        supplier_code: str | None,
        description: str | None,
        existing_price,
        suggested_price,
        changed: bool,
        reason: str,
    ):
        retail_price_report.append({
            "product_id": product_id,
            "name": name,
            "sku": sku,
            "supplier_code": supplier_code,
            "description": description,
            "existing_retail_price": float_or_none(existing_price),
            "suggested_retail_price": float_or_none(suggested_price),
            "changed": changed,
            "reason": reason,
        })

    # 1. Matched lines: optionally update existing product costs, queue for consignment
    for idx, m in enumerate(matched):
        if body.update_costs_for_existing:
            try:
                retail = body.matched_overrides.get(str(idx)) \
                    or body.matched_overrides.get(idx)
                product = await _get_existing_product(m.get("product_id"))
                existing_retail = _existing_retail(product)
                should_update_retail, retail_reason = retail_update_decision(
                    existing_retail,
                    retail,
                )
                upd = {}
                if should_update_retail:
                    upd["retail_price"] = float(retail)
                landed_cost = _landed_unit_cost(m["quantity"], m["unit_cost"])
                upd["supply_price"] = landed_cost
                result = await client.update_product(m["product_id"], **upd)
                if result is None:
                    # Update was skipped (e.g., 404) — note it but proceed.
                    errors.append(
                        f"Could not update prices on {m.get('product_name')} "
                        f"(product may be archived); consignment will still include it."
                    )
                else:
                    product_cache[m["product_id"]] = result
                    _report_retail_decision(
                        product_id=m.get("product_id"),
                        name=m.get("product_name"),
                        sku=m.get("product_sku"),
                        supplier_code=m.get("supplier_code"),
                        description=m.get("description"),
                        existing_price=existing_retail,
                        suggested_price=retail,
                        changed=should_update_retail,
                        reason=retail_reason,
                    )
                    products_updated.append({
                        "product_id": m["product_id"],
                        "name": m.get("product_name"),
                        "new_supply_price": landed_cost,
                        "invoice_unit_cost": m["unit_cost"],
                        "new_retail_price": (
                            retail if should_update_retail else None
                        ),
                    })
            except LightspeedError as exc:
                errors.append(f"Failed to update {m.get('product_name')}: {exc}")
            except Exception as exc:
                # Pricing-update issues should never block import.
                logger.exception("Unexpected error updating matched product")
                errors.append(
                    f"Unexpected error updating {m.get('product_name')}: {exc}"
                )

        items_for_lightspeed.append(MatchedLineItem(
            product_id=m["product_id"],
            count=float(m["quantity"]),
            cost=_landed_unit_cost(m["quantity"], m["unit_cost"]),
            received=float(m["quantity"]) if body.receive_immediately else None,
        ))

    # 2. Decisions for uncertain lines
    for dec in body.decisions:
        if dec.decision == "skip":
            skipped.append({"description": dec.description, "reason": "user skipped"})
            continue

        if dec.decision == "match_existing":
            if not dec.lightspeed_product_id:
                errors.append(f"Skipped {dec.description}: no product chosen")
                continue
            # Save mapping for next time
            if dec.supplier_code and invoice.supplier_id:
                await upsert_mapping(
                    session,
                    supplier_id=invoice.supplier_id,
                    supplier_code=dec.supplier_code,
                    lightspeed_product_id=dec.lightspeed_product_id,
                    lightspeed_sku=None,
                    product_name=dec.description,
                )
                await remember_supplier_item(
                    session,
                    supplier_id=invoice.supplier_id,
                    supplier_name=invoice.supplier_name,
                    supplier_code=dec.supplier_code,
                    description=dec.description,
                    barcode=dec.barcode,
                    unit_cost=dec.unit_cost,
                    lightspeed_product_id=dec.lightspeed_product_id,
                    status="linked",
                )
            if body.update_costs_for_existing:
                try:
                    product = await _get_existing_product(dec.lightspeed_product_id)
                    existing_retail = _existing_retail(product)
                    should_update_retail, retail_reason = retail_update_decision(
                        existing_retail,
                        dec.retail_price_override,
                    )
                    landed_cost = _landed_unit_cost(dec.quantity, dec.unit_cost)
                    upd = {"supply_price": landed_cost}
                    if should_update_retail:
                        upd["retail_price"] = dec.retail_price_override
                    result = await client.update_product(
                        dec.lightspeed_product_id, **upd
                    )
                    if result:
                        product_cache[dec.lightspeed_product_id] = result
                    _report_retail_decision(
                        product_id=dec.lightspeed_product_id,
                        name=(product or {}).get("name") or dec.description,
                        sku=(product or {}).get("sku"),
                        supplier_code=dec.supplier_code,
                        description=dec.description,
                        existing_price=existing_retail,
                        suggested_price=dec.retail_price_override,
                        changed=should_update_retail,
                        reason=retail_reason,
                    )
                    if result is None:
                        errors.append(
                            f"Could not update prices on {dec.description} "
                            f"(product may be archived); consignment will still include it."
                        )
                    else:
                        products_updated.append({
                            "product_id": dec.lightspeed_product_id,
                            "name": dec.description,
                            "new_supply_price": landed_cost,
                            "invoice_unit_cost": dec.unit_cost,
                            "new_retail_price": (
                                dec.retail_price_override
                                if should_update_retail else None
                            ),
                        })
                except LightspeedError as exc:
                    errors.append(f"Failed to update: {exc}")

            items_for_lightspeed.append(MatchedLineItem(
                product_id=dec.lightspeed_product_id,
                count=dec.quantity,
                cost=_landed_unit_cost(dec.quantity, dec.unit_cost),
                received=dec.quantity if body.receive_immediately else None,
            ))
            continue

        if dec.decision == "create_new":
            if not dec.new_product_name:
                errors.append("Skipped a create_new: no product name given")
                continue
            try:
                landed_cost = _landed_unit_cost(dec.quantity, dec.unit_cost)
                created = await client.create_product(
                    name=dec.new_product_name,
                    sku=dec.new_product_sku,
                    supplier_id=invoice.supplier_id,
                    supplier_code=dec.supplier_code,
                    barcode=dec.barcode,
                    supply_price=landed_cost,
                    retail_price=dec.new_retail_price,
                )
                new_id = created.get("id")
                if not new_id:
                    errors.append(
                        f"create_product returned no id for {dec.new_product_name}"
                    )
                    continue
                products_created.append({
                    "product_id": new_id, "name": dec.new_product_name,
                    "sku": dec.new_product_sku, "supply_price": landed_cost,
                    "invoice_unit_cost": dec.unit_cost,
                    "retail_price": dec.new_retail_price,
                })
                await upsert_cached_product(session, created)
                # Save mapping for next time
                if dec.supplier_code and invoice.supplier_id:
                    await upsert_mapping(
                        session,
                        supplier_id=invoice.supplier_id,
                        supplier_code=dec.supplier_code,
                        lightspeed_product_id=new_id,
                        lightspeed_sku=dec.new_product_sku,
                        product_name=dec.new_product_name,
                    )
                    await remember_supplier_item(
                        session,
                        supplier_id=invoice.supplier_id,
                        supplier_name=invoice.supplier_name,
                        supplier_code=dec.supplier_code,
                        description=dec.description or dec.new_product_name,
                        barcode=dec.barcode,
                        unit_cost=dec.unit_cost,
                        lightspeed_product_id=new_id,
                        status="linked",
                    )
                items_for_lightspeed.append(MatchedLineItem(
                    product_id=new_id,
                    count=dec.quantity,
                    cost=landed_cost,
                    received=dec.quantity if body.receive_immediately else None,
                ))
            except LightspeedError as exc:
                errors.append(f"Failed to create {dec.new_product_name}: {exc}")
            continue

        if dec.decision == "queue_enrich":
            # Defer this product to the enrichment pipeline. We don't add
            # it to the consignment yet — that happens when the user
            # approves the enriched draft on the enrichment review page.
            queued_for_enrichment.append({
                "name": dec.description or dec.supplier_code or "(unnamed)",
                "supplier_code": dec.supplier_code,
                "barcode": dec.barcode,
                "kind_hint": dec.kind_hint if dec.kind_hint in ("dry_good", "live_fish") else None,
                "quantity": dec.quantity,
                "unit_cost": dec.unit_cost,
                "retail_price": dec.retail_price_override,
            })
            continue

        errors.append(f"Unknown decision type: {dec.decision}")

    if not items_for_lightspeed and not queued_for_enrichment:
        # Distinguish "you skipped everything" from "everything errored".
        skipped_count = len(skipped)
        decisions_count = len(body.decisions)
        if skipped_count > 0 and skipped_count == decisions_count and not errors:
            msg = (
                f"All {skipped_count} uncertain line(s) were skipped, and "
                f"there were no matched lines to import. Nothing to push to "
                f"Lightspeed."
            )
        elif not matched and not body.decisions:
            msg = (
                "No matched lines and no decisions submitted. Nothing to "
                "push to Lightspeed."
            )
        elif errors:
            msg = (
                f"All items failed to process: {'; '.join(errors[:3])}"
                + (f" (and {len(errors) - 3} more)" if len(errors) > 3 else "")
            )
        else:
            msg = "No items to import after decisions applied"
        invoice.status = "FAILED"
        invoice.error = msg
        raise HTTPException(400, msg)

    # 3. Push consignment (if we have any items now). If everything was
    # queued for enrichment and nothing matched, we skip the consignment
    # and create it later when the first enriched product is approved.
    consignment_id: str | None = None
    consignment_status: str | None = None
    items_added = 0
    items_failed = 0
    consignment_errors: list[str] = []

    receive_now = body.receive_immediately and not queued_for_enrichment
    if items_for_lightspeed:
        try:
            result = await client.import_invoice(
                outlet_id=outlet_id,
                supplier_id=invoice.supplier_id,
                supplier_invoice_number=invoice.supplier_invoice_number,
                items=items_for_lightspeed,
                receive_immediately=receive_now,
                name=f"Invoice {invoice.supplier_invoice_number}",
            )
        except LightspeedError as exc:
            invoice.status = "FAILED"
            invoice.error = str(exc)
            raise HTTPException(502, str(exc)) from exc
        consignment_id = result["consignment_id"]
        consignment_status = result["status"]
        items_added = result["items_added"]
        items_failed = result["items_failed"]
        consignment_errors = result.get("errors", [])
        invoice.consignment_id = consignment_id

    # 4. Queue enrichment drafts for the products we deferred
    if queued_for_enrichment:
        import uuid as _uuid
        enrichment_batch_id = _uuid.uuid4().hex[:12]
        for q in queued_for_enrichment:
            draft = EnrichmentDraft(
                batch_id=enrichment_batch_id,
                input_name=q["name"],
                kind=q["kind_hint"] or "unknown",
                final_name=q["name"],
                supplier_id=invoice.supplier_id,
                supplier_code=q["supplier_code"],
                barcode=q["barcode"],
                supply_price=q["unit_cost"],
                status="PENDING_ENRICH",  # not drafted yet — done in background
                source_invoice_id=invoice.id,
                source_consignment_id=consignment_id,
                source_quantity=q["quantity"],
                source_cost=q["unit_cost"],
                source_receive_immediately=body.receive_immediately,
                retail_price=q["retail_price"],
            )
            session.add(draft)

    # Status: if everything got pushed, mark IMPORTED. If we have queued
    # items waiting on enrichment, the invoice is partially done.
    if queued_for_enrichment and items_for_lightspeed:
        invoice.status = "IMPORTED_PARTIAL"
    elif queued_for_enrichment:
        invoice.status = "AWAITING_ENRICHMENT"
    else:
        invoice.status = "IMPORTED"

    # Kick off background enrichment for the queued products. Runs after
    # the response returns to the client.
    if enrichment_batch_id:
        background_tasks.add_task(_enrich_pending_drafts, enrichment_batch_id)

    return {
        "ok": True,
        "consignment_id": consignment_id,
        "status": consignment_status or invoice.status,
        "items_added": items_added,
        "items_failed": items_failed,
        "products_created": products_created,
        "products_updated": products_updated,
        "retail_price_report": retail_price_report,
        "additional_costs": [
            {"label": c.label, "amount": float(c.amount)}
            for c in body.additional_costs
        ],
        "additional_cost_total": additional_cost_total,
        "skipped": skipped,
        "queued_for_enrichment_count": len(queued_for_enrichment),
        "enrichment_batch_id": enrichment_batch_id,
        "enrichment_redirect": (
            f"/enrich/review/{enrichment_batch_id}" if enrichment_batch_id else None
        ),
        "errors": errors + consignment_errors,
    }


# --------------------------------------------------------------------- #
# History                                                               #
# --------------------------------------------------------------------- #

@app.get("/invoices", dependencies=[Depends(require_auth)])
async def list_invoices(
    limit: int = 50, session: AsyncSession = Depends(_session),
):
    rows = (await session.execute(
        select(Invoice).order_by(Invoice.created_at.desc()).limit(limit)
    )).scalars().all()
    return {"data": [{
        "id": r.id, "filename": r.filename,
        "supplier_name": r.supplier_name,
        "supplier_invoice_number": r.supplier_invoice_number,
        "invoice_date": r.invoice_date, "total": r.total,
        "status": r.status, "consignment_id": r.consignment_id,
        "created_at": r.created_at.isoformat(),
    } for r in rows]}


# --------------------------------------------------------------------- #
# CSV export (backup)                                                   #
# --------------------------------------------------------------------- #

@app.get("/invoices/{invoice_id}/csv", dependencies=[Depends(require_auth)])
async def export_csv(
    invoice_id: int, session: AsyncSession = Depends(_session),
):
    invoice = (await session.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    )).scalar_one_or_none()
    if not invoice:
        raise HTTPException(404, "Invoice not found")

    lines = (await session.execute(
        select(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id)
    )).scalars().all()

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
        "Supplier", "Invoice #", "Invoice date",
        "Supplier code", "Description", "Barcode",
        "Quantity", "Unit cost",
        "Bucket", "Lightspeed product id",
        "Suggested retail", "Pricing source",
    ])
    for l in lines:
        w.writerow([
            invoice.supplier_name or "",
            invoice.supplier_invoice_number or "",
            invoice.invoice_date or "",
            l.supplier_code or "", l.description or "", l.barcode or "",
            l.quantity, l.unit_cost,
            l.bucket, l.lightspeed_product_id or "",
            l.suggested_retail_price if l.suggested_retail_price is not None else "",
            l.pricing_source or "",
        ])

    filename = f"invoice-{invoice_id}.csv"
    return Response(
        content=out.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------- #
# Product lookup (used by the review UI's manual-pick feature)          #
# --------------------------------------------------------------------- #

@app.get("/products/search", dependencies=[Depends(require_auth)])
async def search_products(q: str, session: AsyncSession = Depends(_session)):
    """Search products by name (for manual selection in the review UI).

    Walks the full Lightspeed catalog and ranks locally. The Lightspeed
    /search endpoint did not reliably apply free-text product queries for
    this app, so every query could return the same unrelated page.
    """
    q = (q or "").strip()
    if not q:
        return {"data": []}
    try:
        status_info = await catalog_status(session)
        if not status_info["product_count"]:
            await sync_lightspeed_catalog(session, _client())
        products = await search_cached_products(session, q, limit=20)
    except LightspeedError as exc:
        raise HTTPException(502, str(exc)) from exc
    items = []
    for p in products:
        items.append({
            "id": p["id"], "name": p.get("name"), "sku": p.get("sku"),
            "supply_price": p.get("supply_price"),
        })
    return {"data": items}


# --------------------------------------------------------------------- #
# Pricing rules CRUD (for settings page)                                #
# --------------------------------------------------------------------- #

@app.get("/pricing/rules", dependencies=[Depends(require_auth)])
async def list_rules(session: AsyncSession = Depends(_session)):
    rows = (await session.execute(
        select(PricingRule).order_by(PricingRule.priority.asc())
    )).scalars().all()
    return {"data": [{
        "id": r.id, "name": r.name, "keywords": r.keywords,
        "multiplier": r.multiplier, "rounding": r.rounding,
        "priority": r.priority, "enabled": r.enabled,
    } for r in rows]}


class RuleIn(BaseModel):
    name: str
    keywords: str | None = None
    multiplier: float
    rounding: str = "charm"
    priority: int = 100
    enabled: bool = True


@app.post("/pricing/rules", dependencies=[Depends(require_auth)])
async def create_rule(body: RuleIn, session: AsyncSession = Depends(_session)):
    rule = PricingRule(**body.dict())
    session.add(rule)
    await session.flush()
    return {"id": rule.id}


@app.delete("/pricing/rules/{rule_id}", dependencies=[Depends(require_auth)])
async def delete_rule(rule_id: int, session: AsyncSession = Depends(_session)):
    rule = (await session.execute(
        select(PricingRule).where(PricingRule.id == rule_id)
    )).scalar_one_or_none()
    if rule:
        await session.delete(rule)
    return {"ok": True}


# --------------------------------------------------------------------- #
# MSRP upload (CSV) per supplier                                        #
# --------------------------------------------------------------------- #

@app.post("/pricing/msrp", dependencies=[Depends(require_auth)])
async def upload_msrp(
    supplier_id: str = Form(...),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(_session),
):
    """Upload an MSRP CSV. Columns: supplier_code, barcode, msrp, notes (any
    subset; one of supplier_code or barcode is required)."""
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    added = 0
    errors = []
    for i, row in enumerate(reader, start=2):
        code = (row.get("supplier_code") or "").strip() or None
        barcode = (row.get("barcode") or "").strip() or None
        if not code and not barcode:
            errors.append(f"Row {i}: needs supplier_code or barcode")
            continue
        try:
            msrp = float(str(row.get("msrp") or "").replace("$", "").replace(",", ""))
        except ValueError:
            errors.append(f"Row {i}: invalid msrp")
            continue
        session.add(SupplierMsrp(
            supplier_id=supplier_id, supplier_code=code, barcode=barcode,
            msrp=msrp, notes=(row.get("notes") or "").strip() or None,
        ))
        added += 1
    return {"added": added, "errors": errors}


# --------------------------------------------------------------------- #
# Product enrichment                                                    #
# --------------------------------------------------------------------- #

import uuid as _uuid


def _append_draft_warning(draft: EnrichmentDraft, message: str):
    warnings = list((draft.warnings or {}).get("list", []))
    if message not in warnings:
        warnings.append(message)
    draft.warnings = {"list": warnings}


def _upc_lookup_warning(result: UpcLookupResult) -> str:
    product = f" ({result.product_name})" if result.product_name else ""
    return (
        f"UPC lookup found {result.upc} from {result.source}{product}; "
        f"confidence {result.confidence:.2f}."
    )


async def _outlet_id_for_client(client) -> str | None:
    outlet_id = DEFAULT_OUTLET_ID
    if outlet_id:
        return outlet_id
    outlets = await client.list_outlets()
    return outlets[0]["id"] if outlets else None


async def _source_consignment_status(client, consignment_id: str | None) -> str | None:
    if not consignment_id:
        return None
    try:
        consignment = await client.get_consignment(consignment_id)
        return (consignment or {}).get("status")
    except LightspeedError as exc:
        logger.warning("Could not fetch consignment %s: %s", consignment_id, exc)
        return None


async def _receive_source_consignment_if_ready(
    session: AsyncSession,
    draft: EnrichmentDraft,
    client,
) -> bool:
    if not draft.source_receive_immediately:
        return False
    if not draft.source_invoice_id or not draft.source_consignment_id:
        return False

    remaining = (await session.execute(
        select(func.count(EnrichmentDraft.id))
        .where(EnrichmentDraft.source_invoice_id == draft.source_invoice_id)
        .where(EnrichmentDraft.status.notin_(("CREATED", "SKIPPED")))
    )).scalar_one()
    if remaining:
        return False

    invoice = (await session.execute(
        select(Invoice).where(Invoice.id == draft.source_invoice_id)
    )).scalar_one_or_none()
    if not invoice:
        return False

    status = await _source_consignment_status(client, draft.source_consignment_id)
    if status == "RECEIVED":
        return False

    outlet_id = await _outlet_id_for_client(client)
    if not outlet_id:
        _append_draft_warning(
            draft,
            "All draft items are resolved, but no outlet_id was available to "
            "mark the consignment received.",
        )
        return False

    name = f"Invoice {invoice.supplier_invoice_number}"
    try:
        if status != "DISPATCHED":
            await client.update_consignment_status(
                draft.source_consignment_id,
                status="DISPATCHED",
                outlet_id=outlet_id,
                name=name,
            )
        await client.update_consignment_status(
            draft.source_consignment_id,
            status="RECEIVED",
            outlet_id=outlet_id,
            name=name,
        )
        invoice.status = "IMPORTED"
        return True
    except LightspeedError as exc:
        _append_draft_warning(
            draft,
            f"Added the product to consignment {draft.source_consignment_id}, "
            f"but failed to receive the consignment: {exc}. Receive it "
            f"manually in Lightspeed to update inventory.",
        )
        return False


async def _create_received_followup_consignment(
    session: AsyncSession,
    draft: EnrichmentDraft,
    client,
    product_id: str,
) -> str | None:
    if not draft.source_invoice_id or not draft.source_quantity:
        return None
    invoice = (await session.execute(
        select(Invoice).where(Invoice.id == draft.source_invoice_id)
    )).scalar_one_or_none()
    if not invoice:
        return None
    outlet_id = await _outlet_id_for_client(client)
    if not outlet_id:
        return None
    name = f"Invoice {invoice.supplier_invoice_number} - new products"
    consignment = await client.create_consignment(
        name=name,
        outlet_id=outlet_id,
        supplier_id=invoice.supplier_id,
        supplier_invoice=invoice.supplier_invoice_number,
    )
    consignment_id = consignment.get("id")
    if not consignment_id:
        return None
    await client.add_product_to_consignment(
        consignment_id,
        MatchedLineItem(
            product_id=product_id,
            count=float(draft.source_quantity),
            cost=float(draft.source_cost or draft.supply_price or 0),
            received=float(draft.source_quantity),
        ),
    )
    await client.update_consignment_status(
        consignment_id,
        status="DISPATCHED",
        outlet_id=outlet_id,
        name=name,
    )
    await client.update_consignment_status(
        consignment_id,
        status="RECEIVED",
        outlet_id=outlet_id,
        name=name,
    )
    return consignment_id


async def _lookup_and_apply_draft_upc(
    session: AsyncSession,
    draft: EnrichmentDraft,
    client,
    *,
    force: bool = False,
) -> UpcLookupResult | None:
    if draft.barcode and not force:
        return None
    result = await lookup_upc_for_product(
        session,
        client=client,
        supplier_id=draft.supplier_id,
        supplier_code=draft.supplier_code,
        product_name=draft.final_name or draft.input_name,
    )
    if result:
        draft.barcode = result.upc
        _append_draft_warning(draft, _upc_lookup_warning(result))
    return result


async def _enrich_pending_drafts(batch_id: str):
    """Background task: draft content for every PENDING_ENRICH row in a
    batch, one at a time. Marks each DRAFT when done, or records error.
    Runs in its own DB session since the request session has closed."""
    # Fetch the user's category and brand lists ONCE per batch so OpenAI
    # can pick from real values. Only LEAF categories are offered — products
    # shouldn't be assigned to parent categories.
    client = getattr(app.state, "lightspeed", None)
    category_names: list[str] = []
    brand_names: list[str] = []
    if client:
        try:
            cats = await client.list_categories()
            # The /product_categories API gives us category_path directly,
            # which already includes all ancestors. We only want leaves.
            leaf_names = []
            for c in cats:
                if not isinstance(c, dict):
                    continue
                if not c.get("leaf_category"):
                    continue
                path = c.get("category_path") or []
                if isinstance(path, list) and path:
                    full = " / ".join(
                        p.get("name", "") for p in path
                        if isinstance(p, dict) and p.get("name")
                    )
                    if full:
                        leaf_names.append(full)
                elif c.get("name"):
                    leaf_names.append(c["name"])
            category_names = sorted(set(leaf_names))
        except Exception as exc:
            logger.warning("Could not list categories: %s", exc)
        try:
            brands = await client.list_brands()
            brand_names = sorted({b.get("name") for b in brands if b.get("name")})
        except Exception as exc:
            logger.warning("Could not list brands: %s", exc)

    async with session_scope() as session:
        rows = (await session.execute(
            select(EnrichmentDraft)
            .where(EnrichmentDraft.batch_id == batch_id)
            .where(EnrichmentDraft.status == "PENDING_ENRICH")
        )).scalars().all()
        for draft in rows:
            name = draft.final_name or draft.input_name
            kind_hint = draft.kind if draft.kind in (
                "dry_good", "live_fish", "live_invert", "live_plant", "live_coral"
            ) else None
            try:
                if client and not draft.barcode:
                    try:
                        await _lookup_and_apply_draft_upc(session, draft, client)
                    except Exception as exc:
                        logger.warning("UPC lookup failed for draft %s: %s", draft.id, exc)
                catalog_facts = None
                try:
                    catalog_item = await find_supplier_catalog_item(
                        session,
                        supplier_id=draft.supplier_id,
                        supplier_code=draft.supplier_code,
                        barcode=draft.barcode,
                        product_name=name,
                    )
                    catalog_facts = supplier_catalog_facts_text(catalog_item)
                    if catalog_item:
                        draft.final_name = catalog_item.description or draft.final_name
                        draft.barcode = catalog_item.barcode or draft.barcode
                        name = draft.final_name or draft.input_name
                        _append_draft_warning(
                            draft,
                            f"Using supplier catalog facts from {catalog_item.catalog_source or 'supplier catalog'}.",
                        )
                except Exception as exc:
                    logger.warning("Supplier catalog fact lookup failed for draft %s: %s", draft.id, exc)
                result = await enrich_product(
                    name,
                    supplier_code=draft.supplier_code,
                    barcode=draft.barcode,
                    supply_price=draft.supply_price,
                    kind_hint=kind_hint,
                    available_categories=category_names,
                    available_brands=brand_names,
                    product_facts=catalog_facts,
                )
                draft.kind = result.kind
                draft.final_name = result.cleaned_name or draft.final_name or name
                draft.description = result.description_html
                draft.product_category = result.product_category
                draft.brand_name = result.brand_name
                draft.tags = {"list": result.suggested_tags} if result.suggested_tags else None
                combined_warnings = list((draft.warnings or {}).get("list", []))
                combined_warnings.extend(w for w in result.warnings if w not in combined_warnings)
                draft.warnings = {"list": combined_warnings} if combined_warnings else None
                draft.status = "DRAFT"
            except EnrichmentError as exc:
                draft.error = str(exc)
                draft.warnings = {"list": [f"Enrichment failed: {exc}"]}
                draft.status = "DRAFT"  # so the user can still edit/skip
            await session.flush()


class EnrichItemIn(BaseModel):
    name: str
    supplier_name: str | None = None
    kind_hint: str | None = None  # 'dry_good' | 'live_fish' | 'live_invert' | 'live_plant' | 'live_coral'


class EnrichBatchRequest(BaseModel):
    items: list[EnrichItemIn]


@app.post("/enrich/batch", dependencies=[Depends(require_auth)])
async def enrich_products_batch(
    body: EnrichBatchRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(_session),
):
    """Enrich a batch of products. Each gets queued as PENDING_ENRICH and
    a background task drafts content for all of them."""
    if not body.items:
        raise HTTPException(400, "No products submitted")
    if len(body.items) > 100:
        raise HTTPException(400, "Max 100 products per batch")

    batch_id = _uuid.uuid4().hex[:12]
    valid_kinds = ("dry_good", "live_fish", "live_invert", "live_plant", "live_coral")
    for item in body.items:
        if not item.name or not item.name.strip():
            continue
        kind = item.kind_hint if item.kind_hint in valid_kinds else "unknown"
        draft = EnrichmentDraft(
            batch_id=batch_id,
            input_name=item.name.strip(),
            final_name=item.name.strip(),
            kind=kind,
            status="PENDING_ENRICH",
        )
        session.add(draft)
    await session.flush()

    # Run drafting in background so the response is fast
    background_tasks.add_task(_enrich_pending_drafts, batch_id)

    return {
        "batch_id": batch_id,
        "count": len(body.items),
        "redirect": f"/enrich/review/{batch_id}",
    }


@app.get("/enrich/batch/{batch_id}", dependencies=[Depends(require_auth)])
async def get_enrich_batch(
    batch_id: str, session: AsyncSession = Depends(_session),
):
    """Return all drafts in a batch for the review screen."""
    rows = (await session.execute(
        select(EnrichmentDraft)
        .where(EnrichmentDraft.batch_id == batch_id)
        .order_by(EnrichmentDraft.id.asc())
    )).scalars().all()
    if not rows:
        raise HTTPException(404, "Batch not found")
    return {"batch_id": batch_id, "drafts": [_draft_to_dict(d) for d in rows]}


def _draft_to_dict(d: EnrichmentDraft) -> dict:
    return {
        "id": d.id,
        "input_name": d.input_name,
        "kind": d.kind,
        "description": d.description,
        "final_name": d.final_name,
        "sku": d.sku,
        "barcode": d.barcode,
        "supplier_id": d.supplier_id,
        "supplier_code": d.supplier_code,
        "supply_price": d.supply_price,
        "retail_price": d.retail_price,
        "has_photo": d.has_photo,
        "product_category": d.product_category,
        "product_category_id": d.product_category_id,
        "brand_name": d.brand_name,
        "brand_id": d.brand_id,
        "tags": (d.tags or {}).get("list", []),
        "status": d.status,
        "lightspeed_product_id": d.lightspeed_product_id,
        "warnings": (d.warnings or {}).get("list", []),
        "error": d.error,
        "source_invoice_id": d.source_invoice_id,
        "source_consignment_id": d.source_consignment_id,
        "source_quantity": d.source_quantity,
        "source_receive_immediately": d.source_receive_immediately,
    }


class DraftUpdate(BaseModel):
    """Fields the user edits on the review screen."""
    final_name: str | None = None
    kind: str | None = None
    description: str | None = None
    sku: str | None = None
    barcode: str | None = None
    supplier_id: str | None = None
    supplier_code: str | None = None
    supply_price: float | None = None
    retail_price: float | None = None
    has_photo: bool | None = None
    product_category: str | None = None
    product_category_id: str | None = None
    brand_name: str | None = None
    brand_id: str | None = None
    tags: list[str] | None = None


@app.put("/enrich/draft/{draft_id}", dependencies=[Depends(require_auth)])
async def update_draft(
    draft_id: int, body: DraftUpdate,
    session: AsyncSession = Depends(_session),
):
    """Save edits to a single draft."""
    draft = (await session.execute(
        select(EnrichmentDraft).where(EnrichmentDraft.id == draft_id)
    )).scalar_one_or_none()
    if not draft:
        raise HTTPException(404, "Draft not found")
    if draft.status == "CREATED":
        raise HTTPException(409, "This product was already created")

    for fieldname in (
        "final_name", "kind", "description", "sku", "barcode",
        "supplier_id", "supplier_code", "supply_price", "retail_price",
        "has_photo", "product_category", "product_category_id",
        "brand_name", "brand_id",
    ):
        val = getattr(body, fieldname)
        if val is not None:
            setattr(draft, fieldname, val)
    if body.tags is not None:
        draft.tags = {"list": body.tags}

    return {"ok": True, "draft": _draft_to_dict(draft)}


@app.post("/enrich/draft/{draft_id}/lookup-upc", dependencies=[Depends(require_auth)])
async def lookup_draft_upc(
    draft_id: int,
    session: AsyncSession = Depends(_session),
):
    """Look up and apply a real UPC for supported supplier product drafts."""
    draft = (await session.execute(
        select(EnrichmentDraft).where(EnrichmentDraft.id == draft_id)
    )).scalar_one_or_none()
    if not draft:
        raise HTTPException(404, "Draft not found")
    if draft.status == "CREATED":
        raise HTTPException(409, "This product was already created")
    if draft.barcode:
        return {
            "ok": True,
            "message": "Draft already has a barcode.",
            "draft": _draft_to_dict(draft),
            "lookup": None,
        }

    client = _client()
    try:
        result = await _lookup_and_apply_draft_upc(session, draft, client)
    except Exception as exc:
        logger.warning("UPC lookup failed for draft %s: %s", draft.id, exc)
        raise HTTPException(502, f"UPC lookup failed: {exc}")

    if not result:
        message = (
            "No trusted UPC found. Lookup currently supports Central Pet, "
            "Phillips Pet, and Reef H2O using existing catalog data."
        )
        _append_draft_warning(draft, message)
        return {"ok": False, "message": message, "draft": _draft_to_dict(draft)}

    return {
        "ok": True,
        "message": f"Found UPC {result.upc}.",
        "lookup": {
            "upc": result.upc,
            "source": result.source,
            "confidence": result.confidence,
            "product_id": result.product_id,
            "product_name": result.product_name,
            "notes": result.notes,
        },
        "draft": _draft_to_dict(draft),
    }


@app.post("/enrich/draft/{draft_id}/reenrich", dependencies=[Depends(require_auth)])
async def reenrich_draft(
    draft_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(_session),
):
    """Re-run enrichment on a draft — useful after changing the kind hint
    or fixing the product name."""
    draft = (await session.execute(
        select(EnrichmentDraft).where(EnrichmentDraft.id == draft_id)
    )).scalar_one_or_none()
    if not draft:
        raise HTTPException(404, "Draft not found")
    if draft.status == "CREATED":
        raise HTTPException(409, "This product was already created")

    draft.status = "PENDING_ENRICH"
    draft.error = None
    background_tasks.add_task(_enrich_pending_drafts, draft.batch_id)
    return {"ok": True, "draft": _draft_to_dict(draft)}


@app.post("/enrich/draft/{draft_id}/create", dependencies=[Depends(require_auth)])
async def create_from_draft(
    draft_id: int, session: AsyncSession = Depends(_session),
):
    """Push a single approved draft to Lightspeed as a new product."""
    draft = (await session.execute(
        select(EnrichmentDraft).where(EnrichmentDraft.id == draft_id)
    )).scalar_one_or_none()
    if not draft:
        raise HTTPException(404, "Draft not found")
    if draft.status == "CREATED":
        raise HTTPException(409, "Already created")

    name = draft.final_name or draft.input_name
    if not name:
        raise HTTPException(400, "Product needs a name")

    client = _client()

    # Resolve category name to id (if user picked a name but no id)
    category_id = draft.product_category_id
    if not category_id and draft.product_category:
        try:
            cats = await client.list_categories()
            for c in cats:
                if not isinstance(c, dict):
                    continue
                if c.get("leaf_category") is False:
                    continue
                full = _category_full_name(c)
                if full == draft.product_category or c.get("id") == draft.product_category:
                    category_id = c["id"]
                    break
        except Exception as exc:
            logger.warning("Category resolve failed: %s", exc)

    # Resolve brand name to id
    brand_id = draft.brand_id
    brand_name = draft.brand_name
    if not brand_name and draft.supplier_id:
        try:
            suppliers = await client.list_suppliers()
            supplier = next(
                (s for s in suppliers if s.get("id") == draft.supplier_id),
                None,
            )
            brand_name = (supplier or {}).get("name") or "Generic"
        except Exception as exc:
            logger.warning("Supplier brand fallback failed: %s", exc)
            brand_name = "Generic"
    if not brand_name:
        brand_name = "Generic"

    if not brand_id and brand_name:
        try:
            brands = await client.list_brands()
            for b in brands:
                if (b.get("name") or "").strip().lower() == brand_name.strip().lower():
                    brand_id = b["id"]
                    break
            if not brand_id:
                created_brand = await client.create_brand(brand_name.strip())
                brand_id = created_brand.get("id")
                brand_name = created_brand.get("name") or brand_name
        except Exception as exc:
            logger.warning("Brand resolve failed: %s", exc)

    try:
        created = await client.create_product(
            name=name,
            sku=draft.sku,
            supplier_id=draft.supplier_id,
            supplier_code=draft.supplier_code,
            barcode=draft.barcode,
            supply_price=draft.supply_price,
            retail_price=draft.retail_price,
            description=draft.description,
            brand_id=brand_id,
            category_id=category_id,
        )
    except LightspeedError as exc:
        # Surface the full Lightspeed error message — it usually
        # explains WHICH field was rejected (e.g. category_id format).
        draft.error = str(exc)
        logger.warning("create_product failed for draft %s: %s", draft.id, exc)
        raise HTTPException(502, f"Lightspeed create failed: {exc}")
    except Exception as exc:
        # Catch absolutely anything else (response parsing, attribute errors)
        # and turn it into a 502 with a useful message, instead of a generic
        # "Internal Server Error".
        draft.error = f"{type(exc).__name__}: {exc}"
        logger.exception("Unexpected error creating draft %s", draft.id)
        raise HTTPException(502, f"Create failed: {type(exc).__name__}: {exc}")

    # Defensive: some Lightspeed endpoints wrap the product in a list.
    # create_product is supposed to unwrap, but if a future change forgets,
    # surface a useful error rather than crashing with AttributeError.
    if isinstance(created, list):
        created = created[0] if created else {}
    if not isinstance(created, dict):
        draft.error = f"Unexpected response from Lightspeed: {type(created).__name__}"
        raise HTTPException(
            502, f"Unexpected Lightspeed response (got {type(created).__name__})"
        )

    new_id = created.get("id")
    if not new_id:
        draft.error = "create_product returned no id"
        raise HTTPException(502, "Lightspeed did not return a product id")

    draft.status = "CREATED"
    draft.lightspeed_product_id = new_id
    draft.product_category_id = category_id
    draft.brand_id = brand_id
    draft.brand_name = brand_name
    draft.error = None
    await upsert_cached_product(session, created)

    # Add to source consignment if there is one
    added_to_consignment = False
    if (
        not draft.source_consignment_id
        and draft.source_invoice_id
        and draft.source_quantity
    ):
        invoice = (await session.execute(
            select(Invoice).where(Invoice.id == draft.source_invoice_id)
        )).scalar_one_or_none()
        if invoice:
            if invoice.consignment_id:
                draft.source_consignment_id = invoice.consignment_id
            elif invoice.supplier_invoice_number:
                outlet_id = await _outlet_id_for_client(client)
                if outlet_id:
                    try:
                        consignment = await client.create_consignment(
                            name=f"Invoice {invoice.supplier_invoice_number}",
                            outlet_id=outlet_id,
                            supplier_id=invoice.supplier_id,
                            supplier_invoice=invoice.supplier_invoice_number,
                        )
                        draft.source_consignment_id = consignment.get("id")
                        invoice.consignment_id = draft.source_consignment_id
                        if invoice.status == "AWAITING_ENRICHMENT":
                            invoice.status = "IMPORTED_PARTIAL"
                    except LightspeedError as exc:
                        warns = (draft.warnings or {}).get("list", [])
                        warns.append(
                            f"Created the product, but failed to create a "
                            f"consignment for invoice {invoice.supplier_invoice_number}: "
                            f"{exc}. Add it manually in Lightspeed."
                        )
                        draft.warnings = {"list": warns}

    if draft.source_consignment_id and draft.source_quantity:
        try:
            status = await _source_consignment_status(client, draft.source_consignment_id)
            if status == "RECEIVED":
                followup_id = await _create_received_followup_consignment(
                    session,
                    draft,
                    client,
                    new_id,
                )
                if not followup_id:
                    raise LightspeedError(
                        "Original consignment is already RECEIVED and a "
                        "follow-up consignment could not be created."
                    )
                draft.source_consignment_id = followup_id
                added_to_consignment = True
                _append_draft_warning(
                    draft,
                    "The original invoice consignment was already received, "
                    f"so this product was received on follow-up consignment {followup_id}.",
                )
            else:
                received_qty = (
                    float(draft.source_quantity)
                    if draft.source_receive_immediately else None
                )
                await client.add_product_to_consignment(
                    draft.source_consignment_id,
                    MatchedLineItem(
                        product_id=new_id,
                        count=float(draft.source_quantity),
                        cost=float(draft.source_cost or draft.supply_price or 0),
                        received=received_qty,
                    ),
                )
                added_to_consignment = True
                await _receive_source_consignment_if_ready(session, draft, client)
        except LightspeedError as exc:
            warns = (draft.warnings or {}).get("list", [])
            warns.append(
                f"Created the product, but failed to add it to consignment "
                f"{draft.source_consignment_id}: {exc}. Add it manually in "
                f"Lightspeed."
            )
            draft.warnings = {"list": warns}

    if draft.supplier_id and draft.supplier_code:
        await upsert_mapping(
            session,
            supplier_id=draft.supplier_id,
            supplier_code=draft.supplier_code,
            lightspeed_product_id=new_id,
            lightspeed_sku=draft.sku,
            product_name=name,
        )
        await remember_supplier_item(
            session,
            supplier_id=draft.supplier_id,
            supplier_name=None,
            supplier_code=draft.supplier_code,
            description=name,
            barcode=draft.barcode,
            unit_cost=draft.source_cost or draft.supply_price,
            lightspeed_product_id=new_id,
            status="linked",
        )

    return {
        "ok": True,
        "lightspeed_product_id": new_id,
        "added_to_consignment": added_to_consignment,
        "draft": _draft_to_dict(draft),
    }


@app.post("/enrich/draft/{draft_id}/image", dependencies=[Depends(require_auth)])
async def upload_draft_image(
    draft_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(_session),
):
    """Upload an approved product image to the created Lightspeed product."""
    draft = (await session.execute(
        select(EnrichmentDraft).where(EnrichmentDraft.id == draft_id)
    )).scalar_one_or_none()
    if not draft:
        raise HTTPException(404, "Draft not found")
    if not draft.lightspeed_product_id:
        raise HTTPException(409, "Create the product before uploading an image")

    content_type = file.content_type or ""
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(400, "Use a JPG, PNG, or WebP image")
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(400, "Empty image")
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(400, "Image is too large (max 10 MB)")

    try:
        await _client().upload_product_image(
            draft.lightspeed_product_id,
            image_bytes=image_bytes,
            filename=file.filename or "product-image",
            content_type=content_type,
        )
    except LightspeedError as exc:
        raise HTTPException(502, f"Lightspeed image upload failed: {exc}") from exc

    draft.has_photo = True
    return {"ok": True, "draft": _draft_to_dict(draft)}


@app.post("/enrich/draft/{draft_id}/skip", dependencies=[Depends(require_auth)])
async def skip_draft(
    draft_id: int, session: AsyncSession = Depends(_session),
):
    draft = (await session.execute(
        select(EnrichmentDraft).where(EnrichmentDraft.id == draft_id)
    )).scalar_one_or_none()
    if not draft:
        raise HTTPException(404, "Draft not found")
    if draft.status != "CREATED":
        draft.status = "SKIPPED"
        if draft.source_receive_immediately and draft.source_consignment_id:
            try:
                await _receive_source_consignment_if_ready(session, draft, _client())
            except Exception as exc:
                logger.warning(
                    "Could not receive source consignment after skipping draft %s: %s",
                    draft.id,
                    exc,
                )
    return {"ok": True}


@app.delete("/enrich/draft/{draft_id}", dependencies=[Depends(require_auth)])
async def delete_draft(
    draft_id: int, session: AsyncSession = Depends(_session),
):
    draft = (await session.execute(
        select(EnrichmentDraft).where(EnrichmentDraft.id == draft_id)
    )).scalar_one_or_none()
    if draft:
        await session.delete(draft)
    return {"ok": True}


@app.get("/enrich/batches", dependencies=[Depends(require_auth)])
async def list_enrich_batches(session: AsyncSession = Depends(_session)):
    """List recent enrichment batches with progress counts."""
    rows = (await session.execute(
        select(EnrichmentDraft).order_by(EnrichmentDraft.created_at.desc())
    )).scalars().all()
    batches: dict = {}
    for d in rows:
        b = batches.setdefault(d.batch_id, {
            "batch_id": d.batch_id,
            "created_at": d.created_at.isoformat(),
            "total": 0, "created": 0, "skipped": 0, "draft": 0,
        })
        b["total"] += 1
        if d.status == "CREATED":
            b["created"] += 1
        elif d.status == "SKIPPED":
            b["skipped"] += 1
        else:
            b["draft"] += 1
    return {"data": list(batches.values())}


# --------------------------------------------------------------------- #
# Direct API endpoints retained                                         #
# --------------------------------------------------------------------- #

@app.get("/debug/sku", dependencies=[Depends(require_auth)])
async def debug_sku(sku: str):
    """Direct test of SKU lookup. Returns what /search and ?sku= both
    return so we can see exactly what Lightspeed knows about this SKU."""
    client = _client()
    out = {"input_sku": sku, "lowercased": sku.lower()}
    try:
        product = await client.find_product_by_sku(sku)
        out["find_product_by_sku"] = (
            None if not product else {
                "id": product["id"], "sku": product.get("sku"),
                "name": product.get("name"),
            }
        )
    except Exception as exc:
        out["find_product_by_sku"] = f"error: {exc}"

    try:
        raw = await client._request(
            "GET", "/search",
            params={"type": "products", "sku": sku.lower(), "page_size": 5},
        )
        out["raw_search_results"] = [{
            "id": p["id"], "sku": p.get("sku"), "name": p.get("name"),
        } for p in raw.get("data", [])]
    except Exception as exc:
        out["raw_search_results"] = f"error: {exc}"

    try:
        raw2 = await client._request(
            "GET", "/products",
            params={"sku": sku, "page_size": 5},
        )
        out["legacy_sku_param_results"] = [{
            "id": p["id"], "sku": p.get("sku"), "name": p.get("name"),
        } for p in raw2.get("data", [])]
    except Exception as exc:
        out["legacy_sku_param_results"] = f"error: {exc}"

    return out


@app.get("/debug/barcode", dependencies=[Depends(require_auth)])
async def debug_barcode(barcode: str):
    """Direct test of barcode lookup — useful when invoice UPCs should
    match Lightspeed's sku/barcode field."""
    client = _client()
    out = {"input_barcode": barcode}
    try:
        product = await client.find_product_by_barcode(barcode)
        out["find_product_by_barcode"] = (
            None if not product else {
                "id": product["id"], "sku": product.get("sku"),
                "name": product.get("name"),
                "barcode": product.get("barcode"),
            }
        )
    except Exception as exc:
        out["find_product_by_barcode"] = f"error: {exc}"

    # Also try SKU lookup with the barcode value (since your sku field IS the UPC)
    try:
        product = await client.find_product_by_sku(barcode)
        out["find_product_by_sku_using_barcode"] = (
            None if not product else {
                "id": product["id"], "sku": product.get("sku"),
                "name": product.get("name"),
            }
        )
    except Exception as exc:
        out["find_product_by_sku_using_barcode"] = f"error: {exc}"

    return out


@app.get("/consignments/{consignment_id}", dependencies=[Depends(require_auth)])
async def get_consignment(consignment_id: str):
    try:
        return await _client().get_consignment(consignment_id)
    except LightspeedNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except LightspeedError as exc:
        raise HTTPException(502, str(exc)) from exc
