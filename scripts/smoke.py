"""End-to-end smoke: start fake vision + server, act as the web client over WS /ws.

hello -> confirm power_off -> photo  =>  expect step SAFETY first, then a GUIDE step and an
overlay with a target box. Every received message must validate as contracts server_message.
Exit 0 on success.
"""
import asyncio
import base64

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from contracts import validate  # noqa: E402

PY = sys.executable
VISION_PORT = int(os.environ.get("SMOKE_VISION_PORT", "18001"))
SERVER_PORT = int(os.environ.get("SMOKE_SERVER_PORT", "18000"))
# Synthetic stand-in for a JPEG; the fake vision does not decode it.
FAKE_JPEG = base64.b64encode(b"\xff\xd8\xff\xe0smoke-test-not-a-real-photo\xff\xd9").decode()


def wait_http(url, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise SystemExit(f"SMOKE FAIL: {url} did not come up")


async def client():
    msgs = []
    async with websockets.connect(f"ws://127.0.0.1:{SERVER_PORT}/ws") as ws:
        async def send(m):
            await ws.send(json.dumps(m))

        async def drain(seconds):
            end = time.time() + seconds
            while time.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, end - time.time()))
                except asyncio.TimeoutError:
                    break
                m = json.loads(raw)
                validate("server_message", m)
                msgs.append(m)
                print("<-", m["type"], {k: m[k] for k in ("state", "text", "code", "level") if k in m})

        await send({"v": 1, "type": "hello", "session_id": "smoke", "board_id": "demo-b650", "mode": "photo", "client": "web"})
        await drain(1.0)
        await send({"v": 1, "type": "confirm", "session_id": "smoke", "what": "power_off", "value": True})
        await drain(1.0)
        await send({"v": 1, "type": "photo", "session_id": "smoke", "seq": 1, "w": 640, "h": 480, "jpeg_b64": FAKE_JPEG})
        await drain(3.0)
    return msgs


def main():
    env = dict(os.environ, VISION_URL=f"http://127.0.0.1:{VISION_PORT}")
    procs = [
        subprocess.Popen([PY, "-m", "uvicorn", "vision.app:app", "--port", str(VISION_PORT)], cwd=ROOT, env=env),
        subprocess.Popen([PY, "-m", "uvicorn", "server.app:app", "--port", str(SERVER_PORT)], cwd=ROOT, env=env),
    ]
    try:
        wait_http(f"http://127.0.0.1:{VISION_PORT}/health")
        wait_http(f"http://127.0.0.1:{SERVER_PORT}/health")
        msgs = asyncio.run(client())
    finally:
        for p in procs:
            p.terminate()
    states = [m["state"] for m in msgs if m["type"] == "step"]
    overlays = [m for m in msgs if m["type"] == "overlay" and any(b["role"] == "target" for b in m["boxes"])]
    ok = bool(states) and states[0] == "SAFETY" and "GUIDE" in states and bool(overlays)
    print(f"steps={states} target_overlays={len(overlays)}")
    print("SMOKE PASS" if ok else "SMOKE FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
