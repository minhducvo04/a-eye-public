# Server

Run from the repository root with `make server` (`server.app:app`).
`GET /health` returns `{"ok": true}`. `/ws` accepts contract v1 messages.
If `client-web/` exists when the app is created, it is served at `/`, after API routes.

Send `hello`, then a true `power_off` confirmation (or the exact voice text
`power is off` / `it's off`), then a photo or frame. Each connection has its own
session. A new hello resets it. A false power-off confirmation resets safety.
Images are held only in session memory; disconnect releases the session.

`steps.Session` is pure logic with injected timestamps and board data. `handle`
returns messages and an optional `(operation, payload)` vision request; `accept`
consumes its result. `app.py` handles I/O and validates all outgoing messages.
Inject a vision object with async `call(operation, payload)` using
`create_app(vision=...)`; raise `VisionUnavailable` for recoverable failures.
The default HTTP client validates requests/responses and uses `VISION_URL`
(default `http://127.0.0.1:8001`) with a five-second timeout.

Safety and confidence thresholds come from contracts. Live-frame freshness is
checked on receipt, before cached-image verification, and after vision calls.
Photos do not expire. Blur/glare blocks locating; verification must report
`abstain` for bad image quality under the current vision response contract.
A low-confidence positive verification abstains and returns to AWAIT.
Other visible headers below the header confidence threshold are omitted.
Pin coordinates are numbered in the order of the vision grid, only with pin_ok.

The demo board is a fixture, not a real motherboard guide. Its table currently
covers fans, front panel and RAM; other allowed parts have no checked target.
Market, parts scanning, saved projects and audio return `internal: not wired yet`.
There is no persistence, STT/TTS integration or cloud-image forwarding here.
Use a trusted local vision host; this package does not implement cloud cropping.

Tests use synthetic image strings, TestClient WebSockets and httpx MockTransport,
with no network or real ports:

```sh
.venv/bin/python -m pytest -q server
.venv/bin/python -m pytest -q
```
