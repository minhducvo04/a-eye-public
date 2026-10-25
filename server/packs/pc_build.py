"""PC pack; board-specific header names are added for each session."""
from server.gates import ALLOWED_PARTS, refusal_for_text
from . import Pack, STYLE

PACK = Pack('pc_build', 'PC build helper',
            'You help assemble a PC. Guide only these supported parts: '
            + ', '.join(ALLOWED_PARTS) + '.' + STYLE,
            ('the motherboard', 'the connector'), True, refusal_for_text)


def board_prompt(board):
    return ' Board header names: ' + ', '.join(h['name'] for h in board['headers']) + '.'
