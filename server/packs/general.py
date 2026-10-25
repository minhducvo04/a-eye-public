"""General visual helper and shared refusal boundary."""
import re

from server.gates import refusal_for_text
from . import Pack, STYLE


def refuse(text):
    refusal = refusal_for_text(text)
    if refusal:
        return refusal
    if re.search(r'\bgas\s+(line|pipe|supply|connection)s?\b|\b(weapon\w*|gun\w*|firearm\w*|bomb\w*|explosive\w*)\b', text, re.I):
        return "I can't help with gas lines or weapons."
    return None


PACK = Pack('general', 'General helper', 'You are a general visual helper.' + STYLE,
            ('the object',), False, refuse)
