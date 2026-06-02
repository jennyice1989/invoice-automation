from __future__ import annotations

from app.upc_lookup import (
    extract_upc,
    score_upc_candidate,
    supplier_supports_upc_lookup,
)


def test_supplier_supports_targeted_upc_lookup_names():
    assert supplier_supports_upc_lookup("Central Pet Distribution")
    assert supplier_supports_upc_lookup("Phillips Pet Food & Supplies")
    assert supplier_supports_upc_lookup("ReefH2O Distribution")
    assert supplier_supports_upc_lookup("Reef H2O")


def test_supplier_rejects_non_target_names():
    assert not supplier_supports_upc_lookup("Seagrest Farms")
    assert not supplier_supports_upc_lookup("Generic Vendor")
    assert not supplier_supports_upc_lookup(None)


def test_extract_upc_from_barcode_list_or_sku():
    assert extract_upc({"barcode": ["000116070782"]}) == "000116070782"
    assert extract_upc({"sku": "000116768702"}) == "000116768702"
    assert extract_upc({"barcode": "SC07076", "sku": "not-a-upc"}) is None


def test_score_upc_candidate_prefers_embedded_supplier_code():
    product = {
        "name": "Seachem Shrimp Accessories - Tube ASM7078",
        "sku": "000116070782",
        "barcode": "000116070782",
    }

    assert score_upc_candidate(
        product,
        product_name="Shrimp Tube",
        supplier_code="SC07078",
    ) >= 0.94


def test_score_upc_candidate_rejects_weak_name_match():
    product = {
        "name": "Seachem Curved Fine Tip Forceps",
        "sku": "000116768702",
        "barcode": "000116768702",
    }

    assert score_upc_candidate(
        product,
        product_name="CaribSea Arag-Alive Fiji Pink Sand 20 lb",
        supplier_code="CB12345",
    ) < 0.70
