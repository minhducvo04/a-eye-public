import json

import pytest
from jsonschema import ValidationError

from contracts import ALLOWED_PARTS, HERE, schema, validate

EXAMPLES = json.loads((HERE / "examples.json").read_text())
MESSAGE_DEFS = [n for n, d in schema()["$defs"].items() if isinstance(d, dict) and d.get("type") == "object"]


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_examples_validate(name):
    for ex in EXAMPLES[name]:
        validate(name, ex)


def test_every_object_contract_has_an_example():
    missing = sorted(set(MESSAGE_DEFS) - set(EXAMPLES) - {"part_specs", "box", "scene_object", "draw_anchor"})
    assert missing == []


def test_client_and_server_unions():
    for n in ["hello", "frame", "photo", "confirm", "market", "user_text"]:
        validate("client_message", EXAMPLES[n][0])
    for n in ["overlay", "say", "step", "verdict", "error", "market_results"]:
        validate("server_message", EXAMPLES[n][0])


def test_rejects_unknown_part_and_bad_coords():
    with pytest.raises(ValidationError):
        validate("identify_request", {"image_b64": "x", "candidates": ["psu_mains_cable"]})
    bad = dict(EXAMPLES["overlay"][0], boxes=[dict(EXAMPLES["overlay"][0]["boxes"][0], bbox=[1.2, 0, 0, 0])])
    with pytest.raises(ValidationError):
        validate("overlay", bad)


def test_rejects_wrong_version_and_extra_fields():
    with pytest.raises(ValidationError):
        validate("hello", dict(EXAMPLES["hello"][0], v=2))
    with pytest.raises(ValidationError):
        validate("frame", dict(EXAMPLES["frame"][0], raw_video="nope"))


def test_allowed_parts():
    assert "cpu_fan_cable" in ALLOWED_PARTS and len(ALLOWED_PARTS) == 8


def test_board_tables_validate():
    from pathlib import Path
    boards = sorted((HERE.parent / "data" / "boards").glob("*.json"))
    assert boards
    for p in boards:
        validate("header_table", json.loads(p.read_text()))


def test_v2_unions_and_v1_isolated():
    for n in ["v2_hello", "v2_frame", "v2_user_text", "v2_barge_in", "v2_confirm"]:
        validate("client_message_v2", EXAMPLES[n][0])
    for n in ["v2_draw", "v2_say", "v2_audio", "v2_agent_state", "v2_market_results", "v2_error"]:
        validate("server_message_v2", EXAMPLES[n][0])
    with pytest.raises(ValidationError):
        validate("client_message", EXAMPLES["v2_hello"][0])
    with pytest.raises(ValidationError):
        validate("client_message_v2", EXAMPLES["hello"][0])


def test_v2_draw_rejects_bad_ops():
    bad = dict(EXAMPLES["v2_draw"][0], ops=[{"kind": "circle", "id": "c", "center": [0.5, 0.5]}])
    with pytest.raises(ValidationError):
        validate("v2_draw", bad)
    bad = dict(EXAMPLES["v2_draw"][0], ops=[{"kind": "laser", "id": "x"}])
    with pytest.raises(ValidationError):
        validate("v2_draw", bad)
