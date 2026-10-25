"""Headless client: explicit Session calls plus RAM-only private motion Events."""
import asyncio
import base64
import json
import secrets
import time

from PIL import ImageFilter, ImageStat

from device import hw
from device.capture import Capture
from device.trigger import Motion, Power, Speech

try:
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed
except ImportError:  # websockets < 13
    from websockets import connect  # type: ignore
    from websockets.exceptions import ConnectionClosed  # type: ignore


def sharpness(image):
    """Edge variance: higher is sharper. Picks the best of a short burst (motion blur on a worn camera)."""
    img = image.convert('L')
    img.thumbnail((320, 320))
    return ImageStat.Stat(img.filter(ImageFilter.FIND_EDGES)).var[0]


class Device:
    def __init__(self, cfg, camera=None, mic=None, speaker=None, indicator=None, person=None, log=None):
        self.cfg = cfg
        t = cfg['trigger']
        self.power = Power(idle_s=t['idle_off_s'], cap_s=t['cap_s'])
        self.camera = camera or hw.make_camera(cfg)
        self.mic = mic or hw.make_mic(cfg)
        self.speaker = speaker or hw.make_speaker(cfg)
        self.indicator = indicator or hw.make_indicator(cfg)
        self.person = person if person is not None else (hw.PersonFilter() if t['drop_person_frames'] else None)
        self.events = asyncio.Queue()
        self.log = log or (lambda kind, **kw: print(json.dumps(dict(t=round(time.time(), 2), event=kind, **kw)),
                                                    flush=True))
        self.stats = dict(calls=0, frames_sent=0, frames_dropped_person=0, utterances=0, audio_in_bytes=0,
                          audio_out_bytes=0, replies=[], turns=[], off_reasons=[], find_items=[], errors=[])
        self.stop = asyncio.Event()
        self.capture = Capture(self)
        self.unauthorized = False

    # ---- top level ----------------------------------------------------------------------------------------------

    async def run(self, presses=None):
        """Wait for triggers, run one call per on-period. `presses` is an optional coroutine feeding self.events."""
        self.indicator.set(False)
        if self.person is None or not self.person.available:
            self.log('person_filter', available=False, note='Detector disabled/unavailable; Event and Session refuse frames')
        feeders = [asyncio.create_task(presses)] if presses else []
        try:
            while not self.stop.is_set():
                if self.cfg['trigger']['motion'] and not getattr(self, '_armed_once', False):
                    self._armed_once = True
                    await self.capture.run()
                getter = asyncio.create_task(self.events.get())
                stopper = asyncio.create_task(self.stop.wait())
                await asyncio.wait({getter, stopper}, return_when=asyncio.FIRST_COMPLETED)
                stopper.cancel()
                await asyncio.gather(stopper, return_exceptions=True)
                if not getter.done():
                    getter.cancel()
                    break
                kind, source = getter.result()
                if kind in ('arm', 'remember'):
                    await self.capture.run(immediate=kind == 'remember')
                if kind in ('press', 'session') and self.power.turn_on(source):
                    await self.call()
                if self.unauthorized:
                    break
        finally:
            for task in feeders:
                task.cancel()
            await asyncio.gather(*feeders, return_exceptions=True)
            self.speaker.close()

    # ---- one on-period ------------------------------------------------------------------------------------------

    async def call(self):
        self.stats['calls'] += 1
        self.capture.signal('Session')
        self.log('on', reason=self.power.reason)
        backoff = 1
        try:
            while self.power.on and not self.stop.is_set():
                try:
                    await self.session()
                    break
                except ConnectionClosed as exc:
                    code = getattr(getattr(exc, 'rcvd', None), 'code', None)
                    if code == 4401:
                        self.unauthorized = True
                        self.log('unauthorized', note='check A_EYE_TOKEN or the Access service token')
                        self.power.turn_off('unauthorized')
                        break
                    self.log('disconnected', code=code)
                except OSError as exc:
                    self.log('connect_failed', error=type(exc).__name__)
                if self.power.on:
                    await asyncio.sleep(backoff)
                    backoff = min(30, backoff * 2)
        finally:
            self.power.turn_off(self.power.reason if not self.power.on else 'stopped')
            self.stats['off_reasons'].append(self.power.reason)
            self.capture.signal('Off')
            self.log('off', reason=self.power.reason)

    async def session(self):
        cfg = self.cfg
        self.sid = 'dev-' + secrets.token_hex(4)
        hello = dict(v=2, type='hello', session_id=self.sid, pack=cfg['server']['pack'], tts='server', stt='server',
                     camera=True, **cfg.hello_privacy())
        if cfg['server']['voice']:
            hello['voice'] = cfg['server']['voice']
        async with connect(cfg['server']['url'], additional_headers=cfg.headers(), max_size=8 * 2 ** 20,
                           open_timeout=10) as ws:
            self.ws = ws
            await ws.send(json.dumps(hello))
            while True:   # no hello-ack: wait for listening
                msg = json.loads(await asyncio.wait_for(ws.recv(), 15))
                if msg.get('type') == 'agent_state' and msg.get('state') == 'listening':
                    break
            self.log('listening', private=hello.get('private', False))
            tasks = [asyncio.create_task(c) for c in (self.receive(ws), self.send_frames(ws), self.send_mic(ws),
                                                      self.button_off())]
            try:
                while self.power.on and not self.stop.is_set():
                    if self.power.tick():
                        break
                    done = [t for t in tasks if t.done()]
                    if done and not self.power.on:
                        break
                    if done:
                        done[0].result()        # raise ConnectionClosed into call()
                        break
                    await asyncio.sleep(0.25)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.speaker.stop()
            await ws.close(1000)

    async def button_off(self):
        """While on, a press turns the device off."""
        while True:
            kind, _ = await self.events.get()
            if kind == 'remember':
                self.power.turn_off('remember')
                self.events.put_nowait(('remember', 'command'))
                return
            if kind == 'press':
                self.power.press()
                return

    async def send_frames(self, ws):
        c = self.cfg['camera']
        period = 1.0 / max(0.1, float(c['fps']))
        change = Motion(min_share=self.cfg['trigger']['motion_share'])
        seq = 0
        try:
            self.camera.open()
            first = True
            while True:
                started = time.monotonic()
                shots = []
                for _ in range(3 if first else 1):          # a short burst on turn-on; keep the sharpest
                    img = await asyncio.to_thread(self.camera.read)
                    if img is not None:
                        shots.append(img)
                first = False
                if shots:
                    img = max(shots, key=sharpness) if len(shots) > 1 else shots[0]
                    if change.changed(img):
                        self.power.change()
                    if await asyncio.to_thread(hw.blocks_frame, self.person, img):
                        self.stats['frames_dropped_person'] += 1
                    else:
                        jpeg, w, h = await asyncio.to_thread(hw.encode_jpeg, img, c['long_side'], c['quality'])
                        seq += 1
                        await ws.send(json.dumps(dict(v=2, type='frame', session_id=self.sid, seq=seq,
                                                      ts_ms=int(time.time() * 1000), w=w, h=h,
                                                      jpeg_b64=base64.b64encode(jpeg).decode())))
                        self.stats['frames_sent'] += 1
                await asyncio.sleep(max(0.0, period - (time.monotonic() - started)))
        finally:
            self.camera.close()

    async def send_mic(self, ws):
        a = self.cfg['audio']
        vad = Speech(threshold=a['threshold'])
        seq = 0
        self.mic.open()
        try:
            async for chunk in self.mic.chunks():
                playing = self.speaker.playing()
                if playing and not a['barge_in']:
                    vad.reset()            # half duplex: the speaker would trigger our own mic
                    continue
                for kind, data in vad.feed(chunk):
                    if kind == 'start':
                        self.power.change()
                        self.stats['utterances'] += 1
                        if playing:        # barge-in: stop locally first, then tell the server
                            self.speaker.stop()
                            await ws.send(json.dumps(dict(v=2, type='barge_in', session_id=self.sid,
                                                          utterance_id=playing[0], at_ms=playing[1])))
                        self.log('speech_start')
                    if kind in ('start', 'chunk') and data:
                        seq += 1
                        await ws.send(json.dumps(dict(v=2, type='audio_in', session_id=self.sid, seq=seq,
                                                      sample_rate=16000, pcm16_b64=base64.b64encode(data).decode())))
                        self.stats['audio_in_bytes'] += len(data)
                    if kind == 'end':
                        seq += 1
                        await ws.send(json.dumps(dict(v=2, type='audio_in', session_id=self.sid, seq=seq,
                                                      sample_rate=16000, pcm16_b64='', final=True)))
                        self.log('speech_end')
        finally:
            self.mic.close()

    async def receive(self, ws):
        async for raw in ws:
            msg = json.loads(raw)
            kind = msg.get('type')
            if kind == 'audio':
                pcm = base64.b64decode(msg.get('pcm16_b64') or '')
                if pcm:
                    self.speaker.play(msg['utterance_id'], pcm)
                    self.stats['audio_out_bytes'] += len(pcm)
                    self.power.change()       # a reply is still coming: not idle
                if msg.get('final'):
                    self.speaker.finish(msg['utterance_id'])
            elif kind == 'say':
                self.stats['replies'].append(msg.get('text', ''))
                self.log('say', text=msg.get('text', ''))
            elif kind == 'turn':
                self.stats['turns'].append(dict(stage=msg['stage'], text=msg.get('text'), at=time.time()))
                self.log('turn', stage=msg['stage'], **({'text': msg['text']} if msg.get('text') else {}))
            elif kind == 'find_item':
                self.stats['find_items'].append({k: msg.get(k) for k in ('item', 'found', 'place', 'home', 'when')})
                self.log('find_item', item=msg.get('item'), found=msg.get('found'))
            elif kind == 'error':
                self.stats['errors'].append(msg.get('code'))
                self.log('server_error', code=msg.get('code'))
