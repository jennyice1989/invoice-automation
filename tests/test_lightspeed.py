"""
Tests for the Lightspeed client. We mock the HTTP layer so this runs
offline; the goal is to lock in the request shape and the consignment
state machine, not to test Lightspeed itself.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.lightspeed import (
    LightspeedAuthError,
    LightspeedClient,
    LightspeedError,
    MatchedLineItem,
)


def _mock_transport(handler):
    return httpx.MockTransport(handler)


@pytest.fixture
def client_factory():
    def _make(handler):
        client = LightspeedClient("teststore", "test-token")
        # Swap in a mock transport so no real network calls happen.
        client._client = httpx.AsyncClient(
            transport=_mock_transport(handler),
            headers=client._client.headers,
        )
        return client
    return _make


@pytest.mark.asyncio
async def test_create_consignment_sends_correct_payload(client_factory):
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "cons-1",
                "name": "Invoice INV-001",
                "outlet_id": "out-1",
                "status": "OPEN",
                "type": "SUPPLIER",
            },
        )

    client = client_factory(handler)
    result = await client.create_consignment(
        name="Invoice INV-001",
        outlet_id="out-1",
        supplier_id="sup-1",
        supplier_invoice="INV-001",
    )

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/2.0/consignments")
    # `reference` was not passed, so it should not be in the payload.
    assert captured["body"] == {
        "name": "Invoice INV-001",
        "outlet_id": "out-1",
        "type": "SUPPLIER",
        "status": "OPEN",
        "supplier_id": "sup-1",
        "supplier_invoice": "INV-001",
    }
    assert result["id"] == "cons-1"
    await client.close()


@pytest.mark.asyncio
async def test_create_consignment_retries_without_order_number_fields(client_factory):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "reference" in body:
            return httpx.Response(
                400,
                json={"errors": {"global": ["Order number validation failed"]}},
            )
        if "supplier_invoice" in body:
            return httpx.Response(
                400,
                json={"errors": {"global": ["Order number validation failed"]}},
            )
        return httpx.Response(200, json={"id": "cons-1", "name": body["name"]})

    client = client_factory(handler)
    result = await client.create_consignment(
        name="Invoice INV-001",
        outlet_id="out-1",
        supplier_id="sup-1",
        supplier_invoice="INV-001",
        reference="INV-001",
    )

    assert result["id"] == "cons-1"
    assert "reference" in bodies[0]
    assert "reference" not in bodies[1]
    assert "supplier_invoice" in bodies[1]
    assert "supplier_invoice" not in bodies[2]
    assert bodies[2]["name"] == "Invoice INV-001"
    await client.close()


@pytest.mark.asyncio
async def test_add_product_includes_received_when_set(client_factory):
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"product_id": "prod-1", "count": "5"})

    client = client_factory(handler)
    await client.add_product_to_consignment(
        "cons-1",
        MatchedLineItem(product_id="prod-1", count=5, cost=12.50, received=5),
    )

    assert captured["body"] == {
        "product_id": "prod-1",
        "count": 5,
        "cost": 12.5,
        "received": 5,
    }
    await client.close()


@pytest.mark.asyncio
async def test_add_product_omits_received_when_none(client_factory):
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    client = client_factory(handler)
    await client.add_product_to_consignment(
        "cons-1",
        MatchedLineItem(product_id="prod-1", count=5, cost=12.50),
    )

    assert "received" not in captured["body"]
    await client.close()


@pytest.mark.asyncio
async def test_invalid_status_rejected(client_factory):
    client = client_factory(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        await client.update_consignment_status(
            "cons-1", status="BOGUS", outlet_id="out-1", name="x"
        )
    await client.close()


@pytest.mark.asyncio
async def test_auth_error_raises(client_factory):
    def handler(request):
        return httpx.Response(401, json={"error": "invalid token"})

    client = client_factory(handler)
    with pytest.raises(LightspeedAuthError):
        await client.list_outlets()
    await client.close()


@pytest.mark.asyncio
async def test_list_products_walks_all_pages(client_factory):
    seen_after: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        seen_after.append(after)
        if after is None:
            return httpx.Response(200, json={"data": [
                {"id": "p1", "name": "First", "active": True, "version": 10},
                {"id": "p2", "name": "Second", "active": True, "version": 11},
            ]})
        if after == "11":
            return httpx.Response(200, json={"data": [
                {"id": "p3", "name": "Third", "active": True, "version": 12},
            ]})
        return httpx.Response(200, json={"data": []})

    client = client_factory(handler)
    products = await client.list_products(page_size=2)

    assert [p["id"] for p in products] == ["p1", "p2", "p3"]
    assert seen_after == [None, "11"]
    await client.close()


@pytest.mark.asyncio
async def test_list_suppliers_walks_all_pages(client_factory):
    seen_after: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        seen_after.append(after)
        if after is None:
            return httpx.Response(200, json={"data": [
                {"id": "s1", "name": "First", "version": 10},
                {"id": "s2", "name": "Second", "version": 11},
            ]})
        if after == "11":
            return httpx.Response(200, json={"data": [
                {"id": "s3", "name": "Third", "version": 12},
            ]})
        return httpx.Response(200, json={"data": []})

    client = client_factory(handler)
    suppliers = await client.list_suppliers(page_size=2)

    assert [s["id"] for s in suppliers] == ["s1", "s2", "s3"]
    assert seen_after == [None, "11"]
    await client.close()


@pytest.mark.asyncio
async def test_list_categories_walks_all_pages(client_factory):
    seen_after: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after")
        seen_after.append(after)
        if after is None:
            return httpx.Response(200, json={"data": [
                {"id": "c1", "name": "Dry Goods", "version": 10},
                {"id": "c2", "name": "Food", "version": 11},
            ]})
        if after == "11":
            return httpx.Response(200, json={"data": [
                {"id": "c3", "name": "Frozen", "version": 12},
            ]})
        return httpx.Response(200, json={"data": []})

    client = client_factory(handler)
    categories = await client.list_categories(page_size=2)

    assert [c["id"] for c in categories] == ["c1", "c2", "c3"]
    assert seen_after == [None, "11"]
    await client.close()


@pytest.mark.asyncio
async def test_create_brand_posts_name(client_factory):
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": {"id": "b1", "name": "Generic"}})

    client = client_factory(handler)
    brand = await client.create_brand("Generic")

    assert captured["body"] == {"name": "Generic"}
    assert brand["id"] == "b1"
    await client.close()


@pytest.mark.asyncio
async def test_find_supplier_by_name_normalizes_punctuation_and_suffix(client_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [
            {"id": "wrong", "name": "Aquatic Wholesale"},
            {"id": "right", "name": "Xtreme Aquatic Foods Inc"},
        ]})

    client = client_factory(handler)
    supplier = await client.find_supplier_by_name("XTREME AQUATIC FOODS, INC.")

    assert supplier["id"] == "right"
    await client.close()


@pytest.mark.asyncio
async def test_find_supplier_by_name_matches_compact_distribution_alias(client_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [
            {"id": "wrong", "name": "Reef Chowda"},
            {"id": "right", "name": "Reef H2O Distribution"},
        ]})

    client = client_factory(handler)
    supplier = await client.find_supplier_by_name("ReefH2O")

    assert supplier["id"] == "right"
    await client.close()


@pytest.mark.asyncio
async def test_search_products_ranks_full_catalog_locally(client_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [
            {"id": "wrong", "name": "Clown Tang Small/Medium", "sku": "11042"},
            {
                "id": "right",
                "name": "Seachem Shrimp Accessories - Tube ASM7078",
                "sku": "000116070782",
                "supplier_code": "SC07078",
            },
        ]})

    client = client_factory(handler)
    products = await client.search_products("Seachem Aquavitro Shrimp Tube")

    assert products[0]["id"] == "right"
    await client.close()


@pytest.mark.asyncio
async def test_create_product_accepts_data_list_response(client_factory):
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{
            "id": "prod-1",
            "name": "New Product",
            "sku": "SKU-1",
        }]})

    client = client_factory(handler)
    product = await client.create_product(
        name="New Product",
        sku="SKU-1",
        supplier_code="SUP-1",
        supply_price=1.25,
        retail_price=2.99,
    )

    assert captured["body"]["name"] == "New Product"
    assert captured["body"]["sku"] == "SKU-1"
    assert captured["body"]["supplier_code"] == "SUP-1"
    assert product["id"] == "prod-1"
    await client.close()


@pytest.mark.asyncio
async def test_create_product_fetches_product_when_response_is_id_string(client_factory):
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path.endswith("/products"):
            return httpx.Response(
                200,
                json={"data": "123e4567-e89b-12d3-a456-426614174000"},
            )
        if request.method == "GET" and request.url.path.endswith(
            "/products/123e4567-e89b-12d3-a456-426614174000"
        ):
            return httpx.Response(200, json={"data": {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "name": "Fetched Product",
            }})
        return httpx.Response(404)

    client = client_factory(handler)
    product = await client.create_product(name="Fetched Product")

    assert calls == [
        ("POST", "/api/2.0/products"),
        ("GET", "/api/2.0/products/123e4567-e89b-12d3-a456-426614174000"),
    ]
    assert product["name"] == "Fetched Product"
    await client.close()


@pytest.mark.asyncio
async def test_create_product_reports_unexpected_string_response(client_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": "missing required field"})

    client = client_factory(handler)
    with pytest.raises(LightspeedError, match="missing required field"):
        await client.create_product(name="Bad Product")
    await client.close()


@pytest.mark.asyncio
async def test_import_invoice_full_receive_flow(client_factory):
    """End-to-end: create -> add items -> dispatched -> received."""
    calls: list[tuple[str, str]] = []
    consignment_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))

        if request.method == "POST" and path.endswith("/consignments"):
            consignment_body.update(json.loads(request.content))
            return httpx.Response(200, json={
                "id": "cons-1", "name": "x", "status": "OPEN",
            })
        if request.method == "POST" and "/products" in path:
            return httpx.Response(200, json={"product_id": "p"})
        if request.method == "PUT":
            body = json.loads(request.content)
            return httpx.Response(200, json={"status": body["status"]})
        return httpx.Response(404)

    client = client_factory(handler)
    result = await client.import_invoice(
        outlet_id="out-1",
        supplier_id="sup-1",
        supplier_invoice_number="INV-001",
        items=[
            MatchedLineItem(product_id="p1", count=3, cost=10),
            MatchedLineItem(product_id="p2", count=2, cost=20),
        ],
        receive_immediately=True,
    )

    methods = [m for m, _ in calls]
    # 1 create + 2 add product + 2 status updates (DISPATCHED, RECEIVED)
    assert methods.count("POST") == 3
    assert methods.count("PUT") == 2
    assert "reference" not in consignment_body
    assert result["status"] == "RECEIVED"
    assert result["items_added"] == 2
    assert result["items_failed"] == 0
    await client.close()


@pytest.mark.asyncio
async def test_import_invoice_stops_at_open_when_not_receiving(client_factory):
    calls: list[tuple[str, str]] = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path.endswith("/consignments"):
            return httpx.Response(200, json={"id": "c1", "status": "OPEN"})
        return httpx.Response(200, json={})

    client = client_factory(handler)
    result = await client.import_invoice(
        outlet_id="out-1",
        supplier_id=None,
        supplier_invoice_number="INV-002",
        items=[MatchedLineItem(product_id="p1", count=1, cost=1)],
        receive_immediately=False,
    )

    assert result["status"] == "OPEN"
    # No PUTs — we never moved status.
    assert all(method != "PUT" for method, _ in calls)
    await client.close()


@pytest.mark.asyncio
async def test_import_invoice_empty_items_rejected(client_factory):
    client = client_factory(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        await client.import_invoice(
            outlet_id="out-1",
            supplier_id=None,
            supplier_invoice_number="X",
            items=[],
        )
    await client.close()
