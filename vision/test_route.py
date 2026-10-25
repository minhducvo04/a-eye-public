"""Per-model routing through offline Ollama HTTP with synthetic frames."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import vision.app as service
from vision.ollama import OllamaBackend
from vision.probe_ollama import synthetic_scene


@pytest.mark.parametrize("model,box,order,units", [
    ("qwen3.5:9b-q4_K_M", [100, 200, 400, 600], "x1,y1,x2,y2", "0 to 1000"),
    ("qwen2.5vl:7b-q4_K_M", [128, 192, 512, 576], "x1,y1,x2,y2", "pixels"),
    ("gemma4:12b", [100, 200, 400, 600], "x1,y1,x2,y2", "0 to 1000"),
    ("gemma4:26b-a4b", [200, 100, 600, 400], "y1,x1,y2,x2", "0 to 1000"),
    ("unknown", [100, 200, 400, 600], "x1,y1,x2,y2", "0 to 1000"),
])
def test_formats(model, box, order, units):
    def handle(request):
        payload = json.loads(request.content)
        assert payload["model"] == model
        assert payload["options"] == {"temperature": 0, "num_ctx": 4096, "num_predict": 60}
        assert payload["keep_alive"] == "30m"
        if model.startswith(("qwen3.5", "gemma4")):
            assert payload["think"] is False
        prompt = payload["messages"][0]["content"]
        assert order in prompt and units in prompt
        return httpx.Response(200, json={"message": {"content": json.dumps(
            {"label": "red square", "bbox_2d": box})}})
    real = OllamaBackend(model=model, transport=httpx.MockTransport(handle))
    result = real.scene({"image_b64": synthetic_scene(), "queries": ["red square"]}, "ok")
    assert result["objects"][0]["bbox"] == pytest.approx([.1, .2, .3, .4])
    real.client.close()


@pytest.mark.parametrize("operation", ["scene", "locate"])
@pytest.mark.parametrize("failure", ["http", "timeout", "broken", "wrong_label", "zero_area"])
def test_fallback_once(operation, failure, monkeypatch):
    monkeypatch.setenv("VISION_GROUND_MODEL", "qwen3.5:9b")
    monkeypatch.setenv("VISION_FALLBACK_MODEL", "qwen2.5vl:7b")
    # One board label isolates retry counts from optional secondary locate labels.
    monkeypatch.setattr("vision.ollama.headers_for", lambda _: [{"name": "CPU_FAN"}])
    models = []
    def handle(request):
        model = json.loads(request.content)["model"]
        models.append(model)
        if len(models) == 1:
            if failure == "http":
                return httpx.Response(500)
            if failure == "timeout":
                raise httpx.ReadTimeout("offline", request=request)
            text = {"broken": "nonsense", "wrong_label": '{"label":"other","bbox_2d":[100,200,400,600]}',
                    "zero_area": '{"label":"CPU_FAN","bbox_2d":[100,200,100,600]}'}[failure]
        else:
            text = '{"label":"CPU_FAN","bbox_2d":[128,192,512,576]}'
        return httpx.Response(200, json={"message": {"content": text}})
    real = OllamaBackend(transport=httpx.MockTransport(handle))
    result = getattr(real, operation)({"image_b64": synthetic_scene(), "queries": ["CPU_FAN"],
        "board_id": "demo-b650", "header": "CPU_FAN"}, "ok")
    box = result["objects"][0]["bbox"] if operation == "scene" else result["bbox"]
    assert box == pytest.approx([.1, .2, .3, .4])
    assert models == ["qwen3.5:9b", "qwen2.5vl:7b"]
    real.client.close()


def test_caption_and_health(monkeypatch):
    monkeypatch.setenv("VISION_GROUND_MODEL", "gemma4:12b")
    monkeypatch.setenv("VISION_CAPTION_MODEL", "qwen2.5vl:7b")
    def handle(request):
        if request.url.path == "/api/chat":
            assert json.loads(request.content)["model"] == "qwen2.5vl:7b"
            return httpx.Response(200, json={"message": {"content": '{"caption":"Shapes."}'}})
        return httpx.Response(200, json={"models": [
            {"name": "gemma4:12b", "size": 100, "size_vram": 50},
            {"name": "qwen2.5vl:7b", "size": 100, "size_vram": 100}]})
    real = OllamaBackend(transport=httpx.MockTransport(handle))
    assert real.scene({"image_b64": synthetic_scene()}, "ok")["caption"] == "Shapes."
    monkeypatch.setattr(real, "warmup", lambda: None)
    monkeypatch.setattr(service, "backend", real)
    with TestClient(service.app) as client:
        assert client.get("/health").json() == {"ok": True, "models": ["gemma4:12b", "qwen2.5vl:7b"],
                                                "gpu": "partial", "fake": False}
    real.client.close()


def test_caption_with_queries(monkeypatch):
    monkeypatch.setenv("VISION_GROUND_MODEL", "qwen3.5:9b")
    monkeypatch.setenv("VISION_CAPTION_MODEL", "qwen2.5vl:7b")
    models = []
    def handle(request):
        model = json.loads(request.content)["model"]
        models.append(model)
        answer = ({"caption": "A red square on a table."} if model == "qwen2.5vl:7b"
                  else {"label": "red square", "bbox_2d": [100, 200, 400, 600]})
        return httpx.Response(200, json={"message": {"content": json.dumps(answer)}})
    real = OllamaBackend(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(real, "warmup", lambda: None)
    monkeypatch.setattr(service, "backend", real)
    with TestClient(service.app) as client:
        response = client.post("/scene", json={"image_b64": synthetic_scene(),
            "queries": ["red square"], "caption": True})
    assert response.status_code == 200
    result = response.json()
    assert result["caption"] == "A red square on a table."
    assert result["objects"][0]["bbox"] == pytest.approx([.1, .2, .3, .4])
    assert models == ["qwen3.5:9b", "qwen2.5vl:7b"]
    real.client.close()


def test_failed_fallback_stops_and_boxes_clamp():
    from vision.ollama import box_normalized
    calls = []
    def handle(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"message": {"content": "unparseable"}})
    real = OllamaBackend(model="qwen3.5:9b", transport=httpx.MockTransport(handle))
    assert real.scene({"image_b64": synthetic_scene(), "queries": ["red square"]}, "ok")["objects"] == []
    assert calls == ["qwen3.5:9b", real.fallback_model]
    assert box_normalized([-100, 200, 1200, 600], 1280, 960, "qwen3.5") == pytest.approx([0, .2, 1, .4])
    assert box_normalized([100, 200, 100, 600], 1280, 960, "qwen3.5") is None
    real.client.close()


def test_bench_model_and_iou(monkeypatch, capsys):
    from vision import probe_ollama
    calls = []
    def handle(request):
        payload = json.loads(request.content)
        calls.append(payload["model"])
        return httpx.Response(200, json={"message": {"content":
            '{"label":"red square","bbox_2d":[100,100,300,300]}'}})
    monkeypatch.setattr(probe_ollama, "OllamaBackend", lambda **kwargs:
        OllamaBackend(**kwargs, transport=httpx.MockTransport(handle)))
    probe_ollama.main(["--bench", "3", "--model", "qwen2.5vl:7b"])
    output = capsys.readouterr().out
    assert calls == ["qwen2.5vl:7b"] * 3
    assert output.count("IoU=1.0000") == 3
    assert "IoU median=1.0000 min=1.0000 max=1.0000" in output
    assert "wall median_ms=" in output and "min_ms=" in output and "max_ms=" in output


def test_long_caption_gets_enough_tokens(monkeypatch):
    """Reported live: num_predict 40 cut a long qwen2.5vl caption mid-string, so every frame read
    "Could not describe the frame." The caption call now asks for 15 words and allows 80 tokens."""
    monkeypatch.setenv("VISION_CAPTION_MODEL", "qwen2.5vl:7b")
    long_caption = ("A person holds a black four-pin fan cable above a motherboard next to a silver heatsink "
                    "on a wooden desk under a lamp")
    seen = {}
    def handle(request):
        body = json.loads(request.content)
        seen["num_predict"] = body["options"]["num_predict"]
        seen["prompt"] = body["messages"][0]["content"]
        return httpx.Response(200, json={"message": {"content": json.dumps({"caption": long_caption})}})
    real = OllamaBackend(transport=httpx.MockTransport(handle))
    assert real.scene({"image_b64": synthetic_scene()}, "ok")["caption"] == long_caption
    assert seen["num_predict"] >= 80 and "15 words" in seen["prompt"]
    real.client.close()
