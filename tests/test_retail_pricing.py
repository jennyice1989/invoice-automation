from __future__ import annotations

import pytest

from app.retail_pricing import (
    _dedupe_offers,
    _offer_from_serpapi_result,
    fetch_serpapi_market_prices,
    RetailOffer,
)


def test_offer_from_serpapi_result_normalizes_good_offer():
    offer = _offer_from_serpapi_result({
        "position": 1,
        "title": "Seachem Prime 500 ml",
        "source": "Chewy",
        "extracted_price": 21.99,
        "product_link": "https://example.test/product",
    })

    assert offer == RetailOffer(
        seller="Chewy",
        title="Seachem Prime 500 ml",
        price=21.99,
        url="https://example.test/product",
        position=1,
    )


def test_offer_from_serpapi_result_filters_noisy_results():
    assert _offer_from_serpapi_result({
        "title": "Used filter",
        "source": "Petco",
        "extracted_price": 12.99,
        "second_hand_condition": "used",
    }) is None
    assert _offer_from_serpapi_result({
        "title": "Sale food",
        "source": "PetSmart",
        "extracted_price": 12.99,
        "old_price": "$19.99",
    }) is None
    assert _offer_from_serpapi_result({
        "title": "Marketplace item",
        "source": "Amazon",
        "extracted_price": 12.99,
    }) is None


def test_dedupe_offers_keeps_first_seller_price_pair():
    offers = _dedupe_offers([
        RetailOffer("Chewy", "A", 19.99, position=2),
        RetailOffer("Chewy", "B", 19.99, position=1),
        RetailOffer("Petco", "C", 21.99, position=3),
    ])

    assert offers == [
        RetailOffer("Chewy", "B", 19.99, position=1),
        RetailOffer("Petco", "C", 21.99, position=3),
    ]


@pytest.mark.asyncio
async def test_fetch_serpapi_market_prices_parses_shopping_results(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "shopping_results": [
                    {
                        "position": 1,
                        "title": "Seachem Prime",
                        "source": "Chewy",
                        "extracted_price": 21.99,
                    },
                    {
                        "position": 2,
                        "title": "Seachem Prime sale",
                        "source": "PetSmart",
                        "extracted_price": 17.99,
                        "old_price": "$22.99",
                    },
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.params = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params):
            self.params = params
            return FakeResponse()

    monkeypatch.setattr("app.retail_pricing.httpx.AsyncClient", FakeClient)

    result = await fetch_serpapi_market_prices("000116070782", api_key="key")

    assert result.provider == "serpapi"
    assert result.query == "000116070782"
    assert result.raw_count == 2
    assert result.offers == [
        RetailOffer("Chewy", "Seachem Prime", 21.99, position=1)
    ]
