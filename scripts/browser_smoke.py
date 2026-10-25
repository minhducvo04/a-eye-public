"""Browser smoke test for /agent.html: fails on any console error and checks the camera and mic toggles.

Runs a real Chromium (Playwright) with a fake camera and microphone against a running server:

    uv run --with playwright python scripts/browser_smoke.py http://127.0.0.1:8010
    (first time only: uv run --with playwright python -m playwright install chromium)

Checks: no console errors or page errors from load to the end; the pack picker starts a session; Camera on flips
its state and frames start; Mic flips its state; Hold to talk can be pressed; a socket question gets a reply
caption; Settings shows Use my phone first and scrolls with the wheel; no long task over 300 ms after a find card. Exit 0 on pass. Synthetic media only (Chromium's fake devices).
"""
import json
import os
import re
from urllib.parse import urlencode, urlsplit, parse_qs, unquote_plus
import sys

from playwright.sync_api import sync_playwright

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010").rstrip("/")
IGNORED = ("favicon.ico",)


def redact(text):
    # Keep this runner standalone: it is launched directly from scripts/ with optional dependencies.
    def replace(match):
        if unquote_plus(match[2]).lower() in {'token', 'pair', 'join', 'owner'}:
            return match[1] + match[2] + '=[redacted]'
        return match[0]
    text = re.sub(r'([?&])([^=&\s]+)=([^&\s"\']*)', replace, text)
    token = os.environ.get("A_EYE_TOKEN", "")
    return text.replace(token, "[redacted]") if token else text


def page_url():
    token = os.environ.get("A_EYE_TOKEN", "")
    return BASE + "/agent.html" + ("?" + urlencode({"token": token}) if token else "")


def main():
    problems, sent = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--use-fake-ui-for-media-stream",
                                          "--use-fake-device-for-media-stream"])
        context = browser.new_context(permissions=["camera", "microphone"])
        # Long tasks, and a handle on the call socket so the smoke can show a find card without the brain.
        context.add_init_script("""(() => {
          window.__long = [];
          try { new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__long.push([e.startTime, e.duration]); })
            .observe({ entryTypes: ['longtask'] }); } catch (e) {}
          const WS = window.WebSocket; window.__ws = [];
          window.WebSocket = class extends WS { constructor(...a) { super(...a); window.__ws.push(this); } };
        })()""")
        page = context.new_page()
        page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}")
                if m.type == "error" and not any(x in m.text for x in IGNORED) else None)
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        page.on("websocket", lambda ws: ws.on("framesent", lambda f: sent.append(f)))

        page.goto(page_url())
        page.wait_for_timeout(1200)
        # The proof counters now live in the API, not in the call page DOM.
        def proof(session_id=''):
            return page.evaluate("""async (sessionId) => {
              const { ownerQuery } = await import('./call-owner.js');
              const query = new URLSearchParams(location.search);
              if (sessionId) query.set('session_id', sessionId);
              const response = await fetch('/api/proof?' + query + ownerQuery());
              if (!response.ok) throw new Error('Proof request failed');
              return response.json();
            }""", session_id)
        assert isinstance(proof()['pictures_saved'], int)
        page.click('button[data-pack="general"]')
        page.wait_for_timeout(600)
        hello = next(json.loads(f) for f in sent if isinstance(f, str) and json.loads(f).get('type') == 'hello')
        counts = proof(hello['session_id'])
        assert counts['cloud_ai_calls'] == 0 and counts['bytes_to_cloud'] == 0, counts
        if page.locator("#start-overlay:not(.hidden)").count():
            page.click("#start-overlay")
            page.wait_for_timeout(400)

        def pressed(sel):
            return page.get_attribute(sel, "aria-pressed")

        before = pressed("#start-camera")
        page.click("#start-camera")
        page.wait_for_timeout(2500)
        after = pressed("#start-camera")
        msgs = []
        for f in sent:
            try:
                msgs.append(json.loads(f))
            except (TypeError, ValueError):
                pass
        frames = sum(1 for m in msgs if m.get("type") == "frame")
        if before == after:
            problems.append(f"camera toggle did not change state ({before} -> {after})")
        if not any(m.get("type") == "camera" and m.get("on") is True for m in msgs):
            problems.append("no camera on message sent")
        if frames == 0:
            problems.append("no frames sent after Camera on")

        mic_before = pressed("#mic")
        page.click("#mic", timeout=3000)
        page.wait_for_timeout(1200)
        if pressed("#mic") == mic_before:
            problems.append(f"mic toggle did not change state ({mic_before})")

        hold = page.locator("#hold")
        if hold.count():
            box = hold.bounding_box()
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.mouse.down()
            page.wait_for_timeout(600)
            page.mouse.up()

        # The voice-first page no longer has a typed-message input. Exercise the same question contract.
        page.evaluate("""() => window.__ws[window.__ws.length - 1].send(JSON.stringify({
          v: 2, type: 'user_text', text: 'what do you see?', final: true, source: 'typed'
        }))""")
        page.wait_for_function("document.getElementById('replies').textContent.trim().length > 0", timeout=6000)

        # Settings: the phone pairing button is first and in view; the sheet scrolls with the mouse wheel.
        page.locator("#call .open-settings").click()
        page.wait_for_timeout(500)
        pair = page.locator("#pair-request").bounding_box()
        if not pair or pair["y"] + pair["height"] > page.viewport_size["height"]:
            problems.append("Use my phone is not visible when Settings opens")
        # The actual displayed QR/link must join a camera without the master token.
        page.click('#pair-request')
        page.wait_for_function("document.getElementById('pair-link').href.includes('join=')")
        pair_url = page.locator('#pair-link').get_attribute('href')
        pair_query = parse_qs(urlsplit(pair_url).query)
        if 'token' in pair_query or not pair_query.get('join'):
            problems.append('pair link did not use a scoped join secret')
        if not page.locator('#pair-qr').is_visible():
            problems.append('pair QR is hidden')
        phone = context.new_page()
        phone.goto(pair_url)
        phone.get_by_role('button', name='Start', exact=True).click()
        phone.get_by_text('Camera + mic connected', exact=True).wait_for()
        phone.close()
        page.get_by_text('Phone disconnected; using this device’s camera.', exact=True).wait_for()
        if os.environ.get('A_EYE_TOKEN'):
            if page.locator('#caregiver-link').get_attribute('aria-disabled') != 'true':
                problems.append('token call offered unsupported caregiver controls')
            caregiver = context.new_page()
            caregiver.goto(BASE + '/caregiver.html?' + urlencode({'token': os.environ['A_EYE_TOKEN']}))
            caregiver.get_by_text('Caregiver settings require personal sign-in.', exact=False).wait_for()
            if not caregiver.locator('#save').is_disabled():
                problems.append('token caregiver page allowed saving to a different owner')
            caregiver.close()
        sheet = page.locator("#settings").bounding_box()
        page.mouse.move(sheet["x"] + sheet["width"] / 2, sheet["y"] + sheet["height"] / 2)
        page.mouse.wheel(0, 400)
        page.wait_for_timeout(500)
        scrolled = page.evaluate("document.getElementById('settings').scrollTop")
        if scrolled <= 0:
            problems.append("Settings did not scroll with the mouse wheel")
        page.mouse.move(5, 5)  # over the backdrop: the open sheet still scrolls
        page.mouse.wheel(0, -400)
        page.wait_for_timeout(500)
        if page.evaluate("document.getElementById('settings').scrollTop") >= scrolled:
            problems.append("wheel over the backdrop did not scroll Settings")
        page.click("#settings-done")

        # Find card (split view: kept frame, live pane, arrow): no long task over 300 ms in the 6 s after it shows.
        card_at = page.evaluate("""() => {
          const c = document.createElement('canvas'); c.width = 640; c.height = 480;
          const g = c.getContext('2d'); for (let i = 0; i < 400; i++) { g.fillStyle = `hsl(${i % 360},60%,50%)`; g.fillRect((i * 37) % 640, (i * 53) % 480, 40, 30); }
          const frame = c.toDataURL('image/jpeg', 0.8).split(',')[1];
          const msg = { v: 2, type: 'find_item', item: 'keys', t_s: 3, frame_b64: frame, sure: true, found: true,
            objects: [{ label: 'keys', bbox: [0.4, 0.5, 0.15, 0.15] }], live: { object_id: 'o1', bbox: [0.5, 0.5, 0.1, 0.1] } };
          const ws = window.__ws[window.__ws.length - 1];
          ws.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(msg) }));
          return performance.now();
        }""")
        page.wait_for_timeout(6000)
        if not page.locator(".memory-split").count():
            problems.append("find card did not show")
        longest = page.evaluate("(t) => Math.max(0, ...window.__long.filter(([s]) => s >= t).map(([, d]) => d))", card_at)
        print(f"longest task after the find card: {round(longest)} ms")
        if longest > 300:
            problems.append(f"long task of {round(longest)} ms after the find card (limit 300 ms)")
        browser.close()

    print(redact(json.dumps({"frames_sent": frames, "problems": problems}, indent=1)))
    print("BROWSER SMOKE PASS" if not problems else "BROWSER SMOKE FAIL")
    sys.exit(0 if not problems else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(redact(str(error)))
        sys.exit(1)
