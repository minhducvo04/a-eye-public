"""Scorer checks (no network): the parts that decide the benchmark's accuracy numbers."""
from eval.backends.run import iou, kw_hit, parse_json, score_row, to_norm


def test_iou_identity_and_disjoint():
    assert iou([0, 0, 1, 1], [0, 0, 1, 1]) == 1
    assert iou([0, 0, .2, .2], [.5, .5, 1, 1]) == 0


def test_box_formats_normalize_the_same_box():
    assert to_norm([640, 480, 1280, 960], "pixels", 1280, 960) == [.5, .5, 1, 1]
    assert to_norm([500, 500, 1000, 1000], "norm1000", 1280, 960) == [.5, .5, 1, 1]
    assert to_norm([10, 10, 5, 5], "pixels", 100, 100) is None
    assert to_norm("bad", "pixels", 100, 100) is None


def test_parse_json_takes_last_answer_object_after_echoed_schema():
    text = 'Schema {"topic": "<x>", "answer": "<y>"} ... final {"topic": "cpu", "answer": "socket"}'
    assert parse_json(text)["topic"] == "cpu"
    assert parse_json("no json here") is None


def test_score_row_keyword_and_iou():
    case = {"topic_keywords": ["cpu"], "target_keywords": ["socket"], "box": [.4, .3, .6, .6]}
    row = {"text": '{"topic": "CPU install", "target": "CPU socket", "bbox": [410, 230, 610, 460], "answer": "there"}',
           "img_wh": [1000, 768]}
    s = score_row(case, row, "pixels")
    assert s["topic_kw"] and s["target_kw"] and s["iou"] > .3
    assert not kw_hit("", ["cpu"])


def test_generic_ollama_backends():
    from eval.backends import run
    assert run.backend("ollama:moondream:1.8b")[1] == "norm1000"
    assert run.backend("ollama-cpu4:qwen2.5vl:3b")[1] == "pixels"
    assert run.CPU4 == {"num_gpu": 0, "num_thread": 4}
