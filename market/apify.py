"""Apify client: run one actor synchronously and map dataset items to offers.

Endpoint (checked against Apify docs):
POST https://api.apify.com/v2/actors/{actorId}/run-sync-get-dataset-items
https://docs.apify.com/api/v2/actor-run-sync-get-dataset-items-post

The actor id and the item field names are UNVERIFIED. Set APIFY_ACTOR_ID to override
the placeholder. APIFY_TOKEN is read from the environment and never logged.
"""

import os
from datetime import datetime, timezone

import httpx

DEFAULT_ACTOR_ID = "UNVERIFIED~shopping-scraper"
_SPEC_KEYS = (
    "socket", "ddr_gen", "dimm_format", "ecc", "buffered", "speed_mts", "max_speed_mts",
    "capacity_gb", "max_capacity_gb", "dimm_slots", "modules", "form_factor", "tdp_w",
    "wattage_w", "height_mm", "cooler_clearance_mm", "pcie_gen", "m2_key",
)


def fetch_offers(query_key, category, client=None):
    """Return offer dicts without compat. Pass an httpx.Client in tests."""
    token = os.environ.get("APIFY_TOKEN") or ""
    if not token:
        raise RuntimeError("APIFY_TOKEN is not set")
    actor = os.environ.get("APIFY_ACTOR_ID") or DEFAULT_ACTOR_ID
    url = f"https://api.apify.com/v2/actors/{actor}/run-sync-get-dataset-items"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"query": query_key, "category": category}
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=120)
    try:
        response = client.post(url, headers=headers, json=body)
        response.raise_for_status()
        payload = response.json()
    finally:
        if own_client:
            client.close()
    return map_items(payload)


def map_items(payload):
    rows = _rows(payload)
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    offers = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        offer = _one(row, fetched_at)
        if offer is not None:
            offers.append(offer)
    return offers


def _rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "organic", "results"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return rows
    return []


def _one(row, fetched_at):
    title = row.get("title") or row.get("name")
    url = row.get("url") or row.get("link")
    price = _number(row.get("price_usd", row.get("price", row.get("extracted_price"))))
    if not title or not url or price is None:
        return None
    offer = {
        "title": str(title),
        "retailer": _retailer(row, url),
        "price_usd": price,
        "url": str(url),
        "source": "apify",
        "fetched_at": fetched_at,
        "specs": _specs(row),
    }
    shipping = _number(row.get("shipping_usd", row.get("shipping")))
    if shipping is not None and shipping >= 0:
        offer["shipping_usd"] = shipping
    condition = row.get("condition")
    if condition in ("new", "used", "refurbished"):
        offer["condition"] = condition
    return offer


def _retailer(row, url):
    for key in ("retailer", "seller", "store"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    host = httpx.URL(url).host or ""
    return host or "unknown"


def _specs(row):
    raw = row.get("specs") if isinstance(row.get("specs"), dict) else row
    specs = {}
    for key in _SPEC_KEYS:
        if key in raw and raw[key] is not None:
            specs[key] = raw[key]
    if specs.get("ddr_gen") not in (None, "DDR4", "DDR5"):
        specs.pop("ddr_gen", None)
    if specs.get("dimm_format") not in (None, "DIMM", "SODIMM"):
        specs.pop("dimm_format", None)
    return specs


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None
