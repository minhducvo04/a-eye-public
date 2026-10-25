"""Synthetic images and mocked Ollama HTTP; no GPU or network needed."""
import base64
from io import BytesIO
import json

import httpx
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from contracts import validate
from vision.ollama import OllamaBackend
import vision.app as service


@pytest.fixture
def body():
    stream = BytesIO()
    Image.new("RGB", (640, 480), "darkgreen").save(stream, format="JPEG")
    return {"image_b64": base64.b64encode(stream.getvalue()).decode(),
            "board_id": "demo-b650", "header": "CPU_FAN"}


def backend(answer, requests=None):
    def handle(request):
        if requests is not None:
            requests.append(request)
        return httpx.Response(200, json={"message": {"content": answer}})
    return OllamaBackend(model="qwen2.5vl:7b-q4_K_M", transport=httpx.MockTransport(handle))


def check(operation, result):
    validate(operation + "_response", {**result, "ms": 0})
    return result


def test_locate_resize_and_fences(body):
    requests = []
    answer = 'Here: ```json\n[{"label":"cpu fan","bbox_2d":[128,96,384,288]},' \
             '{"label":"SYS_FAN1","bbox_2d":[-10,900,1400,1000]}]\n``` done'
    result = check("locate", backend(answer, requests).locate(body, "ok"))
    assert result["bbox"] == pytest.approx([.1, .1, .2, .2])
    assert result["conf"] == .7 and result["source"] == "ocr"
    assert "pins" not in result
    assert result["others"][0]["bbox"] == pytest.approx([0, .9375, 1, .0625])
    payload = json.loads(requests[0].content)
    assert "format" not in payload and payload["stream"] is False
    assert payload["options"] == {"temperature": 0, "num_ctx": 4096, "num_predict": 60}
    image = Image.open(BytesIO(base64.b64decode(payload["messages"][0]["images"][0])))
    assert image.size == (1280, 960)
    assert "DDR5_A2" not in payload["messages"][0]["content"]
    assert len(requests) == 4
    assert requests[0].extensions["timeout"]["read"] == 8


@pytest.mark.parametrize("label,conf", [("CPU_FAM", .5), ("CPU_FAN", .7)])
def test_exact_fuzzy(body, label, conf):
    result = check("locate", backend(json.dumps({"label": label, "bbox_2d": [0, 0, 100, 100]})).locate(body, "ok"))
    assert result["found"] and result["conf"] == conf


@pytest.mark.parametrize("answer", ['nonsense', '[]', '{"label":"CPU_FAN","bbox_2d":[5,5,1,1]}'])
def test_locate_missing(body, answer):
    assert check("locate", backend(answer).locate(body, "ok"))["found"] is False


@pytest.mark.parametrize("item,expected", [("ram_stick", "ram_stick"), ("cpu_fan_cable", "unknown"), ("alien", "unknown")])
def test_identify(body, item, expected):
    result = check("identify", backend(json.dumps({"item": item, "confidence": 1})).identify(
        {"image_b64": body["image_b64"], "candidates": ["ram_stick"]}, "ok"))
    assert result == {"item": expected, "conf": .85}


@pytest.mark.parametrize("answer,passed,abstain", [
    ('broken', False, True), ('{"plugged":"true","label":"CPU_FAN"}', False, True),
    ('{"plugged":true,"label":"cpu fan"}', True, False),
    ('{"plugged":true,"label":"SYS_FAN1"}', False, False),
    ('{"plugged":false,"label":"CPU_FAN"}', False, False)])
def test_verify(body, answer, passed, abstain):
    result = check("verify", backend(answer).verify(body, "ok"))
    assert result["pass"] is passed and result.get("abstain", False) is abstain
    assert result["conf"] == (.3 if abstain else .75)


@pytest.mark.parametrize("vram,expected", [(100, "gpu"), (50, "partial"), (0, "cpu"), (None, "not_loaded")])
def test_health(vram, expected):
    model = "test-model"
    def handle(request):
        assert request.extensions["timeout"]["read"] == 2
        rows = [{"name": model, "size": 100, "size_vram": vram}]
        if request.url.path == "/api/ps" and vram is None:
            rows = []
        return httpx.Response(200, json={"models": rows + [{"name": "qwen2.5vl:7b-q4_K_M"}]})
    result = OllamaBackend(model=model, transport=httpx.MockTransport(handle)).health()
    validate("health_response", result)
    assert result == {"ok": True, "models": [model, "qwen2.5vl:7b-q4_K_M"], "gpu": expected, "fake": False}


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
def test_auto_fallback_and_retry(body, monkeypatch, caplog, error):
    calls = []
    def handle(request):
        calls.append(request.url.path)
        if len(calls) <= 2:
            raise error("unreachable", request=request)
        return httpx.Response(200, json={"message": {"content": '{"item":"ram_stick","confidence":0.8}'}})
    monkeypatch.setenv("VISION_BACKEND", "auto")
    monkeypatch.setattr(OllamaBackend, "warmup", lambda self: None)
    monkeypatch.setattr(service, "backend", service.select_backend(
        OllamaBackend(model="qwen2.5vl:7b-q4_K_M", transport=httpx.MockTransport(handle))))
    with TestClient(service.app) as client:
        request = {"image_b64": body["image_b64"], "candidates": ["ram_stick"]}
        assert client.post("/identify", json=request).json()["conf"] == .9
        assert client.get("/health").json() == {"ok": True, "models": ["fake"], "fake": True}
        assert client.post("/identify", json=request).json()["conf"] == .8
    assert "ollama unreachable, using fake" in caplog.text
    assert body["image_b64"] not in caplog.text


def test_alias_and_portrait(body):
    stream = BytesIO()
    Image.new("RGB", (600, 1200), "darkgreen").save(stream, format="JPEG")
    body.update(image_b64=base64.b64encode(stream.getvalue()).decode(), header="DDR5_A2")
    requests = []
    result = check("locate", backend(
        '[{"label":"DDR5_A2","bbox_2d":[64,128,192,384]}]', requests).locate(body, "ok"))
    assert result["bbox"] == pytest.approx([.1, .1, .2, .2])
    payload = json.loads(requests[0].content)
    image = Image.open(BytesIO(base64.b64decode(payload["messages"][0]["images"][0])))
    assert image.size == (640, 1280)


def test_selection_and_env(monkeypatch):
    from vision.fake import FakeBackend
    from vision.ollama import AutoBackend
    monkeypatch.delenv("VISION_BACKEND", raising=False)
    assert isinstance(service.select_backend(), FakeBackend)
    monkeypatch.setenv("OLLAMA_URL", "http://pc:11434/")
    monkeypatch.setenv("VISION_GROUND_MODEL", "configured-model")
    real = OllamaBackend(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    assert real.url == "http://pc:11434" and real.model == "configured-model"
    monkeypatch.setenv("VISION_BACKEND", "ollama")
    assert service.select_backend(real) is real
    monkeypatch.setenv("VISION_BACKEND", "auto")
    assert isinstance(service.select_backend(real), AutoBackend)


def test_auto_does_not_hide_http_errors(body):
    from vision.ollama import AutoBackend
    from vision.fake import FakeBackend
    real = OllamaBackend(model="qwen2.5vl:7b-q4_K_M", transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    with pytest.raises(httpx.HTTPStatusError):
        AutoBackend(real, FakeBackend()).locate(body, "ok")


def test_probe_offline(monkeypatch, capsys):
    from vision import probe_ollama
    image, target = probe_ollama.synthetic_board()
    assert probe_ollama.iou(target, target) == pytest.approx(1)
    x, y, w, h = target
    def handle(request):
        if request.url.path != "/api/chat":
            return httpx.Response(200, json={"models": []})
        answer = [{"label": "CPU_FAN", "bbox_2d": [x * 1280, y * 960, (x + w) * 1280, (y + h) * 960]}]
        return httpx.Response(200, json={"message": {"content": json.dumps(answer)}})
    real = OllamaBackend(model="qwen2.5vl:7b-q4_K_M", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(probe_ollama, "OllamaBackend", lambda: real)
    probe_ollama.main()
    output = capsys.readouterr().out
    assert '"ok": false' in output and "IoU=1.0000" in output and "ms=" in output
    assert image not in output
