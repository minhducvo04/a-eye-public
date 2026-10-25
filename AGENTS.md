# A-eye: rules for every agent and human on the team

A-eye is a real-time AI video call that sees the camera, talks back, and draws on the live picture (boxes, arrows,
circles, paths, labels, pins). Skill packs give it task knowledge (PC building from the board's manual, cooking,
assembling); a second agent, the shopper, finds parts that fit. Cal Hacks 13.0 project.

## Module map

| Path | What | Talks to |
|---|---|---|
| `contracts/` | `schema.json` (JSON Schema, source of truth), `types.ts`, Python `validate()`, examples | everyone |
| `server/` | Orchestrator: WS `/ws2` agent (packs in `server/packs/`, helper and shopper), model router `llm.py`; WS `/ws` v1 step flow and gates; serves `client-web/` | client, vision, voice, market |
| `vision/` | Vision service over HTTP (`/scene` for v2; `/identify`, `/locate`, `/verify`, `/health`). Qwen2.5-VL on the PC GPU through Ollama, fake fallback | server |
| `voice/` | Cartesia TTS and STT adapter (stub first) | server (in process) |
| `client-web/` | Browser call pages: `agent.html` (v2: draw layer, presence, voice, tracker) and `index.html` (v1) | server |
| `ios/` | Native SwiftUI app (the team). Same WS contract as `client-web/` | server |
| `market/` | Price search (Apify, Bright Data) and `compat.py` compatibility checker | server |
| `data/` | Hand-checked header tables `data/boards/<board_id>.json` | server, vision |
| `eval/` | Accuracy harness: replays held-out frames through vision, writes a table | vision |
| `docs/` | Plans, pitch, notes | |

## How we work

1. **Contracts are the source of truth.** Build against `contracts/schema.json`. Change it only by a PR that keeps
   or bumps `v`, updates `types.ts` and `examples.json`, and passes `make test-py`. Never change a contract inside a
   feature branch "to make it fit".
2. **One owner per package.** Edit only the files your package owns. Need something from another package? Ask its
   owner; do not edit their files.
3. **One branch and worktree per package**: `~/Projects/calhacks-kit/wt.sh <package>` makes
   `../a-eye-wt/<package>` on branch `pkg/<package>`. Without the kit: `git worktree add -b pkg/<name> ../a-eye-wt/<name>`.
4. **Tests required per module.** Each package ships tests next to its code (`<module>/test_*.py`, or
   `client-web/*.test.mjs` run by `node --test`). Test what you changed. `make test` stays green on `main`.
5. **Finish with `RESULT.md`** in your worktree: files changed, limits, and `Test: <command> - PASS/FAIL (<result>)`.
   The integrator merges after reading it and the diff, then runs `make smoke`.
6. **One plain command per shell call.** No long chained one-liners.
7. **Speed first.** Smallest thing that works for the demo. Standard library before new dependencies; a new
   dependency goes in `requirements.txt` in your PR and needs a reason.

## Safety gate (code, not prompt)

Each skill pack has a `refuse()` check that runs before any model call. The PC pack adds:


- No step leaves `SAFETY` until `confirm{what:"power_off", value:true}` (button, or a closed-set voice match). The
  first spoken line is "Switch the PSU off and unplug it."
- Allowed parts only: `contracts.ALLOWED_PARTS` (CPU fan, system fan, front panel, RAM, SATA, M.2, 24-pin, 8-pin EPS).
  Anything else, and any question about opening the PSU or mains power, gets a fixed refusal line.
- Confidence gate: header box only when `conf >= 0.6`, else "move closer"; pin level only when vision says
  `pin_ok`; a green verdict only when `conf >= 0.8`; abstain on blur, glare or a frame older than 1.5 s.
- `market/compat.py` decides compatibility in code. Unknown specs are "unverified", never "fits". An LLM only explains.

## Privacy

- No raw video, frames or audio are stored on our machines: not in git, not in the database, not in logs. The one
  exception is Rewind, when the user turns it on: silent clips go to TwelveLabs for search and are deleted at call
  end unless the user keeps them.
- Eval clips stay local, outside the repo (for example `~/a-eye-private/clips/`). `.gitignore` blocks `clips/`,
  `frames/`, `*.mp4`, `*.mov`, `*.wav`. Coding agents never get real clips; use synthetic or prop-board frames.
- Frames go only to our own vision host, except Rewind clips when Rewind is on. The cloud brain gets the scene as
  text (caption and object list), plus at most one small crop through `server/llm.py`'s size check. `LOCAL_ONLY=1`
  or Private mode blocks every cloud service: Claude, Cartesia and TwelveLabs.
- Cartesia receives only the AI's own sentences.

## Secrets

`.env` is gitignored; `.env.example` lists names only. Never print, log or commit a key. Check a key by length or
prefix only.

## Run it locally

```sh
make setup      # .venv + Python deps (needs uv)
make test       # all module tests
make vision     # fake vision on :8001
make server     # orchestrator on :8000, open http://localhost:8000
make smoke      # end to end: photo -> server -> fake vision -> box + step
```

## Devpost rules

Hacking started 6:00 PM PT Fri Oct 23, 2026; this repo was created after that. Deadline Sun Oct 25, 9:00 AM PT.
The repo stays private until the team decides to make it public for Devpost.
