"""Real Chromium start regression; use the same local mock server as browser_smoke.py.

    A_EYE_TOKEN=... uv run --no-project --with playwright python scripts/browser_start_smoke.py URL

Synthetic camera/mic only. Checks the actual UI and real /ws2 open + hello, not Google availability.
"""
import json
import time
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

from browser_smoke import page_url, redact


def main():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-fake-ui-for-media-stream",
                                          "--use-fake-device-for-media-stream"])

        def run(name, *, mode=None, google=None, private=False, keyboard=False, viewport=None):
            context = browser.new_context(permissions=["camera", "microphone"],
                                          viewport=viewport or {"width": 1280, "height": 720})
            prefs = {}
            if google is not None:
                prefs['aeye.cloud'] = json.dumps({'google': google})
            if private:
                prefs['aeye.private'] = '1'
            context.add_init_script(f"for (const [k,v] of Object.entries({json.dumps(prefs)})) localStorage.setItem(k,v);")
            context.add_init_script("""(() => {
              const WS = window.WebSocket; window.__calls = [];
              window.WebSocket = class extends WS {
                constructor(...args) { super(...args); window.__calls.push(this); }
              };
            })()""")
            page = context.new_page()
            errors, hello, received = [], [], []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)

            def sent(frame):
                if isinstance(frame, str):
                    msg = json.loads(frame)
                    if msg.get('type') == 'hello':
                        hello.append(msg)

            def socket(ws):
                ws.on('framesent', sent)
                ws.on('framereceived', lambda frame: received.append(json.loads(frame)) if isinstance(frame, str) else None)

            page.on('websocket', socket)
            page.goto(page_url())
            page.wait_for_function("document.getElementById('ring-title').textContent === 'Gemini Live'")
            target = page.locator(f'button[data-pack="{mode}"]' if mode else '#ring-hint')
            started = time.monotonic()
            if keyboard:
                target.focus()
                target.press('Enter')
            else:
                target.click()
            if google is False and mode is None:
                notice = page.locator('#live-start-notice')
                notice.wait_for(state='visible', timeout=3000)
                assert page.locator('#settings').is_visible()
                assert 'Google' in notice.inner_text()
                box = notice.bounding_box()
                assert box and 0 <= box['y'] < page.viewport_size['height']
                assert page.evaluate('window.__calls.length') == 0
                # Consent remains off until the user changes it; retry uses the same start control.
                assert not page.locator('#cloud-google').is_checked()
                page.locator('#cloud-google').check()
                page.locator('#settings-done').click()
                started = time.monotonic()
                page.locator('#ring-hint').click()
            page.wait_for_function("window.__calls.some(ws => ws.url.includes('/ws2') && ws.readyState === WebSocket.OPEN)", timeout=3000)
            elapsed = time.monotonic() - started
            assert elapsed < 3, f'{name}: socket took {elapsed:.3f}s'
            page.wait_for_function("!document.getElementById('call').classList.contains('hidden')")
            assert hello, f'{name}: no hello'
            deadline = started + 3
            while not any(m.get('type') == 'agent_state' for m in received) and time.monotonic() < deadline:
                page.wait_for_timeout(20)
            assert any(m.get('type') == 'agent_state' for m in received), f'{name}: no server session response'
            query = parse_qs(urlsplit(page.evaluate('window.__calls[0].url')).query)
            expected_live = not private and google is not False and mode in (None, 'general', 'gemini-live')
            if google is False and mode is None:
                expected_live = True  # explicitly enabled above
            assert (query.get('mode') == ['gemini-live']) == expected_live
            assert hello[0]['pack'] == ('general' if mode in (None, 'gemini-live') else mode)
            assert bool(hello[0].get('private')) == private
            assert not errors, errors
            results.append(f'{name}: /ws2 open + hello in {elapsed:.3f}s')
            context.close()

        run('default tap')
        run('default keyboard', keyboard=True)
        run('default mobile', viewport={'width': 390, 'height': 844})
        run('Google off: visible Settings notice, then consent + retry', google=False)
        run('Private pipeline', private=True)
        for mode in ['gemini-live', 'general', 'pc_build', 'cooking', 'assemble', 'memory', 'home']:
            run(mode, mode=mode)
        run('General with Google off', mode='general', google=False)
        browser.close()
    print('\n'.join(results))
    print(f'BROWSER START SMOKE PASS ({len(results)} cases; /ws2 within 3 s)')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(redact(str(error)))
        raise SystemExit(1)
