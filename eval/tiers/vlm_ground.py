"""Tiny-tier VLM check that does not need A-eye's JSON: a one-sentence caption and a box for the gold target name.

Tiny models rarely follow the full turn format (eval/backends/run.py measures that separately). Here each frame
gets two short prompts. Caption: does it name the target (a gold target keyword)? Box: IoU >= 0.3 with the gold
box, reading the first four numbers as 0-1, 0-1000 or pixels by their range. Ollama only; "cpu4" = 4 threads,
no GPU (about a Raspberry Pi 5).

  python -m eval.tiers.vlm_ground --model moondream:1.8b --device cpu4 --out eval/tiers/out/ground.jsonl
"""
import argparse
import json
import re
import time

import httpx

from eval.backends.run import CPU4, OLLAMA, image_b64, iou, kw_hit, load_cases


def ask(model, prompt, b64, options):
    t0 = time.perf_counter()
    r = httpx.post(OLLAMA + '/api/chat', timeout=900, json={
        'model': model, 'stream': False, 'keep_alive': '30m', 'think': False,  # qwen3-vl thinks by default
        'options': {'temperature': 0, 'num_ctx': 4096, 'num_predict': 80, **options},
        'messages': [{'role': 'user', 'content': prompt, 'images': [b64]}]})
    r.raise_for_status()
    data = r.json()
    return data['message']['content'], time.perf_counter() - t0, data


def read_box(text, scale_w, scale_h):
    nums = [float(x) for x in re.findall(r'-?\d+(?:\.\d+)?', text)][:4]
    if len(nums) < 4:
        return None
    x1, y1, x2, y2 = nums[0] / scale_w, nums[1] / scale_h, nums[2] / scale_w, nums[3] / scale_h
    if x2 <= x1 or y2 <= y1:
        return None
    return [max(0, x1), max(0, y1), min(1, x2), min(1, y2)]


def scales(text, model, w, h):
    """Native scale first (0-1 for small numbers, pixels for qwen2.5vl, else 0-1000), then all three."""
    nums = [float(x) for x in re.findall(r'-?\d+(?:\.\d+)?', text)][:4]
    native = (1, 1) if nums and max(nums) <= 1.5 else (w, h) if model.startswith('qwen2.5vl') else (1000, 1000)
    return native, [(1, 1), (1000, 1000), (w, h)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', required=True)
    ap.add_argument('--device', choices=['gpu', 'cpu4'], default='gpu')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    options = CPU4 if a.device == 'cpu4' else {}
    out = open(a.out, 'a')
    ask(a.model, 'Reply OK.', image_b64(load_cases()[0])[0], options)  # warm-up, untimed
    for case in load_cases():
        b64, (w, h) = image_b64(case)
        row = dict(model=a.model, device=a.device, case=case['id'])
        try:
            caption, row['caption_s'], _ = ask(a.model, 'Describe this image in one sentence.', b64, options)
            row['caption'] = caption.strip()[:300]
            row['target_named'] = kw_hit(caption, case['target_keywords'])
            text, row['ground_s'], data = ask(
                a.model, f'Give the bounding box of the {case["target"]} in this image as [x1, y1, x2, y2].',
                b64, options)
            row['ground_text'] = text.strip()[:200]
            native, every = scales(text, a.model, w, h)
            box = read_box(text, *native)
            row['iou'] = round(iou(box, case['box']), 3) if box else 0.0
            row['iou_any_scale'] = round(max([iou(b, case['box']) for b in (read_box(text, *s) for s in every) if b]
                                             or [0.0]), 3)
        except Exception as exc:
            row['error'] = type(exc).__name__ + ': ' + str(exc)[:200]
        try:
            ps = httpx.get(OLLAMA + '/api/ps', timeout=10).json()['models']
            row['mem_mb'] = next((m['size'] >> 20 for m in ps if m['name'] == a.model), None)
        except Exception:
            pass
        out.write(json.dumps(row) + '\n')
        out.flush()
        print(a.model, a.device, case['id'], row.get('iou'), row.get('target_named'), round(row.get('ground_s', 0), 2),
              flush=True)


if __name__ == '__main__':
    main()
