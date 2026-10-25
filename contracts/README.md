# contracts/

- `schema.json`: every message as a JSON Schema `$def`. Source of truth.
- `types.ts`: hand-written TypeScript mirror for the web and iOS teams.
- `__init__.py`: `validate(name, obj)`, `is_valid`, `ALLOWED_PARTS`, gate thresholds.
- `examples.json`: one or more valid examples per contract; tests validate them all.

| Contract | Direction | Transport |
|---|---|---|
| `hello`, `project_open`, `frame`, `photo`, `confirm`, `user_text`, `parts_scan`, `market`, `audio` | client to server | WS `/ws` |
| `overlay`, `say`, `step`, `verdict`, `parts`, `market_results`, `summary`, `annotated`, `error`, `audio` | server to client | WS `/ws` |
| `identify_*`, `locate_*`, `verify_*`, `health_response` | server to vision | HTTP JSON |
| `voice_say`, `voice_transcript`, `voice_barge_in`, `voice_spoken` | server and voice adapter | in process |
| `market_query`, `market_response`, `offer`, `compat`, `part` | server to market | in process |
| `header_table` | data file | `data/boards/<board_id>.json` |

Step machine: `SAFETY -> IDENTIFY -> LOCATE -> GUIDE -> AWAIT -> VERIFY -> DONE | RETRY (back to LOCATE)`.
Coordinates are normalized 0..1 of the sent frame; `bbox` is `[x, y, w, h]` from the top-left.
