"""Cooking stub."""
from . import Pack, STYLE
from .general import refuse

PACK = Pack('cooking', 'Cooking helper',
            'You help with cooking. Keep fingers away from knife edges. '
            'Use heat protection and keep flammables away from hot surfaces.' + STYLE,
            ('the pan', 'the knife'), False, refuse)
