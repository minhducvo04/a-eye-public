"""Bright Data client: one Web Unlocker / SERP request, mapped to offers.

Endpoint (checked against Bright Data docs):
POST https://api.brightdata.com/request
https://docs.brightdata.com/api-reference/rest-api/unlocker/unlock-website
https://docs.brightdata.com/products/serp-api/introduction

BRIGHTDATA_API_KEY and BRIGHTDATA_ZONE come from the environment and are never logged.
The SERP JSON shape used for prices is UNVERIFIED: documented organic results have
title and link, not a price. Items without a numeric price are dropped.
"""

import os
from datetime import datetime, timezone
from urllib.parse import quote_plus

import httpx

_SPEC_KEYS = (
    "socket", "ddr_gen", "dimm_format", "ecc", "buffered", "speed_mts", "max_speed_mts",
    "capacity_gb", "max_capacity_gb", "dimm_slots", "modules", "form_factor", "tdp_w",
    "wattage_w", "height_mm", "cooler_clearance_mm", "pcie_gen", "m2_key",
)


def fetch_offers(query_key, category, client=None):
    """Return offer dicts without compat. Pass an httpx.Client in tests."""
    api_key = os.environ.get("BRIGHTDATA_API_KEY") or ""
    zone = os.environ.get("BRIGHTDATA_ZONE") or ""
    if not api_key:
        raise RuntimeError("BRIGHTDATA_API_KEY is not set")
    if not zone:
        raise RuntimeError("BRIGHTDATA_ZONE is not set")
    target = (
        "https://www.google.com/search?q="
        + quote_plus(f"{category} {query_key}")
        + "&hl=en&gl=us&brd_json=1"
    )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"zone": zone, "url": target, "format": "raw", "data_format": "parsed_light"}
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=60)
    try:
        response = client.post("https://api.brightdata.com/request", headers=headers, json=body)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Bright Data response was not JSON (HTTP {response.status_code})"
            ) from exc
    finally:
        if own_client:
            client.close()
    if isinstance(payload, str):
        raise RuntimeError("Bright Data response was not a JSON object")
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
        for key in ("organic", "results", "items"):
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
        "source": "brightdata",
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
    raw = row.get("specs") if isinstance(row.get("specs"), dict) else {}
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
