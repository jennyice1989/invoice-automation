from __future__ import annotations

import pytest

from app.product_categories import (
    assignable_paths,
    bulk_create_payload,
    category_id_for_path,
    category_setup_preview,
    suggest_category,
    taxonomy_nodes,
)


class Product:
    def __init__(self, name: str, category_name: str | None = None, raw: dict | None = None):
        self.name = name
        self.category_name = category_name
        self.raw = raw or {}


def test_taxonomy_counts_and_pvc_grouping():
    nodes = taxonomy_nodes()
    assignable = assignable_paths()

    assert len(nodes) == 110
    assert len(assignable) == 98
    assert "Plumbing / PVC Fittings" in assignable
    assert "Plumbing / PVC Elbows" not in assignable
    assert "Plumbing / PVC Tees" not in assignable
    assert "Plumbing / PVC Unions" not in assignable


def test_category_matching_uses_full_hierarchical_path():
    categories = [
        {"id": "root-clean", "name": "Cleaning"},
        {
            "id": "pads",
            "name": "Algae Pads & Scrapers",
            "category_path": [{"name": "Cleaning"}, {"name": "Algae Pads & Scrapers"}],
        },
        {"id": "root-food", "name": "Food"},
        {
            "id": "food-pads",
            "name": "Algae Pads & Scrapers",
            "category_path": [{"name": "Food"}, {"name": "Algae Pads & Scrapers"}],
        },
    ]

    assert category_id_for_path(categories, "Cleaning / Algae Pads & Scrapers") == "pads"
    assert category_id_for_path(categories, "Food / Algae Pads & Scrapers") == "food-pads"


def test_duplicate_category_paths_block_matching():
    categories = [
        {"id": "one", "name": "PVC Fittings", "parent_name": "Plumbing"},
        {"id": "two", "name": "PVC Fittings", "parent_name": "Plumbing"},
    ]

    preview = category_setup_preview(categories)

    assert category_id_for_path(categories, "Plumbing / PVC Fittings") is None
    assert preview["duplicates"] == [{"path": "Plumbing / PVC Fittings", "ids": ["one", "two"]}]


def test_bulk_payload_uses_parent_references_and_repeated_setup_is_empty():
    categories: list[dict] = []
    payload = bulk_create_payload(categories)
    payload_by_name = {item["name"]: item for item in payload}

    assert payload_by_name["Plumbing"]["payload_id"] == "cat_plumbing"
    assert payload_by_name["PVC Fittings"]["payload_parent_category_id"] == "cat_plumbing"

    existing = []
    for item in payload:
        parent_payload = item.get("payload_parent_category_id")
        parent_name = None
        if parent_payload:
            parent_name = next(p["name"] for p in payload if p["payload_id"] == parent_payload)
        existing.append({
            "id": item["payload_id"],
            "name": item["name"],
            "parent_name": parent_name,
        })

    assert bulk_create_payload(existing) == []


@pytest.mark.parametrize(
    ("name", "expected_path", "confidence"),
    [
        ("Schedule 40 PVC 90 Degree Elbow 1 inch", "Plumbing / PVC Fittings", "high"),
        ("True Union Check Valve 3/4 inch", "Plumbing / Check Valves", "high"),
        ("Portable Seawater Refractometer", "Testing / Hydrometers & Refractometers", "high"),
        ("Python Gravel Vacuum Cleaner", "Cleaning / Gravel Vacs", "high"),
    ],
)
def test_suggestions_for_clear_product_categories(name, expected_path, confidence):
    suggestion = suggest_category(Product(name))

    assert suggestion["path"] == expected_path
    assert suggestion["confidence"] == confidence


def test_live_animals_and_replacement_parts_are_not_silently_misclassified():
    guppy = suggest_category(Product("Blue Cobra Male Guppy"))
    replacement = suggest_category(Product("Replacement Lid for Canister Filter"))

    assert guppy["path"] is None
    assert guppy["confidence"] == "review"
    assert replacement["path"] == "Filtration / Accessories & Replacement Parts"
    assert "not a complete filter" in replacement["reason"]
