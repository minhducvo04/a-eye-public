"""Load only validated, local board tables."""
import json
from pathlib import Path
import re

from contracts import validate

BOARDS = Path(__file__).resolve().parents[1] / 'data' / 'boards'


def load_board(board_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', board_id):
        raise ValueError('Invalid board ID')
    board = json.loads((BOARDS / f'{board_id}.json').read_text())
    validate('header_table', board)
    if board['board_id'] != board_id:
        raise ValueError('Board ID does not match table')
    return board
