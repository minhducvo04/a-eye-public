"""One spoken target, grounded locally on a fresh frame; failure means no pointer."""
import asyncio
import math
import re
from time import monotonic

from contracts import validate

BUDGET_S = 1.5
TTL_MS = 2500
RULES = (' When your answer names a visible object, finish with one silent [POINT: object phrase] tag. '
         'Copy the object phrase exactly from your spoken answer, including colour or side; use words, not an ID. '
         'Example: SAY: Use the blue mug on the left. [POINT: blue mug on the left] '
         'At most one tag, at most 10 words inside, no list or punctuation inside the tag. '
         'Omit the tag only if unsure, the object is absent, or the answer is not about a visible object. '
         'Never claim to be pointing: the server may choose not to draw. The tag is metadata, never speech. '
         'In General mode leave JSON draw empty; the server grounds the tag.')


class TargetTag:
    """Filter before sentence splitting, including tags split across arbitrary provider chunks.

    Unterminated and oversized tags stay silent. A second tag invalidates the whole target.
    Other stream metadata (JSON headers and [EXPERT]) passes through unchanged.
    """
    def __init__(self):
        self.pending = ''
        self.inside = False
        self.count = 0
        self.name = None

    def feed(self, delta):
        out = []
        for char in delta:
            if self.inside:
                if char == ']':
                    match = re.fullmatch(r'\s*:\s*([\w -]{1,80})\s*', self.pending)
                    name = match[1].strip() if match else ''
                    if (self.count == 1 and name and len(name.split()) <= 10
                            and not re.search(r'\b(and|or)\b', name, re.I)):
                        self.name = name
                    self.pending, self.inside = '', False
                elif len(self.pending) <= 96:
                    self.pending += char
                continue
            if self.pending or char == '[':
                self.pending += char
                if self.pending.upper() == '[POINT':
                    self.inside = True
                    self.count += 1
                    self.name = None
                    self.pending = ''
                elif not '[POINT'.startswith(self.pending.upper()):
                    out.append(self.pending)
                    self.pending = ''
            else:
                out.append(char)
        return ''.join(out)

    def target(self, spoken):
        if self.inside or self.pending or self.count != 1 or not self.name:
            return None
        # A model cannot silently pick something it never actually said.
        phrase = lambda s: ' '.join(re.findall(r'\w+', s.casefold()))
        return self.name if f' {phrase(self.name)} ' in f' {phrase(spoken)} ' else None


def visible(name, caption):
    # Grounding can invent a box for an absent object. Require its noun in an independent
    # caption. Captions often say "colorful mugs"; grounding resolves colour/side qualifiers.
    if re.search(r"\b(no|not|hidden|missing|absent|unclear|blurr?\w*|glare|cannot|can't|couldn't)\b", caption, re.I):
        return False
    noun_phrase = re.split(r'\b(on|in|at|near|beside|under|to)\b', name.casefold())[0]
    words = re.findall(r'\w+', noun_phrase)
    noun = words[-1].removesuffix('s') if words else ''
    return bool(noun) and noun in {w.removesuffix('s') for w in re.findall(r'\w+', caption.casefold())}


async def ground(session, vision, name, *, allow_cloud=False):
    """Return a validated draw message or None. Never save or log frame content.

    The existing /scene endpoint is our own vision service. Reject its auto/fake fallback.
    The deadline covers grounding AND an independent caption when the cache is not this frame.
    """
    frame, observed_at = session.frame, session.frame_time
    if not name or not session.camera or not frame or monotonic() - observed_at >= BUDGET_S:
        return None
    cached = session.scene if session.scene_seq == frame['seq'] else None
    caption = cached.get('caption') if cached and cached.get('source') == 'ollama' else None
    if caption is not None and not visible(name, caption):
        return None
    started = monotonic()
    try:
        remaining = min(BUDGET_S, BUDGET_S - (started - observed_at))
        async with asyncio.timeout(remaining):
            scene = await vision.call('scene', dict(image_b64=frame['jpeg_b64'], queries=[name],
                                                   max_objects=1, caption=caption is None))
        validate('scene_response', scene)
        if (monotonic() - started >= BUDGET_S or monotonic() - observed_at >= BUDGET_S
                or not session.camera or not (scene.get('source') == 'ollama'
                    or (allow_cloud and scene.get('source') is None))
                or not visible(name, caption if caption is not None else scene['caption'])):
            return None
        objects = scene['objects']
        if len(objects) != 1:
            return None
        obj = objects[0]
        x, y, w, h = obj['bbox']
        if (obj['label'].casefold() != name.casefold() or obj['conf'] < .7
                or not all(math.isfinite(value) for value in [x, y, w, h, obj['conf']])
                or w <= 0 or h <= 0 or x + w > 1 or y + h > 1):
            return None
        anchor = dict(object_id=f"pointer-{frame['seq']}", bbox=obj['bbox'])
        common = dict(role='target', ttl_ms=TTL_MS, anchor=anchor)
        result = dict(v=2, type='draw', agent='helper', seq=frame['seq'], ops=[
            dict(kind='clear', id='*'),
            dict(kind='box', id='pointer-box', bbox=obj['bbox'], **common),
            dict(kind='arrow', id='pointer-arrow', **{'from': [.5, .95], 'to': [x + w / 2, y + h / 2]}, **common)])
        validate('v2_draw', result)
        return result
    except Exception:
        # Provider failures, invalid coordinates and timeouts all abstain; cancellation propagates.
        return None
