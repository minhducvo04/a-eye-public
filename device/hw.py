"""Hardware behind small interfaces, each with a fake for tests.

Real parts are optional imports: picamera2 or OpenCV for the camera, sounddevice for the mic and speaker, gpiozero
for the button and LED. Nothing here opens a camera or a microphone until the device is turned on.
"""
import asyncio
import glob
import os
import sys
import time
import wave
from pathlib import Path

from PIL import Image

RATE = 16000
CHUNK = 3200                      # 100 ms of 16 kHz mono PCM16


# ---- camera -----------------------------------------------------------------------------------------------------

class ImageSequenceCamera:
    """Fake camera: a folder (or glob) of images played as a clip at `clip_fps`, holding the last image."""

    def __init__(self, path, clip_fps=1.0, loop=False):
        pattern = os.path.join(path, '*') if os.path.isdir(path) else path
        self.paths = sorted(p for p in glob.glob(pattern) if p.lower().endswith(('.jpg', '.jpeg', '.png')))
        if not self.paths:
            raise ValueError(f'no images at {path}')
        self.clip_fps, self.loop, self.t0 = clip_fps, loop, None

    def open(self):
        self.t0 = time.monotonic()

    def open_low(self):
        self.open()

    def read(self):
        i = int((time.monotonic() - self.t0) * self.clip_fps)
        i = i % len(self.paths) if self.loop else min(i, len(self.paths) - 1)
        with Image.open(self.paths[i]) as img:
            return img.convert('RGB')

    def close(self):
        self.t0 = None


class OpenCVCamera:
    """USB webcam or a video file through OpenCV (pip install opencv-python-headless)."""

    def __init__(self, source=0):
        self.source, self.cap = source, None

    def open(self):
        import cv2
        self.cap = cv2.VideoCapture(self.source)
        if not self.cap.isOpened():
            raise RuntimeError(f'camera {self.source!r} did not open')

    def open_low(self):
        import cv2
        self.open()
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 64)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 48)
        self.cap.set(cv2.CAP_PROP_FPS, 1)

    def read(self):
        import cv2
        ok, frame = self.cap.read()
        if not ok:
            return None
        return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class PiCamera:
    """Raspberry Pi camera module through picamera2 (apt install python3-picamera2)."""

    def __init__(self):
        self.cam = None

    def open_low(self):
        self.open(size=(64, 48), fps=1)

    def open(self, size=(640, 480), fps=1):
        from picamera2 import Picamera2
        self.cam = Picamera2()
        self.cam.configure(self.cam.create_still_configuration(main={'size': size, 'format': 'RGB888'},
                                                              controls={'FrameRate': fps}))
        self.cam.start()

    def read(self):
        return Image.fromarray(self.cam.capture_array()[:, :, ::-1])   # RGB888 arrives as BGR in memory

    def close(self):
        if self.cam is not None:
            self.cam.stop()
            self.cam.close()
            self.cam = None


def make_camera(cfg):
    c = cfg['camera']
    source = c['source']
    if source == 'images':
        return ImageSequenceCamera(c['path'], clip_fps=c['fps'])
    if source == 'video':
        return OpenCVCamera(c['path'])
    if source == 'opencv':
        return OpenCVCamera(c['index'])
    if source in ('picamera', 'auto'):
        try:
            import picamera2  # noqa: F401
            return PiCamera()
        except ImportError:
            if source == 'picamera':
                raise
        return OpenCVCamera(c['index'])
    raise ValueError(f'unknown camera.source {source!r}')


def encode_jpeg(image, long_side=640, quality=70):
    """Upright RGB JPEG, long side at most `long_side`. Returns (bytes, w, h)."""
    import io
    img = image.convert('RGB')
    img.thumbnail((long_side, long_side))
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=quality)
    return buf.getvalue(), img.width, img.height


class PersonFilter:
    """Drops frames that show a person, on the device, before anything is sent.

    Uses OpenCV's built-in HOG people detector when OpenCV is installed (no model download). Without OpenCV it
    reports available=False and refuses every frame; errors also refuse transmission.
    """

    def __init__(self):
        try:
            import cv2
            self.cv2 = cv2
            self.hog = cv2.HOGDescriptor()
            self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            self.available = True
        except Exception:
            self.cv2 = self.hog = None
            self.available = False

    def has_person(self, image):
        if not self.available:
            return True
        try:
            import numpy as np
            with image.convert('L') as img:
                img.thumbnail((400, 400))
                if img.width < 64 or img.height < 128:
                    return True
                rects, weights = self.hog.detectMultiScale(np.asarray(img), winStride=(8, 8),
                                                          padding=(8, 8), scale=1.05)
            return any(float(w) > 0.5 for w in (weights.ravel() if len(rects) else []))
        except Exception:
            return True


def blocks_frame(person, image):
    """Only an available detector's explicit negative result permits transmission."""
    try:
        return person is None or not person.available or person.has_person(image) is not False
    except Exception:
        return True


# ---- microphone -------------------------------------------------------------------------------------------------

def read_wav_16k(path):
    """Mono 16-bit 16 kHz PCM from a WAV file (convert with: afconvert -f WAVE -d LEI16@16000 -c 1 in out)."""
    with wave.open(str(path)) as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != RATE:
            raise ValueError(f'{path}: need mono 16-bit 16 kHz WAV')
        return w.readframes(w.getnframes())


class WavMic:
    """Fake mic: silence, with each (at_s, wav) clip starting `at_s` seconds after the mic opens. Real-time paced."""

    def __init__(self, script):
        self.script = [(float(at), read_wav_16k(path)) for at, path in script]
        self.open_at = None

    def open(self):
        self.open_at = time.monotonic()

    def _pcm_at(self, start, n):
        out = bytearray(n)
        for at, pcm in self.script:
            offset = int(at * RATE) * 2
            lo, hi = max(start, offset), min(start + n, offset + len(pcm))
            if lo < hi:
                out[lo - start:hi - start] = pcm[lo - offset:hi - offset]
        return bytes(out)

    async def chunks(self):
        sent = 0
        while self.open_at is not None:
            due = self.open_at + (sent + CHUNK) / 2 / RATE
            await asyncio.sleep(max(0.0, due - time.monotonic()))
            yield self._pcm_at(sent, CHUNK)
            sent += CHUNK

    def close(self):
        self.open_at = None


class SoundDeviceMic:
    """Real mic through sounddevice (pip install sounddevice; apt install libportaudio2 on a Pi)."""

    def __init__(self):
        self.stream = self.queue = None

    def open(self):
        import sounddevice as sd
        loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()

        def callback(data, frames, when, status):
            loop.call_soon_threadsafe(self.queue.put_nowait, bytes(data))

        self.stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype='int16', blocksize=CHUNK // 2,
                                        callback=callback)
        self.stream.start()

    async def chunks(self):
        while self.stream is not None:
            yield await self.queue.get()

    def close(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None


def make_mic(cfg, script=None):
    kind = cfg['audio']['mic']
    if kind == 'wav' or script:
        return WavMic(script or [])
    if kind in ('sounddevice', 'auto'):
        return SoundDeviceMic()
    if kind == 'none':
        return WavMic([])
    raise ValueError(f'unknown audio.mic {kind!r}')


# ---- speaker ----------------------------------------------------------------------------------------------------

class NullSpeaker:
    """Fake speaker: keeps each reply utterance in memory (and writes a WAV if `record_dir` is set).

    It pretends to play in real time so barge-in and half-duplex muting behave as on a real speaker.
    """

    def __init__(self, record_dir=''):
        self.record_dir = record_dir
        self.utterances = {}             # utterance_id -> bytearray
        self.order = []
        self.current = None
        self.play_until = 0.0
        self.started_at = 0.0

    def play(self, utterance_id, pcm):
        now = time.monotonic()
        if utterance_id not in self.utterances:
            self.utterances[utterance_id] = bytearray()
            self.order.append(utterance_id)
        self.utterances[utterance_id] += pcm
        if self.current != utterance_id:
            self.current, self.started_at = utterance_id, max(now, self.play_until)
        self.play_until = max(now, self.play_until) + len(pcm) / 2 / RATE

    def finish(self, utterance_id):
        if self.record_dir and utterance_id in self.utterances:
            Path(self.record_dir).mkdir(parents=True, exist_ok=True)
            with wave.open(str(Path(self.record_dir) / f'{utterance_id}.wav'), 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(bytes(self.utterances[utterance_id]))

    def playing(self):
        """(utterance_id, elapsed_ms) while sound is coming out, else None."""
        now = time.monotonic()
        if self.current is None or now >= self.play_until:
            return None
        return self.current, int(max(0.0, now - self.started_at) * 1000)

    def stop(self):
        self.play_until = 0.0
        self.current = None

    def total_bytes(self):
        return sum(len(v) for v in self.utterances.values())

    def close(self):
        pass


class SoundDeviceSpeaker(NullSpeaker):
    """Real speaker through sounddevice: one output stream, a byte queue fed by reply audio."""

    def __init__(self, record_dir=''):
        super().__init__(record_dir)
        self.buffer = bytearray()
        self.stream = None

    def _ensure(self):
        if self.stream is None:
            import sounddevice as sd

            def callback(out, frames, when, status):
                n = len(out)
                take = bytes(self.buffer[:n])
                del self.buffer[:n]
                out[:] = take + b'\x00' * (n - len(take))

            self.stream = sd.RawOutputStream(samplerate=RATE, channels=1, dtype='int16', callback=callback)
            self.stream.start()

    def play(self, utterance_id, pcm):
        self._ensure()
        super().play(utterance_id, pcm)
        self.utterances[utterance_id] = bytearray()   # do not keep real replies in memory
        self.buffer += pcm

    def stop(self):
        super().stop()
        self.buffer.clear()

    def close(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None


def make_speaker(cfg):
    kind = cfg['audio']['speaker']
    if kind == 'null':
        return NullSpeaker(cfg['audio']['record_dir'])
    if kind in ('sounddevice', 'auto'):
        try:
            import sounddevice  # noqa: F401
            return SoundDeviceSpeaker()
        except (ImportError, OSError):
            if kind == 'sounddevice':
                raise
            return NullSpeaker()
    raise ValueError(f'unknown audio.speaker {kind!r}')


# ---- indicator and button ---------------------------------------------------------------------------------------

class TerminalIndicator:
    def __init__(self, stream=None):
        self.stream = stream or sys.stderr
        self.state = None

    def set(self, on, detail=''):
        self.state = ('Session' if on else 'Off') if isinstance(on, bool) else on
        print(f'[{self.state}] {detail}', file=self.stream, flush=True)


class GpioLed(TerminalIndicator):
    def __init__(self, pin):
        super().__init__()
        from gpiozero import PWMLED
        self.led = PWMLED(pin)

    def set(self, on, detail=''):
        super().set(on, detail)
        self.led.source = None
        if self.state == 'Off':
            self.led.off()
        elif self.state == 'Armed':
            import itertools
            import math
            self.led.value = 0.03
            self.led.source_delay = 0.1
            self.led.source = itertools.cycle([0.03 + 0.12 * (1 - math.cos(i * math.pi / 20)) / 2
                                               for i in range(40)])
        elif self.state == 'Event':
            self.led.value = 0.6
        elif self.state == 'Session':
            self.led.value = 1


def make_indicator(cfg):
    if cfg['indicator']['kind'] == 'gpio':
        return GpioLed(cfg['indicator']['led_pin'])
    return TerminalIndicator()


async def keyboard_presses(queue):
    """Mac testing: press Enter in the terminal to toggle."""
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            return
        command = line.strip().lower()
        kind = {'arm': 'arm', 'remember this': 'remember', 'session': 'session'}.get(command, 'press')
        queue.put_nowait((kind, 'keyboard'))


async def gpio_presses(queue, pin, remember_pin=22):
    """A push button between `pin` and ground (internal pull-up)."""
    from gpiozero import Button
    loop = asyncio.get_running_loop()
    button = Button(pin, bounce_time=0.05)
    remember = Button(remember_pin, bounce_time=0.05)
    remember.when_pressed = lambda: loop.call_soon_threadsafe(queue.put_nowait, ('remember', 'gpio'))
    button.when_pressed = lambda: loop.call_soon_threadsafe(queue.put_nowait, ('press', 'gpio'))
    await asyncio.Event().wait()


async def scripted_presses(queue, times):
    """Tests: press at these seconds after start."""
    start = time.monotonic()
    for at in times:
        await asyncio.sleep(max(0.0, start + at - time.monotonic()))
        queue.put_nowait(('press', 'script'))
