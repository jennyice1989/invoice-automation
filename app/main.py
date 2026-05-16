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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    APP_PASSWORD, COOKIE_MAX_AGE, COOKIE_NAME, check_password,
    make_token, require_auth, require_auth_html,
)
from app.db import (
    EnrichmentDraft,
    Invoice, InvoiceLine, PricingRule, SupplierMsrp, SupplierSkuMapping,
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
from app.pricing import PricingResult, price_line
from app.ui import (
    LOGIN_HTML, INDEX_HTML, HISTORY_HTML, REVIEW_HTML, SETTINGS_HTML,
    ENRICH_HTML, ENRICH_REVIEW_HTML,
)

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


@app.get("/enrich", response_class=HTMLResponse)
async def enrich_page(request: Request):
    if redirect := require_auth_html(request.cookies.get(COOKIE_NAME)):
        return redirect
    return HTMLResponse(ENRICH_HTML)


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
        if not c.get("leaf_category"):
            continue
        path = c.get("category_path") or []
        if path:
            full = " / ".join(p.get("name", "") for p in path if p.get("name"))
        else:
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

    async def _price(supplier_code, barcode, description, cost):
        try:
            return await price_line(
                session, supplier_id=supplier_id,
                supplier_code=supplier_code, barcode=barcode,
                description=description, cost=cost,
            )
        except Exception as exc:
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
            "matched_by": m.matched_by, "confidence": m.confidence,
            "suggested_retail_price": pr.price, "pricing_source": pr.source,
            "pricing_notes": pr.notes,
        })

    for u in unmatched_results:
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

    items_for_lightspeed: list[MatchedLineItem] = []
    products_created: list[dict] = []
    products_updated: list[dict] = []
    skipped: list[dict] = []
    errors: list[str] = []
    queued_for_enrichment: list[dict] = []
    enrichment_batch_id: str | None = None

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
                result = await client.update_product(m["product_id"], **upd)
                if result is None:
                    # Update was skipped (e.g., 404) — note it but proceed.
                    errors.append(
                        f"Could not update prices on {m.get('product_name')} "
                        f"(product may be archived); consignment will still include it."
                    )
                else:
                    products_updated.append({
                        "product_id": m["product_id"],
                        "name": m.get("product_name"),
                        "new_supply_price": m["unit_cost"],
                        "new_retail_price": retail,
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

    if items_for_lightspeed:
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
async def search_products(q: str):
    """Search products by name (for manual selection in the review UI).

    Uses the X-Series /search endpoint with type=products. The /products
    list endpoint does NOT honor `search` as a query parameter — it
    silently ignores it and returns the default product listing, which
    is why every query was returning the same results.
    """
    q = (q or "").strip()
    if not q:
        return {"data": []}
    try:
        data = await _client()._request(
            "GET", "/search",
            params={"type": "products", "query": q, "page_size": 20},
        )
    except LightspeedError as exc:
        raise HTTPException(502, str(exc)) from exc
    items = []
    for p in data.get("data", []):
        # Filter out deleted/inactive products — they show up in search
        # but can't be used (PUT against them returns 404).
        if p.get("deleted_at"):
            continue
        if p.get("active") is False:
            continue
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


async def _enrich_pending_drafts(batch_id: str):
    """Background task: draft content for every PENDING_ENRICH row in a
    batch, one at a time. Marks each DRAFT when done, or records error.
    Runs in its own DB session since the request session has closed."""
    # Fetch the user's category and brand lists ONCE per batch so Claude
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
                if not c.get("leaf_category"):
                    continue
                path = c.get("category_path") or []
                if path:
                    full = " / ".join(p.get("name", "") for p in path if p.get("name"))
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
                result = await enrich_product(
                    name,
                    barcode=draft.barcode,
                    kind_hint=kind_hint,
                    available_categories=category_names,
                    available_brands=brand_names,
                )
                draft.kind = result.kind
                draft.final_name = result.cleaned_name or draft.final_name or name
                draft.description = result.description_html
                draft.product_category = result.product_category
                draft.brand_name = result.brand_name
                draft.tags = {"list": result.suggested_tags} if result.suggested_tags else None
                draft.warnings = {"list": result.warnings} if result.warnings else None
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
                if not c.get("leaf_category"):
                    continue
                path = c.get("category_path") or []
                if path:
                    full = " / ".join(p.get("name", "") for p in path if p.get("name"))
                else:
                    full = c.get("name") or ""
                if full == draft.product_category:
                    category_id = c["id"]
                    break
        except Exception as exc:
            logger.warning("Category resolve failed: %s", exc)

    # Resolve brand name to id
    brand_id = draft.brand_id
    if not brand_id and draft.brand_name:
        try:
            brands = await client.list_brands()
            for b in brands:
                if (b.get("name") or "").strip().lower() == draft.brand_name.strip().lower():
                    brand_id = b["id"]
                    break
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
        draft.error = str(exc)
        raise HTTPException(502, f"Lightspeed create failed: {exc}")

    new_id = created.get("id")
    if not new_id:
        draft.error = "create_product returned no id"
        raise HTTPException(502, "Lightspeed did not return a product id")

    draft.status = "CREATED"
    draft.lightspeed_product_id = new_id
    draft.product_category_id = category_id
    draft.brand_id = brand_id
    draft.error = None

    # Add to source consignment if there is one
    added_to_consignment = False
    if draft.source_consignment_id and draft.source_quantity:
        try:
            from app.lightspeed import MatchedLineItem
            await client.add_product_to_consignment(
                draft.source_consignment_id,
                MatchedLineItem(
                    product_id=new_id,
                    count=float(draft.source_quantity),
                    cost=float(draft.source_cost or draft.supply_price or 0),
                ),
            )
            added_to_consignment = True
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

    return {
        "ok": True,
        "lightspeed_product_id": new_id,
        "added_to_consignment": added_to_consignment,
        "draft": _draft_to_dict(draft),
    }


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
