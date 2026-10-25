"""Run from the repo root: .venv/bin/python -m vision.probe_ollama."""
import argparse
import base64
from io import BytesIO
import json
import sys
from statistics import median
from time import perf_counter

from PIL import Image, ImageDraw, ImageFont

from contracts import validate
from vision.ollama import OllamaBackend


def synthetic_board():
    image = Image.new("RGB", (1280, 960), (15, 65, 40))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=40)
    target = None
    for label, position in [("CPU_FAN", (160, 140)), ("SYS_FAN1", (850, 360)),
                            ("F_PANEL", (600, 780))]:
        bounds = draw.textbbox(position, label, font=font)
        draw.text(position, label, font=font, fill="white")
        if label == "CPU_FAN":
            x1, y1, x2, y2 = bounds
            target = [x1 / 1280, y1 / 960, (x2 - x1) / 1280, (y2 - y1) / 960]
    stream = BytesIO()
    image.save(stream, format="JPEG")
    return base64.b64encode(stream.getvalue()).decode("ascii"), target


def iou(box, target):
    x, y, w, h = box
    tx, ty, tw, th = target
    overlap = max(0, min(x + w, tx + tw) - max(x, tx)) * max(0, min(y + h, ty + th) - max(y, ty))
    union = w * h + tw * th - overlap
    return overlap / union if union else 0


def synthetic_scene():
    image = Image.new("RGB", (1280, 960), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=36)
    draw.rectangle((100, 100, 300, 300), fill="red")
    draw.ellipse((700, 100, 950, 350), fill="blue")
    draw.polygon([(450, 550), (300, 800), (600, 800)], fill="green")
    for label, position in [("red square", (100, 320)), ("blue circle", (700, 370)),
                            ("green triangle", (300, 820))]:
        draw.text(position, label, font=font, fill="black")
    stream = BytesIO()
    image.save(stream, format="JPEG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def main(argv=()):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", action="store_true", help="Ground labelled synthetic shapes")
    parser.add_argument("--bench", type=int, metavar="N", help="Time N single-label scene calls")
    parser.add_argument("--model", help="Override the ground model for this probe")
    args = parser.parse_args(argv)
    if args.bench is not None and args.bench < 1:
        parser.error("--bench must be positive")
    image, target = (synthetic_scene(), None) if args.scene or args.bench else synthetic_board()
    backend = OllamaBackend(model=args.model) if args.model else OllamaBackend()
    try:
        if args.bench:
            target = [100 / 1280, 100 / 960, 200 / 1280, 200 / 960]
            scores = []
            samples = {key: [] for key in
                       ("wall", "load_duration", "prompt_eval_duration", "eval_duration")}
            # Include the first (possibly cold) request, and expose it separately.
            for index in range(args.bench):
                start = perf_counter()
                result = backend.scene({"image_b64": image, "queries": ["red square"]}, "ok")
                result["ms"] = (perf_counter() - start) * 1000
                validate("scene_response", result)
                values = {"wall": result["ms"], **{key: value / 1_000_000
                          for key, value in backend.last_timings.items()}}
                score = iou(result["objects"][0]["bbox"], target) if result["objects"] else 0
                scores.append(score)
                print(f"run={index + 1} model={backend.last_ground_model} IoU={score:.4f} " + " ".join(
                    f"{key}_ms={values[key]:.1f}" if key in values else f"{key}_ms=n/a"
                    for key in samples), flush=True)
                for key, value in values.items():
                    samples[key].append(value)
            print(f"IoU median={median(scores):.4f} min={min(scores):.4f} max={max(scores):.4f}")
            for key, values in samples.items():
                if values:
                    print(f"{key} median_ms={median(values):.1f} min_ms={min(values):.1f} "
                          f"max_ms={max(values):.1f} n={len(values)}")
                else:
                    print(f"{key}: unavailable")
            return
        health = backend.health()
        validate("health_response", health)
        print("health:", json.dumps(health), flush=True)
        start = perf_counter()
        if args.scene:
            result = backend.scene({"image_b64": image, "queries": ["red square"]}, "ok")
            result["ms"] = (perf_counter() - start) * 1000
            validate("scene_response", result)
            print(json.dumps(result))
            return
        result = backend.locate({"image_b64": image, "board_id": "demo-b650", "header": "CPU_FAN"}, "ok")
        result["ms"] = (perf_counter() - start) * 1000
        validate("locate_response", result)
        score = iou(result["bbox"], target) if result["found"] else 0
        print(f'CPU_FAN found={result["found"]} IoU={score:.4f} ms={result["ms"]:.1f}')
    finally:
        backend.client.close()


if __name__ == "__main__":
    main(sys.argv[1:])
