"""End-to-end run of the device client without hardware, against a real local server.

    .venv/bin/python -m device.fake_turn            # from the repo root; writes a JSON summary to stdout

Starts the vision service and the A-eye server from this checkout on free ports (never 8000/8001), with auth off
and a temporary memory folder. The device gets a synthetic camera clip (a table with three cups; a key ring
appears 1 s in; no people), a fake mic (macOS `say`: "Where are my keys?" a few seconds after turn-on) and a null
speaker that keeps the reply audio. One scripted button press turns it on; nothing else happens, so it must turn
itself off after the idle time. Private mode is on in hello. Vision is the fake backend and every Ollama call
goes to a closed local port unless --box-ollama; speech recognition and Kokoro still run on the GPU box. Media live in a temp folder that is removed after.
"""
import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from device import config, hw
from device.client import Device

ROOT = Path(__file__).resolve().parent.parent
W, H = 480, 360


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def table_frame(keys, arm=False):
    """A synthetic key table: wall, wooden top, three cups, optionally a key ring in front of the middle cup and
    an arm (a plain skin-coloured shape, no person) reaching in to put it down."""
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (W, H), (196, 170, 132))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 90], fill=(222, 222, 214))
    for cx in (110, 240, 370):
        d.polygon([(cx - 42, 150), (cx + 42, 150), (cx + 32, 250), (cx - 32, 250)], fill=(176, 52, 46))
        d.ellipse([cx - 42, 140, cx + 42, 160], fill=(150, 40, 36))
    if keys:
        d.ellipse([214, 272, 246, 304], outline=(120, 120, 128), width=5)
        d.rectangle([244, 284, 300, 292], fill=(205, 175, 70))
        for x in (270, 282, 292):
            d.rectangle([x, 292, x + 5, 300], fill=(205, 175, 70))
        d.rectangle([226, 300, 238, 336], fill=(170, 170, 178))
    if arm:
        d.polygon([(W, 150), (W, 330), (330, 300), (300, 240)], fill=(224, 172, 140))
        d.ellipse([270, 220, 350, 300], fill=(214, 160, 128))
    return img


def write_clip(folder, n=8, keys_at=1):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        table_frame(i >= keys_at).save(folder / f'{i:03d}.jpg', quality=90)


def say_wav(text, path):
    aiff = path.with_suffix('.aiff')
    subprocess.run(['say', '-o', str(aiff), text], check=True)
    subprocess.run(['afconvert', '-f', 'WAVE', '-d', 'LEI16@16000', '-c', '1', str(aiff), str(path)], check=True)


def wait_http(url, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(0.3)
    raise SystemExit(f'{url} did not come up')


def start_stack(tmp, mock, box_ollama=False):
    port, vport = free_port(), free_port()
    env = dict(os.environ, PYTHONUNBUFFERED='1', VISION_URL=f'http://127.0.0.1:{vport}', A_EYE_MEMORY_DIR=str(tmp / 'memory'))
    if not box_ollama:
        # Leave the GPU box's Ollama alone (the server warms the talker at startup, which evicts other jobs):
        # a closed local port for every Ollama call, and the fake vision backend. STT and Kokoro still use the box.
        env['OLLAMA_URL'] = 'http://127.0.0.1:9'
    for name in ('A_EYE_TOKEN', 'CF_ACCESS_TEAM_DOMAIN', 'CF_ACCESS_AUD', 'A_EYE_MOCK', 'ANTHROPIC_API_KEY',
                 'CARTESIA_API_KEY', 'TWELVELABS_API_KEY'):
        env.pop(name, None)     # a private test server: no auth, and no cloud keys at all
    if mock:
        env['A_EYE_MOCK'] = '1'
    procs = []
    if not mock:
        procs.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'vision.app:app', '--port', str(vport)],
                                      cwd=ROOT, env=dict(env, VISION_BACKEND='auto' if box_ollama else 'fake'),
                                      stdout=open(tmp / 'vision.log', 'w'), stderr=subprocess.STDOUT))
    procs.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server.app:app', '--port', str(port)],
                                  cwd=ROOT, env=env, stdout=open(tmp / 'server.log', 'w'), stderr=subprocess.STDOUT))
    if not mock:
        wait_http(f'http://127.0.0.1:{vport}/health')
    wait_http(f'http://127.0.0.1:{port}/health')
    return port, procs


def consent_memory(port):
    """Local memory on for the test call (both consents, temporary folder), as the demo rehearsal does."""
    body = json.dumps({'consent': {'person': True, 'caregiver': True}, 'remembering': True,
                       'items': [{'item': 'keys', 'home': 'blue bowl'}], 'kept_days': 1}).encode()
    req = urllib.request.Request(f'http://127.0.0.1:{port}/api/memory/settings', data=body, method='PUT',
                                 headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception:
        return False


async def run_device(port, tmp, ask_at, idle_s, question_wav):
    cfg = config.load(overrides={
        'server': {'url': f'ws://127.0.0.1:{port}/ws2'},
        'trigger': {'button': 'none', 'idle_off_s': idle_s, 'cap_s': 120},
        'camera': {'source': 'images', 'path': str(tmp / 'clip'), 'fps': 1.0},
        'audio': {'mic': 'wav', 'speaker': 'null', 'record_dir': str(tmp / 'replies')},
    })
    events = []
    t0 = time.monotonic()

    def log(kind, **kw):
        events.append(dict(t=round(time.monotonic() - t0, 2), event=kind, **kw))
        print(json.dumps(events[-1]), file=sys.stderr, flush=True)

    device = Device(cfg, mic=hw.WavMic([(ask_at, question_wav)]), log=log)
    runner = asyncio.create_task(device.run(hw.scripted_presses(device.events, [0.5])))
    deadline = time.monotonic() + ask_at + idle_s + 60
    while time.monotonic() < deadline and not device.stats['off_reasons']:
        await asyncio.sleep(0.2)
    device.stop.set()
    await asyncio.wait_for(runner, 10)
    return device, events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mock', action='store_true', help='A_EYE_MOCK=1 server (no GPU box)')
    ap.add_argument('--idle', type=float, default=20.0, help='idle_off_s for this run (production default is 90)')
    ap.add_argument('--ask-at', type=float, default=6.0)
    ap.add_argument('--box-ollama', action='store_true',
                    help="use the GPU box's Ollama for vision and the talker (warms models there; off by default)")
    ap.add_argument('--keep', action='store_true', help='keep the temp folder (synthetic media only)')
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix='aeye-device-'))
    procs = []
    try:
        write_clip(tmp / 'clip')
        say_wav('Where are my keys?', tmp / 'q.wav')
        port, procs = start_stack(tmp, args.mock, args.box_ollama)
        memory = consent_memory(port)
        device, events = asyncio.run(run_device(port, tmp, args.ask_at, args.idle, tmp / 'q.wav'))
        s = device.stats
        on_at = next((e['t'] for e in events if e['event'] == 'on'), None)
        off = next((e for e in events if e['event'] == 'off'), {})
        received = next((e for e in events if e['event'] == 'turn' and e.get('stage') == 'received'), None)
        heard = next((e.get('text') for e in events if e['event'] == 'turn' and e.get('text')), None)
        last_audio_change = off.get('t')
        checks = {
            'turned on by the button': on_at is not None,
            'server said listening (private hello)': any(e['event'] == 'listening' and e.get('private')
                                                         for e in events),
            'frames sent': s['frames_sent'] > 0,
            'utterance sent as audio_in': s['utterances'] >= 1 and s['audio_in_bytes'] > 0,
            'turn received': received is not None,
            'reply text': bool(s['replies']),
            'reply audio received': s['audio_out_bytes'] > 0,
            'turned itself off (idle)': off.get('reason') == 'idle',
            'no server errors': not s['errors'],
        }
        print(json.dumps(dict(mode='mock' if args.mock else 'live', box_ollama=args.box_ollama, memory_consent=memory, idle_off_s=args.idle,
                              checks=checks, ok=all(checks.values()), heard=heard,
                              replies=s['replies'], find_items=s['find_items'], frames_sent=s['frames_sent'],
                              frames_dropped_person=s['frames_dropped_person'],
                              audio_in_s=round(s['audio_in_bytes'] / 32000, 2),
                              audio_out_s=round(s['audio_out_bytes'] / 32000, 2),
                              reply_utterances=len(device.speaker.order), on_at=on_at, off_at=last_audio_change,
                              off_reason=off.get('reason'), timeline=events), indent=1))
        return 0 if all(checks.values()) else 1
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        if args.keep:
            print(f'kept {tmp}', file=sys.stderr)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
