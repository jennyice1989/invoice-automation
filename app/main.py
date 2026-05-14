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
    Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status,
)
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    APP_PASSWORD, COOKIE_MAX_AGE, COOKIE_NAME, check_password,
    make_token, require_auth, require_auth_html,
)
from app.db import (
    Invoice, InvoiceLine, PricingRule, SupplierMsrp, SupplierSkuMapping,
    find_existing_invoice, init_db, session_scope, upsert_mapping,
)
from app.extraction import ExtractionError, extract_invoice_from_pdf
from app.lightspeed import (
    LightspeedAuthError, LightspeedClient, LightspeedError,
    LightspeedNotFoundError, MatchedLineItem,
)
from app.matching import MatchingService, RawInvoiceLine
from app.pricing import PricingResult, price_line
from app.ui import LOGIN_HTML, INDEX_HTML, HISTORY_HTML, REVIEW_HTML, SETTINGS_HTML

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


# --------------------------------------------------------------------- #
# Upload / process                                                      #
# --------------------------------------------------------------------- #

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

    try:
        extracted = await extract_invoice_from_pdf(pdf_bytes)
    except ExtractionError as exc:
        raise HTTPException(502, f"Extraction failed: {exc}")

    # Resolve supplier
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
    if supplier_id and extracted.invoice_number:
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

    # Create invoice row
    invoice = Invoice(
        filename=file.filename,
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        supplier_invoice_number=extracted.invoice_number,
        invoice_date=extracted.invoice_date,
        subtotal=extracted.subtotal,
        tax=extracted.tax,
        total=extracted.total,
        page_count=extracted.page_count,
        status="EXTRACTED",
    )
    session.add(invoice)
    await session.flush()

    # Match line items
    matched_results: list = []
    unmatched_results: list = []
    if supplier_id and extracted.lines:
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

    # Price every line (matched and unmatched alike)
    async def _price(supplier_code, barcode, description, cost):
        try:
            return await price_line(
                session,
                supplier_id=supplier_id,
                supplier_code=supplier_code, barcode=barcode,
                description=description, cost=cost,
            )
        except Exception as exc:  # never let pricing block a workflow
            logger.warning("Pricing failed: %s", exc)
            return PricingResult(price=None, source="none", notes=str(exc))

    invoice_lines_for_db: list[InvoiceLine] = []
    matched_payload: list[dict] = []
    new_payload: list[dict] = []
    uncertain_payload: list[dict] = []

    for m in matched_results:
        pr = await _price(
            m.raw.supplier_code, m.raw.barcode, m.raw.description, m.raw.unit_cost,
        )
        meta = {
            "matched_by": m.matched_by, "confidence": m.confidence,
            "product_sku": m.product_sku, "product_name": m.product_name,
        }
        line = InvoiceLine(
            invoice_id=invoice.id,
            supplier_code=m.raw.supplier_code, description=m.raw.description,
            barcode=m.raw.barcode, quantity=m.raw.quantity,
            unit_cost=m.raw.unit_cost, bucket="match",
            lightspeed_product_id=m.product_id,
            suggested_retail_price=pr.price,
            pricing_source=pr.source, match_meta=meta,
        )
        invoice_lines_for_db.append(line)
        matched_payload.append({
            "supplier_code": m.raw.supplier_code, "description": m.raw.description,
            "barcode": m.raw.barcode, "quantity": m.raw.quantity,
            "unit_cost": m.raw.unit_cost, "product_id": m.product_id,
            "product_sku": m.product_sku, "product_name": m.product_name,
            "matched_by": m.matched_by, "confidence": m.confidence,
            "suggested_retail_price": pr.price, "pricing_source": pr.source,
            "pricing_notes": pr.notes,
        })

    for u in unmatched_results:
        pr = await _price(
            u.raw.supplier_code, u.raw.barcode, u.raw.description, u.raw.unit_cost,
        )
        meta = {"candidates": u.candidates, "reason": u.reason}
        line = InvoiceLine(
            invoice_id=invoice.id,
            supplier_code=u.raw.supplier_code, description=u.raw.description,
            barcode=u.raw.barcode, quantity=u.raw.quantity,
            unit_cost=u.raw.unit_cost, bucket="uncertain",
            suggested_retail_price=pr.price,
            pricing_source=pr.source, match_meta=meta,
        )
        invoice_lines_for_db.append(line)
        uncertain_payload.append({
            "supplier_code": u.raw.supplier_code, "description": u.raw.description,
            "barcode": u.raw.barcode, "quantity": u.raw.quantity,
            "unit_cost": u.raw.unit_cost, "candidates": u.candidates,
            "reason": u.reason, "suggested_retail_price": pr.price,
            "pricing_source": pr.source, "pricing_notes": pr.notes,
        })

    session.add_all(invoice_lines_for_db)

    # Cache full payload for the review page
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
        "new": new_payload,        # populated when user marks "create new"
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
    decision: str  # 'match_existing' | 'create_new' | 'skip'
    # For match_existing:
    lightspeed_product_id: str | None = None
    # For create_new:
    new_product_name: str | None = None
    new_product_sku: str | None = None
    new_retail_price: float | None = None
    # Always:
    retail_price_override: float | None = None  # for updating existing products


class FinalizeRequest(BaseModel):
    invoice_id: int
    receive_immediately: bool = False
    update_costs_for_existing: bool = True
    # Decisions for uncertain/new lines, keyed by index into the uncertain list
    decisions: list[LineDecision] = Field(default_factory=list)
    # Per-matched-line retail price overrides, keyed by index into matched list
    matched_overrides: dict[int, float] = Field(default_factory=dict)


@app.post("/invoices/finalize", dependencies=[Depends(require_auth)])
async def finalize_invoice(
    body: FinalizeRequest, session: AsyncSession = Depends(_session),
):
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

    items_for_lightspeed: list[MatchedLineItem] = []
    products_created: list[dict] = []
    products_updated: list[dict] = []
    skipped: list[dict] = []
    errors: list[str] = []

    # 1. Matched lines: optionally update existing product costs, queue for consignment
    for idx, m in enumerate(matched):
        if body.update_costs_for_existing:
            try:
                retail = body.matched_overrides.get(str(idx)) \
                    or body.matched_overrides.get(idx) \
                    or m.get("suggested_retail_price")
                upd = {}
                if retail is not None:
                    upd["retail_price"] = float(retail)
                upd["supply_price"] = float(m["unit_cost"])
                await client.update_product(m["product_id"], **upd)
                products_updated.append({
                    "product_id": m["product_id"],
                    "name": m.get("product_name"),
                    "new_supply_price": m["unit_cost"],
                    "new_retail_price": retail,
                })
            except LightspeedError as exc:
                errors.append(f"Failed to update {m.get('product_name')}: {exc}")

        items_for_lightspeed.append(MatchedLineItem(
            product_id=m["product_id"],
            count=float(m["quantity"]), cost=float(m["unit_cost"]),
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
            if body.update_costs_for_existing:
                try:
                    upd = {"supply_price": dec.unit_cost}
                    if dec.retail_price_override is not None:
                        upd["retail_price"] = dec.retail_price_override
                    await client.update_product(dec.lightspeed_product_id, **upd)
                    products_updated.append({
                        "product_id": dec.lightspeed_product_id,
                        "name": dec.description,
                        "new_supply_price": dec.unit_cost,
                        "new_retail_price": dec.retail_price_override,
                    })
                except LightspeedError as exc:
                    errors.append(f"Failed to update: {exc}")

            items_for_lightspeed.append(MatchedLineItem(
                product_id=dec.lightspeed_product_id,
                count=dec.quantity, cost=dec.unit_cost,
            ))
            continue

        if dec.decision == "create_new":
            if not dec.new_product_name:
                errors.append("Skipped a create_new: no product name given")
                continue
            try:
                created = await client.create_product(
                    name=dec.new_product_name,
                    sku=dec.new_product_sku,
                    supplier_id=invoice.supplier_id,
                    supplier_code=dec.supplier_code,
                    barcode=dec.barcode,
                    supply_price=dec.unit_cost,
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
                    "sku": dec.new_product_sku, "supply_price": dec.unit_cost,
                    "retail_price": dec.new_retail_price,
                })
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
                items_for_lightspeed.append(MatchedLineItem(
                    product_id=new_id, count=dec.quantity, cost=dec.unit_cost,
                ))
            except LightspeedError as exc:
                errors.append(f"Failed to create {dec.new_product_name}: {exc}")
            continue

        errors.append(f"Unknown decision type: {dec.decision}")

    if not items_for_lightspeed:
        invoice.status = "FAILED"
        invoice.error = "No items to import after decisions applied"
        raise HTTPException(400, "No items to import")

    # 3. Push consignment
    try:
        result = await client.import_invoice(
            outlet_id=outlet_id,
            supplier_id=invoice.supplier_id,
            supplier_invoice_number=invoice.supplier_invoice_number,
            items=items_for_lightspeed,
            receive_immediately=body.receive_immediately,
            name=f"Invoice {invoice.supplier_invoice_number}",
        )
    except LightspeedError as exc:
        invoice.status = "FAILED"
        invoice.error = str(exc)
        raise HTTPException(502, str(exc)) from exc

    invoice.status = "IMPORTED"
    invoice.consignment_id = result["consignment_id"]

    return {
        "ok": True,
        "consignment_id": result["consignment_id"],
        "status": result["status"],
        "items_added": result["items_added"],
        "items_failed": result["items_failed"],
        "products_created": products_created,
        "products_updated": products_updated,
        "skipped": skipped,
        "errors": errors + result.get("errors", []),
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
async def search_products(q: str):
    """Search products by name (for manual selection in the review UI)."""
    try:
        # X-Series supports `?search=` on /products
        data = await _client()._request(
            "GET", "/products",
            params={"search": q, "page_size": 20},
        )
    except LightspeedError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"data": [{
        "id": p["id"], "name": p.get("name"), "sku": p.get("sku"),
        "supply_price": p.get("supply_price"),
    } for p in data.get("data", [])]}


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
# Direct API endpoints retained                                         #
# --------------------------------------------------------------------- #

@app.get("/consignments/{consignment_id}", dependencies=[Depends(require_auth)])
async def get_consignment(consignment_id: str):
    try:
        return await _client().get_consignment(consignment_id)
    except LightspeedNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except LightspeedError as exc:
        raise HTTPException(502, str(exc)) from exc
