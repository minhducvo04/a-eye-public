"""Real local HTTP/ws2 + --fake bridge. Synthetic client ACK, no browser/mic/hardware claim."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile

import httpx
import websockets


async def run():
    with tempfile.TemporaryDirectory(prefix='aeye-wake-') as temp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, A_EYE_TOKEN=secrets.token_hex(24), A_EYE_MOCK='1', LOCAL_ONLY='1',
                   A_EYE_MEMORY_DIR=temp)
        base = f'http://127.0.0.1:{port}'
        with open(Path(temp) / 'server.log', 'w') as log:
            server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'server.app:app', '--host',
                                       '127.0.0.1', '--port', str(port), '--no-access-log'],
                                      env=env, stdout=log, stderr=log)
            try:
                async with httpx.AsyncClient(base_url=base, timeout=3) as client:
                    for _ in range(100):
                        try:
                            if (await client.get('/health')).is_success:
                                break
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(.1)
                    else:
                        raise RuntimeError('Local server did not start')
                    headers = {'Authorization': 'Bearer ' + env['A_EYE_TOKEN']}
                    env['A_EYE_OWNER'] = (await client.post('/api/call-owner', headers=headers)).json()['owner']
                    headers['X-Aeye-Owner'] = env['A_EYE_OWNER']
                    async with websockets.connect(base.replace('http', 'ws') + '/ws2', additional_headers=headers) as ws:
                        await ws.send(json.dumps(dict(v=2, type='hello', session_id='wake-smoke',
                                                      pack='general', camera=False, tts='browser')))
                        assert json.loads(await ws.recv())['state'] == 'listening'
                        body = dict(session_id='wake-smoke', ack='', listening=False)
                        await asyncio.sleep(.1)
                        assert (await client.post('/api/trigger/client', headers=headers, json=body)).is_success
                        await client.post('/api/memory/tag', headers=headers, json=dict(
                            name='entryway', kind='place', label='Keys are in the bowl.'))
                        bridge = await asyncio.create_subprocess_exec(sys.executable, 'scripts/wake_bridge.py',
                            '--fake', '--url', base, '--quiet', '1', '--duration', '4', env=env,
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                        lines = []
                        states = set()
                        while bridge.returncode is None:
                            response = (await client.post('/api/trigger/client', headers=headers, json=body)).json()
                            states.add(response['state'])
                            assert response['camera_on'] is False
                            if response.get('wake'):
                                lines.append(response['wake']['line'])
                                body.update(ack=response['wake']['id'], listening=True)
                            await asyncio.sleep(.1)
                        stdout, stderr = await bridge.communicate()
                        assert bridge.returncode == 0, stderr.decode()
                        output = stdout.decode()
                        assert lines == ['Keys are in the bowl.'], lines
                        assert {'EVENT', 'SESSION', 'ARMED'} <= states, states
                        assert 'LED SESSION' in output and output.count('LED ARMED') == 2, output
                        print(output, end='')
                        print('Wake smoke PASS: real HTTP + ws2; one local reminder; client ACK; camera off; quiet rearmed')
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == '__main__':
    asyncio.run(run())
