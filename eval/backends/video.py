"""Live-video strategies on a synthetic 60 s clip built from three public frames (no video is saved).

The clip: 20 s of the CPU socket, 20 s of the onion board, 20 s of the drill, 5 fps, each frame a small random
shift and brightness change of its still (a hand-held camera stand-in). Every strategy uses the same local VLM
(qwen2.5vl:7b on the GPU box) to caption frames and qwen3.5:9b (think off) as the text brain that answers
"what are we working on?" from the rolling scene memory at t = 20, 40, 60 s.

  .venv/bin/python -m eval.backends.video          # writes eval/backends/out/video.json
"""
import base64
import json
import random
import statistics
import time
from io import BytesIO

import httpx
from PIL import Image, ImageEnhance

from eval.backends.run import DATA, OLLAMA, OUT

FPS = 5
SEGMENTS = [("pc_socket.jpg", ["cpu", "socket", "motherboard", "processor"]),
            ("cook_onion.jpg", ["onion", "vegetable", "tomato"]),
            ("asm_drill.jpg", ["drill"])]
SEG_S = 20
VLM = "qwen2.5vl:7b-q4_K_M"
BRAIN = "qwen3.5:9b-q4_K_M"
CAPTION_PROMPT = "In one sentence, what is in this camera frame? Name the main objects."


def frames():
    rnd = random.Random(7)
    for si, (name, _) in enumerate(SEGMENTS):
        base = Image.open(DATA / name).convert("RGB")
        base = base.resize((960, round(960 * base.height / base.width)))
        for k in range(SEG_S * FPS):
            dx, dy = rnd.randint(-10, 10), rnd.randint(-8, 8)
            img = base.transform(base.size, Image.Transform.AFFINE, (1, 0, dx, 0, 1, dy))
            img = ImageEnhance.Brightness(img).enhance(rnd.uniform(0.92, 1.08))
            yield si * SEG_S + k / FPS, si, img


def jpeg_b64(img, long_edge=1280):
    scale = long_edge / max(img.size)
    img = img.resize((round(img.width * scale), round(img.height * scale)))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def small_gray(img):
    return list(img.convert("L").resize((32, 32)).get_flattened_data())


def diff(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def chat(client, model, content, images=None, num_predict=60):
    msg = {"role": "user", "content": f"[{time.time_ns()}] " + content}
    if images:
        msg["images"] = images
    t0 = time.perf_counter()
    r = client.post(OLLAMA + "/api/chat", timeout=120, json={
        "model": model, "stream": False, "think": False, "keep_alive": "30m",
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": num_predict}, "messages": [msg]})
    r.raise_for_status()
    d = r.json()
    return d["message"]["content"].strip(), time.perf_counter() - t0, d.get("prompt_eval_count", 0), d.get("eval_count", 0)


def ask_brain(client, memory, question):
    mem = "\n".join(f"t={t:.0f}s: {c}" for t, c in memory[-8:])
    text, s, pin, pout = chat(client, BRAIN, "Scene memory from the camera (oldest first):\n" + mem +
                              "\n\nQuestion: " + question + " Answer in under 12 words.", num_predict=40)
    return text, s, pin, pout


def run_strategy(name, should_caption):
    memory, calls, lat, tok_in, tok_out, probes = [], 0, [], 0, 0, []
    last_key, last_cap_t = None, -1e9
    brain_tok = 0
    change_seen = {}
    with httpx.Client(timeout=120) as client:
        for t, seg, img in frames():
            g = small_gray(img)
            if should_caption(t, g, last_key, last_cap_t):
                cap, s, pin, pout = chat(client, VLM, CAPTION_PROMPT, [jpeg_b64(img)])
                memory.append((t, cap))
                calls += 1
                lat.append(s)
                tok_in += pin
                tok_out += pout
                last_key, last_cap_t = g, t
                change_seen.setdefault(seg, t - seg * SEG_S)
            if abs(t - (seg + 1) * SEG_S + 1 / FPS) < 1e-6:  # last frame of a segment
                ans, s, pin, pout = ask_brain(client, memory, "What is the user working on right now?")
                brain_tok += pin + pout
                kws = SEGMENTS[seg][1]
                probe = {"t": t + 1 / FPS, "q": "now", "answer": ans, "kept": any(k in ans.lower() for k in kws), "s": s}
                probes.append(probe)
                if seg == 2:
                    ans, s, pin, pout = ask_brain(client, memory, "What object did we look at first in this call?")
                    brain_tok += pin + pout
                    probes.append({"t": 60, "q": "first", "answer": ans,
                                   "kept": any(k in ans.lower() for k in SEGMENTS[0][1]), "s": s})
    minutes = SEG_S * len(SEGMENTS) / 60
    return {"strategy": name, "vlm_calls_per_min": calls / minutes,
            "vlm_tokens_per_min": (tok_in + tok_out) / minutes, "vlm_in_tokens_per_call": tok_in / max(calls, 1),
            "brain_tokens_total": brain_tok, "vlm_call_s_p50": statistics.median(lat) if lat else None,
            "vlm_call_s_p90": sorted(lat)[int(0.9 * (len(lat) - 1))] if lat else None,
            "vlm_busy_s_per_min": sum(lat) / minutes,
            "scene_change_lag_s": change_seen, "probes": probes, "memory": memory}


def fixed_rate(period):
    return lambda t, g, last, last_t: t - last_t >= period - 1e-9


def change_triggered(threshold, heartbeat):
    return lambda t, g, last, last_t: last is None or diff(g, last) > threshold or t - last_t >= heartbeat


def main():
    OUT.mkdir(exist_ok=True)
    # Calibrate: diff between jittered frames of one still vs across a scene cut.
    fs = list(frames())
    same = [diff(small_gray(fs[i][2]), small_gray(fs[i + 1][2])) for i in range(0, 95, 5)]
    cut = diff(small_gray(fs[99][2]), small_gray(fs[100][2]))
    results = {"calibration": {"same_scene_diff_p50": statistics.median(same), "same_scene_diff_max": max(same),
                               "scene_cut_diff": cut}}
    thr = (max(same) + cut) / 2
    results["threshold"] = thr
    results["runs"] = [run_strategy("fixed 1 fps", fixed_rate(1.0)),
                       run_strategy(f"change-triggered (diff>{thr:.0f}, heartbeat 10 s)", change_triggered(thr, 10.0))]
    (OUT / "video.json").write_text(json.dumps(results, indent=1))
    for r in results["runs"]:
        print(json.dumps({k: v for k, v in r.items() if k != "memory"}))
    print(json.dumps(results["calibration"]), thr)


if __name__ == "__main__":
    main()
