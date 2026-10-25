import pytest

from server.gates import part_allowed, power_off_ok, refusal_for_text
from server.steps import Session


def test_power_and_part_gates():
    session = Session('s', 'demo-b650', {'headers': []})
    assert not power_off_ok(session)
    session.power_off = True
    assert power_off_ok(session)
    assert part_allowed('cpu_fan_cable')
    assert not part_allowed('psu')


@pytest.mark.parametrize('text', [
    'open the power supply', 'How do I open my PSU?', 'PSU opening',
    'mains wiring', 'wire a wall outlet',
])
def test_refusal(text):
    assert refusal_for_text(text) == (
        "I can't help with opening a power supply or mains wiring. Those can hurt you."
    )
    assert refusal_for_text('Where is CPU_FAN?') is None
