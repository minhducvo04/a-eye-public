"""Closed-set spoken command matcher. Case and punctuation insensitive."""
from __future__ import annotations

import re

COMMANDS = ("which_way", "say_again", "check_again", "next", "stop", "power_off")

# Longest phrase first so "which way does it go" wins over "which way".
_PHRASES: tuple[tuple[str, str], ...] = (
    ("which way does it go", "which_way"),
    ("which way does this go", "which_way"),
    ("which way does that go", "which_way"),
    ("which way", "which_way"),
    ("say that again", "say_again"),
    ("say it again", "say_again"),
    ("say again", "say_again"),
    ("repeat that", "say_again"),
    ("repeat", "say_again"),
    ("check it again", "check_again"),
    ("check that again", "check_again"),
    ("check again", "check_again"),
    ("next step", "next"),
    ("power is off", "power_off"),
    ("the power is off", "power_off"),
    ("psu is off", "power_off"),
    ("it is off", "power_off"),
    ("its off", "power_off"),
    ("power off", "power_off"),
    ("unplugged", "power_off"),
    ("stop talking", "stop"),
    ("next", "next"),
    ("stop", "stop"),
)


def _norm(text: str) -> str:
    t = text.lower().replace("'", "")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def match_command(text: str) -> str | None:
    """Return a closed-set command id, or None if the phrase is not one of them."""
    n = _norm(text)
    if not n:
        return None
    for phrase, cmd in _PHRASES:
        if n == phrase:
            return cmd
        if re.search(rf"(^|\s){re.escape(phrase)}(\s|$)", n):
            return cmd
    return None
