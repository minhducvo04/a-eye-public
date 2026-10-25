"""Memory aid for early memory loss: calm, short, honest about what was seen. It assists; it never doses.

Hard rules live in code, not only in the prompt:
- refuse(): medicine and dosing questions get a fixed line; "did I take my pill" gets the fixed pill line,
  which is the same whatever the camera saw, because a camera can see a box opened but not a pill swallowed.
- guard(): every spoken line is checked. A claim that the person took (or did not take) a pill becomes the
  pill line; certainty words are softened; "you already asked" style phrases are removed; a "where is" answer
  for a tagged item starts with the item's home.
"""
import json
import os
import re

from server.gates import refusal_for_text
from . import Pack

CONTACT = os.environ.get('MEMORY_CONTACT', 'your caregiver')

# The caregiver tags items and their home spots. Demo defaults; MEMORY_HOMES points at a JSON file to override.
DEMO_HOMES = {'keys': 'the blue bowl by the door', 'glasses': 'the side table', 'wallet': 'the blue bowl by the door',
              'pill box': 'the kitchen counter', 'phone': 'the charger by the toaster'}
ALIASES = {'key': 'keys', 'glass': 'glasses', 'spectacles': 'glasses', 'pills': 'pill box', 'pillbox': 'pill box',
           'medicine box': 'pill box', 'purse': 'wallet', 'cell': 'phone', 'mobile': 'phone'}


def homes():
    path = os.environ.get('MEMORY_HOMES')
    if path:
        try:
            with open(path) as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                return {str(k).lower(): str(v) for k, v in loaded.items()}
        except (OSError, ValueError):
            pass
    return dict(DEMO_HOMES)


def pill_line(seen_at=None):
    """The fixed pill answer. Identical whether or not a pill was taken; only the sighting changes."""
    saw = f'I saw the pill box opened at {seen_at}.' if seen_at else "I didn't see the pill box opened."
    return f"{saw} I can't tell if you took it. Please check the box, or check with {CONTACT}."


MEDICAL_LINE = (f"I can't give advice about medicine or doses. Please check with {CONTACT}, "
                'your doctor or your pharmacist.')

_PILL_QUESTION = re.compile(r'\b(did|have|has)\s+i\b.*\b(take|taken|took|had|swallow\w*)\b.*\b(pill|pills|tablet\w*|'
                            r'medicine|medication|meds|dose)\b|\b(pill|pills|tablet\w*|medicine|medication|meds)\b.*\b'
                            r'(did|have)\s+i\s+(take|taken|had)\b', re.I)
_DOSING = re.compile(r'\b(how (many|much)|should i|can i|is it (ok|okay|safe)|double|extra|skip|miss(ed)?|dosage|'
                     r'dose|mg|milligram\w*)\b.*\b(pill|pills|tablet\w*|medicine|medication|meds|dose|drug\w*)\b|'
                     r'\b(take|taking)\s+(another|one more|two|a second)\b', re.I)


def refuse(text):
    refusal = refusal_for_text(text)
    if refusal:
        return refusal
    if _PILL_QUESTION.search(text):
        return pill_line()
    if _DOSING.search(text):
        return MEDICAL_LINE
    return None


_TOOK = re.compile(r"\byou(?:'ve| have| did)?\s+(?:\w+ly\s+)?(?:already\s+)?(?:took|taken|take|had|swallowed)\b[^.?!]*\b(pill|pills|"
                   r"tablet\w*|medicine|medication|meds|dose)\b|\byou\s+(?:didn't|did not|haven't|have not|never)\s+"
                   r"(?:take|taken|took|had)\b[^.?!]*\b(pill|pills|tablet\w*|medicine|medication|meds|dose)\b", re.I)
_CERTAIN = re.compile(r"\b(definitely|certainly|for sure|surely|without a doubt|absolutely|100 percent)\b,?\s*", re.I)
_IM_SURE = re.compile(r"\bI(?:'m| am) (?:sure|certain|positive)( that)?\b", re.I)
_REPEAT = re.compile(r"[^.?!]*\b(already (asked|told you|said)|asked (me )?(that|this) before|as I (said|told you)|"
                     r"again\?|you forgot( again)?)\b[^.?!]*[.?!]?\s*", re.I)
_WHERE = re.compile(r'\bwhere\b.*\b(is|are|did i (put|leave)|my)\b', re.I)


def item_asked(question):
    q = question.lower()
    for name in sorted(homes(), key=len, reverse=True):
        if re.search(r'\b' + re.escape(name) + r'\b', q):
            return name
    for alias, name in ALIASES.items():
        if re.search(r'\b' + re.escape(alias) + r'\b', q) and name in homes():
            return name
    return None


def home_line(item):
    verb = 'live' if item.endswith('s') else 'lives'
    return f'Your {item} {verb} in {homes()[item]}.'


def guard(text, question='', first=False):
    """Post-condition on every spoken line of the memory pack."""
    clean = _IM_SURE.sub('I think', text)
    clean = _CERTAIN.sub('', clean)
    if _TOOK.search(text) or _TOOK.search(clean):
        return pill_line()
    clean = _REPEAT.sub('', clean).strip()
    if clean:
        clean = clean[0].upper() + clean[1:]
    if first and _WHERE.search(question or ''):
        item = item_asked(question)
        if item:
            home = home_line(item)
            if homes()[item].lower() not in clean.lower()[:80]:
                clean = f'{home} {clean}'.strip()
    return clean


PROMPT = (
    'You are a calm memory helper for someone with early memory loss, on a camera in their home. '
    'Speak slowly and warmly: short sentences of under 12 words, one idea each, at most three sentences. '
    'Say only what the camera saw, with when: "I saw your keys in the blue bowl two minutes ago." '
    'If you did not see it, say "I didn\'t see it here" or "I can\'t be sure", never guess a place. '
    'Use the memory list (age_s is seconds ago) for past sightings. If the item has a home spot, name the '
    'home first. Never say the person took or did not take a pill; never give medicine or dose advice. '
    'If they ask the same thing again, answer the same way, kindly, as if for the first time. '
    'Never say they already asked, never say they forgot. No lists, no numbers they must remember. '
    'Item homes: ' + '; '.join(f'{k}: {v}' for k, v in homes().items()) + '.'
)

# Warm, gentle Cartesia voice (Julia, "soft, graceful tone with a composed rhythm"); Kokoro fallback af_heart.
VOICE = 'cartesia:273f9ef7-9fc2-4def-88bb-ab108c6249ca'
KOKORO_VOICE = 'af_heart'

PACK = Pack('memory', 'Memory helper', PROMPT, ('keys', 'glasses'), False, refuse, voice=VOICE, guard=guard)
