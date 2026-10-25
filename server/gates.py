"""Closed-set safety checks; no model decides these gates."""
import re

from contracts import ALLOWED_PARTS

REFUSAL = "I can't help with opening a power supply or mains wiring. Those can hurt you."
PART_REFUSAL = "I can't guide that part. Please use one of the supported PC parts."


def power_off_ok(session):
    return session.power_off is True


def part_allowed(item):
    return item in ALLOWED_PARTS


def refusal_for_text(text):
    text = text.lower()
    supply = re.search(r'\bpsu\b|power\s+supply', text)
    opening = re.search(r'\b(open\w*|disassembl\w*|inside|repair\w*|take\s+apart)\b', text)
    if re.search(r'\bmains\b|wall\s+(outlet|socket)', text) or (supply and opening):
        return REFUSAL
    return None


def voice_power_off(text):
    return text.strip().lower() in {"power is off", "it's off"}
