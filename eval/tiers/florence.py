"""Florence-2 (base 0.23B, large 0.77B; MIT) on the 16 held-out frames: caption, and a box for the gold target name.

Florence-2 does not chat, so it cannot pick the target from the question; this measures grounding *given the
name* (a tiny-tier pipeline would get the name from a text model or the router). One process per model and
device so peak RSS belongs to one model. CPU rows use 4 threads to approximate a Raspberry Pi 5.

  python eval/tiers/florence.py --model microsoft/Florence-2-base --device cpu --cases eval/backends/cases.json \
      --data eval/backends/data --out florence.jsonl
"""
import argparse
import json
import resource
import time
from pathlib import Path


def iou(a, b):
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    ix = max(0, min(ax2, bx2) - max(a[0], b[0]))
    iy = max(0, min(ay2, by2) - max(a[1], b[1]))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import torch
    from PIL import Image
    from transformers import AutoModelForCausalLM, AutoProcessor
    torch.set_num_threads(4)
    t0 = time.perf_counter()
    dtype = torch.float16 if a.device == "cuda" else torch.float32
    model = AutoModelForCausalLM.from_pretrained(a.model, trust_remote_code=True, torch_dtype=dtype).to(a.device)
    processor = AutoProcessor.from_pretrained(a.model, trust_remote_code=True)
    load = time.perf_counter() - t0

    def ask(task, image, extra=""):
        inputs = processor(text=task + extra, images=image, return_tensors="pt").to(a.device, dtype)
        with torch.inference_mode():
            ids = model.generate(input_ids=inputs["input_ids"], pixel_values=inputs["pixel_values"],
                                 max_new_tokens=128, num_beams=1, do_sample=False)
        text = processor.batch_decode(ids, skip_special_tokens=False)[0]
        return processor.post_process_generation(text, task=task, image_size=image.size)[task]

    out = open(a.out, "a")
    for case in json.load(open(a.cases)):
        image = Image.open(Path(a.data) / case["image"]).convert("RGB")
        w, h = image.size
        row = dict(model=a.model, device=a.device, case=case["id"], load_s=round(load, 2))
        try:
            t0 = time.perf_counter()
            row["caption"] = ask("<CAPTION>", image)
            row["caption_s"] = round(time.perf_counter() - t0, 3)
            t0 = time.perf_counter()
            hit = ask("<CAPTION_TO_PHRASE_GROUNDING>", image, case["target"])
            row["ground_s"] = round(time.perf_counter() - t0, 3)
            boxes = hit.get("bboxes", [])
            if boxes:
                x1, y1, x2, y2 = boxes[0]
                box = [x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h]
                gold = case["box"]  # [x1, y1, x2, y2] normalized
                row["iou"] = round(iou(box, [gold[0], gold[1], gold[2] - gold[0], gold[3] - gold[1]]), 3)
            else:
                row["iou"] = 0.0
        except Exception as exc:
            row["error"] = type(exc).__name__ + ": " + str(exc)[:200]
        row["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
        out.write(json.dumps(row) + "\n")
        out.flush()
        print(json.dumps(row)[:200], flush=True)


if __name__ == "__main__":
    main()
