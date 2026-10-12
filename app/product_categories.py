from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TAXONOMY_PATH = Path(__file__).with_name("category_taxonomy.json")
PVC_GROUP_TERMS = (
    "adapter", "adapters", "coupling", "couplings", "bushing", "bushings",
    "elbow", "elbows", "plug", "plugs", "cap", "caps", "tee", "tees",
    "union", "unions", "wye", "wyes",
)
REPLACEMENT_TERMS = (
    "replacement", "impeller", "shaft", "o-ring", "oring", "gasket",
    "part", "parts", "kit", "cover", "lid", "clip", "bracket",
)
LIVE_TERMS = (
    "fish", "betta", "guppy", "tetra", "cichlid", "shrimp", "snail",
    "coral", "plant", "anubias", "molly", "tang", "clownfish",
)


@dataclass(frozen=True)
class TaxonomyNode:
    name: str
    path: str
    parent_path: str | None
    assignable: bool


def _norm(text: Any) -> str:
    return " ".join(str(text or "").lower().replace("&", " and ").split())


def _tokens(text: Any) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _norm(text)))


def load_taxonomy(path: Path = TAXONOMY_PATH) -> list[dict]:
    return json.loads(path.read_text())


def taxonomy_nodes(taxonomy: list[dict] | None = None) -> list[TaxonomyNode]:
    nodes: list[TaxonomyNode] = []
    for item in taxonomy or load_taxonomy():
        parent = item["name"]
        children = item.get("children") or []
        nodes.append(TaxonomyNode(
            name=parent,
            path=parent,
            parent_path=None,
            assignable=not bool(children),
        ))
        for child in children:
            nodes.append(TaxonomyNode(
                name=child,
                path=f"{parent} / {child}",
                parent_path=parent,
                assignable=True,
            ))
    return nodes


def assignable_paths(taxonomy: list[dict] | None = None) -> list[str]:
    return [node.path for node in taxonomy_nodes(taxonomy) if node.assignable]


def category_full_path(category: dict) -> str:
    path = category.get("category_path") or category.get("path")
    if isinstance(path, list) and path:
        names = []
        for item in path:
            if isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]))
            elif isinstance(item, str) and item:
                names.append(item)
        if names:
            return " / ".join(names)
    parent = category.get("parent_name")
    name = category.get("name") or ""
    return f"{parent} / {name}" if parent else str(name)


def category_path_maps(categories: list[dict]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    by_path: dict[str, dict] = {}
    duplicates: dict[str, list[dict]] = {}
    for category in categories:
        path = category_full_path(category)
        if not path:
            continue
        if path in by_path:
            duplicates.setdefault(path, [by_path[path]]).append(category)
        else:
            by_path[path] = category
    return by_path, duplicates


def category_setup_preview(categories: list[dict], taxonomy: list[dict] | None = None) -> dict:
    nodes = taxonomy_nodes(taxonomy)
    by_path, duplicates = category_path_maps(categories)
    missing = [node for node in nodes if node.path not in by_path]
    return {
        "total_nodes": len(nodes),
        "assignable_count": len([n for n in nodes if n.assignable]),
        "missing": [
            {
                "path": node.path,
                "name": node.name,
                "parent_path": node.parent_path,
                "assignable": node.assignable,
            }
            for node in missing
        ],
        "existing": [
            {"path": path, "id": cat.get("id"), "name": cat.get("name")}
            for path, cat in sorted(by_path.items())
            if path in {n.path for n in nodes}
        ],
        "duplicates": [
            {
                "path": path,
                "ids": [c.get("id") for c in cats],
            }
            for path, cats in sorted(duplicates.items())
        ],
    }


def bulk_create_payload(categories: list[dict], taxonomy: list[dict] | None = None) -> list[dict]:
    nodes = taxonomy_nodes(taxonomy)
    by_path, _ = category_path_maps(categories)
    payload: list[dict] = []
    payload_ids: dict[str, str] = {}
    for node in nodes:
        if node.path in by_path:
            continue
        payload_id = "cat_" + re.sub(r"[^a-z0-9]+", "_", node.path.lower()).strip("_")
        payload_ids[node.path] = payload_id
        item = {"name": node.name, "payload_id": payload_id}
        if node.parent_path:
            parent = by_path.get(node.parent_path)
            if parent and parent.get("id"):
                item["parent_category_id"] = parent["id"]
            elif node.parent_path in payload_ids:
                item["payload_parent_category_id"] = payload_ids[node.parent_path]
            else:
                raise ValueError(f"Missing parent category for {node.path}")
        payload.append(item)
    return payload


def category_id_for_path(categories: list[dict], path: str) -> str | None:
    by_path, duplicates = category_path_maps(categories)
    if path in duplicates:
        return None
    cat = by_path.get(path)
    if not cat:
        return None
    return cat.get("id")


def is_assignable_path(path: str, taxonomy: list[dict] | None = None) -> bool:
    return path in set(assignable_paths(taxonomy))


def suggest_category(product: Any) -> dict:
    name = getattr(product, "name", None) if not isinstance(product, dict) else product.get("name")
    category = getattr(product, "category_name", None) if not isinstance(product, dict) else product.get("category_name")
    raw = getattr(product, "raw", None) if not isinstance(product, dict) else product.get("raw")
    raw_text = ""
    if isinstance(raw, dict):
        raw_text = " ".join(str(raw.get(k) or "") for k in ("description", "supplier_code", "sku"))
    haystack = " ".join([str(name or ""), str(category or ""), raw_text])
    text = _norm(haystack)
    tokens = _tokens(text)

    if tokens & set(LIVE_TERMS):
        return {"path": None, "confidence": "review", "reason": "Possible livestock; leave for manual review"}
    if "replacement" in tokens or "part" in tokens or "parts" in tokens:
        if "skimmer" in tokens:
            return {"path": "Filtration / Replacement Skimmer Pumps & Parts", "confidence": "medium", "reason": "Skimmer replacement part"}
        if "pump" in tokens or "pumps" in tokens:
            return {"path": "Pumps / Accessories & Replacement Parts", "confidence": "medium", "reason": "Pump replacement part"}
        if "filter" in tokens:
            return {"path": "Filtration / Accessories & Replacement Parts", "confidence": "medium", "reason": "Filter replacement part; not a complete filter"}
        return {"path": None, "confidence": "review", "reason": "Replacement part; review equipment family before assigning"}
    if "gravel" in tokens and ("vac" in tokens or "vacuum" in tokens or "vacs" in tokens):
        return {"path": "Cleaning / Gravel Vacs", "confidence": "high", "reason": "Name contains gravel vacuum"}
    if "refractometer" in tokens or "hydrometer" in tokens:
        return {"path": "Testing / Hydrometers & Refractometers", "confidence": "high", "reason": "Testing instrument match"}
    if "check" in tokens and "valve" in tokens:
        return {"path": "Plumbing / Check Valves", "confidence": "high", "reason": "Check valve match"}
    if "pvc" in tokens and (tokens & set(PVC_GROUP_TERMS)):
        return {"path": "Plumbing / PVC Fittings", "confidence": "high", "reason": "PVC fitting grouped under PVC Fittings"}
    if "bulkhead" in tokens or "bulkheads" in tokens:
        return {"path": "Plumbing / Bulkheads", "confidence": "high", "reason": "Bulkhead plumbing part"}
    if "loc" in tokens and "line" in tokens:
        return {"path": "Plumbing / Loc-Line", "confidence": "high", "reason": "Loc-Line match"}
    if "magnet" in tokens and "algae" in tokens:
        return {"path": "Cleaning / Algae Magnets", "confidence": "high", "reason": "Algae magnet match"}
    if ("pad" in tokens or "scraper" in tokens or "scrapers" in tokens) and "algae" in tokens:
        return {"path": "Cleaning / Algae Pads & Scrapers", "confidence": "high", "reason": "Algae scraper/pad match"}
    return {"path": None, "confidence": "review", "reason": "No confident category rule matched"}
