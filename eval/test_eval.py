"""Accuracy harness checks. Images are synthetic bytes in a temp dir. No network."""
import base64
import json
from pathlib import Path

import httpx
import pytest

from contracts import ALLOWED_PARTS, validate
from eval.run import ContractError, build_request, iou, main, run_manifest, score_case
from eval.table import CSV_HEADER, summarize, to_markdown, write_report

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = json.loads((ROOT / "contracts" / "examples.json").read_text())
SAMPLE = json.loads((ROOT / "eval" / "sample" / "manifest.json").read_text())


def test_iou_overlap_and_threshold():
    assert iou([0, 0, 1, 1], [0, 0, 1, 1]) == 1
    assert iou([0, 0, 0.2, 0.2], [0.5, 0.5, 0.2, 0.2]) == 0
    # overlap is a 0.2 square; each box has area 0.16; union is 0.28
    assert iou([0, 0, 0.4, 0.4], [0.2, 0.2, 0.4, 0.4]) == pytest.approx(0.04 / 0.28)
    assert iou([0, 0, 1, 1], [0, 0, 0.3, 1]) == pytest.approx(0.3)
    assert iou([0, 0, 0, 0], [0, 0, 0.2, 0.2]) == 0


def _case(task, truth, **extra):
    case = {
        "case_id": extra.pop("case_id", f"c-{task}"),
        "split": "heldout",
        "board_id": "demo-b650",
        "image": f"{task}.jpg",
        "task": task,
        "header": extra.pop("header", "CPU_FAN"),
        "item": extra.pop("item", "cpu_fan_cable"),
        "pins": extra.pop("pins", []),
        "truth": truth,
    }
    case.update(extra)
    return case


def _locate(found, bbox=None, blur=False, ms=100, conf=0.8):
    body = {"found": found, "conf": conf, "blur": blur, "ms": ms}
    if bbox is not None:
        body["bbox"] = bbox
    return body


def test_score_identify_and_locate():
    hit = score_case(_case("identify", {"item": "cpu_fan_cable"}), EXAMPLES["identify_response"][0])
    assert hit["correct"] is True and hit["abstain"] is False
    assert hit["expected"] == "cpu_fan_cable" and hit["got"] == "cpu_fan_cable"

    miss = score_case(
        _case("identify", {"item": "cpu_fan_cable"}),
        {"item": "ram_stick", "conf": 0.4, "ms": 10},
    )
    assert miss["correct"] is False and miss["got"] == "ram_stick"

    on_threshold = score_case(
        _case("locate", {"bbox": [0, 0, 1, 1]}, header="F_PANEL"),
        _locate(True, [0, 0, 0.3, 1], ms=1200),
    )
    assert on_threshold["correct"] is True

    under = score_case(
        _case("locate", {"bbox": [0, 0, 1, 1]}, header="F_PANEL"),
        _locate(True, [0, 0, 0.29, 1]),
    )
    assert under["correct"] is False

    both_missing = score_case(
        _case("locate", {"found": False}, header="F_PANEL"),
        _locate(False, blur=False, conf=0.4),
    )
    assert both_missing["correct"] is True
    assert both_missing["expected"] == "not_found" and both_missing["got"] == "not_found"

    example = score_case(
        _case("locate", {"bbox": [0.5, 0.5, 0.1, 0.04]}, header="F_PANEL"),
        EXAMPLES["locate_response"][0],
    )
    assert example["correct"] is True and example["ms"] == 1200


def test_abstain_is_not_correct():
    blur = score_case(
        _case("locate", {"found": False}, header="F_PANEL"),
        EXAMPLES["locate_response"][1],
    )
    assert blur["abstain"] is True and blur["correct"] is False

    # A blur miss is still an abstention when the truth has a box.
    blur_box = score_case(
        _case("locate", {"bbox": [0.5, 0.5, 0.1, 0.04]}, header="F_PANEL"),
        EXAMPLES["locate_response"][1],
    )
    assert blur_box["abstain"] is True and blur_box["correct"] is False

    verdict = {
        "pass": False,
        "abstain": True,
        "conf": 0.3,
        "reason": "Too blurry. Hold still and move closer.",
        "ms": 300,
    }
    verify = score_case(_case("verify", {"pass": False}, item="f_panel_bundle"), verdict)
    assert verify["abstain"] is True and verify["correct"] is False

    rows = [blur, score_case(
        _case("locate", {"bbox": [0, 0, 1, 1]}, header="F_PANEL", case_id="loc-ok"),
        _locate(True, [0, 0, 1, 1], ms=50),
    )]
    summary = summarize(rows)
    group = summary["groups"][0]
    assert group["n"] == 2
    assert group["abstain_rate"] == 0.5
    assert group["accuracy"] == 1


def test_verify_wrong_plugs_caught():
    caught = score_case(
        _case("verify", {"pass": False}, header="F_PANEL", item="f_panel_bundle", case_id="caught"),
        EXAMPLES["verify_response"][0],
    )
    assert caught["correct"] is True and caught["got"] is False

    missed = score_case(
        _case("verify", {"pass": False}, header="SYS_FAN1", item="sys_fan_cable", case_id="missed"),
        {"pass": True, "conf": 0.9, "reason": "looks seated", "ms": 50},
    )
    abstained = score_case(
        _case("verify", {"pass": False}, header="CPU_FAN", item="cpu_fan_cable", case_id="skipped"),
        {"pass": False, "abstain": True, "conf": 0.2, "reason": "blur", "ms": 40},
    )
    summary = summarize([caught, missed, abstained])
    assert summary["wrong_plugs_caught"] == 1
    assert missed["correct"] is False and abstained["correct"] is False


def test_table_has_one_row_per_task_header_and_exact_csv(tmp_path):
    rows = [
        score_case(_case("identify", {"item": "cpu_fan_cable"}, case_id="i1"),
                   {"item": "cpu_fan_cable", "conf": 0.9, "ms": 100}),
        score_case(_case("identify", {"item": "cpu_fan_cable"}, case_id="i2"),
                   {"item": "ram_stick", "conf": 0.2, "ms": 300}),
        score_case(_case("locate", {"bbox": [0, 0, 1, 1]}, header="CPU_FAN", case_id="l1"),
                   _locate(True, [0, 0, 1, 1], ms=1200)),
        score_case(_case("locate", {"bbox": [0.1, 0.1, 0.2, 0.2]}, header="F_PANEL", case_id="l2"),
                   EXAMPLES["locate_response"][1]),
        score_case(_case("verify", {"pass": False}, header="F_PANEL", item="f_panel_bundle", case_id="v1"),
                   EXAMPLES["verify_response"][0]),
    ]
    summary = summarize(rows)
    pairs = [(row["task"], row["header"]) for row in summary["groups"]]
    assert pairs == sorted(set(pairs))
    assert len(pairs) == len({(row["task"], row["header"]) for row in rows})

    md = to_markdown(summary)
    body = []
    for line in md.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells[0] in {"task", ""} or set(cells[0]) <= {"-", ":"}:
            continue
        body.append(cells)
    assert len(body) == len(pairs)
    assert [cells[0] for cells in body] == [task for task, _header in pairs]
    assert "Wrong plugs caught: 1" in md

    csv_path, md_path = write_report(rows, tmp_path)
    text = csv_path.read_text()
    assert text.splitlines()[0] == CSV_HEADER
    assert md_path.read_text() == md
    assert not list(tmp_path.glob("*.jpg"))


def _sample_manifest(tmp_path: Path) -> tuple[Path, dict]:
    images = {}
    for case in SAMPLE:
        blob = f"synthetic-{case['case_id']}".encode()
        (tmp_path / case["image"]).write_bytes(blob)
        images[case["image"]] = blob
    cases = list(SAMPLE)
    cases.append({
        "case_id": "train-only",
        "split": "train",
        "board_id": "demo-b650",
        "image": "not-written.jpg",
        "task": "identify",
        "header": "CPU_FAN",
        "item": "cpu_fan_cable",
        "pins": [],
        "truth": {"item": "cpu_fan_cable"},
    })
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(cases))
    return path, images


def test_heldout_filter_and_requests_match_contracts(tmp_path):
    manifest, images = _sample_manifest(tmp_path)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        task = request.url.path.rsplit("/", 1)[-1]
        validate(f"{task}_request", body)
        assert set(body) <= {"image_b64", "candidates", "board_id", "header", "want_pins", "expect"}
        seen.append((task, body))
        if task == "identify":
            assert set(body["candidates"]) == set(ALLOWED_PARTS)
            payload = EXAMPLES["identify_response"][0]
        elif task == "locate":
            assert body["want_pins"] == [5, 6]
            payload = EXAMPLES["locate_response"][0]
        else:
            assert body["expect"] == {"item": "f_panel_bundle", "pins": [5, 6]}
            payload = EXAMPLES["verify_response"][0]
        validate(f"{task}_response", payload)
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = tmp_path / "out"
    rows = run_manifest(manifest, "http://vision.test", out_dir=out, client=client)
    assert [row["case_id"] for row in rows] == ["id-cpu-fan", "loc-fpanel", "ver-wrong-plug"]
    assert all(row["correct"] for row in rows)
    assert [task for task, _body in seen] == ["identify", "locate", "verify"]
    for task, body in seen:
        image_name = {"identify": "identify.jpg", "locate": "locate.jpg", "verify": "verify.jpg"}[task]
        assert body["image_b64"] == base64.b64encode(images[image_name]).decode("ascii")
    assert not (tmp_path / "not-written.jpg").exists()
    assert summarize(rows)["wrong_plugs_caught"] == 1

    csv_path = next(out.glob("*.csv"))
    assert csv_path.read_text().splitlines()[0] == CSV_HEADER
    assert (out / "table.md").is_file()
    names = {path.name for path in out.iterdir()}
    assert names == {csv_path.name, "table.md"}

    again = tmp_path / "cli-out"
    assert main(
        ["--manifest", str(manifest), "--vision-url", "http://vision.test", "--out", str(again)],
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    ) == 0
    assert (again / "table.md").is_file()


def test_split_all_includes_train_and_bad_response_fails(tmp_path):
    manifest, _images = _sample_manifest(tmp_path)
    (tmp_path / "not-written.jpg").write_bytes(b"train-bytes")
    calls = []

    def send(url, body):
        calls.append(url)
        task = url.rsplit("/", 1)[-1]
        if task == "identify":
            return {"item": "cpu_fan_cable", "conf": 0.5, "ms": 1}
        if task == "locate":
            return EXAMPLES["locate_response"][0]
        return EXAMPLES["verify_response"][0]

    rows = run_manifest(manifest, "http://vision.test/", split="all", client=send)
    assert [row["case_id"] for row in rows][-1] == "train-only"
    assert len(calls) == 4
    assert calls[0] == "http://vision.test/identify"

    with pytest.raises(ContractError):
        score_case(_case("locate", {"bbox": [0, 0, 1, 1]}), {"found": True})

    request = build_request(SAMPLE[1], "abc")
    validate("locate_request", request)
