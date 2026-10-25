"""FastAPI boundary for in-memory speech and detection."""
import base64
import binascii
from contextlib import asynccontextmanager
from io import BytesIO
import logging
from threading import Thread
from time import perf_counter

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from boxsvc.engines import Detector, SttEngine, TtsEngine, pcm16_16khz

log = logging.getLogger(__name__)


class SttRequest(BaseModel):
    pcm16_b64: str
    language: str = "en"


class TtsRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str = "af_heart"


class DetectRequest(BaseModel):
    jpeg_b64: str
    imgsz: int = Field(default=320, gt=0)
    conf: float = Field(default=0.35, ge=0, le=1)


def decode(value):
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "Invalid base64") from None


def create_app(engines=None):
    """An explicit engine mapping disables model startup, for tests."""
    loaded = dict(engines or {})
    reasons = {name: "model loading" for name in ("stt", "tts", "detect")}
    device = "cpu"

    def load_models():
        nonlocal device
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            pass
        for name, cls in (("stt", SttEngine), ("tts", TtsEngine), ("detect", Detector)):
            try:
                loaded[name] = cls(device)
            except Exception as exc:
                # Exception messages can contain inputs or local secrets.
                reasons[name] = f"model unavailable ({type(exc).__name__})"
                log.warning("%s: %s", name, reasons[name])

    @asynccontextmanager
    async def lifespan(app):
        if engines is None:
            Thread(target=load_models, daemon=True, name="boxsvc-models").start()
        yield

    app = FastAPI(title="A-eye GPU box service", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Default validation errors may echo request media or text.
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    def engine(name):
        result = loaded.get(name)
        if result is None:
            raise HTTPException(503, f"{name}: {reasons[name]}")
        return result

    @app.get("/health")
    def health():
        return {"ok": True, **{name: loaded.get(name) is not None for name in reasons},
                "device": device}

    @app.post("/stt")
    def stt(body: SttRequest):
        model = engine("stt")
        started = perf_counter()
        pcm = decode(body.pcm16_b64)
        if not pcm or len(pcm) % 2:
            raise HTTPException(422, "Expected nonempty PCM16 samples")
        return {"text": model.transcribe(pcm, body.language),
                "ms": (perf_counter() - started) * 1000}

    @app.post("/tts")
    def tts(body: TtsRequest):
        model = engine("tts")
        started = perf_counter()

        def chunks():
            first = True
            for audio in model.segments(body.text, body.voice):
                chunk = pcm16_16khz(audio)
                if not chunk:
                    continue
                if first:
                    log.info("tts first_chunk_ms=%.3f", (perf_counter() - started) * 1000)
                    first = False
                yield chunk

        return StreamingResponse(chunks(), media_type="application/octet-stream")

    @app.post("/detect")
    def detect(body: DetectRequest):
        model = engine("detect")
        started = perf_counter()
        try:
            with Image.open(BytesIO(decode(body.jpeg_b64))) as source:
                if source.format != "JPEG":
                    raise HTTPException(422, "Expected JPEG")
                image = source.convert("RGB")
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise HTTPException(422, "Invalid JPEG") from None
        with image:
            width, height = image.size
            objects = []
            for item in model.detect(image, body.imgsz, body.conf):
                x1, y1, x2, y2 = item["xyxy"]
                x1, x2 = [max(0.0, min(1.0, x / width)) for x in (x1, x2)]
                y1, y2 = [max(0.0, min(1.0, y / height)) for y in (y1, y2)]
                objects.append({"label": item["label"], "bbox": [x1, y1, max(0, x2-x1), max(0, y2-y1)],
                                "conf": float(item["conf"])})
        return {"objects": objects, "ms": (perf_counter() - started) * 1000}

    return app


app = create_app()
