"""Scene acceptance tests; synthetic images and offline Ollama transport only."""
import base64
from io import BytesIO
import json

import httpx
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from contracts import validate
import vision.app as service
from vision.fake import FakeBackend
from vision.ollama import AutoBackend, OllamaBackend


@pytest.fixture
def body():
    stream = BytesIO()
    Image.new("RGB", (2560, 1920), "white").save(stream, format="JPEG")
    return {"image_b64": base64.b64encode(stream.getvalue()).decode(),
            "queries": ["red square", "blue circle"]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(service, "backend", FakeBackend())
    monkeypatch.delenv("FAKE_SCENARIO", raising=False)
    with TestClient(service.app) as client:
        yield client


def test_fake_scene(client):
    body = {"image_b64": "synthetic", "queries": ["the onion", "the cable in my hand"]}
    response = client.post("/scene", json=body)
    assert response.status_code == 200
    result = response.json()
    validate("scene_response", result)
    assert result["source"] == "fake" and result["ms"] >= 0
    assert [row["label"] for row in result["objects"][:2]] == body["queries"]
    assert len(result["objects"]) <= 8
    assert [row["id"] for row in result["objects"]] == [f"o{i+1}" for i in range(len(result["objects"]))]
    assert result["objects"][0]["bbox"] != result["objects"][1]["bbox"]
    capped = client.post("/scene", json={**body, "max_objects": 1}).json()
    assert [row["label"] for row in capped["objects"]] == ["the onion"]
    canned = client.post("/scene", json={"image_b64": "synthetic"}).json()
    assert canned["caption"] and canned["objects"]
    assert canned["objects"] == client.post("/scene", json={"image_b64": "synthetic"}).json()["objects"]


def test_blurry(client):
    result = client.post("/scene", json={"image_b64": "synthetic", "queries": ["onion"]},
                         headers={"X-Fake-Scenario": "blurry"}).json()
    validate("scene_response", result)
    assert result["objects"] == [] and result["caption"] == "too blurry to see"


@pytest.mark.parametrize("body", [{}, [], {"image_b64": "private-marker", "max_objects": 0},
                                   {"image_b64": "private-marker", "queries": [""]}])
def test_bad_body(client, body, caplog):
    response = client.post("/scene", json=body)
    assert response.status_code == 422
    assert "private-marker" not in response.text + caplog.text


def test_ollama_scene(body):
    requests = []
    answer = {"caption": "Colored shapes sit on a white background.", "objects": [
        {"label": "table", "bbox_2d": [-10, 900, 1400, 1000]},
        {"label": "blue circle", "bbox_2d": [640, 480, 960, 720]},
        {"label": "invalid", "bbox_2d": [5, 5, 5, 10]},
        {"label": "red square", "bbox_2d": [128, 96, 384, 288]}]}
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"message": {"content": "```json\n" + json.dumps(answer) + "\n```"}})
    backend = OllamaBackend(model="qwen2.5vl:7b-q4_K_M", transport=httpx.MockTransport(handle))
    result = backend.scene(body, "ok")
    validate("scene_response", {**result, "ms": 0})
    assert result["source"] == "ollama" and result["caption"] == ""
    assert [row["label"] for row in result["objects"]] == ["red square", "blue circle"]
    assert [row["id"] for row in result["objects"]] == ["o1", "o2"]
    assert [row["conf"] for row in result["objects"]] == [.7, .7]
    assert result["objects"][0]["bbox"] == pytest.approx([.1, .1, .2, .2])
    assert len(requests) == 2
    payload = json.loads(requests[0].content)
    message = payload["messages"][0]
    with Image.open(BytesIO(base64.b64decode(message["images"][0]))) as image:
        assert image.size == (1280, 960)
    assert "red square" in message["content"] and "blue circle" not in message["content"]
    capped = backend.scene({**body, "max_objects": 1}, "ok")
    assert [row["label"] for row in capped["objects"]] == ["red square"]


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
def test_auto_scene(body, error):
    def handle(request):
        raise error("unreachable", request=request)
    backend = AutoBackend(OllamaBackend(model="qwen2.5vl:7b-q4_K_M", transport=httpx.MockTransport(handle)), FakeBackend())
    assert backend.scene(body, "ok") == FakeBackend().scene(body, "ok")
    assert backend.scene(body, "blurry")["objects"] == []


def test_scene_probe(monkeypatch, capsys):
    from vision import probe_ollama
    def handle(request):
        if request.url.path != "/api/chat":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, json={"message": {"content": json.dumps({
            "caption": "Three colored shapes.", "objects": [
                {"label": "red square", "bbox_2d": [100, 100, 300, 300]}]})}})
    monkeypatch.setattr(probe_ollama, "OllamaBackend",
                        lambda: OllamaBackend(model="qwen2.5vl:7b-q4_K_M", transport=httpx.MockTransport(handle)))
    probe_ollama.main(["--scene"])
    output = capsys.readouterr().out
    assert '"caption": ""' in output and '"objects"' in output and '"ms"' in output
    assert "image_b64" not in output


def test_salvage_broken_qwen_json():
    from vision.ollama import salvage_scene
    # Exact shape seen live from qwen2.5vl:7b on the PC (box closed with '}').
    raw = ('```json\n{"caption": "a red plate and a blue box", "objects": [{"label": "the red plate", '
           '"bbox_2d": [205, 296, 509, 600}], {"label": "blue box", "bbox_2d": [800, 196, 1004, 697]}]}\n```')
    got = salvage_scene(raw)
    assert got["caption"] == "a red plate and a blue box"
    assert [r["label"] for r in got["objects"]] == ["the red plate", "blue box"]
    assert got["objects"][0]["bbox_2d"] == [205.0, 296.0, 509.0, 600.0]
