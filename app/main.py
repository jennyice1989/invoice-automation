"""
FastAPI service that exposes the Lightspeed integration.

Your extraction layer (whatever you build next) hands a structured invoice
to POST /invoices/import and gets back a consignment id + status.

Run locally:
    uvicorn app.main:app --reload

Render uses the `start` command in render.yaml.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from app.lightspeed import (
    LightspeedAuthError,
    LightspeedClient,
    LightspeedError,
    LightspeedNotFoundError,
    MatchedLineItem,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- #
# Config                                                                #
# --------------------------------------------------------------------- #

def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        # We don't crash at import — we crash on first use, so the app
        # still boots for /healthz checks even if secrets aren't wired up.
        return ""
    return value


LIGHTSPEED_DOMAIN_PREFIX = _required("LIGHTSPEED_DOMAIN_PREFIX")
LIGHTSPEED_TOKEN = _required("LIGHTSPEED_PERSONAL_TOKEN")
DEFAULT_OUTLET_ID = os.environ.get("LIGHTSPEED_DEFAULT_OUTLET_ID", "")


# --------------------------------------------------------------------- #
# App lifecycle                                                         #
# --------------------------------------------------------------------- #

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Hold a single httpx client for the process lifetime."""
    if LIGHTSPEED_DOMAIN_PREFIX and LIGHTSPEED_TOKEN:
        client = LightspeedClient(LIGHTSPEED_DOMAIN_PREFIX, LIGHTSPEED_TOKEN)
        app.state.lightspeed = client
        try:
            yield
        finally:
            await client.close()
    else:
        logger.warning(
            "LIGHTSPEED_DOMAIN_PREFIX or LIGHTSPEED_PERSONAL_TOKEN missing; "
            "API calls will fail until configured."
        )
        app.state.lightspeed = None
        yield


app = FastAPI(
    title="Invoice -> Lightspeed importer",
    version="0.1.0",
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


# --------------------------------------------------------------------- #
# Request / response models                                             #
# --------------------------------------------------------------------- #

class LineItemIn(BaseModel):
    """A line item that has already been matched to a Lightspeed product."""

    product_id: str = Field(..., description="Lightspeed product UUID")
    count: float = Field(..., gt=0, description="Quantity ordered")
    cost: float = Field(..., ge=0, description="Per-unit cost from invoice")
    received: float | None = Field(
        None, description="Qty actually received; defaults to count on receive"
    )


class InvoiceImportRequest(BaseModel):
    supplier_invoice_number: str
    items: list[LineItemIn]
    supplier_id: str | None = None
    outlet_id: str | None = None
    receive_immediately: bool = Field(
        False,
        description="If true, marks consignment RECEIVED in one call. "
                    "Use when invoice represents goods already in hand.",
    )
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


# --------------------------------------------------------------------- #
# Routes                                                                #
# --------------------------------------------------------------------- #

@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "configured": bool(LIGHTSPEED_DOMAIN_PREFIX)}


@app.get("/outlets")
async def list_outlets() -> dict:
    """List outlets so you can find the right outlet_id for imports."""
    try:
        outlets = await _client().list_outlets()
    except LightspeedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "data": [
            {"id": o["id"], "name": o.get("name")} for o in outlets
        ]
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
    """Substring search over supplier names. Use this when /lookup
    returns found:false to see what the supplier is actually named."""
    try:
        matches = await _client().search_suppliers(q)
    except LightspeedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "matches": [
            {"id": s["id"], "name": s.get("name")} for s in matches
        ]
    }


@app.get("/suppliers")
async def list_suppliers() -> dict:
    """List all suppliers. Useful for first-time inspection."""
    try:
        suppliers = await _client().list_suppliers()
    except LightspeedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "data": [
            {"id": s["id"], "name": s.get("name")} for s in suppliers
        ]
    }


@app.get("/products/lookup", response_model=ProductLookupResponse)
async def lookup_product(
    supplier_code: str | None = None,
    sku: str | None = None,
) -> ProductLookupResponse:
    """
    Look up a product, preferring supplier_code (what's on the invoice).

    Falls back to SKU if no supplier_code is given. This is the endpoint
    your extraction layer will hammer to match invoice lines to products.
    """
    if not (supplier_code or sku):
        raise HTTPException(
            status_code=400,
            detail="Pass either supplier_code or sku",
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


@app.post("/invoices/import", response_model=InvoiceImportResponse)
async def import_invoice(req: InvoiceImportRequest) -> InvoiceImportResponse:
    """
    Push a fully-matched invoice into Lightspeed as a SUPPLIER consignment.

    The caller is responsible for having resolved every line item to a
    Lightspeed product_id. Use /products/lookup to do that resolution.
    """
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
