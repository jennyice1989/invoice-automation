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
    seen_pages: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        seen_pages.append(page)
        if page == "1":
            return httpx.Response(200, json={"data": [
                {"id": "p1", "name": "First", "active": True},
                {"id": "p2", "name": "Second", "active": True},
            ]})
        if page == "2":
            return httpx.Response(200, json={"data": [
                {"id": "p3", "name": "Third", "active": True},
            ]})
        return httpx.Response(200, json={"data": []})

    client = client_factory(handler)
    products = await client.list_products(page_size=2)

    assert [p["id"] for p in products] == ["p1", "p2", "p3"]
    assert seen_pages == ["1", "2"]
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
async def test_import_invoice_full_receive_flow(client_factory):
    """End-to-end: create -> add items -> dispatched -> received."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))

        if request.method == "POST" and path.endswith("/consignments"):
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
