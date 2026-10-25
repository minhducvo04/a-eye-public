"""Real run of the Rewind tools: 3 synthetic scenes -> 3 tiny clips -> search_past (local VLM check) -> recap_call.

Usage: .venv/bin/python scripts/rewind_recap_probe.py --env ~/Projects/a-eye/.env --vision http://127.0.0.1:8001
Uploads exactly 3 clips of about 4 to 5 s each and makes one Pegasus call. The key is read, never printed.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rewind_probe import load_key, scene  # noqa: E402
from server import rewind_tools  # noqa: E402
from server.keyframes import KeyframeGate, signature  # noqa: E402
from server.rewind import Rewind  # noqa: E402
from server.ws2 import SceneClient  # noqa: E402


class Session:
    session_id = 'rewind-recap-probe'
    pack = type('Pack', (), {'id': 'general'})()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env', default='~/Projects/a-eye/.env')
    parser.add_argument('--vision', default='http://127.0.0.1:8001')
    parser.add_argument('--query', default='where did I put the blue mug?')
    args = parser.parse_args()
    load_key(args.env)
    clock = [0.0]
    session = Session()
    session.rewind = Rewind(session.session_id, clock=lambda: clock[0])
    session.rewind.set_on(True)
    gate = KeyframeGate(heartbeat_s=1000)
    timeline = [(0, 'ball'), (8, 'mug'), (16, 'empty')]
    for step in range(int(22 * 4)):
        t = step / 4
        jpeg = scene([k for start, k in timeline if t >= start][-1], t)
        clock[0] = t
        session.rewind.add_frame(jpeg)
        scan, reason = gate.should_scan(signature(jpeg), t)
        if scan:
            session.rewind.keyframe(reason)
            print(f'keyframe t={t:.2f}s reason={reason}')
    session.rewind.close()
    started = monotonic()
    await session.rewind.flush(wait=True)
    print(f'uploads: {[(t, round(ms)) for t, ms in session.rewind.uploads]}; upload + indexing '
          f'{monotonic() - started:.1f}s; clips {[(c.start_s, c.end_s, c.status) for c in session.rewind.clips]}')

    print('say first:', rewind_tools.FIRST_LINE['search_past'])
    started = monotonic()
    result, cards = await rewind_tools.run_tool('search_past', {'query': args.query}, session,
                                                vision=SceneClient(args.vision))
    print(f'search_past {(monotonic() - started) * 1000:.0f} ms:', json.dumps(result))

    client = session.rewind.client
    original = client.analyze

    async def timed_analyze(mp4, prompt, **kwargs):
        began = monotonic()
        text, usage = await original(mp4, prompt, **kwargs)
        print(f'pegasus: {len(mp4)} byte video, {(monotonic() - began) * 1000:.0f} ms, usage {usage}, raw {text[:400]!r}')
        return text, usage

    client.analyze = timed_analyze
    print('say first:', rewind_tools.FIRST_LINE['recap_call'])
    started = monotonic()
    result, cards = await rewind_tools.run_tool('recap_call', {}, session)
    print(f'recap_call {(monotonic() - started) * 1000:.0f} ms:', json.dumps(result))


if __name__ == '__main__':
    asyncio.run(main())
