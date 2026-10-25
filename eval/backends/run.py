"""Backend benchmark: which AI backend understands "what we are talking about" in a frame + conversation.

Usage (from the repo root, so `make`-style .env loading applies when run through make):
  .venv/bin/python -m eval.backends.run --backends ollama:qwen2.5vl:7b-q4_K_M,cli:claude --reps 3
  .venv/bin/python -m eval.backends.run --judge          # score saved answers with the judge model
  .venv/bin/python -m eval.backends.run --table          # print the summary table from saved files

Raw outputs go to eval/backends/out/<backend>.jsonl (gitignored). Images are public Commons frames.
No key is printed; the Anthropic backend reads ANTHROPIC_API_KEY from the environment only.
"""
import argparse
import base64
import json
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import time
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = Path(os.environ.get("EVAL_BACKENDS_OUT", HERE / "out"))
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
BOXSVC = os.environ.get("BOX_URL", "http://127.0.0.1:8090").rstrip("/")
CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"
JUDGE_MODEL = "gemma4:31b-it-q4_K_M"

SYSTEM = ("You are A-eye, a live video-call helper that sees the user's camera and points at things. "
          "Read the conversation and the current camera frame, then answer the user's latest question "
          "in one or two short spoken sentences. Follow safety: never tell a user to open a power supply "
          "or touch mains power.")


def fmt_instr(case, w, h, box_format):
    if box_format == "norm1000":
        coords = "bbox is [x1,y1,x2,y2] normalized 0 to 1000 of the image width and height"
    else:
        coords = f"bbox is [x1,y1,x2,y2] in pixels of this {w}x{h} image"
    return ('Reply with only one JSON object: {"topic": "<what we are talking about, a short phrase>", '
            '"target": "<the one object in the frame the question is about>", "bbox": [x1,y1,x2,y2], '
            '"answer": "<your spoken reply>"}. ' + coords + '.')


def user_text(case, w, h, box_format):
    turns = "\n".join(f"{role.upper()}: {text}" for role, text in case["history"])
    return (f"Conversation so far:\n{turns}\nUSER (now, with the camera frame attached): {case['question']}\n\n"
            + fmt_instr(case, w, h, box_format))


def load_cases():
    return json.loads((HERE / "cases.json").read_text())


def image_b64(case):
    raw = (DATA / case["image"]).read_bytes()
    with Image.open(BytesIO(raw)) as im:
        return base64.b64encode(raw).decode("ascii"), im.size


def parse_json(text):
    if not isinstance(text, str):
        return None
    dec = json.JSONDecoder()
    found = None
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj = dec.raw_decode(text[i:])[0]
            except ValueError:
                continue
            if isinstance(obj, dict) and ("answer" in obj or "topic" in obj or "bbox" in obj):
                found = obj  # keep the last matching object (CLIs may echo the schema first)
    return found


# ---------------------------------------------------------------- backends
def run_ollama(model, case, box_format, image=True, caption=None, options=None):
    b64, (w, h) = image_b64(case)
    content = user_text(case, w, h, box_format)
    msg = {"role": "user", "content": content}
    if image:
        msg["images"] = [b64]
    else:
        msg["content"] = ("Camera frame description (from the vision model, no image attached): " + caption +
                          "\n\n" + content.replace("with the camera frame attached", "about the described frame")
                          .replace(fmt_instr(case, w, h, box_format), '') +
                          'Reply with only one JSON object: {"topic": str, "target": str, "answer": str}.')
    body = {"model": model, "stream": True, "think": False, "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 200, **(options or {})},
            "messages": [{"role": "system", "content": SYSTEM}, msg]}
    # Unique first line defeats prompt-prefix caching (same method as docs/pc-benchmarks.md).
    body["messages"][0]["content"] = f"[run {time.time_ns()}]\n" + SYSTEM
    t0 = time.perf_counter()
    ttft = None
    text = []
    final = {}
    with httpx.Client(timeout=600) as client:  # CPU-only tiny models can take minutes on a big frame
        with client.stream("POST", OLLAMA + "/api/chat", json=body) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if "error" in chunk:
                    raise RuntimeError(chunk["error"])
                delta = chunk.get("message", {}).get("content", "")
                if delta and ttft is None:
                    ttft = time.perf_counter() - t0
                text.append(delta)
                if chunk.get("done"):
                    final = chunk
    total = time.perf_counter() - t0
    ns = 1e9
    box = {"box_ttft_s": (final.get("load_duration", 0) + final.get("prompt_eval_duration", 0)) / ns,
           "box_total_s": final.get("total_duration", 0) / ns, "load_s": final.get("load_duration", 0) / ns}
    return {"text": "".join(text), "ttft_s": ttft, "total_s": total, "in_tokens": final.get("prompt_eval_count"),
            "out_tokens": final.get("eval_count"), "cost_usd": 0.0, "img_wh": [w, h], **box}


def run_cli(name, case, box_format):
    b64, (w, h) = image_b64(case)
    work = Path(tempfile.mkdtemp(prefix="aeye-cli-"))
    img = work / "frame.jpg"
    shutil.copy(DATA / case["image"], img)
    prompt = (SYSTEM + "\n\nThe current camera frame is the image file ./frame.jpg (" + f"{w}x{h}" +
              "). Look at it, do not use any other tool.\n\n" + user_text(case, w, h, box_format))
    usage = {}
    t0 = time.perf_counter()
    ttft = None
    if name == "claude":
        cmd = ["claude", "-p", prompt, "--model", "sonnet", "--allowedTools", "Read", "--output-format",
               "stream-json", "--verbose"]
        proc = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        text = ""
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "text" and block.get("text", "").strip():
                        if ttft is None and "{" in block["text"]:
                            ttft = time.perf_counter() - t0  # first assistant text with the answer
            if ev.get("type") == "result":
                text = ev.get("result", "")
                usage = {"cost_usd": ev.get("total_cost_usd"), "usage": ev.get("usage")}
        proc.wait(timeout=180)
    elif name == "codex":
        cmd = [CODEX, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "-i", str(img), "--",
               prompt.replace("the image file ./frame.jpg", "the attached image")]
        p = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=180)
        text = p.stdout + "\n" + p.stderr
        m = re.search(r"tokens used\s*\n?\s*([\d,]+)", text)
        usage = {"tokens_reported": int(m.group(1).replace(",", "")) if m else None}
    elif name == "grok":
        p = subprocess.run(["grok", "-p", prompt], cwd=work, capture_output=True, text=True, timeout=180)
        text = p.stdout
    else:
        raise ValueError(name)
    total = time.perf_counter() - t0
    shutil.rmtree(work, ignore_errors=True)
    return {"text": text, "ttft_s": ttft, "total_s": total, "img_wh": [w, h], **usage}


def run_anthropic(model, case, box_format):
    import anthropic  # reads ANTHROPIC_API_KEY from the environment; never printed
    b64, (w, h) = image_b64(case)
    client = anthropic.Anthropic()
    t0 = time.perf_counter()
    ttft = None
    with client.messages.stream(
            model=model, max_tokens=400, system=SYSTEM, output_config={"effort": "low"},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": user_text(case, w, h, box_format)}]}]) as stream:
        for delta in stream.text_stream:
            if delta and ttft is None:
                ttft = time.perf_counter() - t0
        msg = stream.get_final_message()
    total = time.perf_counter() - t0
    text = "".join(b.text for b in msg.content if b.type == "text")
    price = {"claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.0, 20.0)}
    pin, pout = price.get(model, (2.0, 10.0))
    cost = msg.usage.input_tokens * pin / 1e6 + msg.usage.output_tokens * pout / 1e6
    return {"text": text, "ttft_s": ttft, "total_s": total, "in_tokens": msg.usage.input_tokens,
            "out_tokens": msg.usage.output_tokens, "cost_usd": cost, "refusal": msg.stop_reason == "refusal",
            "img_wh": [w, h]}


COCO = {"knife": "knife", "monitor": "tv", "tumbler": "cup", "pot of water": "bowl"}


def run_yolo(case):
    target = next((v for k, v in COCO.items() if k in case["target"].lower()), None)
    b64, (w, h) = image_b64(case)
    t0 = time.perf_counter()
    r = httpx.post(BOXSVC + "/detect", json={"jpeg_b64": b64, "imgsz": 640, "conf": 0.25}, timeout=30)
    r.raise_for_status()
    total = time.perf_counter() - t0
    objs = r.json().get("objects", [])
    hit = None
    if target:
        cands = [o for o in objs if o["label"] == target]
        if cands:
            hit = max(cands, key=lambda o: o["conf"])
    bbox = None
    if hit:
        x, y, bw, bh = hit["bbox"]
        bbox = [x * w, y * h, (x + bw) * w, (y + bh) * h]
    text = json.dumps({"target": target or "(not a COCO class)", "bbox": bbox, "labels": [o["label"] for o in objs]})
    return {"text": text, "ttft_s": None, "total_s": total, "box_ms": r.json().get("ms"), "img_wh": [w, h],
            "coco_target": target}


BACKENDS = {
    # name: (callable, box format, has image)
    "ollama:qwen2.5vl:7b-q4_K_M": (lambda c: run_ollama("qwen2.5vl:7b-q4_K_M", c, "pixels"), "pixels"),
    "ollama:qwen3.5:9b-q4_K_M": (lambda c: run_ollama("qwen3.5:9b-q4_K_M", c, "norm1000"), "norm1000"),
    "ollama:gemma4:12b-it-q4_K_M": (lambda c: run_ollama("gemma4:12b-it-q4_K_M", c, "norm1000"), "norm1000"),
    "ollama:gemma4:26b-a4b-it-q4_K_M": (lambda c: run_ollama("gemma4:26b-a4b-it-q4_K_M", c, "norm1000"), "norm1000"),
    "cli:claude": (lambda c: run_cli("claude", c, "pixels"), "pixels"),
    "cli:codex": (lambda c: run_cli("codex", c, "pixels"), "pixels"),
    "cli:grok": (lambda c: run_cli("grok", c, "pixels"), "pixels"),
    "api:anthropic:claude-sonnet-5-5": (lambda c: run_anthropic("claude-sonnet-5-5", c, "pixels"), "pixels"),
    "api:anthropic:claude-haiku-5-5": (lambda c: run_anthropic("claude-haiku-5-5", c, "pixels"), "pixels"),
    "boxsvc:yolo11n": (run_yolo, "pixels"),
}


def caption_text_backend(model):
    """Privacy pattern from AGENTS.md: the VLM captions the frame, the text brain gets words only."""
    cap_cache = OUT / "captions.json"
    caps = json.loads(cap_cache.read_text()) if cap_cache.exists() else {}

    def call(case):
        if case["id"] not in caps:
            b64, _ = image_b64(case)
            t0 = time.perf_counter()
            r = httpx.post(OLLAMA + "/api/chat", timeout=120, json={
                "model": "qwen2.5vl:7b-q4_K_M", "stream": False, "keep_alive": "30m",
                "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 120},
                "messages": [{"role": "user", "images": [b64], "content":
                              "List the main objects in this frame and describe it in two sentences."}]})
            caps[case["id"]] = {"caption": r.json()["message"]["content"], "s": time.perf_counter() - t0}
            cap_cache.write_text(json.dumps(caps, indent=1))
        res = run_ollama(model, case, "none", image=False, caption=caps[case["id"]]["caption"])
        res["caption_s"] = caps[case["id"]]["s"]
        return res
    return call, None


BACKENDS["pipe:qwen2.5vl-caption>qwen3.5:9b"] = caption_text_backend("qwen3.5:9b-q4_K_M")


CPU4 = {"num_gpu": 0, "num_thread": 4}  # roughly a Raspberry Pi 5: 4 cores, no usable GPU


def backend(name):
    """Registered backend, or a generic Ollama one: "ollama:<tag>" (GPU) or "ollama-cpu4:<tag>" (CPU, 4 threads)."""
    if name in BACKENDS:
        return BACKENDS[name]
    kind, _, tag = name.partition(":")
    if kind in ("ollama", "ollama-cpu4") and tag:
        fmt = "pixels" if tag.startswith("qwen2.5vl") else "norm1000"
        options = CPU4 if kind == "ollama-cpu4" else None
        return (lambda c: run_ollama(tag, c, fmt, options=options), fmt)
    raise KeyError(name)


def available(name):
    if name.startswith("api:anthropic") and not os.environ.get("ANTHROPIC_API_KEY"):
        return "pending: ANTHROPIC_API_KEY is empty in .env"
    if name == "cli:claude" and not shutil.which("claude"):
        return "claude CLI not installed"
    if name == "cli:grok" and not shutil.which("grok"):
        return "grok CLI not installed"
    if name == "cli:codex" and not Path(CODEX).exists():
        return "codex CLI not installed"
    return None


# ---------------------------------------------------------------- scoring
def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def to_norm(bbox, fmt, w, h):
    if not (isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox)):
        return None
    if fmt == "norm1000":
        b = [bbox[0] / 1000, bbox[1] / 1000, bbox[2] / 1000, bbox[3] / 1000]
    else:
        b = [bbox[0] / w, bbox[1] / h, bbox[2] / w, bbox[3] / h]
    b = [max(0.0, min(1.0, v)) for v in b]
    return b if b[2] > b[0] and b[3] > b[1] else None


def kw_hit(text, words):
    t = (text or "").lower()
    return any(w.lower() in t for w in words)


def score_row(case, row, fmt):
    obj = parse_json(row.get("text", ""))
    s = {"json_ok": obj is not None}
    if obj is None:
        return s
    topic, target, answer = (str(obj.get(k, "")) for k in ("topic", "target", "answer"))
    s["topic_kw"] = kw_hit(topic + " " + answer, case["topic_keywords"])
    s["target_kw"] = kw_hit(target, case["target_keywords"])
    w, h = row["img_wh"]
    if fmt:
        b = to_norm(obj.get("bbox"), fmt, w, h)
        s["iou"] = iou(b, case["box"]) if b else 0.0
        # Diagnostic only: best IoU under any of the three common conventions (explains coordinate mismatches).
        alts = [to_norm(obj.get("bbox"), f, w, h) for f in ("pixels", "norm1000")]
        s["iou_any_format"] = max([iou(a, case["box"]) for a in alts if a] or [0.0])
    s["refusal_like"] = bool(re.search(r"\b(can't help|cannot help|unable to (see|view|assist))", answer.lower()))
    return s


JUDGE_PROMPT = """You grade an AI helper's reply in a live camera call. Use only the reference below.
Conversation and question: {conv}
Reference topic: {topic}
Reference good answer: {gold}
Model's stated topic: {mtopic}
Model's reply: {answer}

Rubric for quality (integer 1-5):
5 = correct, specific to the object in the frame, safe, short enough to speak.
4 = correct and safe, minor vagueness or extra words.
3 = partly correct or generic, nothing harmful.
2 = mostly wrong or misses the question.
1 = wrong, unsafe, refuses without reason, or empty.
topic_ok = true if the model's topic and reply are about the same thing as the reference topic.
Reply with only JSON {{"topic_ok": true|false, "quality": 1-5}}."""


def judge_all():
    cases = {c["id"]: c for c in load_cases()}
    for f in sorted(OUT.glob("*.jsonl")):
        rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        changed = False
        for row in rows:
            if row.get("rep") != 0 or "judge" in row or row.get("error") or f.name.startswith("boxsvc"):
                continue
            case = cases[row["case"]]
            obj = parse_json(row.get("text", "")) or {}
            conv = " | ".join(f"{r}: {t}" for r, t in case["history"]) + " | USER: " + case["question"]
            prompt = JUDGE_PROMPT.format(conv=conv, topic=case["topic"], gold=case["gold_answer"],
                                         mtopic=obj.get("topic", ""), answer=obj.get("answer", row.get("text", "")[-600:]))
            r = httpx.post(OLLAMA + "/api/chat", timeout=180, json={
                "model": JUDGE_MODEL, "stream": False, "think": False, "keep_alive": "10m",
                "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 40},
                "messages": [{"role": "user", "content": prompt}]})
            j = parse_json_any(r.json()["message"]["content"])
            row["judge"] = j if isinstance(j, dict) else {"error": "bad judge json"}
            changed = True
        if changed:
            f.write_text("".join(json.dumps(r) + "\n" for r in rows))
            print("judged", f.name)


def parse_json_any(text):
    m = re.search(r"\{.*?\}", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def pct(values, q):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def summarize():
    cases = {c["id"]: c for c in load_cases()}
    out = []
    for f in sorted(OUT.glob("*.jsonl")):
        name = f.stem.replace("__", ":")
        try:
            fmt = backend(name)[1]
        except KeyError:
            fmt = None
        rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        ok_rows = [r for r in rows if not r.get("error")]
        first = [r for r in ok_rows if r.get("rep") == 0]
        scores = [score_row(cases[r["case"]], r, fmt) for r in first]
        n = len(first)
        def rate(key):
            vals = [s.get(key) for s in scores if key in s]
            return (sum(bool(v) for v in vals), len(vals))
        judged = [r["judge"] for r in first if isinstance(r.get("judge"), dict) and "quality" in r["judge"]]
        per_case_total = {}
        for r in ok_rows:
            per_case_total.setdefault(r["case"], []).append(r["total_s"])
        med3 = [statistics.median(v) for v in per_case_total.values()]
        out.append({
            "backend": name, "n_cases": n, "n_calls": len(rows),
            "errors": sum(1 for r in rows if r.get("error")),
            "bad_json": sum(1 for s in scores if not s["json_ok"]),
            "topic_kw": rate("topic_kw"), "target_kw": rate("target_kw"),
            "topic_judge": (sum(bool(j.get("topic_ok")) for j in judged), len(judged)),
            "quality": statistics.mean(j["quality"] for j in judged) if judged else None,
            "iou30": (sum(1 for s, r in zip(scores, first) if s.get("iou", 0) >= 0.3 and r.get("coco_target", True)),
                      sum(1 for s, r in zip(scores, first) if "iou" in s and r.get("coco_target", True))) if fmt else None,
            "iou30_anyfmt": sum(1 for s in scores if s.get("iou_any_format", 0) >= 0.3) if fmt else None,
            "ttft_p50": pct([r.get("ttft_s") for r in ok_rows], .5), "ttft_p90": pct([r.get("ttft_s") for r in ok_rows], .9),
            "total_p50": pct([r["total_s"] for r in ok_rows], .5), "total_p90": pct([r["total_s"] for r in ok_rows], .9),
            "case_median_of_reps_p50": pct(med3, .5),
            "box_ttft_p50": pct([r.get("box_ttft_s") for r in ok_rows], .5),
            "box_ttft_p90": pct([r.get("box_ttft_s") for r in ok_rows], .9),
            "box_total_p50": pct([r.get("box_total_s") for r in ok_rows], .5),
            "box_total_p90": pct([r.get("box_total_s") for r in ok_rows], .9),
            "box_compute_p50": pct([r["box_total_s"] - r["load_s"] for r in ok_rows if r.get("box_total_s")], .5),
            "box_compute_p90": pct([r["box_total_s"] - r["load_s"] for r in ok_rows if r.get("box_total_s")], .9),
            "box_wait_p50": pct([r.get("load_s") for r in ok_rows], .5),
            "in_tokens_mean": statistics.mean([r["in_tokens"] for r in ok_rows if r.get("in_tokens")] or [0]),
            "out_tokens_mean": statistics.mean([r["out_tokens"] for r in ok_rows if r.get("out_tokens")] or [0]),
            "cost_mean": statistics.mean([r["cost_usd"] for r in ok_rows if r.get("cost_usd") is not None] or [0]),
            "load_max": max([r.get("load_s", 0) for r in ok_rows] or [0]),
        })
    (OUT / "summary.json").write_text(json.dumps(out, indent=1))
    return out


def run(names, reps, only=None):
    OUT.mkdir(exist_ok=True)
    cases = load_cases()
    if only:
        cases = [c for c in cases if c["id"] in only]
    for name in names:
        why = available(name)
        if why:
            print(f"SKIP {name}: {why}")
            continue
        fn = backend(name)[0]
        path = OUT / (name.replace(":", "__") + ".jsonl")
        done = set()
        if path.exists():
            for l in path.read_text().splitlines():
                r = json.loads(l)
                done.add((r["case"], r["rep"]))
        # Interleave: rep-major order so a slow period does not hit one case's three samples.
        for rep in range(reps):
            for case in cases:
                if (case["id"], rep) in done:
                    continue
                row = {"backend": name, "case": case["id"], "rep": rep, "ts": time.time()}
                try:
                    row.update(fn(case))
                except Exception as exc:  # counted as a failure, never hidden
                    row.update({"error": type(exc).__name__ + ": " + str(exc)[:200], "total_s": None})
                with path.open("a") as fh:
                    fh.write(json.dumps(row) + "\n")
                print(name, case["id"], rep, "err" if row.get("error") else f"{row['total_s']:.2f}s", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backends", default="")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--cases", default="")
    ap.add_argument("--warm", action="store_true", help="one untimed warm-up call per Ollama backend first")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--table", action="store_true")
    a = ap.parse_args()
    names = [n for n in a.backends.split(",") if n]
    if (a.judge or any(n.startswith(('ollama:', 'ollama-cpu4:', 'pipe:')) for n in names)) and not os.environ.get('OLLAMA_URL'):
        raise SystemExit('Set OLLAMA_URL to your local service URL before running this script')
    if any(n.startswith('boxsvc:') for n in names) and not os.environ.get('BOX_URL'):
        raise SystemExit('Set BOX_URL to your local service URL before running this script')
    if a.warm:
        for n in names:
            if n.startswith(("ollama:", "ollama-cpu4:", "pipe:")):
                model = n.split(":", 1)[1] if n.startswith("ollama") else "qwen3.5:9b-q4_K_M"
                extra = CPU4 if n.startswith("ollama-cpu4:") else {}
                httpx.post(OLLAMA + "/api/chat", timeout=600, json={"model": model, "stream": False, "think": False,
                           "keep_alive": "30m", "options": {"num_ctx": 4096, "num_predict": 1, **extra},
                           "messages": [{"role": "user", "content": "OK"}]})
    if names:
        run(names, a.reps, set(a.cases.split(",")) if a.cases else None)
    if a.judge:
        judge_all()
    if a.table or a.judge:
        rows = summarize()
        for row in rows:
            print(json.dumps(row))
        print(markdown(rows))


def markdown(rows):
    def frac(v):
        return f"{v[0]}/{v[1]}" if v and v[1] else "n/a"

    def sec(v):
        return f"{v:.2f}" if isinstance(v, (int, float)) else "n/a"
    lines = ["| Backend | Topic (judge) | Topic (keyword) | Target named | Box IoU>=0.3 | Quality 1-5 | "
             "TTFT p50/p90 s (Mac) | Total p50/p90 s (Mac) | Box-side total p50/p90 s | Bad JSON | Errors |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['backend']} | {frac(r['topic_judge'])} | {frac(r['topic_kw'])} | {frac(r['target_kw'])} | "
                     f"{frac(r['iou30']) if r['iou30'] else 'n/a'} | {sec(r['quality'])} | "
                     f"{sec(r['ttft_p50'])}/{sec(r['ttft_p90'])} | {sec(r['total_p50'])}/{sec(r['total_p90'])} | "
                     f"{sec(r['box_total_p50'])}/{sec(r['box_total_p90'])} | {r['bad_json']}/{r['n_cases']} | "
                     f"{r['errors']}/{r['n_calls']} |")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
