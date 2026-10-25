"""RAM-only event capture. No microphone, filesystem or transport in Armed."""
import asyncio
import base64
import time
from collections import deque
from urllib.parse import urlsplit, urlunsplit

import httpx

from device import hw
from device.trigger import Motion


class Capture:
    def __init__(self, device, clock=time.monotonic):
        self.device, self.clock = device, clock
        self.frames = deque(maxlen=11)
        self.state = 'Off'
        self.events = 0
        self.deadline = None

    def expired(self):
        return self.deadline is not None and self.clock() >= self.deadline

    def signal(self, state):
        self.state = state
        self.device.indicator.set(state)

    def clear(self):
        for _, frame in self.frames:
            frame.close()
        self.frames.clear()

    async def read(self):
        # A slow camera must not prevent the cap from closing it. Release a late frame without sending it.
        pending = asyncio.get_running_loop().run_in_executor(None, self.device.camera.read)

        def discard(future):
            if future.cancelled():
                return
            try:
                image = future.result()
                if image is not None:
                    image.close()
            except Exception:
                pass

        try:
            remaining = None if self.deadline is None else max(0, self.deadline - self.clock())
            return await asyncio.wait_for(asyncio.shield(pending), remaining)
        except BaseException:
            pending.add_done_callback(discard)
            raise

    async def blocks_frame(self, image):
        # The worker owns its copy even if the Event times out and clears the original buffer.
        copy = image.copy()

        def inspect():
            with copy:
                return hw.blocks_frame(self.device.person, copy)

        return await asyncio.shield(asyncio.to_thread(inspect))

    async def send(self, frames):
        url = urlsplit(self.device.cfg['server']['url'])
        target = urlunsplit(('https' if url.scheme == 'wss' else 'http', url.netloc,
                             '/api/memory/event', '', ''))
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.post(target, headers=self.device.cfg.headers(), json={'frames': frames})
            response.raise_for_status()

    async def send_until_off(self, payload):
        sender = asyncio.create_task(self.send(payload))
        stopper = asyncio.create_task(self.device.stop.wait())
        button = asyncio.create_task(self.device.events.get())
        tasks = (sender, stopper, button)
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if button in done:
                kind, source = button.result()
                if kind == 'session':
                    self.device.events.put_nowait((kind, source))
            if stopper in done or button in done:
                return False
            await sender
            return True
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def event(self):
        self.signal('Event')
        payload = []
        try:
            end = self.clock() + 5
            while self.clock() < end and not self.device.stop.is_set() and not self.expired():
                if not self.device.events.empty():
                    kind, source = self.device.events.get_nowait()
                    if kind in ('press', 'session'):
                        if kind == 'session':
                            self.device.events.put_nowait((kind, source))
                        return False
                image = await self.read()
                if image is not None:
                    image.thumbnail((640, 480))
                    self.frames.append((self.clock(), image))
                await asyncio.sleep(1)
            if self.device.stop.is_set() or self.expired():
                return False
            person = self.device.person
            # Unknown detector state is a refusal, never permission to transmit.
            if person is None or not person.available:
                return True
            safe = []
            for _, image in self.frames:
                # Tiny pre-roll cannot support a reliable HOG check: discard it.
                if image.width < 64 or image.height < 128 or await self.blocks_frame(image):
                    self.device.stats['frames_dropped_person'] += 1
                    continue
                safe.append(image)
            for image in safe[::max(1, len(safe) // 3)][:3]:
                jpeg, _, _ = hw.encode_jpeg(image)
                payload.append(base64.b64encode(jpeg).decode())
            if self.expired():
                return False
            if payload:
                if not await self.send_until_off(payload):
                    return False
                self.events += 1
            return True
        finally:
            payload.clear()
            self.clear()

    async def run(self, immediate=False):
        t = self.device.cfg['trigger']
        motion = Motion(min_share=t['motion_share'], zone=t['zone'])
        self.deadline = self.clock() + t['armed_cap_s']
        self.device.mic.close()
        try:
            while not self.device.stop.is_set() and not self.expired():
                self.signal('Armed')
                self.device.camera.open_low()
                trigger = immediate
                immediate = False
                while not trigger and not self.device.stop.is_set():
                    if self.expired():
                        return
                    if not self.device.events.empty():
                        kind, source = self.device.events.get_nowait()
                        if kind in ('press', 'session'):
                            if kind == 'session':
                                self.device.events.put_nowait((kind, source))
                            return
                        trigger = kind == 'remember'
                    image = await self.read()
                    if image is not None:
                        thumb = image.convert('L').resize((64, 48))
                        image.close()
                        now = self.clock()
                        while self.frames and now - self.frames[0][0] >= 5:
                            self.frames.popleft()[1].close()
                        self.frames.append((now, thumb))
                        trigger = trigger or motion.changed(thumb)
                    if not trigger:
                        await asyncio.sleep(min(1, max(0, self.deadline - self.clock())))
                self.device.camera.close()
                if self.device.stop.is_set() or self.expired():
                    return
                self.signal('Event')
                self.device.camera.open()
                remaining = self.deadline - self.clock()
                if remaining <= 0:
                    return
                try:
                    if not await asyncio.wait_for(self.event(), remaining):
                        return
                except asyncio.TimeoutError:
                    return
                self.device.camera.close()
                motion.reset()
        except asyncio.TimeoutError:
            return
        finally:
            self.device.camera.close()
            self.clear()
            self.signal('Off')
            self.deadline = None
