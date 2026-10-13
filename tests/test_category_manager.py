from __future__ import annotations

import pytest

from app import main
from app.db import CatalogProduct


class FakeSession:
    def __init__(self):
        self.added = []
        self.flushed = False

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushed = True


class FakeCategoryClient:
    def __init__(self, *, products, updates):
        self.products = products
        self.updates = updates
        self.update_calls = []

    async def list_categories(self):
        return [
            {"id": "cat-plumbing", "name": "Plumbing"},
            {
                "id": "cat-pvc",
                "name": "PVC Fittings",
                "category_path": [
                    {"name": "Plumbing"},
                    {"name": "PVC Fittings"},
                ],
            },
            {
                "id": "cat-check",
                "name": "Check Valves",
                "category_path": [
                    {"name": "Plumbing"},
                    {"name": "Check Valves"},
                ],
            },
        ]

    async def get_product(self, product_id):
        value = self.products[product_id]
        if isinstance(value, list):
            return value.pop(0)
        return value

    async def update_product_category(self, product_id, category_id):
        self.update_calls.append((product_id, category_id))
        return self.updates.get(product_id)


@pytest.mark.asyncio
async def test_category_apply_rejects_stale_preview(monkeypatch):
    client = FakeCategoryClient(
        products={
            "prod-1": {
                "id": "prod-1",
                "name": "PVC Elbow",
                "product_category_id": "cat-check",
                "product_category_name": "Plumbing / Check Valves",
            },
        },
        updates={},
    )
    session = FakeSession()
    monkeypatch.setattr(main, "_client", lambda: client)

    body = main.CategoryAssignmentRequest(assignments=[
        main.CategoryAssignmentItem(
            product_id="prod-1",
            category_path="Plumbing / PVC Fittings",
            preview_category_id="cat-old",
        ),
    ])

    result = await main.apply_category_assignments(body, session=session)

    assert result["ok"] is False
    assert result["results"] == [{
        "product_id": "prod-1",
        "ok": False,
        "stale": True,
        "error": "Product category changed since preview; refresh before applying",
    }]
    assert client.update_calls == []
    assert session.flushed is True
    assert session.added[0].status == "stale"


@pytest.mark.asyncio
async def test_category_apply_records_partial_failures_and_updates_cache(monkeypatch):
    client = FakeCategoryClient(
        products={
            "prod-ok": [
                {
                    "id": "prod-ok",
                    "name": "PVC Tee",
                    "product_category_id": "cat-old",
                    "product_category_name": "Old Category",
                },
                {
                    "id": "prod-ok",
                    "name": "PVC Tee",
                    "product_category_id": "cat-pvc",
                    "product_category_name": "Plumbing / PVC Fittings",
                },
            ],
            "prod-missing": {
                "id": "prod-missing",
                "name": "PVC Union",
                "product_category_id": "cat-old",
                "product_category_name": "Old Category",
            },
        },
        updates={
            "prod-ok": {"id": "prod-ok", "product_category_id": "cat-pvc"},
            "prod-missing": None,
        },
    )
    session = FakeSession()
    upserted = []

    async def fake_upsert_cached_product(session_arg, product):
        upserted.append((session_arg, product))

    monkeypatch.setattr(main, "_client", lambda: client)
    monkeypatch.setattr(main, "upsert_cached_product", fake_upsert_cached_product)

    body = main.CategoryAssignmentRequest(assignments=[
        main.CategoryAssignmentItem(
            product_id="prod-ok",
            category_path="Plumbing / PVC Fittings",
            preview_category_id="cat-old",
        ),
        main.CategoryAssignmentItem(
            product_id="prod-missing",
            category_path="Plumbing / PVC Fittings",
            preview_category_id="cat-old",
        ),
    ])

    result = await main.apply_category_assignments(body, session=session)

    assert result["ok"] is False
    assert result["results"] == [
        {
            "product_id": "prod-ok",
            "ok": True,
            "category": "Plumbing / PVC Fittings",
        },
        {
            "product_id": "prod-missing",
            "ok": False,
            "error": "Lightspeed could not update this product",
        },
    ]
    assert client.update_calls == [
        ("prod-ok", "cat-pvc"),
        ("prod-missing", "cat-pvc"),
    ]
    assert upserted == [(session, {
        "id": "prod-ok",
        "name": "PVC Tee",
        "product_category_id": "cat-pvc",
        "product_category_name": "Plumbing / PVC Fittings",
    })]
    assert [row.status for row in session.added] == ["success", "failed"]
    assert session.flushed is True


@pytest.mark.asyncio
async def test_category_product_row_flags_variant_family_members():
    class FamilySession:
        async def execute(self, _stmt):
            class Result:
                def scalars(self):
                    return self

                def all(self):
                    return [
                        CatalogProduct(
                            lightspeed_product_id="prod-1",
                            name="Blue Widget Small",
                            active=True,
                            raw={"product_family_id": "family-1"},
                        ),
                        CatalogProduct(
                            lightspeed_product_id="prod-2",
                            name="Blue Widget Large",
                            category_name="Old Category",
                            active=True,
                            raw={"product_family_id": "family-1"},
                        ),
                    ]

            return Result()

    row = await main._category_product_row(
        FamilySession(),
        CatalogProduct(
            lightspeed_product_id="prod-1",
            name="Blue Widget Small",
            active=True,
            raw={"product_family_id": "family-1"},
        ),
    )

    assert row["requires_family_review"] is True
    assert row["family_members"] == [{
        "id": "prod-2",
        "name": "Blue Widget Large",
        "category_name": "Old Category",
    }]
