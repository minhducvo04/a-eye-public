"""Replay a local manifest through the vision service and score held-out cases.

Images are read from the manifest directory and sent as base64. They are not copied,
logged, or written anywhere else.
"""
import argparse
import base64
import json
import sys
from pathlib import Path

from jsonschema import ValidationError

from contracts import ALLOWED_PARTS, validate
from eval.table import write_report

IOU_MIN = 0.3


class ContractError(ValueError):
    """A vision request or response missed the contract. The message never includes image bytes."""


def iou(a, b) -> float:
    """Intersection over union for [x, y, w, h] boxes. A zero area returns 0."""
    ax, ay, aw, ah = (float(v) for v in a)
    bx, by, bw, bh = (float(v) for v in b)
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union


def check(name: str, obj) -> None:
    try:
        validate(name, obj)
    except ValidationError as exc:
        raise ContractError(f"{name} failed validation: {exc.message}") from None


def is_abstain(response: dict) -> bool:
    """abstain: true, or a locate miss blamed on blur. Those are never correct."""
    if response.get("abstain") is True:
        return True
    return response.get("found") is False and response.get("blur") is True


def truth_bbox(truth: dict) -> list | None:
    """A locate truth with no bbox means the header is not in the frame."""
    if "bbox" not in truth or truth["bbox"] is None:
        return None
    bbox = truth["bbox"]
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ContractError("locate truth bbox must be [x, y, w, h]")
    return [float(v) for v in bbox]


def build_request(case: dict, image_b64: str) -> dict:
    """Vision POST body for this case. The body is validated before it is returned."""
    task = case["task"]
    pins = case.get("pins") or []
    if task == "identify":
        body = {"image_b64": image_b64, "candidates": list(case.get("candidates") or ALLOWED_PARTS)}
    elif task == "locate":
        body = {"image_b64": image_b64, "board_id": case["board_id"], "header": case["header"]}
        if pins:
            body["want_pins"] = list(pins)
    elif task == "verify":
        expect = {"item": case["item"]}
        if pins:
            expect["pins"] = list(pins)
        body = {
            "image_b64": image_b64,
            "board_id": case["board_id"],
            "header": case["header"],
            "expect": expect,
        }
    else:
        raise ContractError(f"unknown task {task!r} on {case.get('case_id')}")
    check(f"{task}_request", body)
    return body


def score_case(case: dict, response: dict) -> dict:
    """One result row. Abstentions are flagged and are not marked correct."""
    task = case["task"]
    check(f"{task}_response", response)
    abstain = is_abstain(response)
    header = case.get("header") or ""
    truth = case["truth"]

    if task == "identify":
        expected = truth["item"]
        got = response["item"]
        correct = (not abstain) and got == expected
    elif task == "locate":
        expected_bbox = truth_bbox(truth)
        found = response.get("found") is True
        got_bbox = response.get("bbox") if found else None
        if expected_bbox is None:
            expected = "not_found"
            correct = (not abstain) and not found
        elif found and got_bbox is not None and iou(expected_bbox, got_bbox) >= IOU_MIN:
            expected = expected_bbox
            correct = not abstain
        else:
            expected = expected_bbox
            correct = False
        got = got_bbox if found else "not_found"
    elif task == "verify":
        expected = bool(truth["pass"])
        got = bool(response["pass"])
        correct = (not abstain) and got == expected
    else:
        raise ContractError(f"unknown task {task!r} on {case.get('case_id')}")

    return {
        "case_id": case["case_id"],
        "task": task,
        "header": header,
        "expected": expected,
        "got": got,
        "correct": correct,
        "conf": response["conf"],
        "ms": response["ms"],
        "abstain": abstain,
    }


def read_image_b64(manifest_path: Path, rel: str) -> str:
    """Read one manifest image. The bytes are not copied to another file."""
    data = (manifest_path.parent / rel).read_bytes()
    if not data:
        raise ContractError(f"image is empty: {rel}")
    return base64.b64encode(data).decode("ascii")


def select_cases(cases: list, split: str) -> list:
    if split == "all":
        return list(cases)
    return [case for case in cases if case.get("split") == split]


def post_vision(client, url: str, body: dict) -> dict:
    """POST via an httpx client, or a callable `(url, body) -> dict` used by tests."""
    post = getattr(client, "post", None)
    if post is None:
        data = client(url, body)
    else:
        response = post(url, json=body)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise ContractError("vision response was not an object")
    return data


def run_manifest(manifest, vision_url: str, *, out_dir=None, split: str = "heldout", client=None) -> list:
    """Score the chosen split. `client` is an httpx.Client or a `(url, body) -> dict` function."""
    if client is None:
        raise ContractError("a vision client is required")
    manifest_path = Path(manifest)
    cases = json.loads(manifest_path.read_text())
    rows = []
    base = vision_url.rstrip("/")
    for case in select_cases(cases, split):
        image_b64 = read_image_b64(manifest_path, case["image"])
        body = build_request(case, image_b64)
        payload = post_vision(client, f"{base}/{case['task']}", body)
        rows.append(score_case(case, payload))
    if out_dir is not None:
        write_report(rows, Path(out_dir))
    return rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m eval.run")
    parser.add_argument("--manifest", required=True, help="path to a JSON list of cases")
    parser.add_argument("--vision-url", required=True, help="vision service base URL")
    parser.add_argument("--out", default="eval/out", help="directory for the csv and table.md")
    parser.add_argument("--split", default="heldout", choices=["heldout", "train", "all"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, client=None) -> int:
    args = parse_args(argv)
    own_client = client is None
    if own_client:
        import httpx

        client = httpx.Client(timeout=60.0)
    try:
        run_manifest(
            args.manifest,
            args.vision_url,
            out_dir=args.out,
            split=args.split,
            client=client,
        )
    finally:
        if own_client:
            client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
