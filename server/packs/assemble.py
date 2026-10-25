"""Furniture and gadget assembly stub."""
from . import Pack, STYLE
from .general import refuse

PACK = Pack('assemble', 'Assembly helper',
            'You help assemble furniture and gadgets using the visible instructions.' + STYLE,
            ('the screw', 'the hole', 'the instructions page'), False, refuse)
