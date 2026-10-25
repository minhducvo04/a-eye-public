"""Optional intent routing and session-bound Rewind callbacks. No query logging."""
import re
import os
from time import monotonic

from server import rewind_tools
from server.llm import TOOL_ERROR
from server.logs import event


def rewind_callback(session, emit, *, vision=None, app=None, turn_id=None, speak=None):
    async def on_tool(name, args):
        started = monotonic()
        ok = False
        # Only log known names: model-supplied strings may contain user content.
        known = name in rewind_tools.FIRST_LINE
        try:
            if not known:
                raise ValueError('Unknown tool')
            if speak:
                await speak(rewind_tools.FIRST_LINE[name])
            if name in {t['name'] for t in rewind_tools.MEMORY_TOOLS}:
                result, cards = await rewind_tools.run_memory_tool(name, args, session, app, turn_id=turn_id)
            else:
                result, cards = await rewind_tools.run_tool(name, args, session, vision=vision, app=app,
                                                            turn_id=turn_id)
            for card in cards:
                await emit(card)
            ok = not result.get('is_error', False)
            return result
        except Exception:
            return {'is_error': True, 'say': TOOL_ERROR}
        finally:
            event('tool_call', session.session_id, turn_id=turn_id, dependency=name if known else 'unknown_tool',
                  ms=(monotonic() - started) * 1000, reason={'ok': ok})
    return on_tool


async def route_turn(session, text):
    if os.environ.get('ROUTER', 'off') != 'on':
        return 'unsure'
    try:
        # router/route.py: aroute(text, recent_turns) -> {label, confidence, ...}; never raises by contract.
        from router.route import aroute
        result = await aroute(text, session.history[-10:])
        event('route', session.session_id, reason={'label': result.get('label')}, ms=result.get('ms'))
        return result['label'] if result.get('label') in ('past', 'expert', 'now', 'unsure') else 'unsure'
    except Exception:
        event('dependency_error', session.session_id, dependency='router', level='error')
        return 'unsure'


RECAP = re.compile(r"\b(recap|summar\w*|what have i done|what did i do|did i (skip|miss)|so far)\b", re.I)


def past_tool(text):
    """A past-labelled turn asks for a recap of the call, or for one moment."""
    return 'recap_call' if RECAP.search(text or '') else 'search_past'


def warm_router():
    """First router call loads the embedder (about 1 s); do it at startup when ROUTER=on."""
    if os.environ.get('ROUTER', 'off') != 'on':
        return
    try:
        from router.route import route
        route('hi')
    except Exception:
        event('dependency_error', dependency='router', level='error')


FORGET = re.compile(r"\b(forget (that|this|it|everything|the (clips?|recording))|delete (the )?(clips?|recording))\b", re.I)


def forget_request(text):
    """A spoken request to delete this call's Rewind clips."""
    return bool(FORGET.search(text or ''))
