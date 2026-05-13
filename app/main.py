"""
FastAPI service that exposes the Lightspeed integration.

Two main flows:

  POST /invoices/match     -> raw extracted lines -> matched + unmatched
  POST /invoices/import    -> fully-matched lines -> Lightspeed consignment

And supporting endpoints for mappings, suppliers, products, outlets.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import (
    SupplierSkuMapping,
    init_db,
    session_scope,
    upsert_mapping,
)
from app.extraction import ExtractionError, extract_invoice_from_pdf
from app.lightspeed import (
    LightspeedAuthError,
    LightspeedClient,
    LightspeedError,
    LightspeedNotFoundError,
    MatchedLineItem,
)
from app.matching import MatchingService, RawInvoiceLine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- #
# Config                                                                #
# --------------------------------------------------------------------- #

LIGHTSPEED_DOMAIN_PREFIX = os.environ.get("LIGHTSPEED_DOMAIN_PREFIX", "")
LIGHTSPEED_TOKEN = os.environ.get("LIGHTSPEED_PERSONAL_TOKEN", "")
DEFAULT_OUTLET_ID = os.environ.get("LIGHTSPEED_DEFAULT_OUTLET_ID", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")


# --------------------------------------------------------------------- #
# App lifecycle                                                         #
# --------------------------------------------------------------------- #

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
        logger.warning("DATABASE_URL not set; mapping features will fail")

    try:
        yield
    finally:
        if app.state.lightspeed:
            await app.state.lightspeed.close()


app = FastAPI(
    title="Invoice -> Lightspeed importer",
    version="0.2.0",
    lifespan=lifespan,
)


def _client() -> LightspeedClient:
    client = getattr(app.state, "lightspeed", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lightspeed credentials not configured",
        )
    return client


async def _session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that hands out a transactional session."""
    async with session_scope() as session:
        yield session


# --------------------------------------------------------------------- #
# Models                                                                #
# --------------------------------------------------------------------- #

class LineItemIn(BaseModel):
    product_id: str
    count: float = Field(..., gt=0)
    cost: float = Field(..., ge=0)
    received: float | None = None


class InvoiceImportRequest(BaseModel):
    supplier_invoice_number: str
    items: list[LineItemIn]
    supplier_id: str | None = None
    outlet_id: str | None = None
    receive_immediately: bool = False
    name: str | None = None


class InvoiceImportResponse(BaseModel):
    consignment_id: str
    status: str
    items_added: int
    items_failed: int
    errors: list[dict]


class SupplierLookupResponse(BaseModel):
    found: bool
    supplier_id: str | None
    name: str | None


class ProductLookupResponse(BaseModel):
    found: bool
    product_id: str | None
    sku: str | None
    name: str | None
    matched_by: str | None


class RawLineIn(BaseModel):
    supplier_code: str | None = None
    description: str | None = None
    barcode: str | None = None
    quantity: float = Field(..., gt=0)
    unit_cost: float = Field(..., ge=0)


class InvoiceMatchRequest(BaseModel):
    supplier_id: str
    lines: list[RawLineIn]


class MatchedLineOut(BaseModel):
    supplier_code: str | None
    description: str | None
    quantity: float
    unit_cost: float
    product_id: str
    product_sku: str | None
    product_name: str | None
    matched_by: str
    confidence: float


class UnmatchedLineOut(BaseModel):
    supplier_code: str | None
    description: str | None
    quantity: float
    unit_cost: float
    candidates: list[dict]
    reason: str


class MatchResponse(BaseModel):
    matched: list[MatchedLineOut]
    unmatched: list[UnmatchedLineOut]
    summary: dict


class MappingCreate(BaseModel):
    supplier_id: str
    supplier_code: str
    lightspeed_product_id: str
    lightspeed_sku: str | None = None
    product_name: str | None = None


class MappingOut(BaseModel):
    id: int
    supplier_id: str
    supplier_code: str
    lightspeed_product_id: str
    lightspeed_sku: str | None
    product_name: str | None


# --------------------------------------------------------------------- #
# Health and discovery                                                  #
# --------------------------------------------------------------------- #

@app.get("/healthz")
async def healthz() -> dict:
    return {
        "ok": True,
        "lightspeed_configured": bool(LIGHTSPEED_DOMAIN_PREFIX),
        "db_configured": bool(DATABASE_URL),
    }


@app.get("/outlets")
async def list_outlets() -> dict:
    try:
        outlets = await _client().list_outlets()
    except LightspeedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"data": [{"id": o["id"], "name": o.get("name")} for o in outlets]}


# --------------------------------------------------------------------- #
# Suppliers                                                             #
# --------------------------------------------------------------------- #

@app.get("/suppliers")
async def list_suppliers() -> dict:
    try:
        suppliers = await _client().list_suppliers()
    except LightspeedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "data": [{"id": s["id"], "name": s.get("name")} for s in suppliers]
    }


@app.get("/suppliers/lookup", response_model=SupplierLookupResponse)
async def lookup_supplier(name: str) -> SupplierLookupResponse:
    try:
        supplier = await _client().find_supplier_by_name(name)
    except LightspeedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if supplier is None:
        return SupplierLookupResponse(found=False, supplier_id=None, name=None)
    return SupplierLookupResponse(
        found=True, supplier_id=supplier["id"], name=supplier.get("name")
    )


@app.get("/suppliers/search")
async def search_suppliers(q: str) -> dict:
    try:
        matches = await _client().search_suppliers(q)
    except LightspeedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "matches": [{"id": s["id"], "name": s.get("name")} for s in matches]
    }


# --------------------------------------------------------------------- #
# Products                                                              #
# --------------------------------------------------------------------- #

@app.get("/products/lookup", response_model=ProductLookupResponse)
async def lookup_product(
    supplier_code: str | None = None,
    sku: str | None = None,
) -> ProductLookupResponse:
    if not (supplier_code or sku):
        raise HTTPException(
            status_code=400, detail="Pass either supplier_code or sku"
        )

    client = _client()
    try:
        product = None
        matched_by = None
        if supplier_code:
            product = await client.find_product_by_supplier_code(supplier_code)
            matched_by = "supplier_code" if product else None
        if product is None and sku:
            product = await client.find_product_by_sku(sku)
            matched_by = "sku" if product else None
    except LightspeedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if product is None:
        return ProductLookupResponse(
            found=False, product_id=None, sku=None, name=None, matched_by=None
        )

    return ProductLookupResponse(
        found=True,
        product_id=product["id"],
        sku=product.get("sku"),
        name=product.get("name"),
        matched_by=matched_by,
    )


# --------------------------------------------------------------------- #
# Matching                                                              #
# --------------------------------------------------------------------- #

@app.post("/invoices/match", response_model=MatchResponse)
async def match_invoice(
    req: InvoiceMatchRequest,
    session: AsyncSession = Depends(_session),
) -> MatchResponse:
    """
    Resolve raw invoice lines to Lightspeed products.

    Returns separate matched/unmatched lists. Unmatched lines come with
    up to 3 fuzzy-match candidates so a UI can suggest them.
    """
    service = MatchingService(_client(), session)
    raw_lines = [
        RawInvoiceLine(
            supplier_code=l.supplier_code,
            description=l.description,
            barcode=l.barcode,
            quantity=l.quantity,
            unit_cost=l.unit_cost,
        )
        for l in req.lines
    ]
    result = await service.match_invoice(req.supplier_id, raw_lines)

    matched_out = [
        MatchedLineOut(
            supplier_code=m.raw.supplier_code,
            description=m.raw.description,
            quantity=m.raw.quantity,
            unit_cost=m.raw.unit_cost,
            product_id=m.product_id,
            product_sku=m.product_sku,
            product_name=m.product_name,
            matched_by=m.matched_by,
            confidence=m.confidence,
        )
        for m in result.matched
    ]
    unmatched_out = [
        UnmatchedLineOut(
            supplier_code=u.raw.supplier_code,
            description=u.raw.description,
            quantity=u.raw.quantity,
            unit_cost=u.raw.unit_cost,
            candidates=u.candidates,
            reason=u.reason,
        )
        for u in result.unmatched
    ]

    return MatchResponse(
        matched=matched_out,
        unmatched=unmatched_out,
        summary={
            "total_lines": len(req.lines),
            "matched_count": len(matched_out),
            "unmatched_count": len(unmatched_out),
            "by_method": _count_by(matched_out, "matched_by"),
        },
    )


def _count_by(items, attr: str) -> dict:
    counts: dict = {}
    for item in items:
        key = getattr(item, attr)
        counts[key] = counts.get(key, 0) + 1
    return counts


# --------------------------------------------------------------------- #
# Mappings (the "memory")                                               #
# --------------------------------------------------------------------- #

@app.post("/mappings", response_model=MappingOut)
async def create_mapping(
    body: MappingCreate,
    session: AsyncSession = Depends(_session),
) -> MappingOut:
    """
    Teach the system: 'when supplier X sends code Y, that's product Z.'
    Idempotent — calling twice with the same supplier+code updates the
    existing mapping rather than failing.
    """
    mapping = await upsert_mapping(
        session,
        supplier_id=body.supplier_id,
        supplier_code=body.supplier_code,
        lightspeed_product_id=body.lightspeed_product_id,
        lightspeed_sku=body.lightspeed_sku,
        product_name=body.product_name,
    )
    await session.flush()
    return MappingOut(
        id=mapping.id,
        supplier_id=mapping.supplier_id,
        supplier_code=mapping.supplier_code,
        lightspeed_product_id=mapping.lightspeed_product_id,
        lightspeed_sku=mapping.lightspeed_sku,
        product_name=mapping.product_name,
    )


@app.get("/mappings")
async def list_mappings(
    supplier_id: str | None = None,
    session: AsyncSession = Depends(_session),
) -> dict:
    stmt = select(SupplierSkuMapping).order_by(SupplierSkuMapping.id.desc())
    if supplier_id:
        stmt = stmt.where(SupplierSkuMapping.supplier_id == supplier_id)
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "data": [
            {
                "id": m.id,
                "supplier_id": m.supplier_id,
                "supplier_code": m.supplier_code,
                "lightspeed_product_id": m.lightspeed_product_id,
                "lightspeed_sku": m.lightspeed_sku,
                "product_name": m.product_name,
            }
            for m in rows
        ]
    }


# --------------------------------------------------------------------- #
# Import (final stage)                                                  #
# --------------------------------------------------------------------- #

@app.post("/invoices/import", response_model=InvoiceImportResponse)
async def import_invoice(req: InvoiceImportRequest) -> InvoiceImportResponse:
    outlet_id = req.outlet_id or DEFAULT_OUTLET_ID
    if not outlet_id:
        raise HTTPException(
            status_code=400,
            detail="outlet_id required (or set LIGHTSPEED_DEFAULT_OUTLET_ID)",
        )

    items = [
        MatchedLineItem(
            product_id=i.product_id,
            count=i.count,
            cost=i.cost,
            received=i.received,
        )
        for i in req.items
    ]

    try:
        result = await _client().import_invoice(
            outlet_id=outlet_id,
            supplier_id=req.supplier_id,
            supplier_invoice_number=req.supplier_invoice_number,
            items=items,
            receive_immediately=req.receive_immediately,
            name=req.name,
        )
    except LightspeedAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except LightspeedNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LightspeedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return InvoiceImportResponse(**result)


@app.get("/consignments/{consignment_id}")
async def get_consignment(consignment_id: str) -> dict:
    try:
        return await _client().get_consignment(consignment_id)
    except LightspeedNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LightspeedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --------------------------------------------------------------------- #
# Upload + extract + match (the one-shot endpoint)                      #
# --------------------------------------------------------------------- #

@app.post("/invoices/process")
async def process_invoice(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(_session),
) -> dict:
    """
    Full pipeline: PDF in, matched invoice out (or unmatched lines for review).

    1. Read uploaded PDF
    2. Extract structured data via Claude
    3. Resolve supplier name -> supplier_id (exact, then fuzzy)
    4. Run matching pipeline on line items
    5. Return everything to the caller; let them decide whether to import
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400, detail="Only PDF files are supported"
        )

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(pdf_bytes) > 30 * 1024 * 1024:
        raise HTTPException(
            status_code=400, detail="PDF too large (max 30 MB)"
        )

    # 1. Extract
    try:
        invoice = await extract_invoice_from_pdf(pdf_bytes)
    except ExtractionError as exc:
        raise HTTPException(status_code=502, detail=f"Extraction failed: {exc}")

    # 2. Resolve supplier
    client = _client()
    supplier = None
    supplier_id: str | None = None
    if invoice.supplier_name:
        try:
            supplier = await client.find_supplier_by_name(invoice.supplier_name)
            if not supplier:
                fuzzy = await client.search_suppliers(invoice.supplier_name)
                if len(fuzzy) == 1:
                    supplier = fuzzy[0]
                elif len(fuzzy) > 1:
                    # Multiple matches — surface them, don't pick.
                    invoice.warnings.append(
                        f"Supplier name '{invoice.supplier_name}' matches "
                        f"{len(fuzzy)} suppliers; pick one manually."
                    )
        except LightspeedError as exc:
            invoice.warnings.append(f"Supplier lookup failed: {exc}")

    if supplier:
        supplier_id = supplier["id"]

    # 3. Match line items
    matched: list = []
    unmatched: list = []
    if supplier_id and invoice.lines:
        service = MatchingService(client, session)
        raw_lines = [
            RawInvoiceLine(
                supplier_code=l.supplier_code,
                description=l.description,
                barcode=l.barcode,
                quantity=l.quantity,
                unit_cost=l.unit_cost,
            )
            for l in invoice.lines
        ]
        try:
            mr = await service.match_invoice(supplier_id, raw_lines)
            matched = [
                {
                    "supplier_code": m.raw.supplier_code,
                    "description": m.raw.description,
                    "quantity": m.raw.quantity,
                    "unit_cost": m.raw.unit_cost,
                    "product_id": m.product_id,
                    "product_sku": m.product_sku,
                    "product_name": m.product_name,
                    "matched_by": m.matched_by,
                    "confidence": m.confidence,
                }
                for m in mr.matched
            ]
            unmatched = [
                {
                    "supplier_code": u.raw.supplier_code,
                    "description": u.raw.description,
                    "quantity": u.raw.quantity,
                    "unit_cost": u.raw.unit_cost,
                    "candidates": u.candidates,
                    "reason": u.reason,
                }
                for u in mr.unmatched
            ]
        except LightspeedError as exc:
            invoice.warnings.append(f"Matching failed: {exc}")
    elif not supplier_id:
        invoice.warnings.append(
            f"Supplier '{invoice.supplier_name}' not found in Lightspeed; "
            f"cannot match line items."
        )

    return {
        "invoice": {
            "supplier_name": invoice.supplier_name,
            "supplier_id": supplier_id,
            "invoice_number": invoice.invoice_number,
            "invoice_date": invoice.invoice_date,
            "currency": invoice.currency,
            "subtotal": invoice.subtotal,
            "tax": invoice.tax,
            "total": invoice.total,
        },
        "matched": matched,
        "unmatched": unmatched,
        "warnings": invoice.warnings,
        "summary": {
            "total_lines": len(invoice.lines),
            "matched_count": len(matched),
            "unmatched_count": len(unmatched),
        },
    }


# --------------------------------------------------------------------- #
# Web UI                                                                #
# --------------------------------------------------------------------- #

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _INDEX_HTML


_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Invoice Importer</title>
<style>
  :root {
    --bg: #fafaf9; --fg: #1c1917; --muted: #78716c;
    --border: #e7e5e4; --accent: #0c4a6e; --accent-soft: #f0f9ff;
    --good: #166534; --warn: #92400e; --bad: #991b1b;
    --good-bg: #f0fdf4; --warn-bg: #fffbeb; --bad-bg: #fef2f2;
  }
  * { box-sizing: border-box; }
  body { font: 15px/1.5 system-ui, -apple-system, sans-serif;
         color: var(--fg); background: var(--bg); margin: 0;
         padding: 32px 16px; }
  .container { max-width: 960px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .subtitle { color: var(--muted); margin: 0 0 32px; font-size: 14px; }
  .drop {
    border: 2px dashed var(--border); border-radius: 12px;
    padding: 48px 24px; text-align: center; cursor: pointer;
    background: white; transition: all 0.15s ease;
  }
  .drop:hover, .drop.over {
    border-color: var(--accent); background: var(--accent-soft);
  }
  .drop p { margin: 8px 0; color: var(--muted); }
  .drop strong { color: var(--fg); }
  .drop input { display: none; }
  .status { margin-top: 24px; padding: 16px 20px; border-radius: 8px;
            background: white; border: 1px solid var(--border); }
  .status.hidden { display: none; }
  .spinner {
    display: inline-block; width: 14px; height: 14px;
    border: 2px solid var(--border); border-top-color: var(--accent);
    border-radius: 50%; animation: spin 0.8s linear infinite;
    vertical-align: middle; margin-right: 8px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .results { margin-top: 24px; }
  .results.hidden { display: none; }
  .card { background: white; border: 1px solid var(--border);
          border-radius: 8px; padding: 20px; margin-bottom: 16px; }
  .card h2 { margin: 0 0 12px; font-size: 16px; }
  .meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 12px 24px; }
  .meta div { font-size: 13px; }
  .meta label { color: var(--muted); display: block; font-size: 12px; }
  .meta span { font-weight: 500; }
  table { width: 100%; border-collapse: collapse; font-size: 13px;
          margin-top: 8px; }
  th, td { text-align: left; padding: 8px 12px;
           border-bottom: 1px solid var(--border); }
  th { font-weight: 600; color: var(--muted); font-size: 12px;
       text-transform: uppercase; letter-spacing: 0.04em; }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
           font-size: 11px; font-weight: 600; text-transform: uppercase; }
  .badge.mapping { background: #ede9fe; color: #5b21b6; }
  .badge.sku { background: #dbeafe; color: #1e40af; }
  .badge.barcode { background: #ccfbf1; color: #0f766e; }
  .badge.fuzzy_name { background: #fef3c7; color: #92400e; }
  .warnings { background: var(--warn-bg); border: 1px solid #fde68a;
              color: var(--warn); padding: 12px 16px; border-radius: 8px;
              margin-bottom: 16px; font-size: 13px; }
  .warnings ul { margin: 4px 0 0; padding-left: 20px; }
  .candidates { padding: 4px 0; font-size: 12px; color: var(--muted); }
  .cand-btn {
    display: inline-block; background: white; border: 1px solid var(--border);
    border-radius: 4px; padding: 4px 8px; margin: 2px 4px 2px 0;
    cursor: pointer; font-size: 12px; color: var(--fg);
  }
  .cand-btn:hover { border-color: var(--accent); background: var(--accent-soft); }
  .cand-btn.unmatch-skip { background: #fef2f2; border-color: #fecaca; color: #991b1b; }
  .actions { margin-top: 20px; display: flex; gap: 12px; align-items: center; }
  button.primary {
    background: var(--accent); color: white; border: none;
    padding: 10px 20px; border-radius: 6px; font-size: 14px;
    font-weight: 500; cursor: pointer;
  }
  button.primary:hover { background: #075985; }
  button.primary:disabled { background: var(--muted); cursor: not-allowed; }
  label.receive { display: inline-flex; align-items: center; gap: 8px;
                  font-size: 13px; color: var(--muted); }
  .success { background: var(--good-bg); border: 1px solid #bbf7d0;
             color: var(--good); padding: 16px; border-radius: 8px; }
  .error { background: var(--bad-bg); border: 1px solid #fecaca;
           color: var(--bad); padding: 16px; border-radius: 8px; }
</style>
</head>
<body>
<div class="container">
  <h1>Invoice Importer</h1>
  <p class="subtitle">Drop a PDF invoice. We extract it, match line items
  to Lightspeed products, and push it as a SUPPLIER consignment.</p>

  <label class="drop" id="drop">
    <input type="file" id="file" accept="application/pdf" />
    <p><strong>Drop a PDF here</strong> or click to select</p>
    <p style="font-size: 12px;">Max 30 MB</p>
  </label>

  <div class="status hidden" id="status"></div>
  <div class="results hidden" id="results"></div>
</div>

<script>
const drop = document.getElementById('drop');
const fileInput = document.getElementById('file');
const statusEl = document.getElementById('status');
const resultsEl = document.getElementById('results');

let currentResult = null;
let unmatchedDecisions = {};  // index -> {product_id, sku, name} or 'skip'

drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', e => {
  e.preventDefault();
  drop.classList.remove('over');
  if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', e => {
  if (e.target.files[0]) handleFile(e.target.files[0]);
});

async function handleFile(file) {
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    showStatus('Only PDF files are supported.', 'error');
    return;
  }
  showStatus('<span class="spinner"></span>Extracting and matching... (10-30 seconds)');
  resultsEl.classList.add('hidden');
  currentResult = null;
  unmatchedDecisions = {};

  const form = new FormData();
  form.append('file', file);
  try {
    const resp = await fetch('/invoices/process', { method: 'POST', body: form });
    const data = await resp.json();
    if (!resp.ok) {
      showStatus('Error: ' + (data.detail || resp.statusText), 'error');
      return;
    }
    currentResult = data;
    statusEl.classList.add('hidden');
    renderResults(data);
  } catch (err) {
    showStatus('Network error: ' + err.message, 'error');
  }
}

function showStatus(html, kind) {
  statusEl.innerHTML = html;
  statusEl.className = 'status' + (kind ? ' ' + kind : '');
}

function renderResults(data) {
  const inv = data.invoice;
  let html = '';

  if (data.warnings && data.warnings.length) {
    html += '<div class="warnings"><strong>Warnings</strong><ul>';
    for (const w of data.warnings) html += '<li>' + escape(w) + '</li>';
    html += '</ul></div>';
  }

  html += '<div class="card"><h2>Invoice</h2><div class="meta">';
  html += metaRow('Supplier', inv.supplier_name || '—');
  html += metaRow('Supplier ID', inv.supplier_id || 'not resolved');
  html += metaRow('Invoice #', inv.invoice_number || '—');
  html += metaRow('Date', inv.invoice_date || '—');
  html += metaRow('Subtotal', fmtMoney(inv.subtotal, inv.currency));
  html += metaRow('Total', fmtMoney(inv.total, inv.currency));
  html += '</div></div>';

  if (data.matched.length) {
    html += '<div class="card"><h2>Matched (' + data.matched.length + ')</h2>';
    html += '<table><thead><tr><th>From invoice</th><th>Matched product</th>'
         + '<th>How</th><th class="num">Qty</th><th class="num">Unit cost</th></tr></thead><tbody>';
    for (const m of data.matched) {
      html += '<tr>';
      html += '<td>' + escape(m.supplier_code || m.description || '—') + '</td>';
      html += '<td><strong>' + escape(m.product_name || '') + '</strong>'
           + '<br><span style="color:var(--muted);font-size:12px">'
           + escape(m.product_sku || '') + '</span></td>';
      html += '<td><span class="badge ' + m.matched_by + '">'
           + m.matched_by.replace('_', ' ') + '</span>'
           + (m.confidence < 1 ? ' ' + Math.round(m.confidence * 100) + '%' : '')
           + '</td>';
      html += '<td class="num">' + m.quantity + '</td>';
      html += '<td class="num">' + m.unit_cost.toFixed(2) + '</td>';
      html += '</tr>';
    }
    html += '</tbody></table></div>';
  }

  if (data.unmatched.length) {
    html += '<div class="card"><h2>Unmatched — needs review (' + data.unmatched.length + ')</h2>';
    html += '<p style="font-size:13px;color:var(--muted);margin-top:0">'
         + 'Pick a candidate or skip the line. Picks are saved as permanent '
         + 'mappings — next time this supplier sends this code, it\\'ll match automatically.</p>';
    html += '<table><thead><tr><th>From invoice</th><th>Candidates</th>'
         + '<th class="num">Qty</th><th class="num">Unit cost</th></tr></thead><tbody>';
    data.unmatched.forEach((u, i) => {
      html += '<tr id="u-row-' + i + '">';
      html += '<td><strong>' + escape(u.description || '—') + '</strong>'
           + (u.supplier_code ? '<br><span style="color:var(--muted);font-size:12px">'
              + escape(u.supplier_code) + '</span>' : '') + '</td>';
      html += '<td><div class="candidates" id="cand-' + i + '">';
      if (u.candidates && u.candidates.length) {
        for (const c of u.candidates) {
          html += '<button class="cand-btn" onclick="pickCandidate(' + i
               + ',\\'' + c.product_id + '\\',\\'' + escapeAttr(c.sku || '')
               + '\\',\\'' + escapeAttr(c.name || '')
               + '\\')">' + escape(c.name || '') + ' '
               + '<span style="color:var(--muted)">('
               + Math.round(c.confidence * 100) + '%)</span></button>';
        }
      } else {
        html += '<span style="color:var(--muted)">no candidates</span>';
      }
      html += '<button class="cand-btn unmatch-skip" onclick="skipLine(' + i + ')">Skip</button>';
      html += '</div></td>';
      html += '<td class="num">' + u.quantity + '</td>';
      html += '<td class="num">' + u.unit_cost.toFixed(2) + '</td>';
      html += '</tr>';
    });
    html += '</tbody></table></div>';
  }

  html += '<div class="actions">';
  html += '<label class="receive"><input type="checkbox" id="receive" /> '
       + 'Mark as RECEIVED immediately (updates inventory)</label>';
  html += '<button class="primary" id="importBtn" onclick="doImport()">'
       + 'Import to Lightspeed</button>';
  html += '</div>';
  html += '<div id="importResult" style="margin-top:16px"></div>';

  resultsEl.innerHTML = html;
  resultsEl.classList.remove('hidden');
  updateImportButton();
}

function metaRow(label, value) {
  return '<div><label>' + label + '</label><span>' + escape(value) + '</span></div>';
}

function fmtMoney(n, cur) {
  if (n == null) return '—';
  return (cur ? cur + ' ' : '') + n.toFixed(2);
}

function escape(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[c]);
}
function escapeAttr(s) { return String(s == null ? '' : s).replace(/'/g, "\\\\'"); }

async function pickCandidate(idx, productId, sku, name) {
  const u = currentResult.unmatched[idx];
  // Save the mapping if supplier_code is present, so it's automatic next time.
  if (u.supplier_code && currentResult.invoice.supplier_id) {
    try {
      await fetch('/mappings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          supplier_id: currentResult.invoice.supplier_id,
          supplier_code: u.supplier_code,
          lightspeed_product_id: productId,
          lightspeed_sku: sku,
          product_name: name,
        }),
      });
    } catch (e) { console.warn('Failed to save mapping', e); }
  }
  unmatchedDecisions[idx] = {product_id: productId, sku, name};
  const cand = document.getElementById('cand-' + idx);
  cand.innerHTML = '<span style="color:var(--good);font-weight:500">✓ '
                 + escape(name) + '</span> '
                 + '<button class="cand-btn" onclick="resetLine(' + idx + ')">change</button>';
  updateImportButton();
}
function skipLine(idx) {
  unmatchedDecisions[idx] = 'skip';
  const cand = document.getElementById('cand-' + idx);
  cand.innerHTML = '<span style="color:var(--muted)">skipped</span> '
                 + '<button class="cand-btn" onclick="resetLine(' + idx + ')">undo</button>';
  updateImportButton();
}
function resetLine(idx) {
  delete unmatchedDecisions[idx];
  renderResults(currentResult);  // re-render to restore candidate buttons
}

function updateImportButton() {
  const btn = document.getElementById('importBtn');
  if (!btn) return;
  const unmatched = currentResult.unmatched || [];
  const undecided = unmatched.filter((_, i) => !(i in unmatchedDecisions)).length;
  if (undecided > 0) {
    btn.disabled = true;
    btn.textContent = 'Resolve ' + undecided + ' unmatched line(s) first';
  } else {
    btn.disabled = false;
    btn.textContent = 'Import to Lightspeed';
  }
}

async function doImport() {
  const inv = currentResult.invoice;
  if (!inv.supplier_id) { alert('No supplier resolved; cannot import.'); return; }
  if (!inv.invoice_number) { alert('No invoice number; cannot import.'); return; }

  const items = currentResult.matched.map(m => ({
    product_id: m.product_id, count: m.quantity, cost: m.unit_cost,
  }));
  currentResult.unmatched.forEach((u, i) => {
    const d = unmatchedDecisions[i];
    if (d && d !== 'skip') {
      items.push({product_id: d.product_id, count: u.quantity, cost: u.unit_cost});
    }
  });

  if (items.length === 0) {
    alert('No items to import.');
    return;
  }

  const btn = document.getElementById('importBtn');
  btn.disabled = true; btn.textContent = 'Importing...';

  try {
    const resp = await fetch('/invoices/import', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        supplier_invoice_number: inv.invoice_number,
        supplier_id: inv.supplier_id,
        items,
        receive_immediately: document.getElementById('receive').checked,
      }),
    });
    const data = await resp.json();
    const out = document.getElementById('importResult');
    if (resp.ok) {
      out.innerHTML = '<div class="success">✓ Imported as consignment '
                    + '<code>' + data.consignment_id + '</code> '
                    + '(status: ' + data.status + ', '
                    + data.items_added + ' items added)</div>';
    } else {
      out.innerHTML = '<div class="error">Import failed: '
                    + escape(data.detail || resp.statusText) + '</div>';
      btn.disabled = false; btn.textContent = 'Retry import';
    }
  } catch (err) {
    document.getElementById('importResult').innerHTML =
      '<div class="error">Network error: ' + escape(err.message) + '</div>';
    btn.disabled = false; btn.textContent = 'Retry import';
  }
}
</script>
</body>
</html>"""
