"""
Lightspeed X-Series API client.

Wraps the v2.0 endpoints needed to push invoices into Lightspeed as
SUPPLIER consignments. Handles auth, retries, rate limiting, and the
stateful consignment workflow (OPEN -> SENT -> DISPATCHED -> RECEIVED).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class LightspeedError(Exception):
    """Base error for Lightspeed API failures."""


class LightspeedAuthError(LightspeedError):
    """401/403 — token invalid or missing scope."""


class LightspeedNotFoundError(LightspeedError):
    """404 — resource doesn't exist."""


class LightspeedRateLimitError(LightspeedError):
    """429 — slow down."""


@dataclass
class MatchedLineItem:
    """A line item from an invoice that has already been matched to a
    Lightspeed product. Your extraction + matching layer produces these;
    this client consumes them.

    `count` is the quantity ordered. `received` is what actually arrived
    (default to count if you're receiving in one step). `cost` is the
    per-unit cost — Lightspeed uses this to update the product's supply
    price and weighted average cost.
    """

    product_id: str
    count: float
    cost: float
    received: float | None = None


class LightspeedClient:
    """
    Thin async client over the X-Series 2.0 API.

    Auth uses a Personal Token (Setup > Personal Tokens in the Lightspeed
    admin). For multi-retailer use you'd swap this for OAuth, but for
    internal use a Personal Token is fine and never expires unless revoked.
    """

    def __init__(
        self,
        domain_prefix: str,
        personal_token: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        if not domain_prefix or not personal_token:
            raise ValueError("domain_prefix and personal_token are required")

        self.base_url = f"https://{domain_prefix}.retail.lightspeed.app/api/2.0"
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {personal_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                # Lightspeed asks integrations to identify themselves.
                "User-Agent": "invoice-importer/0.1 (internal)",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "LightspeedClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------ #
    # Core request handling                                              #
    # ------------------------------------------------------------------ #

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """
        Execute a request with retry on 429 / 5xx.

        Lightspeed's rate limiting is documented as a leaky-bucket; the API
        returns 429 with a Retry-After header when you exceed it. We honor
        the header and back off exponentially on 5xx.
        """
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                resp = await self._client.request(
                    method, url, json=json, params=params
                )
            except httpx.RequestError as exc:
                last_exc = exc
                logger.warning("Network error on %s %s: %s", method, path, exc)
                await self._backoff(attempt)
                continue

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "1"))
                logger.warning("Rate limited; sleeping %.1fs", retry_after)
                await asyncio.sleep(retry_after)
                continue

            if 500 <= resp.status_code < 600:
                logger.warning(
                    "Server error %s on %s %s; retrying",
                    resp.status_code, method, path,
                )
                await self._backoff(attempt)
                continue

            if resp.status_code in (401, 403):
                raise LightspeedAuthError(
                    f"Auth failed ({resp.status_code}): {resp.text[:200]}"
                )

            if resp.status_code == 404:
                raise LightspeedNotFoundError(f"Not found: {path}")

            if not resp.is_success:
                raise LightspeedError(
                    f"{method} {path} failed ({resp.status_code}): "
                    f"{resp.text[:500]}"
                )

            # 204 No Content is possible on some endpoints
            if resp.status_code == 204 or not resp.content:
                return {}
            return resp.json()

        raise LightspeedError(
            f"{method} {path} failed after {self.max_retries} attempts: {last_exc}"
        )

    async def _backoff(self, attempt: int) -> None:
        await asyncio.sleep(2 ** attempt)

    # ------------------------------------------------------------------ #
    # Lookups                                                            #
    # ------------------------------------------------------------------ #

    async def list_outlets(self) -> list[dict]:
        """Return all outlets. Most retailers have one; we still ask."""
        data = await self._request("GET", "/outlets")
        return data.get("data", [])

    async def list_suppliers(self, *, page_size: int = 200) -> list[dict]:
        """Return all suppliers. Useful for debugging name mismatches and
        for building a local supplier->id cache in the extraction layer."""
        data = await self._request(
            "GET", "/suppliers", params={"page_size": page_size}
        )
        return data.get("data", [])

    async def search_suppliers(self, query: str) -> list[dict]:
        """Substring search over supplier names. Case- and space-insensitive.
        Returns all suppliers whose name contains the query."""
        normalized = "".join(query.lower().split())
        suppliers = await self.list_suppliers()
        return [
            s for s in suppliers
            if normalized in "".join(s.get("name", "").lower().split())
        ]

    async def find_supplier_by_name(self, name: str) -> dict | None:
        """
        Look up a supplier by name. Lightspeed's supplier list is small
        enough to scan client-side; the API doesn't expose a name filter.
        """
        # /suppliers is paginated; for typical retailers (< few hundred
        # suppliers) one page is enough. If you exceed that, add pagination.
        data = await self._request("GET", "/suppliers", params={"page_size": 200})
        needle = name.strip().lower()
        for supplier in data.get("data", []):
            if supplier.get("name", "").strip().lower() == needle:
                return supplier
        return None

    async def find_product_by_sku(self, sku: str) -> dict | None:
        """Find a product by exact SKU using the /search endpoint.

        The list-products `?sku=` parameter has been observed to return
        non-matching results (treating absent matches as a default
        product). The dedicated /search endpoint with type=products is
        the documented exact-match path. SKU values must be lowercased.
        """
        if not sku:
            return None
        data = await self._request(
            "GET",
            "/search",
            params={
                "type": "products",
                "sku": sku.lower(),
                "page_size": 5,
            },
        )
        items = data.get("data", [])
        # Defensive: verify the returned item's SKU actually matches.
        # If /search ever returns broader results, this filters them.
        needle = sku.lower()
        for item in items:
            if (item.get("sku") or "").lower() == needle:
                return item
        return None

    async def find_product_by_supplier_code(
        self, supplier_code: str
    ) -> dict | None:
        """Find a product by supplier_code. Falls back to list-products
        since /search doesn't index supplier_code as a search field."""
        if not supplier_code:
            return None
        data = await self._request(
            "GET",
            "/products",
            params={"supplier_code": supplier_code, "page_size": 5},
        )
        # Same defense as SKU: verify exact match before trusting result.
        needle = supplier_code.strip().lower()
        for item in data.get("data", []):
            if (item.get("supplier_code") or "").strip().lower() == needle:
                return item
        return None

    async def find_product_by_barcode(self, barcode: str) -> dict | None:
        """Find a product by exact barcode."""
        if not barcode:
            return None
        data = await self._request(
            "GET",
            "/search",
            params={
                "type": "products",
                "barcode": barcode,
                "page_size": 5,
            },
        )
        needle = barcode.strip()
        for item in data.get("data", []):
            pb = item.get("barcode")
            if isinstance(pb, list):
                if needle in pb:
                    return item
            elif (pb or "").strip() == needle:
                return item
        return None

    # ------------------------------------------------------------------ #
    # Consignment workflow                                               #
    # ------------------------------------------------------------------ #

    async def create_consignment(
        self,
        *,
        name: str,
        outlet_id: str,
        supplier_id: str | None = None,
        supplier_invoice: str | None = None,
        reference: str | None = None,
    ) -> dict:
        """
        Create a SUPPLIER consignment in OPEN status.

        `supplier_invoice` is the supplier's invoice number — shows up in
        the Lightspeed UI and is searchable. Always set it.
        """
        payload: dict[str, Any] = {
            "name": name,
            "outlet_id": outlet_id,
            "type": "SUPPLIER",
            "status": "OPEN",
        }
        if supplier_id:
            payload["supplier_id"] = supplier_id
        if supplier_invoice:
            payload["supplier_invoice"] = supplier_invoice
        if reference:
            payload["reference"] = reference

        data = await self._request("POST", "/consignments", json=payload)
        return data.get("data", data)

    async def add_product_to_consignment(
        self,
        consignment_id: str,
        item: MatchedLineItem,
    ) -> dict:
        """
        Add one line item to the consignment.

        If `received` is provided, the product is recorded as already
        received in this call — useful for the "single-step receive"
        pattern where the invoice represents goods that physically arrived.
        """
        payload: dict[str, Any] = {
            "product_id": item.product_id,
            "count": item.count,
            "cost": item.cost,
        }
        if item.received is not None:
            payload["received"] = item.received

        return await self._request(
            "POST",
            f"/consignments/{consignment_id}/products",
            json=payload,
        )

    async def update_consignment_status(
        self,
        consignment_id: str,
        *,
        status: str,
        outlet_id: str,
        name: str,
    ) -> dict:
        """
        Move the consignment through its lifecycle.

        Valid transitions for SUPPLIER:
          OPEN -> SENT -> DISPATCHED -> RECEIVED
        SENT can be skipped. DISPATCHED creates the IN_TRANSIT stock
        movement; RECEIVED is what actually adds inventory.

        Once RECEIVED, received quantities are locked.
        """
        valid = {"OPEN", "SENT", "DISPATCHED", "RECEIVED", "CANCELLED"}
        if status not in valid:
            raise ValueError(f"Invalid status {status!r}; expected one of {valid}")

        return await self._request(
            "PUT",
            f"/consignments/{consignment_id}",
            json={
                "outlet_id": outlet_id,
                "name": name,
                "type": "SUPPLIER",
                "status": status,
            },
        )

    async def get_consignment(self, consignment_id: str) -> dict:
        data = await self._request("GET", f"/consignments/{consignment_id}")
        return data.get("data", data)

    # ------------------------------------------------------------------ #
    # Product create / update                                            #
    # ------------------------------------------------------------------ #

    async def create_product(
        self,
        *,
        name: str,
        sku: str | None = None,
        supplier_id: str | None = None,
        supplier_code: str | None = None,
        barcode: str | None = None,
        supply_price: float | None = None,
        retail_price: float | None = None,
        description: str | None = None,
    ) -> dict:
        """Create a product in Lightspeed. Returns the new product dict."""
        payload: dict[str, Any] = {"name": name}
        if sku:
            payload["sku"] = sku
        if supplier_id:
            payload["supplier_id"] = supplier_id
        if supplier_code:
            payload["supplier_code"] = supplier_code
        if barcode:
            payload["barcode"] = barcode
        if supply_price is not None:
            payload["supply_price"] = supply_price
        if retail_price is not None:
            # X-Series stores retail price as a list of (price_book_id, price);
            # the simplest write path is "price_excluding_tax" on default book.
            payload["price_excluding_tax"] = retail_price
        if description:
            payload["description"] = description

        data = await self._request("POST", "/products", json=payload)
        return data.get("data", data)

    async def update_product(
        self,
        product_id: str,
        *,
        retail_price: float | None = None,
        supply_price: float | None = None,
        supplier_code: str | None = None,
    ) -> dict:
        """Update select fields on an existing product."""
        payload: dict[str, Any] = {}
        if retail_price is not None:
            payload["price_excluding_tax"] = retail_price
        if supply_price is not None:
            payload["supply_price"] = supply_price
        if supplier_code is not None:
            payload["supplier_code"] = supplier_code

        if not payload:
            return {}

        data = await self._request(
            "PUT", f"/products/{product_id}", json=payload
        )
        return data.get("data", data)

    # ------------------------------------------------------------------ #
    # High-level: push a complete invoice                                #
    # ------------------------------------------------------------------ #

    async def import_invoice(
        self,
        *,
        outlet_id: str,
        supplier_id: str | None,
        supplier_invoice_number: str,
        items: list[MatchedLineItem],
        receive_immediately: bool = False,
        name: str | None = None,
    ) -> dict:
        """
        Push a full invoice through the consignment workflow.

        If `receive_immediately` is True, the consignment is created,
        populated, and marked RECEIVED in one shot — appropriate when the
        invoice represents goods that are already on your shelves.

        Otherwise it stops at OPEN so you can review and receive manually
        in the Lightspeed UI, or call `update_consignment_status` later.

        Returns a summary dict with the consignment id, final status, and
        any per-item errors so the caller can present a clear result.
        """
        if not items:
            raise ValueError("Cannot import an invoice with zero line items")

        consignment_name = name or f"Invoice {supplier_invoice_number}"

        consignment = await self.create_consignment(
            name=consignment_name,
            outlet_id=outlet_id,
            supplier_id=supplier_id,
            supplier_invoice=supplier_invoice_number,
            reference=supplier_invoice_number,
        )
        consignment_id = consignment["id"]
        logger.info("Created consignment %s", consignment_id)

        item_errors: list[dict] = []
        for item in items:
            try:
                # When receiving immediately we set `received` = count by
                # default so the inventory math is correct on RECEIVED.
                if receive_immediately and item.received is None:
                    item = MatchedLineItem(
                        product_id=item.product_id,
                        count=item.count,
                        cost=item.cost,
                        received=item.count,
                    )
                await self.add_product_to_consignment(consignment_id, item)
            except LightspeedError as exc:
                logger.warning(
                    "Failed to add product %s: %s", item.product_id, exc
                )
                item_errors.append(
                    {"product_id": item.product_id, "error": str(exc)}
                )

        final_status = "OPEN"
        if receive_immediately and not item_errors:
            # Skip SENT; go OPEN -> DISPATCHED -> RECEIVED.
            await self.update_consignment_status(
                consignment_id,
                status="DISPATCHED",
                outlet_id=outlet_id,
                name=consignment_name,
            )
            await self.update_consignment_status(
                consignment_id,
                status="RECEIVED",
                outlet_id=outlet_id,
                name=consignment_name,
            )
            final_status = "RECEIVED"

        return {
            "consignment_id": consignment_id,
            "status": final_status,
            "items_added": len(items) - len(item_errors),
            "items_failed": len(item_errors),
            "errors": item_errors,
        }
