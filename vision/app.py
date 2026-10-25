"""Vision HTTP boundary: contract validation and timing around a backend."""
from contextlib import asynccontextmanager
import logging
import threading

import json
import os
from time import perf_counter

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from server import privacy, cloud_meter
from jsonschema import ValidationError

from contracts import validate
from vision.fake import FakeBackend, SCENARIOS


def warm_backend(real):
    started = perf_counter()
    status = "ok"
    try:
        real.warmup()
    except Exception:
        # Exception strings may include request content; log status and time only.
        status = "failed"
    logging.getLogger(__name__).info("ollama warm-up %s ms=%.1f", status,
                                     (perf_counter() - started) * 1000)


@asynccontextmanager
async def lifespan(app):
    from vision.ollama import AutoBackend, OllamaBackend
    real = backend.ollama if isinstance(backend, AutoBackend) else backend
    if isinstance(real, OllamaBackend):
        thread = threading.Thread(target=warm_backend, args=(real,), daemon=True)
        app.state.warmup_thread = thread
        thread.start()
    yield


app = FastAPI(title="A-eye vision", lifespan=lifespan)
def select_backend(ollama=None):
    mode = os.environ.get("VISION_BACKEND", "fake")
    if mode == "fake":
        return FakeBackend()
    if mode == "gemini":
        from vision.gemini import GeminiBackend
        return GeminiBackend()
    if mode == "openai":
        from vision.openai import OpenAIBackend
        return OpenAIBackend()
    if mode == "openrouter":
        from vision.openai import OpenRouterBackend
        return OpenRouterBackend()
    if mode not in {"ollama", "auto", "cloud_fallback"}:
        raise ValueError("VISION_BACKEND must be fake, ollama, auto, gemini, openai, openrouter, or cloud_fallback")
    from vision.ollama import AutoBackend, OllamaBackend
    real = ollama if ollama is not None else OllamaBackend()
    if mode == "cloud_fallback":
        from vision.gemini import CloudFallbackBackend
        real.timeout = 4
        real.ground_fallback = False
        return CloudFallbackBackend(real)
    return AutoBackend(real, FakeBackend()) if mode == "auto" else real


backend = select_backend()


async def run(request, operation):
    started = perf_counter()
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail="Invalid JSON body") from None
    try:
        validate(operation + "_request", body)
    except ValidationError:
        # ValidationError text can contain image data. Never echo or log it.
        raise HTTPException(status_code=422, detail="Invalid " + operation + " request") from None
    scenario = request.headers.get("X-Fake-Scenario", os.environ.get("FAKE_SCENARIO", "ok"))
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=422, detail="Invalid fake scenario")
    private_token = privacy.PRIVATE.set(request.headers.get('X-Aeye-Private') != '0')
    switches_token = privacy.SWITCHES.set({'google': request.headers.get('X-Aeye-Google') == '1'})
    meter = cloud_meter.Meter()
    meter_token = cloud_meter.CURRENT.set(meter)
    try:
        result = getattr(backend, operation)(body, scenario)
    finally:
        privacy.PRIVATE.reset(private_token)
        privacy.SWITCHES.reset(switches_token)
        cloud_meter.CURRENT.reset(meter_token)
    result["ms"] = (perf_counter() - started) * 1000
    validate(operation + "_response", result)
    return JSONResponse(result, headers={'X-Aeye-Cloud-Calls': str(meter.calls),
                                         'X-Aeye-Cloud-Bytes': str(meter.bytes)})


@app.get("/health")
def health():
    result = backend.health()
    # health_response disallows ms; keep the contract unchanged.
    validate("health_response", result)
    return result


@app.post("/identify")
async def identify(request: Request):
    return await run(request, "identify")


@app.post("/locate")
async def locate(request: Request):
    return await run(request, "locate")


@app.post("/verify")
async def verify(request: Request):
    return await run(request, "verify")


@app.post("/scene")
async def scene(request: Request):
    return await run(request, "scene")
