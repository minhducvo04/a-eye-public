"""Load and check router JSONL files. One object per line: text, context (0 to 2 earlier turns), label, mode.

Blank lines and lines starting with '#' are skipped, so a file can carry a header note.
"""
import json
from pathlib import Path

from router.labels import LABELS

HERE = Path(__file__).resolve().parent / 'data'
TRAIN = HERE / 'train.jsonl'
HELDOUT = HERE / 'heldout.jsonl'
HELDOUT_TEAM = HERE / 'heldout_team.jsonl'


def turn_text(turn):
    """A turn is a plain string or a {role, text|content} dict."""
    if isinstance(turn, dict):
        return str(turn.get('text') or turn.get('content') or '')
    return str(turn or '')


def check(row, where='row'):
    text = row.get('text')
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f'{where}: text must be a non-empty string')
    if row.get('label') not in LABELS:
        raise ValueError(f'{where}: label must be one of {LABELS}')
    context = row.get('context', [])
    if not isinstance(context, list) or len(context) > 2:
        raise ValueError(f'{where}: context must be a list of at most 2 turns')
    return {**row, 'text': text.strip(), 'context': [turn_text(t) for t in context]}


def load(path):
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        rows.append(check(json.loads(line), f'{path.name}:{number}'))
    return rows


def heldout():
    """The hand-written set plus the team's own lines. Never used for training or tuning."""
    return load(HELDOUT) + load(HELDOUT_TEAM)
