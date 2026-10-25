#!/usr/bin/env python3
"""USB PIR to authenticated owner wake door; no camera or media access."""
import argparse
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class API:
    def __init__(self, url, token, owner):
        self.url = url.rstrip('/')
        self.headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token,
                        'X-Aeye-Owner': owner}

    def call(self, path, body=None):
        request = Request(self.url + '/api/trigger' + path, headers=self.headers,
                          data=None if body is None else json.dumps(body).encode())
        with urlopen(request, timeout=3) as response:
            return json.load(response)


class Bridge:
    def __init__(self, serial, api, zone='entryway', cooldown=5, quiet=30, clock=time.monotonic):
        self.serial, self.api, self.zone = serial, api, zone
        self.cooldown, self.quiet, self.clock = max(5, cooldown), quiet, clock
        self.high = False
        self.last_motion = self.last_trigger = self.last_refresh = float('-inf')
        self.led = None
        self.last_led = float('-inf')

    def step(self):
        now = self.clock()
        line = self.serial.readline().decode('ascii', errors='ignore').strip()
        if line in ('P1', 'P0'):
            self.high = line == 'P1'
            if self.high:
                self.last_motion = now
        if self.high:
            self.last_motion = now
        try:
            state = self.api.call('/status')['state']
            if now - self.last_motion < self.quiet:
                if state in ('ARMED', 'OFF') and now - self.last_trigger >= self.cooldown:
                    self.last_trigger = now
                    state = self.api.call('', dict(source='arduino-pir', kind='presence', zone=self.zone))['state']
                # Only positive PIR readings extend the server lease. P0 never cancels a short arm pulse.
                if self.high and (line == 'P1' or now - self.last_refresh >= .5) and state in ('EVENT', 'SESSION'):
                    self.last_refresh = now
                    state = self.api.call('/presence', dict(zone=self.zone, quiet_s=self.quiet))['state']
            self.set_led(state)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError):
            self.set_led('OFF')  # Failure is never shown as an acknowledged session.

    def set_led(self, state):
        if state not in ('OFF', 'ARMED', 'EVENT', 'SESSION'):
            state = 'OFF'
        changed = state != self.led
        if changed or self.clock() - self.last_led >= 1:
            self.last_led = self.clock()
            self.serial.write(('L ' + state + '\n').encode())
            self.led = state
            if changed:
                print('LED ' + state, flush=True)


class FakeSerial:
    """Short motion pulses, with quiet gaps shorter than the one-second demo lease."""
    def __init__(self, clock=time.monotonic):
        self.clock, self.started = clock, clock()
        self.events = iter([(0.2, b'P1\n'), (.4, b'P0\n'), (1, b'P1\n'),
                            (1.2, b'P0\n'), (1.8, b'P1\n'), (2, b'P0\n')])
        self.next = next(self.events, None)
        self.commands = []

    def readline(self):
        if self.next and self.clock() - self.started >= self.next[0]:
            _, line = self.next
            self.next = next(self.events, None)
            return line
        return b''

    def write(self, data):
        self.commands.append(data)

    def close(self):
        pass


def find_port():
    from serial.tools import list_ports
    ports = [p.device for p in list_ports.comports()
             if p.vid in (0x2341, 0x2A03, 0x1A86, 0x0403) or 'usbmodem' in p.device]
    if len(ports) != 1:
        raise SystemExit('Specify --port: expected exactly one Arduino USB serial board.')
    return ports[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--zone', default='entryway')
    parser.add_argument('--port')
    parser.add_argument('--quiet', type=float, default=30)
    parser.add_argument('--cooldown', type=float, default=5)
    parser.add_argument('--fake', action='store_true')
    parser.add_argument('--duration', type=float, default=0, help='seconds; 0 runs until Ctrl-C')
    args = parser.parse_args()
    if not 1 <= args.quiet <= 300:
        parser.error('--quiet must be between 1 and 300 seconds')
    token, owner = os.getenv('A_EYE_TOKEN', ''), os.getenv('A_EYE_OWNER', '')
    if not token or not owner:
        parser.error('Set A_EYE_TOKEN and A_EYE_OWNER; values are never printed.')
    if args.fake:
        serial = FakeSerial()
    else:
        import serial as pyserial
        serial = pyserial.Serial(args.port or find_port(), 115200, timeout=.1)
        time.sleep(2)  # UNO resets when the USB port opens.
    bridge = Bridge(serial, API(args.url, token, owner), args.zone, args.cooldown, args.quiet)
    started = time.monotonic()
    try:
        while not args.duration or time.monotonic() - started < args.duration:
            bridge.step()
            time.sleep(.1)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.set_led('OFF')
        serial.close()
    print('Bridge finished' + (' (fake serial; real HTTP)' if args.fake else ''), flush=True)


if __name__ == '__main__':
    main()
