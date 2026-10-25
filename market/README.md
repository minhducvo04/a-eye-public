# market

Price search and a deterministic compatibility check for A-eye. `compat.py` decides fit in code. An unknown spec is `unverified`, never `fits`.

## Search

`search(query)` takes a `market_query` and returns a `market_response`. The RAM query key is `ram:<ddr_gen>:<dimm_format>` from the owned board. Offers marked `no` are dropped. `fits` results come first, then `unverified`, each group sorted by `price_usd + shipping_usd`. `budget_usd` filters on that total. `max_results` defaults to 3.

`MARKET_SOURCE` selects the loader behind `load_offers(query_key, category)`:

| Value | Behavior |
|---|---|
| `fixture` (default) | Reads `market/fixtures/ram_offers.json`. Invented listings only. |
| `apify` | `APIFY_TOKEN`, optional `APIFY_ACTOR_ID` |
| `brightdata` | `BRIGHTDATA_API_KEY`, `BRIGHTDATA_ZONE` |

Tokens stay in the environment. The clients do not print them.

## RAM rules

Checked against the owned `board` specs:

- `ddr_gen` and `dimm_format` must match.
- `ecc` or `buffered` memory does not fit a consumer board unless the board spec has `ecc: true`. A missing `ecc` flag is unverified. A missing `buffered` flag is treated as not buffered when `ecc` is false.
- `speed_mts` above `max_speed_mts` still fits, with the reason `will run at <max> MT/s`.
- `capacity_gb` must be within `max_capacity_gb`, and `modules` within `dimm_slots`.
- A missing field needed for a comparison is named in `unknown`. Any hard failure yields `no`. Otherwise a missing field yields `unverified`.

## SSD rules

`m2_key` must match. `pcie_gen` above the board still fits, with the reason `runs at PCIe <n> speed`.

Other categories return `unverified` and `unknown: ["category rules not written yet"]`. CPU specs are not checked.

## Tests

```sh
.venv/bin/python -m pytest -q market
```
