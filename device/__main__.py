"""python -m device --config /etc/a-eye/device.toml

Credentials come from the environment (A_EYE_TOKEN, or CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET).
"""
import argparse
import asyncio
import signal

from device import config, hw
from device.client import Device


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m device', description='A-eye headless device client')
    ap.add_argument('--config', help='TOML settings file (see device/device.example.toml)')
    ap.add_argument('--url', help='override server.url, for example ws://192.168.1.20:8000/ws2')
    args = ap.parse_args(argv)
    overrides = {'server': {'url': args.url}} if args.url else None
    cfg = config.load(args.config, overrides)
    asyncio.run(run(cfg))


async def run(cfg):
    device = Device(cfg)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, device.stop.set)
    loop.add_signal_handler(signal.SIGUSR1, lambda: device.events.put_nowait(('press', 'signal')))
    button = cfg['trigger']['button']
    presses = {'keyboard': lambda: hw.keyboard_presses(device.events),
               'gpio': lambda: hw.gpio_presses(device.events, cfg['trigger']['button_pin'], cfg['trigger']['remember_pin']),
               'none': lambda: None}[button]()
    print('A-eye device ready. ' + {'keyboard': 'Press Enter to turn on or off.',
                                     'gpio': 'Press the button to turn on or off.',
                                     'none': 'Waiting for motion or SIGUSR1.'}[button], flush=True)
    await device.run(presses)


if __name__ == '__main__':
    main()
