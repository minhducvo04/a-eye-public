"""Run after unplugging WAN, leaving LAN up. Synthetic input only; no downloads."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from server import memory, privacy
from server.box_client import box_url
from server.brain_local import make_llm
from server.llm import TURN_BRAIN
from server.vision_client import VisionClient


def seed(store):
    store.append(dict(id='proof-yesterday', t=time.time() - 86400,
                      note='Fixture keys on the blue tray.', labels=['keys'], tags=[], crops={}))


async def services():
    privacy.set_call(private=True)
    results = {}

    async def check(name, operation):
        try:
            ok = await asyncio.wait_for(operation(), 25)
            results[name] = bool(ok)
            print(f'{"PASS" if ok else "SKIP"} | {name} | '
                  f'{"answered locally" if ok else "service unavailable or fallback"}', flush=True)
        except Exception as exc:
            results[name] = False
            print(f'SKIP | {name} | unavailable ({type(exc).__name__})', flush=True)

    async def talker():
        metadata = {}
        TURN_BRAIN.set(metadata)
        text = ''.join([s async for s in make_llm().stream('Reply briefly.', 'Say ready.', max_tokens=8)])
        return bool(text) and metadata.get('provider') == 'local'

    async def vision():
        from scripts.keyframe_bench import synthetic_frame
        client = VisionClient()
        async with httpx.AsyncClient(timeout=3) as http:
            response = await http.get(client.url.rstrip('/') + '/health')
            response.raise_for_status()
            if response.json().get('fake') is not False:
                return False
        result = await client.call('scene', dict(image_b64=synthetic_frame(0), queries=['box']))
        return isinstance(result.get('caption'), str)

    async def stt():
        from server.box_client import BoxClient
        box = BoxClient()
        try:
            return isinstance(await box.stt(bytes(16000)), str)
        finally:
            await box.aclose()

    async def tts():
        async with httpx.AsyncClient(timeout=8) as http:
            response = await http.post(box_url() + '/tts', json=dict(text='Proof wall ready.', voice='af_kore'))
            response.raise_for_status()
            return bool(response.content)

    async def router():
        from router.route import aroute
        result = await aroute('Where were my keys yesterday?')
        return 'error' not in result

    async def search():
        with tempfile.TemporaryDirectory(prefix='proof-memory-') as folder:
            store = memory.Store('fixture', base=folder, cipher=memory.Cipher(''))
            seed(store)
            result = await memory.find(store, 'Where were my keys yesterday?')
            # Also verify semantic search's local embedding service, not just exact-label lookup.
            vectors = await asyncio.to_thread(memory.GemmaEmbedder().embed, ['fixture keys'])
            return result['found'] and bool(vectors[0])

    for name, operation in [('local talker', talker), ('local vision', vision), ('STT', stt),
                            ('TTS', tts), ('router', router), ('local memory search', search)]:
        await check(name, operation)
    return results


def guards():
    command = [sys.executable, '-m', 'pytest', '-q', 'server/test_proof_wall.py',
               'voice/test_home_first.py', 'voice/test_private.py', 'server/test_brain_openai_compat.py']
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    summary = next((line for line in reversed(result.stdout.splitlines()) if 'passed' in line or 'failed' in line),
                   'pytest unavailable')
    print(f'{"PASS" if result.returncode == 0 else "FAIL"} | Private cloud boundary tests | {summary}')
    return result.returncode == 0


if __name__ == '__main__':
    os.environ['LOCAL_ONLY'] = '1'
    print('WAN must be unplugged by the judge; this command does not change networking.')
    available = asyncio.run(services())
    safe = guards()
    print(f'OFFLINE CHECK: {sum(available.values())}/6 services; guards={"PASS" if safe else "FAIL"}')
    sys.exit(0 if all(available.values()) and safe else 1)
