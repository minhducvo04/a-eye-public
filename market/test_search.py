"""Fixture search plus Apify and Bright Data mapping without the network."""

import json
import re
from pathlib import Path

import httpx
import pytest
from contracts import validate

from market.apify import fetch_offers as fetch_apify
from market.brightdata import fetch_offers as fetch_brightdata
from market.compat import check
from market.search import search

BOARD_PATH = Path(__file__).resolve().parents[1] / "data" / "boards" / "demo-b650.json"
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _query(**overrides):
    specs = json.loads(BOARD_PATH.read_text())["specs"]
    query = {
        "v": 1,
        "category": "ram",
        "owned_parts": [{
            "category": "board",
            "model": "Demo B650 board",
            "specs": specs,
            "confirmed": True,
            "source": "fixture",
        }],
        "max_results": 3,
    }
    query.update(overrides)
    return query


def _totals(items):
    return [item["price_usd"] + item.get("shipping_usd", 0) for item in items]


def test_fixture_search_returns_at_most_three_sorted_fits(monkeypatch):
    monkeypatch.setenv("MARKET_SOURCE", "fixture")
    query = _query()
    validate("market_query", query)
    result = search(query)
    validate("market_response", result)
    assert result["query_key"] == "ram:DDR5:DIMM"
    assert result["category"] == "ram"
    assert len(result["items"]) <= 3
    assert len(result["items"]) == 3
    assert [item["compat"]["status"] for item in result["items"]] == ["fits", "fits", "fits"]
    assert _totals(result["items"]) == sorted(_totals(result["items"]))
    assert _totals(result["items"]) == [40, 65, 70]
    titles = [item["title"] for item in result["items"]]
    assert "Fixture DDR4 DIMM 32GB" not in titles
    assert "Fixture DDR5 DIMM ECC 32GB" not in titles
    assert result["items"][1]["explain"].startswith("Fits because:")
    assert "will run at 6400 MT/s" in result["items"][1]["explain"]
    assert result["items"][1]["explain"].endswith(".")


def test_fits_come_before_cheaper_unverified(monkeypatch):
    monkeypatch.setenv("MARKET_SOURCE", "fixture")
    result = search(_query(max_results=4))
    validate("market_response", result)
    statuses = [item["compat"]["status"] for item in result["items"]]
    assert statuses == ["fits", "fits", "fits", "unverified"]
    assert result["items"][3]["title"] == "Fixture DDR5 DIMM missing specs"
    assert result["items"][3]["explain"] == "Unverified: ecc, speed_mts unknown."
    assert result["items"][3]["price_usd"] < result["items"][0]["price_usd"]


def test_budget_uses_price_plus_shipping(monkeypatch):
    monkeypatch.setenv("MARKET_SOURCE", "fixture")
    result = search(_query(budget_usd=64, max_results=3))
    validate("market_response", result)
    assert [item["title"] for item in result["items"]] == [
        "Fixture DDR5 DIMM 32GB 6000",
        "Fixture DDR5 DIMM missing specs",
    ]
    assert _totals(result["items"]) == [40, 15]
    assert [item["compat"]["status"] for item in result["items"]] == ["fits", "unverified"]


def test_default_max_results_is_three(monkeypatch):
    monkeypatch.setenv("MARKET_SOURCE", "fixture")
    query = _query()
    del query["max_results"]
    result = search(query)
    assert len(result["items"]) == 3


def test_apify_mapping_uses_mock_transport(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test-token-not-real")
    monkeypatch.setenv("APIFY_ACTOR_ID", "UNVERIFIED~shopping-scraper")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(201, json=[
            {
                "title": "Fixture mapped DDR5",
                "price": "$41.50",
                "url": "https://example.com/apify/1",
                "seller": "example-store-apify",
                "shipping": "2.00",
                "condition": "new",
                "specs": {
                    "ddr_gen": "DDR5",
                    "dimm_format": "DIMM",
                    "ecc": False,
                    "speed_mts": 6000,
                    "capacity_gb": 32,
                    "modules": 2,
                    "ddr_gen_note": "ignore me",
                },
            },
            {"title": "No price", "url": "https://example.com/apify/2"},
        ])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    offers = fetch_apify("ram:DDR5:DIMM", "ram", client=client)
    assert len(offers) == 1
    offer = offers[0]
    assert seen["url"].startswith(
        "https://api.apify.com/v2/actors/UNVERIFIED~shopping-scraper/run-sync-get-dataset-items"
    )
    assert "test-token-not-real" not in seen["url"]
    assert seen["auth"] == "Bearer test-token-not-real"
    assert seen["body"] == {"query": "ram:DDR5:DIMM", "category": "ram"}
    assert offer["source"] == "apify"
    assert offer["price_usd"] == 41.5
    assert offer["shipping_usd"] == 2.0
    assert offer["retailer"] == "example-store-apify"
    assert offer["specs"]["ddr_gen"] == "DDR5"
    assert "ddr_gen_note" not in offer["specs"]
    assert ISO_Z.match(offer["fetched_at"])
    owned = _query()["owned_parts"]
    offer["compat"] = check("ram", offer["specs"], owned)
    offer["explain"] = "Fits because: " + ", ".join(offer["compat"]["reasons"]) + "."
    validate("offer", offer)
    assert offer["compat"]["status"] == "fits"


def test_brightdata_mapping_uses_mock_transport(monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "test-key-not-real")
    monkeypatch.setenv("BRIGHTDATA_ZONE", "serp_zone_test")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json={
            "organic": [
                {
                    "title": "Fixture SERP DDR5",
                    "link": "https://example.com/bd/1",
                    "price": 44,
                    "specs": {
                        "ddr_gen": "DDR5",
                        "dimm_format": "DIMM",
                        "ecc": False,
                        "speed_mts": 6000,
                        "capacity_gb": 32,
                        "modules": 2,
                    },
                },
                {"title": "No price result", "link": "https://example.com/bd/2"},
            ]
        })

    client = httpx.Client(transport=httpx.MockTransport(handler))
    offers = fetch_brightdata("ram:DDR5:DIMM", "ram", client=client)
    assert seen["url"] == "https://api.brightdata.com/request"
    assert "test-key-not-real" not in seen["url"]
    assert seen["auth"] == "Bearer test-key-not-real"
    assert seen["body"]["zone"] == "serp_zone_test"
    assert seen["body"]["format"] == "raw"
    assert seen["body"]["data_format"] == "parsed_light"
    assert "brd_json=1" in seen["body"]["url"]
    assert "test-key-not-real" not in json.dumps(seen["body"])
    assert len(offers) == 1
    offer = offers[0]
    assert offer["source"] == "brightdata"
    assert offer["retailer"] == "example.com"
    assert offer["price_usd"] == 44
    assert offer["url"] == "https://example.com/bd/1"
    assert ISO_Z.match(offer["fetched_at"])
    offer["compat"] = check("ram", offer["specs"], _query()["owned_parts"])
    validate("offer", offer)


def test_live_clients_refuse_without_secrets(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="APIFY_TOKEN is not set"):
        fetch_apify("ram:DDR5:DIMM", "ram", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
    monkeypatch.delenv("BRIGHTDATA_ZONE", raising=False)
    with pytest.raises(RuntimeError, match="BRIGHTDATA_API_KEY is not set"):
        fetch_brightdata("ram:DDR5:DIMM", "ram", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
