"""Stage-demo rehearsal: "Where are my keys?" with Rewind on, then "Forget that."

Drives the real page in Chromium on a free port, with a synthetic camera clip (three cups on a table; at 10 s a
hand puts a key ring down in front of the middle cup) and a fake microphone (macOS `say`): "Where are my keys?" at
about 24 s and "Forget that." about 18 s later. The server runs with ROUTER=on, Rewind on and an in-process
TwelveLabs stand-in (REWIND_FAKE_TL=1: nothing is uploaded; our own vision host still checks the frame).

    make demo-rehearsal            # or: uv run --no-project --with playwright python scripts/demo_rehearsal.py

Writes docs/results/demo-rehearsal.md (timeline and assertions) and screenshots under
docs/results/demo-rehearsal/. Media are synthetic and written to a temp folder that is removed afterwards.
"""
import argparse
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")
OUT_DIR = ROOT / "docs" / "results" / "demo-rehearsal"
REPORT = ROOT / "docs" / "results" / "demo-rehearsal-latest.md"   # each run overwrites this one
W, H, FPS, SECONDS, KEYS_AT = 480, 360, 5, 75, 10.0
ASK_AT, FORGET_GAP = 24.0, 18.0


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def draw_frame(t, rng):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), (196, 170, 132))                      # wooden table
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 90], fill=(222, 222, 214))                       # wall
    for cx in (110, 240, 370):                                              # three cups
        d.polygon([(cx - 42, 150), (cx + 42, 150), (cx + 32, 250), (cx - 32, 250)], fill=(176, 52, 46))
        d.ellipse([cx - 42, 140, cx + 42, 160], fill=(150, 40, 36))
    if KEYS_AT - 0.4 <= t < KEYS_AT + 2.0:                                  # an arm reaches in and puts the keys down
        # A real arm fills a big part of the frame; the keyframe gate only opens a clip on a large change.
        d.polygon([(W, 150), (W, 330), (330, 300), (300, 240)], fill=(224, 172, 140))
        d.ellipse([270, 220, 350, 300], fill=(214, 160, 128))
    if t >= KEYS_AT - 0.4:                                                   # key ring in front of the middle cup
        d.ellipse([214, 272, 246, 304], outline=(120, 120, 128), width=5)
        d.rectangle([244, 284, 300, 292], fill=(205, 175, 70))              # brass key
        for x in (270, 282, 292):
            d.rectangle([x, 292, x + 5, 300], fill=(205, 175, 70))
        d.rectangle([226, 300, 238, 336], fill=(170, 170, 178))             # steel key
    shift = rng.randint(-2, 2)                                              # handheld jitter
    img = img.transform(img.size, Image.AFFINE, (1, 0, shift, 0, 1, 0), fillcolor=(196, 170, 132))
    return img.point(lambda v: max(0, min(255, v + rng.randint(-3, 3))))


def write_y4m(path):
    """Raw YUV 4:2:0 for Chromium's --use-file-for-fake-video-capture."""
    rng = random.Random(7)
    with open(path, "wb") as f:
        f.write(f"YUV4MPEG2 W{W} H{H} F{FPS}:1 Ip A1:1 C420jpeg\n".encode())
        for i in range(FPS * SECONDS):
            img = draw_frame(i / FPS, rng).convert("YCbCr")
            y, cb, cr = img.split()
            f.write(b"FRAME\n" + y.tobytes() + cb.resize((W // 2, H // 2)).tobytes()
                    + cr.resize((W // 2, H // 2)).tobytes())


def write_mic(path, tmp):
    """Silence, the question, silence, the forget command, silence (16-bit WAV)."""
    import wave
    q1, q2 = tmp / "q1.aiff", tmp / "q2.aiff"
    subprocess.run(["say", "-o", str(q1), "Where are my keys?"], check=True)
    subprocess.run(["say", "-o", str(q2), "Forget that."], check=True)
    parts = []
    for src in (q1, q2):
        wav = src.with_suffix(".wav")
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@48000", "-c", "1", str(src), str(wav)], check=True)
        with wave.open(str(wav)) as w:
            parts.append(w.readframes(w.getnframes()))
    rate = 48000
    silence = lambda s: b"\x00\x00" * int(rate * s)
    q1_s = len(parts[0]) / 2 / rate
    body = silence(ASK_AT) + parts[0] + silence(FORGET_GAP - q1_s) + parts[1] + silence(SECONDS - ASK_AT - FORGET_GAP)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(body)


def start(cmd, env, log):
    return subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=open(log, "w"), stderr=subprocess.STDOUT)


def wait_http(url, timeout=30):
    import urllib.request
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(0.3)
    raise SystemExit(f"{url} did not come up")


def server_events(log):
    rows = []
    for line in Path(log).read_text().splitlines():
        if line.startswith("{"):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-only", action="store_true", help="LOCAL_ONLY=1 (needs the local find_item path)")
    ap.add_argument("--memory", action="store_true",
                    help="local memory path: both consents on in a temporary memory folder; find_item answers first")
    ap.add_argument("--keep", action="store_true", help="keep the temp folder (media and server logs) for debugging")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    tmp = Path(tempfile.mkdtemp(prefix="aeye-rehearsal-"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    port, vport = free_port(), free_port()
    env = dict(os.environ, ROUTER="on", REWIND_FAKE_TL="1", REWIND_FLUSH_S="5", VISION_URL=f"http://127.0.0.1:{vport}")
    for name in ("A_EYE_TOKEN", "CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "A_EYE_MOCK"):
        env.pop(name, None)                       # a private rehearsal server on a free port
    if args.local_only:
        env["LOCAL_ONLY"] = "1"
    if args.memory:
        env["A_EYE_MEMORY_DIR"] = str(tmp / "memory")      # never the person's real memory
    procs = []
    timeline, checks, sent_at = [], {}, {}
    try:
        write_y4m(tmp / "cam.y4m")
        write_mic(tmp / "mic.wav", tmp)
        procs.append(start([PY, "-m", "uvicorn", "vision.app:app", "--port", str(vport)],
                           dict(env, VISION_BACKEND="auto"), tmp / "vision.log"))
        procs.append(start([PY, "-m", "uvicorn", "server.app:app", "--port", str(port)], env, tmp / "server.log"))
        wait_http(f"http://127.0.0.1:{vport}/health")
        wait_http(f"http://127.0.0.1:{port}/health")
        if args.memory:
            import urllib.request
            body = json.dumps({"consent": {"person": True, "caregiver": True}, "remembering": True,
                               "items": [{"item": "keys", "home": "blue bowl"}], "kept_days": 1}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/memory/settings", data=body, method="PUT",
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).read()
        with sync_playwright() as p:
            browser = p.chromium.launch(args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-video-capture={tmp / 'cam.y4m'}",
                f"--use-file-for-fake-audio-capture={tmp / 'mic.wav'}", "--autoplay-policy=no-user-gesture-required"])
            page = browser.new_context(permissions=["camera", "microphone"], viewport={"width": 430, "height": 900}).new_page()
            errors, received = [], []
            t0 = time.time()
            # Log every mic/camera open and socket open/close: the fake mic file restarts on each audio open.
            page.add_init_script("""(() => {
              window.__aeyeLog = [];
              const t0 = performance.now(), log = (what) => window.__aeyeLog.push([Math.round(performance.now() - t0), what]);
              const gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
              navigator.mediaDevices.getUserMedia = (c) => { log('getUserMedia ' + (c && c.audio ? 'audio' : '') + (c && c.video ? ' video' : '')); return gum(c); };
              const WS = window.WebSocket;
              window.WebSocket = function (...a) { const ws = new WS(...a); log('ws open'); ws.addEventListener('close', (e) => log('ws close ' + e.code)); return ws; };
              window.WebSocket.prototype = WS.prototype; Object.assign(window.WebSocket, WS);
              // Main-thread lag: a 250 ms timer that fires late means something blocked the page.
              let last = performance.now();
              setInterval(() => { const now = performance.now(); if (now - last > 1000) log('main thread blocked ' + Math.round(now - last - 250) + ' ms'); last = now; }, 250);
              // Long tasks (script) and long animation frames (script + style/layout/paint), with the script that ran.
              window.__aeyeLong = [];
              try { new PerformanceObserver((list) => { for (const e of list.getEntries()) { window.__aeyeLong.push([Math.round(e.startTime - t0), Math.round(e.duration)]); if (e.duration > 200) log('long task ' + Math.round(e.duration) + ' ms ' + (e.attribution && e.attribution[0] ? e.attribution[0].containerType || e.attribution[0].name : '')); } }).observe({ entryTypes: ['longtask'] }); } catch (e) {}
              try { new PerformanceObserver((list) => { for (const e of list.getEntries()) if (e.duration > 200) log('long frame ' + Math.round(e.duration) + ' ms, blocking ' + Math.round(e.blockingDuration || 0) + ', style+layout ' + Math.round(e.styleAndLayoutStart ? e.startTime + e.duration - e.styleAndLayoutStart : 0) + ' ms: ' + (e.scripts || []).map((x) => (x.sourceURL || '').split('/').pop() + ' ' + (x.sourceFunctionName || x.invoker || '') + ' ' + Math.round(x.duration)).slice(0, 3).join(' | ')); }).observe({ type: 'long-animation-frame' }); } catch (e) {}
              // When the find card shows (page time), so long tasks after it can be checked.
              document.addEventListener('DOMContentLoaded', () => new MutationObserver(() => { if (!window.__aeyeCardAt && document.querySelector('.memory-split')) { window.__aeyeCardAt = Math.round(performance.now() - t0); log('find card shown'); } }).observe(document.body, { childList: true, subtree: true }));
            })()""")
            page.on("pageerror", lambda e: errors.append(str(e)))
            cdn = []   # third-party script loads (the voice activity detector comes from a CDN)
            page.on("requestfinished", lambda r: cdn.append((round(time.time() - t0, 2), "ok", r.url.rsplit("/", 1)[-1]))
                    if "jsdelivr" in r.url else None)
            page.on("requestfailed", lambda r: cdn.append((round(time.time() - t0, 2), "FAILED " + (r.failure or ""),
                                                           r.url.rsplit("/", 1)[-1])) if "jsdelivr" in r.url else None)
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

            sent_types = {}

            def on_ws(ws):
                def put(frame):
                    try:
                        kind = json.loads(frame).get("type")
                    except (TypeError, ValueError):
                        return
                    sent_types[kind] = sent_types.get(kind, 0) + 1
                ws.on("framesent", put)

                def got(frame):
                    try:
                        m = json.loads(frame)
                    except (TypeError, ValueError):
                        return
                    received.append((time.time() - t0, m))
                ws.on("framereceived", got)
            page.on("websocket", on_ws)

            def mark(what):
                timeline.append((round(time.time() - t0, 2), what))

            def shot(name):
                try:
                    page.screenshot(path=str(OUT_DIR / f"{name}.png"), timeout=10000)
                    mark(f"screenshot {name}.png")
                except Exception as exc:
                    mark(f"screenshot {name}.png FAILED ({type(exc).__name__}): page frozen")

            page.goto(f"http://127.0.0.1:{port}/agent.html?stt=server")  # Whisper hears the fake mic
            page.wait_for_timeout(1200)
            page.click('button[data-pack="general"]')
            page.wait_for_timeout(500)
            if page.locator("#start-overlay:not(.hidden)").count():
                page.click("#start-overlay")
            page.click("#rewind-btn")
            mark("Rewind on")
            page.click("#start-camera")
            mark("camera on (synthetic clip starts at 0 s)")
            page.wait_for_timeout(500)
            if page.get_attribute("#mic", "aria-pressed") != "true":   # the first tap may already start the mic
                page.click("#mic")
            mark(f"mic {'on' if page.get_attribute('#mic', 'aria-pressed') == 'true' else 'OFF'} "
                 "(fake mic: question at about 24 s)")
            cam_t0 = time.time() - t0
            page.wait_for_timeout(5000)
            shot("1-cups")
            page.wait_for_timeout(int((KEYS_AT + 4 - 5) * 1000))
            shot("2-keys-down")

            def wait_for(pred, seconds):
                end = time.time() + seconds
                while time.time() < end:
                    for t, m in received:
                        if pred(m):
                            return t, m
                    page.wait_for_timeout(200)
                return None, None

            t_hit, hit = wait_for(lambda m: m.get("type") in ("rewind_hit", "find_item") and
                                  (m.get("type") == "rewind_hit" or m.get("found")), ASK_AT + 25)
            if hit:
                page.wait_for_timeout(1500)
                shot("3-rewind-hit")
            t_forget, state = None, None
            end = time.time() + FORGET_GAP + 25
            while time.time() < end and not state:          # a rewind_state with 0 clips AFTER the hit
                for t, m in received:
                    if t_hit and t > t_hit and m.get("type") == "rewind_state" and m.get("clips") == 0:
                        t_forget, state = t, m
                        break
                page.wait_for_timeout(200)
            if state:
                page.wait_for_timeout(1000)
                shot("4-forgotten")
            try:
                client_log = page.evaluate("() => window.__aeyeLog || []")
                card_at = page.evaluate("() => window.__aeyeCardAt || null")
                long_after_card = [d for ms, d in page.evaluate("() => window.__aeyeLong || []") if card_at and ms >= card_at]
            except Exception as exc:
                client_log = [[int((time.time() - t0) * 1000), f"page unresponsive at the end ({type(exc).__name__})"]]
                card_at, long_after_card = None, [10 ** 6]
            browser.close()

        mark(f"page sent: {sent_types}")
        for t, status, name in cdn:
            timeline.append((t, f"cdn {status}: {name}"))
        for ms, what in client_log:
            timeline.append((round(ms / 1000, 2), f"client: {what}"))
        timeline.sort()
        events = server_events(tmp / "server.log")
        routes = [e.get("reason", {}).get("label") for e in events if e.get("event") == "route"]
        deleted = [e for e in events if e.get("event") == "rewind_deleted"]
        # A voice turn: "received" (no text yet) then "thinking" with what Whisper heard.
        heard = next(((t, m) for t, m in received if m.get("type") == "turn" and "key" in (m.get("text") or "").lower()),
                     (None, None))
        t_q = next((t for t, m in received if m.get("type") == "turn" and m.get("stage") == "received"
                    and heard[1] and m.get("turn_id") == heard[1].get("turn_id")), heard[0])
        after_q = lambda kind: next((t for t, m in received if m.get("type") == kind and t_q and t >= t_q), None)
        draws = [m for t, m in received if m.get("type") in ("draw", "rewind_hit") and t_q and t >= t_q]
        boxes = sum(len(m.get("ops", m.get("objects", []))) for m in draws)
        hit_says = [m["text"] for t, m in received if m.get("type") == "say" and t_q and t >= t_q][:3]
        expect_t = KEYS_AT + (cam_t0 - cam_t0)        # clip time of the keys, relative to the camera start
        checks = {
            "heard the question (server STT)": bool(heard[1]),
            "router labelled it past": "past" in routes,
            "find card arrived (rewind_hit or find_item)": bool(hit),
            "card type": hit.get("type") if hit else None,
            "search_past sure": bool(hit and hit.get("sure")),
            "hit time within 4 s of the keys going down": bool(hit and hit.get("t_s") is not None
                                                                and abs(float(hit["t_s"]) - expect_t) <= 4.0),
            "box on the hit frame": bool(hit and hit.get("objects")),
            "first audio after the question": bool(after_q("audio")),
            "forget: clip count 0": bool(state),
            "forget: TwelveLabs delete called": len(deleted) > 0,
            "no page errors": not errors,
            # The card's images, arrow and tracker must never block the page (stage stalls of about 1 s were seen).
            "no long task over 300 ms after the card": bool(card_at) and max(long_after_card, default=0) <= 300,
            "longest task after the card": f"{max(long_after_card, default=0)} ms",
        }
        rows = [
            ("question heard (turn received)", t_q),
            ("first spoken text", after_q("say")),
            ("first audio", after_q("audio")),
            ("rewind_hit card", t_hit),
            ("forget done (clips 0)", t_forget),
        ]
        lines = ["# Demo rehearsal: Where are my keys?", "",
                 f"Run {time.strftime('%Y-%m-%d %H:%M %Z')}, {'LOCAL_ONLY=1' if args.local_only else 'cloud talker'}, "
                 "ROUTER=on, Rewind on, TwelveLabs stand-in (nothing uploaded), real vision host for the frame check, "
                 "headless Chromium with a synthetic camera clip and a `say` microphone. Times are seconds since the "
                 "page opened; the camera clip started at "
                 f"{cam_t0:.1f} s and the keys go down {KEYS_AT:.0f} s into it.", "",
                 "| Check | Result |", "|---|---|"]
        lines += [f"| {k} | {v if isinstance(v, str) else ('PASS' if v else 'FAIL')} |" for k, v in checks.items()]
        lines += ["", "| Moment | t (s) | after the question (s) |", "|---|---|---|"]
        for name, t in rows:
            lines.append(f"| {name} | {t:.2f} | {t - t_q:.2f} |" if t and t_q else f"| {name} | {t if t else 'missing'} | |")
        lines += ["", f"Whisper heard: {heard[1].get('text') if heard[1] else 'nothing'}.", f"Router labels seen: {routes}. Draw and box messages after the question: {len(draws)} "
                  f"({boxes} ops or boxes). TwelveLabs deletes: {len(deleted)}.",
                  f"Hit: {json.dumps({k: hit.get(k) for k in ('type', 't_s', 'sure', 'found', 'place', 'home', 'alternatives', 'query')}) if hit else 'none'}.",
                  f"First spoken lines: {hit_says}.", "", "Timeline:", ""]
        lines += [f"- {t:.2f} s: {what}" for t, what in timeline]
        if errors:
            lines += ["", "Page errors:", ""] + [f"- {e[:200]}" for e in errors[:5]]
        REPORT.write_text("\n".join(lines) + "\n")
        print("\n".join(lines))
        sys.exit(0 if all(v for k, v in checks.items() if k != "card type") else 1)
    finally:
        for proc in procs:
            proc.terminate()
        if args.keep:
            print("kept", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
