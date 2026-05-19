"""
Lightspeed X-Series API client.

Wraps the v2.0 endpoints needed to push invoices into Lightspeed as
SUPPLIER consignments. Handles auth, retries, rate limiting, and the
stateful consignment workflow (OPEN -> SENT -> DISPATCHED -> RECEIVED).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _is_live_product(product: dict) -> bool:
    """True if the product can be updated/used (not deleted, not inactive).

    /search endpoint returns deleted and inactive products in results, but
    PUT/POST against them returns 404. Filter aggressively client-side.
    """
    if product.get("deleted_at"):
        return False
    # X-Series uses `active: True/False` on products
    if product.get("active") is False:
        return False
    return True


def _norm_search(s: str | None) -> str:
    if not s:
        return ""
    return " ".join(str(s).lower().replace("-", " ").split())


def _norm_supplier_name(s: str | None) -> str:
    """Normalize supplier names for Lightspeed/invoice comparison."""
    if not s:
        return ""
    text = str(s).lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [
        token for token in text.split()
        if token not in {
            "inc", "incorporated", "llc", "ltd", "co", "company", "corp",
            "corporation", "distribution", "distributors", "distributor",
        }
    ]
    return " ".join(tokens)


def _compact_supplier_name(s: str | None) -> str:
    return _norm_supplier_name(s).replace(" ", "")


def _supplier_names_match(a: str | None, b: str | None) -> bool:
    left = _norm_supplier_name(a)
    right = _norm_supplier_name(b)
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    compact_left = left.replace(" ", "")
    compact_right = right.replace(" ", "")
    return (
        compact_left == compact_right
        or compact_left in compact_right
        or compact_right in compact_left
    )


def _product_search_score(query: str, product: dict) -> float:
    """Score a catalog product for manual search.

    Lightspeed's /search endpoint is not reliable for free-text product
    lookup here, so the app ranks a fully fetched catalog locally.
    """
    q = _norm_search(query)
    if not q:
        return 0.0

    fields = [
        _norm_search(product.get("name")),
        _norm_search(product.get("sku")),
        _norm_search(product.get("supplier_code")),
        _norm_search(product.get("barcode")),
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
    ) -> Any:
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
            try:
                return resp.json()
            except ValueError as exc:
                raise LightspeedError(
                    f"{method} {path} returned non-JSON response: "
                    f"{resp.text[:500]}"
                ) from exc

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

    async def list_suppliers(
        self,
        *,
        page_size: int = 200,
        max_pages: int = 100,
    ) -> list[dict]:
        """Return all suppliers. Useful for debugging name mismatches and
        for building a local supplier->id cache in the extraction layer."""
        suppliers: list[dict] = []
        seen_ids: set[str] = set()
        after: int | None = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"page_size": page_size}
            if after is not None:
                params["after"] = after
            data = await self._request("GET", "/suppliers", params=params)
            raw_items = data.get("data", []) if isinstance(data, dict) else []
            if not isinstance(raw_items, list) or not raw_items:
                break

            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                supplier_id = item.get("id")
                if supplier_id and supplier_id in seen_ids:
                    continue
                if supplier_id:
                    seen_ids.add(supplier_id)
                suppliers.append(item)

            versions = [
                int(item["version"]) for item in raw_items
                if isinstance(item, dict)
                and str(item.get("version", "")).isdigit()
            ]
            next_after = max(versions) if versions else None
            if len(raw_items) < page_size or next_after is None or next_after == after:
                break
            after = next_after

        return suppliers

    async def list_categories(self, *, page_size: int = 500) -> list[dict]:
        """Return all product categories. X-Series supports hierarchy via
        category_path; categories are returned flat with parent references.

        Defensive against unexpected response shapes — logs and skips
        non-dict items rather than crashing the caller.
        """
        data = await self._request(
            "GET", "/product_categories", params={"page_size": page_size}
        )
        raw = data.get("data", [])
        if not isinstance(raw, list):
            logger.warning(
                "Unexpected /product_categories shape: data is %s, not list. "
                "Full response keys: %s",
                type(raw).__name__, list(data.keys()) if isinstance(data, dict) else "n/a",
            )
            return []
        result = []
        for item in raw:
            if isinstance(item, dict):
                result.append(item)
            else:
                logger.warning(
                    "Skipping non-dict category item: %r (type %s)",
                    item, type(item).__name__,
                )
        return result

    async def list_brands(self, *, page_size: int = 500) -> list[dict]:
        """Return all brands."""
        data = await self._request(
            "GET", "/brands", params={"page_size": page_size}
        )
        return data.get("data", [])

    async def list_tags(self, *, page_size: int = 500) -> list[dict]:
        data = await self._request(
            "GET", "/tags", params={"page_size": page_size}
        )
        return data.get("data", [])

    async def list_products(
        self,
        *,
        supplier_id: str | None = None,
        page_size: int = 200,
        max_pages: int = 200,
    ) -> list[dict]:
        """Return the full live product catalog, walking cursor pagination.

        X-Series API 2.0 uses product `version` cursors (`after`/`before`)
        for list pagination. A traditional `page=2` parameter can be
        ignored, which leaves the app with only the first slice of inventory.
        """
        products: list[dict] = []
        seen_ids: set[str] = set()
        after: int | None = None
        for _ in range(max_pages):
            params: dict[str, Any] = {"page_size": page_size}
            if after is not None:
                params["after"] = after
            if supplier_id:
                params["supplier_id"] = supplier_id
            data = await self._request("GET", "/products", params=params)
            raw_items = data.get("data", [])
            if not isinstance(raw_items, list) or not raw_items:
                break

            added = 0
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                product_id = item.get("id")
                if product_id and product_id in seen_ids:
                    continue
                if product_id:
                    seen_ids.add(product_id)
                if _is_live_product(item):
                    products.append(item)
                    added += 1

            versions = [
                int(item["version"]) for item in raw_items
                if isinstance(item, dict)
                and str(item.get("version", "")).isdigit()
            ]
            next_after = max(versions) if versions else None
            if (
                len(raw_items) < page_size
                or added == 0
                or next_after is None
                or next_after == after
            ):
                break
            after = next_after

        return products

    async def search_products(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> list[dict]:
        """Search products locally across the full live catalog."""
        query = (query or "").strip()
        if not query:
            return []
        products = await self.list_products()
        scored = [
            (_product_search_score(query, product), product)
            for product in products
        ]
        scored = [(score, product) for score, product in scored if score >= 0.25]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [product for _, product in scored[:limit]]

    async def search_suppliers(self, query: str) -> list[dict]:
        """Substring search over supplier names. Case- and space-insensitive.
        Returns all suppliers whose name contains the query."""
        normalized = _norm_supplier_name(query)
        suppliers = await self.list_suppliers()
        return [
            s for s in suppliers
            if _supplier_names_match(normalized, s.get("name"))
        ]

    async def find_supplier_by_name(self, name: str) -> dict | None:
        """
        Look up a supplier by name. Lightspeed's supplier list is small
        enough to scan client-side; the API doesn't expose a name filter.
        """
        needle = _norm_supplier_name(name)
        for supplier in await self.list_suppliers():
            if _supplier_names_match(needle, supplier.get("name")):
                return supplier
        return None

    async def find_product_by_sku(self, sku: str) -> dict | None:
        """Find a product by exact SKU using the /search endpoint.

        Filters out deleted/inactive products — these can appear in
        search results but cannot be updated (PUT returns 404).
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
        needle = sku.lower()
        for item in items:
            if (item.get("sku") or "").lower() != needle:
                continue
            if not _is_live_product(item):
                continue
            return item
        return None

    async def find_product_by_supplier_code(
        self, supplier_code: str
    ) -> dict | None:
        if not supplier_code:
            return None
        data = await self._request(
            "GET",
            "/products",
            params={"supplier_code": supplier_code, "page_size": 5},
        )
        needle = supplier_code.strip().lower()
        for item in data.get("data", []):
            if (item.get("supplier_code") or "").strip().lower() != needle:
                continue
            if not _is_live_product(item):
                continue
            return item
        return None

    async def find_product_by_barcode(self, barcode: str) -> dict | None:
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
            matched = False
            if isinstance(pb, list):
                matched = needle in pb
            else:
                matched = (pb or "").strip() == needle
            if not matched:
                continue
            if not _is_live_product(item):
                continue
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

        try:
            data = await self._request("POST", "/consignments", json=payload)
        except LightspeedError as exc:
            message = str(exc).lower()
            if "order number validation failed" not in message:
                raise

            # Some Lightspeed accounts validate `reference` and/or
            # `supplier_invoice` as an order number. Keep the invoice number
            # in the consignment name and retry with the stricter fields
            # removed so the import is not blocked.
            retry_payload = dict(payload)
            retry_payload.pop("reference", None)
            if retry_payload != payload:
                try:
                    data = await self._request(
                        "POST", "/consignments", json=retry_payload
                    )
                    return data.get("data", data)
                except LightspeedError as retry_exc:
                    if "order number validation failed" not in str(retry_exc).lower():
                        raise

            retry_payload.pop("supplier_invoice", None)
            data = await self._request("POST", "/consignments", json=retry_payload)
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

    async def get_product(self, product_id: str) -> dict:
        """Fetch one product by id and normalize Lightspeed's wrapper."""
        data = await self._request("GET", f"/products/{product_id}")
        product = self._unwrap_product_response(data, context="get product")
        if not isinstance(product, dict):
            raise LightspeedError(
                f"Unexpected Lightspeed response for get product: "
                f"{type(product).__name__}"
            )
        return product

    def _unwrap_product_response(self, data: Any, *, context: str) -> dict | str:
        """Normalize the product response shapes Lightspeed can return."""
        if isinstance(data, dict):
            inner = data.get("data", data)
        else:
            inner = data

        if isinstance(inner, list):
            if not inner:
                raise LightspeedError(
                    f"Lightspeed returned empty data during {context}"
                )
            inner = inner[0]

        if isinstance(inner, dict):
            return inner
        if isinstance(inner, str):
            return inner

        raise LightspeedError(
            f"Unexpected Lightspeed response during {context}: "
            f"{type(inner).__name__}"
        )

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
        brand_id: str | None = None,
        category_id: str | None = None,
        tag_ids: list[str] | None = None,
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
            payload["price_excluding_tax"] = retail_price
        if description:
            payload["description"] = description
        if brand_id:
            payload["brand_id"] = brand_id
        if category_id:
            # X-Series 2.0 uses product_category_id
            payload["product_category_id"] = category_id
        if tag_ids:
            payload["tag_ids"] = tag_ids

        data = await self._request("POST", "/products", json=payload)
        # POST /products may return {"data": [{...product...}]}, a single
        # product dict, or on some accounts just the new product id string.
        inner = self._unwrap_product_response(data, context="product create")
        if isinstance(inner, dict):
            return inner
        if _UUID_RE.match(inner.strip()):
            return await self.get_product(inner.strip())
        raise LightspeedError(
            "Unexpected Lightspeed response during product create: "
            f"string response {inner[:300]!r}"
        )

    async def update_product(
        self,
        product_id: str,
        *,
        retail_price: float | None = None,
        supply_price: float | None = None,
        supplier_code: str | None = None,
    ) -> dict | None:
        """Update select fields on an existing product.

        Returns None if the product can't be updated (404). This is a soft
        failure — the consignment can still proceed; we just couldn't
        refresh prices on the existing product.
        """
        payload: dict[str, Any] = {}
        if retail_price is not None:
            payload["price_excluding_tax"] = retail_price
        if supply_price is not None:
            payload["supply_price"] = supply_price
        if supplier_code is not None:
            payload["supplier_code"] = supplier_code

        if not payload:
            return None

        try:
            data = await self._request(
                "PUT", f"/products/{product_id}", json=payload
            )
            return data.get("data", data)
        except LightspeedNotFoundError:
            logger.warning(
                "Skipping update for product %s (404 — likely deleted)",
                product_id,
            )
            return None

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
