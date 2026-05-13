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

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import (
    SupplierSkuMapping,
    init_db,
    session_scope,
    upsert_mapping,
)
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
