"""Latency constraints exercised with synthetic images and offline HTTP."""
import base64
from io import BytesIO
import json
import threading

import httpx
from PIL import Image
import pytest
from fastapi.testclient import TestClient

import vision.app as service
import vision.ollama as ollama
from vision.probe_ollama import synthetic_scene


@pytest.fixture
def body():
    return {"image_b64": synthetic_scene(), "queries": ["red square", "blue circle", "green triangle", "table"]}


def test_single_queries_budget_and_client(body, monkeypatch):
    now = [0.0]
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        now[0] += .8
        label = body["queries"][len(requests) - 1]
        return httpx.Response(200, json={"message": {"content": json.dumps(
            {"label": label, "bbox_2d": [100, 100, 300, 300]})}})
    monkeypatch.setattr(ollama, "perf_counter", lambda: now[0])
    real = ollama.OllamaBackend(transport=httpx.MockTransport(handle))
    client = real.client
    result = real.scene(body, "ok")
    assert [row["label"] for row in result["objects"]] == body["queries"][:2]
    assert result["caption"] == ""
    assert real.client is client
    assert len(requests) == 2
    for index, payload in enumerate(requests):
        prompt = payload["messages"][0]["content"]
        assert body["queries"][index] in prompt
        assert all(query not in prompt for query in body["queries"] if query != body["queries"][index])
    now[0] = 0
    requests.clear()
    monkeypatch.setattr(ollama, "perf_counter", lambda: 0)
    assert len(real.scene(body, "ok")["objects"]) == 3
    assert len(requests) == 3
    real.client.close()


def test_all_payloads_and_caption(body):
    payloads = []
    def handle(request):
        assert request.extensions["timeout"]["read"] == 8
        payload = json.loads(request.content)
        payloads.append(payload)
        with Image.open(BytesIO(base64.b64decode(payload["messages"][0]["images"][0]))) as image:
            assert max(image.size) == 1280
        return httpx.Response(200, json={"message": {"content": '{"caption":"Shapes."}'}})
    real = ollama.OllamaBackend(transport=httpx.MockTransport(handle))
    real.scene({**body, "queries": ["red square"]}, "ok")
    result = real.scene({"image_b64": body["image_b64"]}, "ok")
    assert result["caption"] == "Shapes." and result["objects"] == []
    real.identify({**body, "candidates": ["ram_stick"]}, "ok")
    real.locate({**body, "board_id": "demo-b650", "header": "CPU_FAN"}, "ok")
    real.verify({**body, "board_id": "demo-b650", "header": "CPU_FAN"}, "ok")
    real.warmup()
    assert [p["options"]["num_predict"] for p in payloads] == [60, 60, 80, 40, 60, 60, 60, 60, 60, 60, 40, 1]
    for payload in payloads:
        assert payload["options"]["temperature"] == 0
        assert payload["options"]["num_ctx"] == 4096
        assert payload["keep_alive"] == "30m"
    real.client.close()


@pytest.mark.parametrize("mode", ["ollama", "auto"])
def test_background_warmup(monkeypatch, mode, caplog):
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    def handle(request):
        entered.set()
        assert release.wait(3)
        return httpx.Response(200, json={"message": {"content": '{}'}})
    real = ollama.OllamaBackend(transport=httpx.MockTransport(handle))
    monkeypatch.setenv("VISION_BACKEND", mode)
    monkeypatch.setattr(service, "backend", service.select_backend(real))
    original = real.warmup
    def warmup():
        try:
            original()
        finally:
            completed.set()
    monkeypatch.setattr(real, "warmup", warmup)
    caplog.set_level("INFO")
    try:
        with TestClient(service.app):
            assert entered.wait(1)
            assert not completed.is_set()  # Startup finished while HTTP is still blocked.
    finally:
        release.set()
        service.app.state.warmup_thread.join(3)
        real.client.close()
    assert completed.is_set()
    assert "warm-up" in caplog.text and "ms=" in caplog.text


def test_single_box_salvage(body):
    raw = '{"label":"red square","bbox_2d":[100,100,300,300}'
    assert ollama.salvage_box(raw)["bbox_2d"] == [100, 100, 300, 300]
    real = ollama.OllamaBackend(transport=httpx.MockTransport(lambda request:
        httpx.Response(200, json={"message": {"content": raw}})))
    result = real.scene({**body, "queries": ["red square"]}, "ok")
    assert result["objects"][0]["label"] == "red square"
    real.client.close()


def test_locate_target_first_budget(body, monkeypatch):
    now = [0.0]
    prompts = []
    def handle(request):
        prompts.append(json.loads(request.content)["messages"][0]["content"])
        now[0] += 1.6
        return httpx.Response(200, json={"message": {"content":
            '{"label":"DDR5_A2","bbox_2d":[100,100,300,300}'}})
    monkeypatch.setattr(ollama, "perf_counter", lambda: now[0])
    real = ollama.OllamaBackend(transport=httpx.MockTransport(handle))
    result = real.locate({**body, "board_id": "demo-b650", "header": "DIMM_A2"}, "ok")
    assert result["found"] and result["others"] == []
    assert len(prompts) == 1 and "DDR5_A2" in prompts[0] and "CPU_FAN" not in prompts[0]
    real.client.close()


def test_bench_timings(monkeypatch, capsys):
    from vision import probe_ollama
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"message": {"content": '{}'},
            "load_duration": 1_000_000, "prompt_eval_duration": 2_000_000,
            "eval_duration": 3_000_000})
    real = ollama.OllamaBackend(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(probe_ollama, "OllamaBackend", lambda: real)
    probe_ollama.main(["--bench", "3"])
    output = capsys.readouterr().out
    assert len(requests) == 6
    assert "wall median_ms=" in output
    for key, value in [("load_duration", 1), ("prompt_eval_duration", 2), ("eval_duration", 3)]:
        assert f"{key} median_ms={value:.1f} min_ms={value:.1f} max_ms={value:.1f} n=3" in output
    assert "image_b64" not in output
