"""Real HTTPS/browser account smoke, synthetic Guest only; no provider or user credentials.

Run with Playwright installed: python scripts/accounts_smoke.py --python /path/to/project/python
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', required=True)
    parser.add_argument('--screenshot', default='/tmp/aeye-accounts-smoke.png')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='aeye-accounts-smoke-') as temporary:
        directory = Path(temporary)
        cert, key = directory / 'cert.pem', directory / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-keyout', str(key), '-out', str(cert), '-subj', '/CN=localhost'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        origin = f'https://127.0.0.1:{port}'
        env = dict(os.environ, A_EYE_MOCK='1', LOCAL_ONLY='1', SESSION_SECRET=secrets.token_urlsafe(48),
                   GOOGLE_CLIENT_ID='', CF_ACCESS_AUD='', CF_ACCESS_TEAM_DOMAIN='',
                   A_EYE_TOKEN=secrets.token_urlsafe(32), A_EYE_MEMORY_DIR=str(directory / 'memory'),
                   CLOUD_BUDGET_PATH=str(directory / 'budget.sqlite3'))
        with (directory / 'server.log').open('w') as log:
            process = subprocess.Popen([args.python, '-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1',
                '--port', str(port), '--no-proxy-headers', '--ssl-certfile', str(cert), '--ssl-keyfile', str(key)],
                cwd=root, env=env, stdout=log, stderr=log)
            try:
                context = ssl._create_unverified_context()
                for _ in range(100):
                    try:
                        urllib.request.urlopen(origin + '/health', context=context, timeout=.2).close()
                        break
                    except OSError:
                        if process.poll() is not None:
                            raise RuntimeError('Synthetic server exited; inspect the local run.')
                        time.sleep(.1)
                else:
                    raise RuntimeError('Synthetic server did not start.')
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=True, args=['--use-fake-ui-for-media-stream',
                        '--use-fake-device-for-media-stream'])
                    alice = browser.new_context(ignore_https_errors=True, viewport={'width': 390, 'height': 844})
                    page = alice.new_page()
                    page.goto(origin + '/signin.html')
                    page.get_by_role('button', name='Continue as Guest').click()
                    page.wait_for_url('**/agent.html')
                    cookie = next(c for c in alice.cookies() if c['name'] == 'aeye_session')
                    assert cookie['secure'] and cookie['httpOnly']
                    # Native browser fetch supplies Origin and the HttpOnly cookie.
                    assert page.evaluate('''async () => (await fetch('/api/memory/tag', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({name: 'synthetic guest keys'})})).status''') == 200
                    bob = browser.new_context(ignore_https_errors=True)
                    other = bob.new_page()
                    other.goto(origin + '/signin.html')
                    other.get_by_role('button', name='Continue as Guest').click()
                    other.wait_for_url('**/agent.html')
                    assert 'synthetic guest keys' not in other.evaluate("async () => (await fetch('/api/me')).text()")
                    page.goto(origin + '/my-data.html')
                    page.get_by_role('button', name='Export my data').wait_for(state='visible')
                    page.locator('summary').click()
                    assert 'synthetic guest keys' in page.locator('#stored-data').inner_text()
                    page.screenshot(path=args.screenshot, full_page=True)
                    with page.expect_download() as download:
                        page.get_by_role('button', name='Export my data').click()
                    assert download.value.suggested_filename == 'a-eye-my-data.zip'
                    page.on('dialog', lambda dialog: dialog.accept())
                    page.get_by_role('button', name='Delete everything').click()
                    page.get_by_text('Your data is deleted. You are signed out.', exact=True).wait_for()
                    assert page.evaluate("async () => (await fetch('/api/me')).status") == 401
                    assert other.evaluate("async () => (await fetch('/api/me')).status") == 200
                    browser.close()
                print('ACCOUNTS HTTPS BROWSER PASS: Guest, Secure/HttpOnly cookie, isolated data, ZIP export, delete, other user preserved')
                print(json.dumps({'screenshot': args.screenshot, 'media': 'synthetic only', 'google_login': 'not exercised'}))
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    main()
