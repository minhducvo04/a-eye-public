"""Home first: four named cloud switches, enforced on every server path. Everything else stays home.

- claude:     "Smarter answers (Claude)": your question and a written scene note, sometimes one small close-up.
- cartesia:   "Natural voice (Cartesia)": only A-eye's reply sentence.
- google:     "Cloud vision (Google)": downscaled camera frames, no local storage.
- twelvelabs: "Video memory search (TwelveLabs)": short silent clips, deleted at call end.

LOCAL_ONLY=1 (whole server) or hello.private (one call) turns all four off. Defaults: Claude on, Cartesia on,
TwelveLabs and Google off. The switches live in a context variable set at hello, so every task the socket starts
afterwards (turns, planner, expert, scene feed, Rewind, voice) inherits them without a server restart. The
value is one dict per call, updated in place, so tasks that are already running see a later change.
"""
import contextvars
import os

CLAUDE, CARTESIA, TWELVELABS = 'claude', 'cartesia', 'twelvelabs'
GOOGLE = 'google'
DEFAULTS = {CLAUDE: True, CARTESIA: True, TWELVELABS: False, GOOGLE: False}
WHAT_LEAVES = {
    GOOGLE: 'Cloud vision (Google): sends downscaled camera frames to Gemini; no local storage',
    CLAUDE: 'Smarter answers (Claude): sends your question and a written scene note, sometimes one small close-up',
    CARTESIA: "Natural voice (Cartesia): sends only A-eye's reply sentence",
    TWELVELABS: 'Video memory search (TwelveLabs): uploads short silent clips; deleted at call end',
}

PRIVATE = contextvars.ContextVar('aeye_private', default=False)
SWITCHES = contextvars.ContextVar('aeye_cloud', default=None)


def server_local_only():
    from server.cloud_meter import BUDGET
    return os.environ.get('LOCAL_ONLY') == '1' or BUDGET.exhausted()


def set_call(cloud=None, private=False, rewind=False):
    """At hello: resolve this call's switches. Returns the dict the server enforces."""
    PRIVATE.set(bool(private))
    switches = dict(DEFAULTS)
    if isinstance(cloud, dict):
        switches.update({k: bool(v) for k, v in cloud.items() if k in DEFAULTS})
    elif rewind:
        switches[TWELVELABS] = True  # older clients: turning Rewind on is the video-search consent
    if private or os.environ.get('LOCAL_ONLY') == '1':
        switches = {k: False for k in DEFAULTS}
    SWITCHES.set(switches)
    return dict(switches)


def update(name, on):
    """Mid-call change (the Rewind toggle). Private mode and LOCAL_ONLY=1 keep everything off."""
    switches = SWITCHES.get()
    if switches is None:
        switches = dict(DEFAULTS)
        SWITCHES.set(switches)
    switches[name] = bool(on) and not PRIVATE.get() and not server_local_only()
    return dict(switches)


def allowed(name):
    """Is this provider allowed for the current call? Outside a call (HTTP routes such as the Rewind recap,
    unit code) there is no per-call switch: only Private/LOCAL_ONLY apply, and Rewind's own per-call state
    (set when its switch was on) decides whether any clip exists to touch."""
    if server_local_only() or PRIVATE.get():
        return False
    switches = SWITCHES.get()
    return True if switches is None else switches[name]


def claude_allowed():
    return allowed(CLAUDE)


def cartesia_allowed():
    return allowed(CARTESIA)


def twelvelabs_allowed():
    return allowed(TWELVELABS)


def local_only():
    """Private mode or LOCAL_ONLY=1: nothing leaves our own machines."""
    return server_local_only() or PRIVATE.get()


def google_allowed():
    # Cloud frames require explicit consent, even outside a WebSocket call.
    switches = SWITCHES.get()
    return not local_only() and switches is not None and switches.get(GOOGLE) is True
