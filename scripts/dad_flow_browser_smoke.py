"""Integrated presenter UI proof; real home TTS + native local note/reply speech.

Run against dad_flow_rehearsal.py --serve using the same A_EYE_TOKEN.
Synthetic browser devices only; no camera frames or audio files are saved.
"""
import base64
import json
import os
import sys
import traceback
from time import monotonic
from urllib.parse import urlencode
from playwright.sync_api import sync_playwright

def safe_exception(kind, value, tb):
    report = ''.join(traceback.format_exception(kind, value, tb))
    token = os.environ.get('A_EYE_TOKEN', '')
    print(report.replace(token, '[redacted]') if token else report, file=sys.stderr)

sys.excepthook = safe_exception
base = sys.argv[1].rstrip('/')
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'])
    context = browser.new_context(permissions=['camera', 'microphone'])
    context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base) else route.abort())
    context.add_init_script("localStorage.setItem('aeye.private', '1');")
    user = context.new_page()
    errors, messages = [], []
    user.on('pageerror', lambda e: errors.append(str(e)))
    user.on('websocket', lambda ws: ws.on('framereceived', lambda frame: messages.append(json.loads(frame))))
    token = os.environ.get('A_EYE_TOKEN', '')
    user.goto(base + '/agent.html?' + urlencode(dict(vad='0', token=token)))
    user.click('button[data-pack="home"]')
    try:
        user.wait_for_function("document.getElementById('guide-presenter').classList.contains('hidden') === false")
    except Exception:
        print('Browser errors:', errors)
        print('Server events:', [(m.get('type'), m.get('stage'), m.get('detail')) for m in messages])
        user.screenshot(path='/private/tmp/dad-flow-browser-error.png')
        raise
    assert not user.is_checked('#guide-test')
    assert user.is_disabled('#guide-pass')
    assert not user.is_visible('#guide-test-label')

    def await_turn(start, expected, detail=None):
        deadline = monotonic() + 45
        while not any(m.get('type') == 'turn' and m.get('stage') == 'done' and m.get('turn_id') != 'presenter'
                      for m in messages[start:]):
            assert monotonic() < deadline, 'turn timeout'
            user.wait_for_timeout(100)
        rows = messages[start:]
        final = [m for m in rows if m.get('type') == 'say' and m.get('final')]
        if expected is None:
            assert not final and any(m.get('detail') == 'Home: complete' for m in rows)
            return
        assert final and expected in final[-1]['text'], expected
        chunks = [m for m in rows if m.get('type') == 'audio' and m['utterance_id'] == final[-1]['utterance_id']]
        assert any(m.get('pcm16_b64') for m in chunks) and any(m.get('final') for m in chunks)
        if detail:
            assert any(m.get('detail') == detail for m in rows), detail
        # Give the real browser PCM player enough time to finish this entire line.
        duration = sum(len(base64.b64decode(m.get('pcm16_b64', ''))) for m in chunks) / 32000
        user.wait_for_timeout((duration + .25) * 1000)

    def typed(text, expected, detail=None):
        start = len(messages)
        user.fill('#typed', text)
        user.locator('#typed').press('Enter')
        await_turn(start, expected, detail)

    def passed(expected):
        start = len(messages)
        user.click('#guide-pass')
        await_turn(start, expected, 'Valve: test closed')
        assert 'TEST MODE' in user.inner_text('#valve-check')
        assert user.is_visible('#guide-test-label')

    await_turn(0, "You can take this one step at a time")
    typed('next', 'Tuesday: shutoff valve under the left cabinet')
    typed('next', 'Gently close')
    typed('next', 'Show me the valve again', 'Valve: unsure')
    assert user.is_visible('#valve-check')
    user.check('#guide-test')
    user.wait_for_function("!document.getElementById('guide-pass').disabled")
    passed('dry supply connection')
    start = len(messages)
    user.click('#guide-stop')
    await_turn(start, 'Stop and move away', 'Stopped by code')
    assert user.is_visible('#proof-safety')
    typed('next', 'Show me the valve again', 'Valve: unsure')
    for expected in ('Only hand-snug', 'dripping tap', 'dry towel', None):
        passed(expected)
    assert user.locator('#family-panel').get_attribute('open') is None
    assert not user.is_visible('#family-sharing')
    user.click('#family-panel summary')
    assert not user.is_checked('#family-complete')
    user.fill('#family-name', 'Fixture Friend')
    user.fill('#family-relation', 'friend')
    user.check('#family-consent')
    user.click('#family-add')
    user.wait_for_function("document.getElementById('family-contact').options.length === 2")
    user.select_option('#family-contact', index=1)
    user.select_option('#family-task', 'sink')
    user.select_option('#family-detail', '2')
    user.check('#family-complete')
    user.wait_for_function("speechSynthesis.getVoices().some(v => v.localService && v.lang.startsWith('en'))", timeout=15000)
    user.click('#family-draft')
    user.wait_for_function("!document.getElementById('family-send').disabled", timeout=60000)
    assert 'cabinet' not in user.inner_text('#family-preview')
    user.click('#family-send')
    user.locator('#family-link').wait_for(state='visible')
    recipient = context.new_page()
    recipient.goto(user.locator('#family-link').get_attribute('href'))
    recipient.click('#recipient-open')
    recipient.locator('#recipient-form').wait_for(state='visible')
    recipient.fill('#recipient-reply', 'Nice work. Check for drips tomorrow.')
    recipient.locator('#recipient-form button').click()
    user.wait_for_function("document.getElementById('family-status').textContent === 'Reply read aloud.'", timeout=60000)
    assert user.inner_text('#proof-pictures') == 'Pictures saved: 0'
    assert user.inner_text('#family-pictures') == 'Pictures sent: 0'
    assert user.inner_text('#proof-cloud-calls') == 'Cloud AI calls: 0'
    user.screenshot(path='/private/tmp/dad-flow-browser.png')
    assert not errors, errors
    print('GROWTH FLOW BROWSER: PASS; sink through reply; native note/reply speech completed; TEST MODE visible; all three counters 0')
    browser.close()
