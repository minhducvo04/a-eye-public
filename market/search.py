"""Find offers that fit the owned build. Fixture data is the default source."""

import json
import os
from pathlib import Path

from contracts import validate

from . import apify, brightdata
from .compat import check

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "ram_offers.json"


def search(query):
    """Run a market_query and return a market_response."""
    validate("market_query", query)
    category = query["category"]
    key = query_key(query)
    raw_offers = load_offers(key, category)
    kept = []
    for raw in raw_offers:
        specs = raw.get("specs") or {}
        verdict = check(category, specs, query["owned_parts"])
        if verdict["status"] == "no":
            continue
        offer = _offer(raw, verdict)
        kept.append(offer)
    budget = query.get("budget_usd")
    if budget is not None:
        kept = [offer for offer in kept if _total(offer) <= budget]
    kept.sort(key=lambda offer: (_rank(offer), _total(offer), offer["title"]))
    limit = query.get("max_results", 3)
    source = _source_name()
    response = {
        "v": query["v"],
        "query_key": key,
        "category": category,
        "items": kept[:limit],
        "note": _note(source),
    }
    validate("market_response", response)
    return response


def query_key(query):
    """RAM key is ram:<ddr_gen>:<dimm_format> from the owned board."""
    category = query.get("category") or "unknown"
    specs = _board_specs(query.get("owned_parts") or [])
    if category == "ram":
        ddr = specs.get("ddr_gen") or "unknown"
        form = specs.get("dimm_format") or "unknown"
        return f"ram:{ddr}:{form}"
    if category == "ssd":
        key = specs.get("m2_key") or "unknown"
        gen = specs.get("pcie_gen")
        gen_text = "unknown" if gen is None else str(gen)
        return f"ssd:{key}:{gen_text}"
    return str(category)


def load_offers(query_key, category):
    """Load offers for MARKET_SOURCE: fixture (default), apify, or brightdata."""
    source = _source_name()
    if source == "fixture":
        return _load_fixture(category)
    if source == "apify":
        return apify.fetch_offers(query_key, category)
    if source == "brightdata":
        return brightdata.fetch_offers(query_key, category)
    raise ValueError(f"Unknown MARKET_SOURCE: {source}")


def _source_name():
    return (os.environ.get("MARKET_SOURCE") or "fixture").strip().lower() or "fixture"


def _load_fixture(category):
    if category != "ram":
        return []
    return json.loads(FIXTURE_PATH.read_text())


def _board_specs(owned):
    for part in owned:
        if part.get("category") == "board":
            return part.get("specs") or {}
    return {}


def _offer(raw, verdict):
    offer = {
        "title": raw["title"],
        "retailer": raw["retailer"],
        "price_usd": raw["price_usd"],
        "url": raw["url"],
        "source": raw["source"],
        "fetched_at": raw["fetched_at"],
        "specs": raw.get("specs") or {},
        "compat": verdict,
        "explain": _explain(verdict),
    }
    if "shipping_usd" in raw and raw["shipping_usd"] is not None:
        offer["shipping_usd"] = raw["shipping_usd"]
    if raw.get("condition") in ("new", "used", "refurbished"):
        offer["condition"] = raw["condition"]
    return offer


def _explain(verdict):
    if verdict["status"] == "fits":
        return "Fits because: " + ", ".join(verdict["reasons"]) + "."
    if verdict["status"] == "unverified":
        return "Unverified: " + ", ".join(verdict["unknown"]) + " unknown."
    return ""


def _total(offer):
    return offer["price_usd"] + offer.get("shipping_usd", 0)


def _rank(offer):
    return 0 if offer["compat"]["status"] == "fits" else 1


def _note(source):
    if source == "fixture":
        return "Fixture offers, not live prices."
    if source == "apify":
        return "Offers from Apify."
    if source == "brightdata":
        return "Offers from Bright Data."
    return ""
