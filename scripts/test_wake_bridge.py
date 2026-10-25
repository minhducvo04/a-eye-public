from scripts.wake_bridge import Bridge, FakeSerial


class API:
    def __init__(self, clock):
        self.clock = clock
        self.until = 0
        self.triggers = 0

    def call(self, path, body=None):
        if path == '':
            self.triggers += 1
            self.until = self.clock() + 30
        if path == '/presence':
            self.until = self.clock() + body['quiet_s']
        return {'state': 'SESSION' if self.until > self.clock() else 'ARMED'}


def test_arm_pulses_keep_awake_and_quiet_rearms():
    now = [0.0]
    clock = lambda: now[0]
    serial = FakeSerial(clock)
    api = API(clock)
    bridge = Bridge(serial, api, quiet=1, clock=clock)
    states = []
    for tick in range(41):
        now[0] = tick / 10
        bridge.step()
        states.append(bridge.led)
    assert states[0] == 'ARMED'
    assert all(s == 'SESSION' for s in states[2:28])
    assert states[-1] == 'ARMED'
    assert api.triggers == 1
    assert b'L SESSION\n' in serial.commands


def test_network_failure_never_shows_session():
    class Down:
        def call(self, *args):
            raise OSError('offline')
    serial = FakeSerial()
    bridge = Bridge(serial, Down())
    bridge.step()
    assert bridge.led == 'OFF'
