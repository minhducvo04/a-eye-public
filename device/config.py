"""Device settings: a TOML file for the plain settings, the environment for credentials.

Credentials never live in the config file: A_EYE_TOKEN (the server's shared token) or a Cloudflare Access service
token as CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET. On a Pi, systemd loads them from /etc/a-eye/device.env.
"""
import os
import tomllib
from dataclasses import dataclass, field

DEFAULTS = {
    'server': {'url': 'ws://127.0.0.1:8000/ws2', 'pack': 'general', 'voice': ''},
    'privacy': {'private': True, 'claude': False, 'cartesia': False, 'twelvelabs': False},
    'trigger': {'button': 'keyboard', 'button_pin': 17, 'remember_pin': 22, 'motion': False, 'motion_share': 0.02,
                'armed_cap_s': 43200, 'zone': [0, 0, 1, 1], 'idle_off_s': 90, 'cap_s': 900, 'drop_person_frames': True},
    'camera': {'source': 'auto', 'index': 0, 'path': '', 'fps': 1.0, 'long_side': 640, 'quality': 70},
    'audio': {'mic': 'auto', 'speaker': 'auto', 'record_dir': '', 'barge_in': False, 'threshold': 500},
    'indicator': {'kind': 'terminal', 'led_pin': 27},
}


@dataclass
class Config:
    data: dict = field(default_factory=lambda: {k: dict(v) for k, v in DEFAULTS.items()})

    def __getitem__(self, section):
        return self.data[section]

    def hello_privacy(self):
        """The hello fields for privacy. Private on: nothing leaves the family's machines (cloud switches off)."""
        p = self.data['privacy']
        if p['private']:
            return {'private': True}
        return {'private': False, 'cloud': {k: bool(p[k]) for k in ('claude', 'cartesia', 'twelvelabs')}}

    def headers(self, env=None):
        """Upgrade headers from the environment. Values are never logged."""
        env = os.environ if env is None else env
        headers = {}
        if env.get('A_EYE_TOKEN'):
            headers['Authorization'] = 'Bearer ' + env['A_EYE_TOKEN']
        if env.get('CF_ACCESS_CLIENT_ID') and env.get('CF_ACCESS_CLIENT_SECRET'):
            headers['CF-Access-Client-Id'] = env['CF_ACCESS_CLIENT_ID']
            headers['CF-Access-Client-Secret'] = env['CF_ACCESS_CLIENT_SECRET']
        return headers


def load(path=None, overrides=None):
    """Defaults, then the TOML file, then `overrides` ({section: {key: value}}). Unknown keys are an error."""
    cfg = Config()
    layers = []
    if path:
        with open(path, 'rb') as f:
            layers.append(tomllib.load(f))
    if overrides:
        layers.append(overrides)
    for layer in layers:
        for section, values in layer.items():
            if section not in cfg.data or not isinstance(values, dict):
                raise ValueError(f'unknown config section [{section}]')
            for key, value in values.items():
                if key not in cfg.data[section]:
                    raise ValueError(f'unknown config key {section}.{key}')
                cfg.data[section][key] = value
    url = cfg['server']['url']
    if not url.startswith(('ws://', 'wss://')):
        raise ValueError('server.url must start with ws:// or wss://')
    zone = cfg['trigger']['zone']
    if (not isinstance(zone, list) or len(zone) != 4 or
            not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in zone) or
            zone[0] >= zone[2] or zone[1] >= zone[3]):
        raise ValueError('trigger.zone must be [left, top, right, bottom] within 0..1')
    cap = cfg['trigger']['armed_cap_s']
    if not isinstance(cap, (int, float)) or not 0 < cap < float('inf'):
        raise ValueError('armed_cap_s must be positive and finite')
    return cfg
