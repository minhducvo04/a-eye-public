"""HTTP acceptance tests using synthetic image strings only."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from contracts import validate
from vision.app import app


REQUESTS = {
    "identify": {"image_b64": "synthetic", "candidates": ["ram_stick", "cpu_fan_cable"]},
    "locate": {"image_b64": "synthetic", "board_id": "demo-b650", "header": "CPU_FAN"},
    "verify": {"image_b64": "synthetic", "board_id": "demo-b650", "header": "CPU_FAN",
               "expect": {"item": "cpu_fan_cable"}},
}
HEADERS = {"CPU_FAN", "SYS_FAN1", "F_PANEL", "DIMM_A2", "DIMM_B2"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("FAKE_SCENARIO", raising=False)
    with TestClient(app) as client:
        yield client


def post(client, endpoint, scenario=None, **changes):
    body = deepcopy(REQUESTS[endpoint])
    body.update(changes)
    validate(endpoint + "_request", body)
    headers = {"X-Fake-Scenario": scenario} if scenario else {}
    response = client.post("/" + endpoint, json=body, headers=headers)
    assert response.status_code == 200
    result = response.json()
    validate(endpoint + "_response", result)
    assert result["ms"] >= 0
    return result


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    validate("health_response", response.json())
    assert response.json() == {"ok": True, "models": ["fake"], "fake": True}


def test_identify(client):
    result = post(client, "identify")
    assert result["item"] == "ram_stick"
    assert result["conf"] == 0.9
    assert len(result["bbox"]) == 4


@pytest.mark.parametrize("header", sorted(HEADERS))
def test_locate(client, header):
    result = post(client, "locate", header=header)
    assert result["found"] is True
    assert result["conf"] == 0.86
    assert result["source"] == "fake"
    assert {other["header"] for other in result["others"]} == HEADERS - {header}
    assert "pins" not in result
    x, y, w, h = result["bbox"]
    assert x + w <= 1 and y + h <= 1


@pytest.mark.parametrize("header", sorted(HEADERS))
def test_pin_gate(client, header):
    result = post(client, "locate", header=header, want_pins=[5, 6])
    if header == "F_PANEL":
        assert result["pins"]["pin_ok"] is True
        assert result["pins"]["grid"]
    else:
        assert "pins" not in result


@pytest.mark.parametrize("changes", [{"header": "missing"}, {"board_id": "missing"}])
def test_unknown_location(client, changes):
    result = post(client, "locate", **changes)
    assert result["found"] is False
    assert result["conf"] == 0
    assert "bbox" not in result and "pins" not in result


def test_verify(client):
    result = post(client, "verify")
    assert result["pass"] is True
    assert result["conf"] == 0.9
    assert result["seen_header"] == "CPU_FAN"


def test_wrong_header(client):
    result = post(client, "verify", "wrong_header")
    assert result["pass"] is False
    assert result["conf"] == 0.9
    assert result["seen_header"] == "SYS_FAN1"
    assert "SYS_FAN1" in result["reason"]


def test_blurry(client):
    locate = post(client, "locate", "blurry", header="F_PANEL", want_pins=[5, 6])
    assert locate["found"] is False and locate["blur"] is True
    assert "bbox" not in locate and "pins" not in locate
    verify = post(client, "verify", "blurry")
    assert verify["abstain"] is True and verify["pass"] is False
    assert verify["conf"] == 0.3


def test_unknown_item(client):
    result = post(client, "identify", "unknown_item")
    assert result["item"] == "unknown" and result["conf"] == 0.2


def test_environment_and_request_override(client, monkeypatch):
    monkeypatch.setenv("FAKE_SCENARIO", "wrong_header")
    assert post(client, "verify")["pass"] is False
    assert post(client, "verify", "ok")["pass"] is True
    assert post(client, "verify")["pass"] is False


@pytest.mark.parametrize("endpoint", REQUESTS)
@pytest.mark.parametrize("body", [{}, [], {"image_b64": "synthetic-private-marker"}])
def test_bad_body(client, endpoint, body, caplog):
    response = client.post("/" + endpoint, json=body)
    assert response.status_code == 422
    assert len(response.json()["detail"]) < 100
    assert "synthetic-private-marker" not in response.text + caplog.text


@pytest.mark.parametrize("endpoint", REQUESTS)
def test_schema_rejects_extra_fields(client, endpoint):
    body = {**REQUESTS[endpoint], "extra": "synthetic-private-marker"}
    response = client.post("/" + endpoint, json=body)
    assert response.status_code == 422
    assert "synthetic-private-marker" not in response.text


def test_malformed_json(client):
    response = client.post("/identify", content='{"image_b64":"synthetic-private-marker",',
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert "synthetic-private-marker" not in response.text


def test_invalid_scenario(client):
    response = client.post("/identify", json=REQUESTS["identify"],
                           headers={"X-Fake-Scenario": "typo"})
    assert response.status_code == 422
