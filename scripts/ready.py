"""Read-only morning probes. Never display URLs, credentials, response bodies or command output."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from websockets.asyncio.client import connect

from server.box_client import box_url
from server.brain_local import _url, expert_model, planner_model, selected, talker_model, vision_models
from server.llm import local_model
from server.vision_client import VisionClient


class Report:
    def __init__(self):
        self.failed = False

    def row(self, name, status, detail, fix=''):
        self.failed |= status == 'FAIL'
        print(f'{status:4} | {name:26} | {detail}' + (f' | Fix: {fix}' if status == 'FAIL' else ''), flush=True)


def present(name):
    return len(os.environ.get(name, '')) > 0


def command(args, timeout):
    try:
        result = subprocess.run(args, cwd=ROOT, capture_output=True, timeout=timeout)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


async def pairing(origin, headers):
    async with asyncio.timeout(8):
        async with connect(origin.replace('http://', 'ws://') + '/ws2',
                           additional_headers=headers, open_timeout=3, close_timeout=1) as ws:
            await ws.send(json.dumps(dict(v=2, type='hello', session_id='readiness-check',
                                          pack='general', private=True, camera=False)))
            await ws.send(json.dumps(dict(v=2, type='pair_request', session_id='readiness-check')))
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get('type') == 'pair':
                    return bool(msg.get('code')) and bool(msg.get('path'))
                if msg.get('type') == 'error':
                    return False
    return False


def required_models():
    models = vision_models() | {local_model()}
    if selected():
        models.update((talker_model(), planner_model(), expert_model()))
    if os.environ.get('EXPERT_LOCAL_MODEL'):
        models.add(os.environ['EXPERT_LOCAL_MODEL'])
    return models


async def network(report, origin='http://127.0.0.1:8000'):
    headers = {'Authorization': 'Bearer ' + os.environ['A_EYE_TOKEN']} if present('A_EYE_TOKEN') else {}
    auth_missing = not headers and (present('CF_ACCESS_TEAM_DOMAIN') or present('CF_ACCESS_AUD'))
    async with httpx.AsyncClient(timeout=4) as client:
        async def check(name, url, predicate, fix, auth=False):
            if auth and auth_missing:
                report.row(name, 'SKIP', 'Access sign-in required; no local token')
                return
            try:
                response = await client.get(url, headers=headers if auth else {})
                if auth and response.status_code in (401, 403):
                    report.row(name, 'SKIP', 'server requires valid authentication')
                    return
                response.raise_for_status()
                ok = predicate(response)
            except Exception:
                ok = False
            report.row(name, 'PASS' if ok else 'FAIL', 'yes' if ok else 'no', fix)

        await check('Server :8000 /health', origin + '/health', lambda r: r.json().get('ok') is True,
                    'Start make server.')
        await check('/api/status features', origin + '/api/status',
                    lambda r: r.json().get('features', {}).get('memory') is True,
                    'Update/restart the server; check /api/status features.', auth=True)
        await check('Vision /health', VisionClient().url.rstrip('/') + '/health',
                    lambda r: r.json().get('ok') is True and not r.json().get('fake', False),
                    'Start GPU vision and check VISION_URL; disable fake vision.')
        await check('Box service /health', box_url() + '/health', lambda r: r.json().get('ok') is True,
                    'Start the box service; check BOX_URL and the private network.')
        await check('Ollama required models', _url().rsplit('/', 1)[0] + '/tags',
                    lambda r: required_models() <= {m.get('name') for m in r.json().get('models', [])},
                    'Check OLLAMA_URL and install configured vision, talker and expert model tags.')
        # Remembered phones use camera-role hello on /ws2, via agent.html?phone=mine.
        await check('Remember-this-phone page', origin + '/agent.html?phone=mine',
                    lambda r: 'agent.js' in r.text, 'Restore client-web/agent.html and restart.')
    if auth_missing:
        report.row('Phone pair_request /ws2', 'SKIP', 'Access sign-in required; no local token')
    else:
        try:
            ok = await pairing(origin, headers)
        except Exception as exc:
            from websockets.exceptions import ConnectionClosed, InvalidStatus
            denied = (isinstance(exc, ConnectionClosed) and exc.rcvd and exc.rcvd.code == 4401) or (
                isinstance(exc, InvalidStatus) and exc.response.status_code in (401, 403))
            if denied:
                report.row('Phone pair_request /ws2', 'SKIP', 'server requires valid authentication')
                return
            ok = False
        report.row('Phone pair_request /ws2', 'PASS' if ok else 'FAIL', 'yes' if ok else 'no',
                   'Start the server with pairing support and check local authentication.')


def local_checks(report):
    report.row('CF_ACCESS_TEAM_DOMAIN set', 'PASS' if present('CF_ACCESS_TEAM_DOMAIN') else 'SKIP',
               'yes' if present('CF_ACCESS_TEAM_DOMAIN') else 'no')
    for name in ('OPENAI_COMPAT_API_KEY', 'ANTHROPIC_API_KEY', 'CARTESIA_API_KEY', 'TWELVELABS_API_KEY'):
        report.row(name + ' set', 'PASS' if present(name) else 'SKIP', 'yes' if present(name) else 'no')
    tailscale = shutil.which('tailscale')
    app_cli = Path('/Applications/Tailscale.app/Contents/MacOS/Tailscale')
    tailscale = tailscale or (str(app_cli) if app_cli.exists() else None)
    report.row('Tailscale installed', 'PASS' if tailscale else 'FAIL', 'yes' if tailscale else 'no',
               'Install Tailscale for remote home access.')
    logged_in = False
    if tailscale:
        try:
            result = subprocess.run([tailscale, 'status', '--json'], capture_output=True, timeout=5)
            logged_in = result.returncode == 0 and json.loads(result.stdout).get('BackendState') == 'Running'
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    report.row('Tailscale logged in', 'PASS' if logged_in else 'FAIL', 'yes' if logged_in else 'no',
               'Open Tailscale and sign in; this check never changes settings.')
    ok = command([sys.executable, '-m', 'device.fake_turn', '--mock'], 120)
    report.row('Device no-hardware test', 'PASS' if ok else 'FAIL', 'yes' if ok else 'no',
               'Run python -m device.fake_turn --mock; install device dependencies and macOS say/afconvert.')
    if not shutil.which('xcodebuild'):
        report.row('Vision Pro simulator build', 'SKIP', 'xcodebuild is not installed')
    else:
        with tempfile.TemporaryDirectory(prefix='aeye-ready-build-') as build:
            ok = command(['xcodebuild', '-project', 'visionos/AEyeVision/AEyeVision.xcodeproj',
                          '-scheme', 'AEyeVision', '-sdk', 'xrsimulator', '-destination',
                          'generic/platform=visionOS Simulator', '-derivedDataPath', build,
                          'CODE_SIGNING_ALLOWED=NO', 'build'], 300)
        report.row('Vision Pro simulator build', 'PASS' if ok else 'FAIL', 'yes' if ok else 'no',
                   'Install/select full Xcode with visionOS simulator SDK; build AEyeVision for simulator.')


def main():
    report = Report()
    print('STATE | CHECK                      | RESULT / REASON | FIX (on failure)', flush=True)
    asyncio.run(network(report))
    local_checks(report)
    return int(report.failed)


if __name__ == '__main__':
    sys.exit(main())
