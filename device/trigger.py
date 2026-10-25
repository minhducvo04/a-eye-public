"""On-device rules: when the device may capture, when it turns itself off, and what counts as a change.

Pure logic with an injectable clock, so the rules are unit tested without a camera or a microphone.
"""
from array import array
from time import monotonic

IDLE_OFF_S = 90          # off after this long with no change (no motion, no speech)
CAP_S = 15 * 60          # hard cap on one capture session


class Power:
    """On/off state of the capture session. Nothing is captured or sent while off."""

    def __init__(self, idle_s=IDLE_OFF_S, cap_s=CAP_S, clock=monotonic):
        self.idle_s, self.cap_s, self.clock = idle_s, cap_s, clock
        self.on = False
        self.reason = None          # why it turned on, or why it last turned off
        self.started = self.last_change = None

    def turn_on(self, reason):
        if self.on:
            return False
        now = self.clock()
        self.on, self.reason, self.started, self.last_change = True, reason, now, now
        return True

    def turn_off(self, reason):
        if not self.on:
            return False
        self.on, self.reason = False, reason
        return True

    def press(self):
        """A button press toggles: on when off, off when on."""
        return self.turn_off('button') if self.on else self.turn_on('button')

    def change(self):
        """Something changed (motion in the picture, the person spoke): restart the idle timer."""
        if self.on:
            self.last_change = self.clock()

    def tick(self):
        """Call about once a second while on. Returns 'cap' or 'idle' when it just turned itself off."""
        if not self.on:
            return None
        now = self.clock()
        if now - self.started >= self.cap_s:
            self.turn_off('cap')
            return 'cap'
        if now - self.last_change >= self.idle_s:
            self.turn_off('idle')
            return 'idle'
        return None

    def remaining_s(self):
        if not self.on:
            return 0.0
        now = self.clock()
        return max(0.0, min(self.cap_s - (now - self.started), self.idle_s - (now - self.last_change)))


class Motion:
    """Cheap change check on a tiny grey thumbnail: a share of pixels moved by more than `pixel_delta` levels.

    Sensor noise and a 2 px handheld jitter on a flat scene stay under the default; a hand or an object put down
    on a fixed surface goes over it. Works on Pillow images; no numpy or OpenCV needed.
    """

    def __init__(self, pixel_delta=25, min_share=0.02, size=(64, 48), zone=None):
        self.pixel_delta, self.min_share, self.size = pixel_delta, min_share, size
        self.previous = None
        self.zone = zone

    def thumb(self, image):
        if self.zone:
            w, h = image.size
            image = image.crop(tuple(int(v * (w if i % 2 == 0 else h)) for i, v in enumerate(self.zone)))
        return image.convert('L').resize(self.size)

    def share(self, image):
        """Share 0..1 of thumbnail pixels that changed since the last call (0 on the first call)."""
        current = self.thumb(image).tobytes()
        previous, self.previous = self.previous, current
        if previous is None:
            return 0.0
        moved = sum(1 for a, b in zip(previous, current) if abs(a - b) > self.pixel_delta)
        return moved / len(current)

    def changed(self, image):
        return self.share(image) >= self.min_share

    def reset(self):
        self.previous = None


class Speech:
    """Energy voice activity on 16 kHz PCM16 chunks: start, stream, end after a short silence.

    feed(chunk) returns a list of events: ('start', preroll_bytes), ('chunk', bytes), ('end', b'').
    The pre-roll keeps the first syllable that arrived before the level crossed the threshold.
    """

    RATE = 16000

    def __init__(self, threshold=500, hang_s=0.7, preroll_s=0.3, max_s=30.0):
        self.threshold, self.hang_s, self.preroll_s, self.max_s = threshold, hang_s, preroll_s, max_s
        self.active = False
        self.preroll = bytearray()
        self.quiet_s = self.spoken_s = 0.0

    @staticmethod
    def level(chunk):
        samples = array('h', chunk[:len(chunk) - len(chunk) % 2])
        return (sum(x * x for x in samples) / len(samples)) ** 0.5 if samples else 0.0

    def feed(self, chunk):
        seconds = len(chunk) / 2 / self.RATE
        loud = self.level(chunk) >= self.threshold
        if not self.active:
            self.preroll += chunk
            keep = int(self.preroll_s * self.RATE) * 2
            del self.preroll[:max(0, len(self.preroll) - keep)]
            if not loud:
                return []
            self.active, self.quiet_s, self.spoken_s = True, 0.0, seconds
            pre, self.preroll = bytes(self.preroll), bytearray()
            return [('start', pre)]
        self.spoken_s += seconds
        self.quiet_s = 0.0 if loud else self.quiet_s + seconds
        if self.quiet_s >= self.hang_s or self.spoken_s >= self.max_s:
            self.active = False
            return [('chunk', chunk), ('end', b'')]
        return [('chunk', chunk)]

    def reset(self):
        self.active = False
        self.preroll = bytearray()
