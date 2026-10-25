import base64
import builtins
from io import BytesIO
import logging
import struct
from threading import Event

from fastapi.testclient import TestClient
from PIL import Image
import pytest

import boxsvc.app as service


class FakeStt:
    def transcribe(self, pcm, language):
        assert pcm == struct.pack("<3h", -100, 0, 100)
        assert language == "en"
        return "hello"


class FakeTts:
    def segments(self, text, voice):
        assert text == "private speech"
        assert voice == "af_heart"
        yield [0.5] * 240
        yield [-0.5] * 240


class FakeDetector:
    def detect(self, image, imgsz, conf):
        assert image.size == (200, 100)
        assert (imgsz, conf) == (320, 0.35)
        return [{"label": "keyboard", "xyxy": [20, 10, 100, 70], "conf": 0.9}]


@pytest.fixture
def client():
    with TestClient(service.create_app({
        "stt": FakeStt(), "tts": FakeTts(), "detect": FakeDetector(),
    })) as client:
        yield client


def requests():
    jpeg = BytesIO()
    Image.new("RGB", (200, 100)).save(jpeg, format="JPEG")
    return {
        "stt": {"pcm16_b64": base64.b64encode(struct.pack("<3h", -100, 0, 100)).decode()},
        "tts": {"text": "private speech"},
        "detect": {"jpeg_b64": base64.b64encode(jpeg.getvalue()).decode()},
    }


def test_health(client):
    assert client.get("/health").json() == {
        "ok": True, "stt": True, "tts": True, "detect": True, "device": "cpu",
    }


def test_stt(client):
    response = client.post("/stt", json=requests()["stt"])
    assert response.status_code == 200
    assert response.json()["text"] == "hello"
    assert isinstance(response.json()["ms"], float)
    assert response.json()["ms"] >= 0


def test_tts(client, caplog):
    with caplog.at_level(logging.INFO, logger="boxsvc.app"):
        with client.stream("POST", "/tts", json=requests()["tts"]) as response:
            data = b"".join(response.iter_bytes())
    assert response.headers["content-type"] == "application/octet-stream"
    assert "x-first-audio-ms" not in response.headers
    assert len(data) == 480 * 2 // 3 * 2
    # Signed, little-endian int16 samples; both segment polarities survive.
    assert struct.unpack("<320h", data) == (16384,) * 160 + (-16384,) * 160
    assert caplog.text.count("first_chunk_ms=") == 1
    assert "private speech" not in caplog.text


def test_detect(client):
    response = client.post("/detect", json=requests()["detect"])
    assert response.status_code == 200
    result = response.json()
    assert result["objects"][0]["bbox"] == pytest.approx([0.1, 0.1, 0.4, 0.6])
    assert result["objects"][0]["label"] == "keyboard"
    assert result["objects"][0]["conf"] == 0.9
    assert isinstance(result["ms"], float)
    assert result["ms"] >= 0


@pytest.mark.parametrize("name", ["stt", "tts", "detect"])
def test_missing_engine(name):
    with TestClient(service.create_app({})) as client:
        assert client.get("/health").json()[name] is False
        response = client.post("/" + name, json=requests()[name])
        assert response.status_code == 503
        assert name in response.json()["detail"]


def test_startup_survives_missing_library(monkeypatch):
    finished = Event()

    def missing(device):
        raise ModuleNotFoundError("private exception detail")

    def detector(device):
        finished.set()
        return FakeDetector()

    monkeypatch.setattr(service, "SttEngine", missing)
    monkeypatch.setattr(service, "TtsEngine", missing)
    monkeypatch.setattr(service, "Detector", detector)
    with TestClient(service.create_app()) as client:
        assert finished.wait(5)
        response = client.post("/stt", json=requests()["stt"])
        assert response.status_code == 503
        assert "ModuleNotFoundError" in response.text
        assert "private exception" not in response.text
        assert client.get("/health").json()["ok"] is True


def test_no_media_written(client, tmp_path, monkeypatch, caplog):
    bodies = requests()
    monkeypatch.chdir(tmp_path)
    original_open = builtins.open

    def no_writes(file, mode="r", *args, **kwargs):
        assert not any(flag in mode for flag in "wax+")
        return original_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", no_writes)
    with caplog.at_level(logging.INFO):
        for name, body in bodies.items():
            assert client.post("/" + name, json=body).status_code == 200
    assert list(tmp_path.iterdir()) == []
    for body in bodies.values():
        assert next(iter(body.values())) not in caplog.text


def test_invalid_media_and_validation_do_not_echo_inputs(client):
    for path, body in [
        ("stt", {"pcm16_b64": "private invalid audio"}),
        ("stt", {"pcm16_b64": "AA=="}),
        ("detect", {"jpeg_b64": "cHJpdmF0ZQ=="}),
        ("tts", {"text": {"private": "speech"}}),
    ]:
        response = client.post("/" + path, json=body)
        assert response.status_code == 422
        assert "private" not in response.text


def test_resample_interpolation_and_clipping():
    data = service.pcm16_16khz([0, 0, 1, -2, 0, 0])
    assert struct.unpack("<4h", data) == (0, 16384, -32767, 0)


def test_abandoned_tts_stream_does_not_hold_the_lock():
    """Seen live: a dropped /tts stream kept the Kokoro lock and every later request hung."""
    from boxsvc.engines import TtsEngine
    engine = TtsEngine.__new__(TtsEngine)
    from threading import Lock
    engine.lock = Lock()
    engine.local_dir = None
    engine.pipeline = lambda text, voice: iter([(None, None, [0.0] * 10), (None, None, [0.0] * 10)])
    first = engine.segments('a', 'af_heart')
    next(first)  # consumer takes one chunk, then abandons the stream
    assert engine.lock.acquire(timeout=0.5)
    engine.lock.release()
