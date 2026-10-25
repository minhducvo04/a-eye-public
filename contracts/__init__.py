"""A-eye contracts: load schema.json and validate messages against its $defs.

Usage:
    from contracts import validate, ALLOWED_PARTS
    validate("frame", msg)            # raises jsonschema.ValidationError
    validate("client_message", msg)   # any client to server message
"""
import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schema.json"
VERSION = 1


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    s = schema()
    if name not in s["$defs"]:
        raise KeyError(f"no contract named {name!r}")
    return Draft202012Validator({"$schema": s["$schema"], "$defs": s["$defs"], "$ref": f"#/$defs/{name}"})


def validate(name: str, obj) -> None:
    """Raise jsonschema.ValidationError if obj does not match contract `name`."""
    _validator(name).validate(obj)


def is_valid(name: str, obj) -> bool:
    return _validator(name).is_valid(obj)


ALLOWED_PARTS = tuple(schema()["$defs"]["item"]["enum"])
STEP_STATES = tuple(schema()["$defs"]["step_state"]["enum"])

# Gate thresholds (assumptions; tune on the train split only, frozen Sat 20:00).
HEADER_MIN_CONF = 0.6
PASS_MIN_CONF = 0.8
STALE_FRAME_MS = 1500
