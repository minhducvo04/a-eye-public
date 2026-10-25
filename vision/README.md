# Vision service

Run `make vision` from the repository root to serve `vision.app:app` on port 8001.
Use `GET /health` and JSON `POST /identify`, `/locate`, `/verify`, `/scene` with the request
shapes in `contracts/examples.json`. Requests and successful responses are checked
with `contracts.validate`. Invalid JSON, contract violations and unsupported
scenario names return HTTP 422 with a short message that excludes input values.

`FAKE_SCENARIO` defaults to `ok`. The `X-Fake-Scenario` request header overrides
it for one request without changing other requests:

| Scenario | Behavior |
| --- | --- |
| `ok` | First identification candidate, canned location, passing verification |
| `wrong_header` | Verification fails at confidence 0.9 and names SYS_FAN1 |
| `blurry` | Location not found with blur; verification abstains at confidence 0.3 |
| `unknown_item` | Identification returns unknown at confidence 0.2 |

Other operations retain their default behavior in each scenario. POST results
include elapsed `ms` for parsing, request validation and backend work. `/health`
omits `ms` because the source-of-truth health contract forbids that property.
Error bodies use FastAPI's `detail` envelope; no vision error contract exists.

`fixtures.json` contains synthetic normalized `[x, y, w, h]` boxes for demo-b650.
Unknown boards or headers are not found. Only F_PANEL requests containing
`want_pins` receive the canned pin grid and `pin_ok: true`; this includes an empty
list. The grid is a full synthetic header grid, not a measurement of requested
pins. These coordinates are not suitable for guiding work on a real board.

`app.py` owns HTTP parsing, validation, scenario selection and timing. Its
`backend` object exposes `health()`, `identify(body, scenario)`,
`locate(body, scenario)`, `verify(body, scenario)` and `scene(body, scenario)`; replace that object to
integrate a real model. Backend POST methods return response fields without `ms`.
The fake ignores image contents and performs no decoding, storage, logging or
network calls. Verification deliberately succeeds by default even for unknown
boards; it is a demo stub, not a safety or physical-connection check.

Test with `.venv/bin/python -m pytest -q vision` (synthetic inputs only).

## Ollama on the team PC

Choose the backend before starting the service:

```sh
VISION_BACKEND=ollama make vision
VISION_BACKEND=auto make vision
```

`VISION_BACKEND` defaults to `fake`. `ollama` uses the PC directly; `auto` retries
Ollama on every request and uses fake demo responses on connection errors or
timeouts. A failed health request returns `fake: true, models: ["fake"]`; the
reason is logged only as `ollama unreachable, using fake`. No extra reason field
is added to health. HTTP errors and malformed model answers do not trigger demo
fallback. Auto mode inherits the fake backend's canned passing verdicts, so its
results are for demos, not physical connection assurance.

`OLLAMA_URL` defaults to `http://127.0.0.1:11434`. Model routing:

| Environment variable | Default | Calls |
| --- | --- | --- |
| `VISION_GROUND_MODEL` | `qwen3.5:9b-q4_K_M` | Scene queries and locate boxes; also identify, verify and warm-up |
| `VISION_CAPTION_MODEL` | `qwen2.5vl:7b-q4_K_M` | Scene captions (no queries, or `caption: true`) |
| `VISION_FALLBACK_MODEL` | `qwen2.5vl:7b-q4_K_M` | One retry per failed box query |

`VISION_MODEL` is replaced by these variables. The constructor's `model=`
overrides the ground model for probes/tests. Chat requests use an 8-second HTTP timeout; each health GET
uses 2 seconds. `/health` checks `/api/tags` for availability and `/api/ps` for
residency. It lists `models: [ground, caption]` and reports the ground model's
`gpu`, `partial`, `cpu`, or `not_loaded` mode; `ok` is false when either
configured model is absent from tags. This exposes CPU fallback after reboot.
These are httpx operation timeouts, not a total wall-clock deadline.

Images are decoded and resized in memory with Pillow: the longer side is exactly
1280 pixels, preserving aspect ratio to the nearest pixel. The code's `BOX_FORMATS`
table controls both native prompts and corner parsing:

| Family | Coordinates | Corner order |
| --- | --- | --- |
| qwen2.5vl | Pixels of the resized image | x1,y1,x2,y2 |
| qwen3.5, gemma4:12b | 0 to 1000 | x1,y1,x2,y2 |
| gemma4:26b | 0 to 1000 | y1,x1,y2,x2 |
| Unknown | 0 to 1000 | x1,y1,x2,y2 |

Corners become normalized `[x,y,w,h]`, clamped to 0..1; zero-area boxes are dropped.
No frames are saved or logged. Every generation uses temperature 0, `num_ctx: 4096`
and `keep_alive: "30m"`; qwen3.5 and gemma4 also receive `think: false`. Token caps are 60 for boxes, 40 for captions,
identification and verification, and 1 for warm-up. One shared `httpx.Client`
reuses connections (keep-alive expiry 30 minutes), including health requests.
Fenced JSON, trailing prose and broken closing brackets on labelled boxes are
accepted; invalid/empty boxes are still discarded.

At startup, Ollama and auto backends send a synthetic 1280×960 warm-up in a daemon
thread. Startup and shutdown do not wait for it; one status/timing line is logged.
A user request arriving immediately can still encounter cold loading. Warm-up
failure does not switch the configured backend or prevent later retries.

Locate requests the target silkscreen first, then at most two other board-table
labels, one per call in table order. It stops starting new labels after 1.5 seconds.
It returns text boxes, never connector or pin grids. Header names and silkscreen
aliases match case-insensitively with underscores and whitespace ignored. Exact
matches get 0.7 confidence; a unique best SequenceMatcher ratio of at least 0.8
gets 0.5. These scores are heuristics, not calibrated probabilities. Ambiguous
fuzzy matches are omitted. Unknown boards/headers and invalid boxes yield not found.

Identify is limited to the supplied candidates and caps confidence at 0.85.
Verify requires a boolean `plugged` and an exact board-label match to pass. Its
confidence is always 0.75, below the 0.8 green gate: the VLM alone must never
produce a green tick. Unparseable verification abstains at 0.3. The fake scenario
argument is ignored by Ollama. Blur, glare and connector seating are not
independently measured here; downstream freshness/confidence gates still apply.

Run the synthetic-only real-PC probe from the repo root:

```sh
.venv/bin/python -m vision.probe_ollama
```

It draws CPU_FAN, SYS_FAN1 and F_PANEL on a dark green image in memory, prints
health first, then CPU_FAN text-box IoU and latency. It uses Ollama directly so an
unreachable PC cannot produce a misleading fake success. The integrator must run
this on a machine with PC access; mocked tests do not establish GPU accuracy.
`OllamaBackend(client=...)` or `OllamaBackend(transport=httpx.MockTransport(...))`
allows offline testing. Callers supplying a client retain ownership of it.

## General scene grounding

`POST /scene` accepts `image_b64`, optional `queries` (up to eight strings), and
optional `max_objects` (1–20, default 8), and optional boolean `caption`. It returns a short `caption`, `objects`
with consecutive `o1`, `o2`, … IDs, normalized `[x,y,w,h]` boxes and confidence,
plus `source` and measured `ms`. The object cap applies even if more queries
were supplied. Ollama processes only the first three queries, in order.

Fake scenes use query labels verbatim at fixed spread-out positions, then canned
motherboard, fan cable and table objects within the cap. The `blurry` scenario
returns no objects and caption `too blurry to see`; other scenarios use the
normal canned scene. These objects do not describe the submitted image.

Ollama scenes send one label per call, sequentially, at 1280 pixels long edge.
Only matching query labels are returned, with heuristic confidence 0.7 and
consecutive IDs. No extra salient objects are requested. Further calls stop once
1.5 seconds have elapsed or `max_objects` is reached; a call already in flight
can exceed this budget, up to the HTTP operation timeout. HTTP/transport errors,
timeouts, or answers without a valid matching box trigger exactly one fallback
attempt using that model's native format. This retry completes even after the
label budget expires (so a failed primary plus retry may take two operation
timeouts). An invalid fallback contributes no box; fallback HTTP errors propagate,
with auto mode retaining its connection/timeout demo fallback. Partial results
survive budget exhaustion when no exception propagates.

With queries, `caption` is an empty string unless `caption: true` is requested.
That flag adds a caption-model call after grounding, so caption latency does not
consume the budget for starting box queries. Without queries, one short caption
call returns no boxes (or `Could not describe the frame.` on parse failure).
Fake scene behavior remains unchanged. Auto mode still falls
back on connection errors/timeouts. Ollama does not independently detect blur.

Run the additional synthetic scene probe on the real PC:

```sh
.venv/bin/python -m vision.probe_ollama --scene
```

It draws labelled red, blue and green shapes in memory, queries `red square`, and
prints health, caption, objects and milliseconds without printing or saving the
image. Offline tests exercise the HTTP boundary and mocked Ollama transport;
they do not establish real-model grounding accuracy.

## Latency breakdown

```sh
.venv/bin/python -m vision.probe_ollama --bench 5 --model qwen3.5:9b-q4_K_M
.venv/bin/python -m vision.probe_ollama --bench 5 --model qwen2.5vl:7b-q4_K_M
```

Runs exactly N single-label scene requests against synthetic shapes using the
same client and selected ground model. A scene request can make two HTTP calls
if fallback is needed. Prints the model actually used and IoU against the known
red square box on each run, plus median/min/max IoU (missing boxes score zero).
Fallback runs must not be attributed to the selected model's accuracy.
Prints each wall time and Ollama's `load_duration`,
`prompt_eval_duration`, and `eval_duration` converted from nanoseconds to ms,
then median/min/max for each. Missing counters are reported as unavailable.
The first request is included and may be cold. Repeated identical synthetic
requests can benefit from prompt caching; this is a diagnostic, not an uncached
accuracy or production-latency benchmark. It never saves or prints the image.

See `docs/pc-benchmarks.md` for measured GPU results. The integrator must run this
probe on the real box; offline tests cannot establish the speedup. Scheduling
LLM speech generation outside grounding calls belongs to the orchestrator and
cannot be enforced in this package. Other callers sharing this model must also
keep `num_ctx` at 4096 to avoid reloads.
