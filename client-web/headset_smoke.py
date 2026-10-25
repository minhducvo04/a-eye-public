"""Synthetic Chromium headset + phone integration. Run against a local mock server.
uv run --no-project --with playwright python client-web/headset_smoke.py http://127.0.0.1:18741
"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

base = sys.argv[1].rstrip('/')
ua = 'Mozilla/5.0 (Apple Vision Pro; CPU visionOS 2_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15'
out = Path('docs/results/headset-web')
out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'])
    ctx = browser.new_context(user_agent=ua, permissions=['camera', 'microphone'], viewport={'width': 1440, 'height': 1000})
    ctx.add_init_script('''
      window.mediaRequests = [];
      const gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = c => { window.mediaRequests.push(c); return gum(c); };
    ''')
    page = ctx.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(base + '/agent.html?stt=server')  # UA alone must activate headset mode
    expect(page.locator('body')).to_have_class('headset')
    page.click('button[data-pack="general"]')
    expect(page.locator('#headset-status')).to_have_text('Waiting for your phone camera')
    page.click('#headset-sound')
    expect(page.locator('#headset-sound')).to_have_text('Sound enabled')
    page.wait_for_timeout(700)
    page.screenshot(path=str(out / 'waiting.png'))
    # Exercise hidden camera and retry handlers too; these must never request video.
    page.evaluate("document.getElementById('start-camera').click(); document.getElementById('permission-retry').click()")
    page.click('#headset-pair')
    expect(page.locator('#pair-qr')).to_be_visible()
    expect(page.locator('#pair-link')).to_have_attribute('href', __import__('re').compile('pair='))
    phone_url = page.locator('#pair-link').get_attribute('href')
    page.click('#settings-done')
    page.click('#mic')
    expect(page.locator('#mic')).to_have_attribute('aria-pressed', 'true')
    phone_ctx = browser.new_context(permissions=['camera', 'microphone'])
    phone = phone_ctx.new_page()
    sent = []
    phone.on('websocket', lambda ws: ws.on('framesent', lambda f: sent.append(json.loads(f))))
    phone.goto(phone_url)
    phone.get_by_role('button', name='Start', exact=True).click()
    expect(page.locator('#headset-status')).to_have_text('Phone camera connected')
    expect(page.locator('#mic')).to_be_disabled()
    expect(page.locator('#mic')).to_have_attribute('aria-pressed', 'false')
    page.wait_for_timeout(1800)
    assert any(m['type'] == 'frame' for m in sent), 'Phone did not send frames'
    requests = len(page.evaluate('window.mediaRequests'))
    page.locator('#call .open-settings').click()
    page.evaluate("document.getElementById('devices-allow').click()")
    page.wait_for_timeout(500)
    assert len(page.evaluate('window.mediaRequests')) == requests, 'Headset opened mic while phone paired'
    page.click('#settings-done')
    page.locator('#typed').fill('What do you see?')
    page.locator('#typed').press('Enter')
    page.wait_for_timeout(1800)
    page.screenshot(path=str(out / 'connected.png'))
    page.click('#phone-kick')
    expect(page.locator('#headset-status')).to_have_text('Waiting for your phone camera')
    expect(page.locator('#mic')).to_have_attribute('aria-pressed', 'true')
    page.click('#end-call')
    page.click('button[data-pack="general"]')
    expect(page.locator('#headset-status')).to_have_text('Waiting for your phone camera')
    assert not any(c.get('video') for c in page.evaluate('window.mediaRequests'))
    assert not errors, errors
    # Reliable override works even with a desktop UA, including an accidental phone-role URL.
    desktop = browser.new_context()
    override = desktop.new_page()
    override.goto(base + '/agent.html?device=headset&pair=123456')
    expect(override.locator('body')).to_have_class('headset')
    expect(override.locator('#picker')).to_be_visible()
    print('HEADSET SMOKE PASS: UA + flag; no local video; QR; real fake-phone join/frames; mic handoff, settings, kick, second call; no page errors')
    browser.close()
