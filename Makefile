# A-eye dev targets. Run from the repo root (or a worktree root).
PY ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
SERVER_PORT ?= 8000
VISION_PORT ?= 8001
# Vision backend: fake (canned), ollama (PC GPU), auto (ollama, fake when the PC is unreachable)
VISION_BACKEND ?= auto

# Load local keys from .env (gitignored) into every recipe. Values are never printed.
-include .env
export

.PHONY: setup test test-py test-web vision server dev smoke phone mock

setup:            ## create .venv and install Python deps
	uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt

test: test-py test-web   ## every module's tests

test-py:
	$(PY) -m pytest -q

test-web:
	@if ls client-web/*.test.mjs >/dev/null 2>&1; then node --test client-web/*.test.mjs; else echo "no client-web tests yet"; fi

vision:           ## vision service on :8001 (VISION_BACKEND=fake|ollama|auto)
	VISION_BACKEND=$(VISION_BACKEND) $(PY) -m uvicorn vision.app:app --host 0.0.0.0 --port $(VISION_PORT)

server:           ## orchestrator on :8000, serves client-web at / (v1) and /agent.html (v2)
	VISION_URL=http://127.0.0.1:$(VISION_PORT) $(PY) -m uvicorn server.app:app --host 0.0.0.0 --port $(SERVER_PORT) --no-proxy-headers

dev:              ## vision + server together; open http://localhost:8000/agent.html
	$(MAKE) vision & $(MAKE) server; kill %1

mock:             ## offline phone backend, no GPU service
	A_EYE_MOCK=1 $(PY) -m uvicorn server.app:app --host 0.0.0.0 --port 8000 --no-proxy-headers

phone:            ## print the HTTPS command for iPhone testing (Safari needs HTTPS for camera and mic)
	@echo "First set A_EYE_TOKEN to a long random secret and restart the server (or configure both CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD)."
	@test -n "$$A_EYE_TOKEN" || { test -n "$$CF_ACCESS_TEAM_DOMAIN" && test -n "$$CF_ACCESS_AUD"; } || { echo "No credentials configured; refusing to suggest tunnel commands."; exit 1; }
	@echo "Run this yourself (it changes Tailscale settings on this Mac):"
	@echo "  tailscale serve --bg $(SERVER_PORT)"
	@echo "Then open https://<this-mac>.<tailnet>.ts.net/agent.html on the phone (same tailnet)."
	@echo "Stop it with: tailscale serve reset"
	@echo "Quick tunnel: cloudflared tunnel --url http://localhost:$(SERVER_PORT)"
	@echo "Open /agent.html?token=... with your configured token, or sign in through configured Cloudflare Access."

smoke:            ## end to end: photo -> server -> fake vision -> box + step
	VISION_BACKEND=fake $(PY) scripts/smoke.py

llm-probe:        ## one router call with .env loaded: make llm-probe Q="your question"
	$(PY) -m server.llm_probe "$(Q)"

.PHONY: gemini-live-check
.PHONY: places-live-check
places-live-check: ## .env-loaded synthetic write/read/delete; prints presence only
	@$(PY) -m server.places_probe

gemini-live-check: ## synthetic camera probe with .env loaded; never prints credentials
	$(PY) scripts/gemini_live_check.py

keys:             ## which keys are set (length only, never the value)
	@for k in ANTHROPIC_API_KEY CARTESIA_API_KEY APIFY_TOKEN BRIGHTDATA_API_KEY; do v=$$(printenv $$k); echo "$$k: $${#v} chars"; done

browser-smoke:    ## real Chromium with fake camera and mic against a running server: make browser-smoke URL=http://127.0.0.1:8010
	uv run --no-project --with playwright python scripts/browser_smoke.py $(or $(URL),http://127.0.0.1:$(SERVER_PORT))

demo-rehearsal:   ## stage demo rehearsal (Where are my keys?) in Chromium with a synthetic camera and a say mic
	uv run --no-project --with playwright --with pillow python scripts/demo_rehearsal.py

.PHONY: ready
ready:            ## morning connection and device checks (no settings changed)
	$(PY) scripts/ready.py

.PHONY: offline-check
offline-check:    ## unplug WAN first; real local service probes and Private boundary tests
	$(PY) scripts/offline_check.py
