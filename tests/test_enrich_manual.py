from app.main import EnrichItemIn, _draft_from_enrich_item


def test_manual_enrich_item_creates_review_draft_without_openai():
    item = EnrichItemIn(
        name="Electric Blue Acara",
        kind_hint="live_fish",
        sku="FISH-ACARA-BLUE",
        barcode="123456789012",
        supplier_code="ACARA-BLUE",
        supply_price=8.5,
        retail_price=24.99,
        product_category="Freshwater Fish / Cichlids",
        brand_name="A2Z Aquariums",
        description="<p>Healthy freshwater cichlid.</p>",
        tags=["fish", "cichlid"],
        has_photo=True,
        draft_with_openai=False,
    )

    draft = _draft_from_enrich_item(item, "batch-1")

    assert draft is not None
    assert draft.batch_id == "batch-1"
    assert draft.input_name == "Electric Blue Acara"
    assert draft.status == "DRAFT"
    assert draft.kind == "live_fish"
    assert draft.sku == "FISH-ACARA-BLUE"
    assert draft.barcode == "123456789012"
    assert draft.supplier_code == "ACARA-BLUE"
    assert draft.supply_price == 8.5
    assert draft.retail_price == 24.99
    assert draft.product_category == "Freshwater Fish / Cichlids"
    assert draft.brand_name == "A2Z Aquariums"
    assert draft.description == "<p>Healthy freshwater cichlid.</p>"
    assert draft.tags == {"list": ["fish", "cichlid"]}
    assert draft.has_photo is True


def test_name_only_enrich_item_still_queues_openai_draft():
    draft = _draft_from_enrich_item(
        EnrichItemIn(name="Seachem Prime 500ml"),
        "batch-2",
    )

    assert draft is not None
    assert draft.status == "PENDING_ENRICH"
    assert draft.kind == "unknown"
