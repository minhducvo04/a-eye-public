"""Deterministic USB-only practice. Confirmations are reports, not visual certification."""
import re
from dataclasses import dataclass
from time import monotonic

from server import safety
from . import Pack, STYLE
from .home import HomeGuide

USB = 'Unplug the USB first'
SERIES = 'Unplug the USB first. Use a 220 ohm resistor in series with the LED before connecting USB.'
HOT = 'Do not touch the hot soldering iron tip; leave it in its stand.'
MAINS = 'Stop; mains power and wall outlets are outside this USB-only build.'
LINES = {USB, SERIES, HOT, MAINS}
TASKS = {
    'ee_led': (
        'Find an Arduino UNO, USB cable, breadboard, LED, 220 ohm resistor and two jumper wires. Show all six part types with USB unplugged.',
        'Place the LED legs in separate, unconnected breadboard rows; the long leg is plus. Show both legs and their rows.',
        "Connect the 220 ohm resistor from the LED short leg's row to the GND rail. Show both resistor ends.",
        "Connect a jumper from UNO pin 9 to the LED long leg's row. Show pin 9 and both jumper ends.",
        'Connect the other jumper from that GND rail to UNO GND. Show both GND labels and jumper ends.',
        'Plug the USB cable into the UNO and your computer, leaving the wiring untouched. Show the USB connection and series resistor.',
        'Select Arduino Uno and its port, then upload the Blink example with LED_BUILTIN replaced by 9. Show the upload result and blinking LED.',
    ),
    'ee_resistor': (
        'Read the resistor bands from the end opposite the spaced tolerance band. Show the bands close up in good light.',
        'Red red brown means 22 times 10, or 220 ohms. Show those three bands; unclear colors need a meter check.',
    ),
    'ee_polarity': (
        'The LED long leg is plus; the short leg and flat rim mark minus. Show both legs and the flat rim with USB unplugged.',
        'If the legs are trimmed or the markings disagree, check the LED datasheet before wiring. Show its package markings.',
    ),
}
HINTS = {
    'ee_led': ('Show all six part types with USB unplugged.', 'Show the LED legs in separate rows.',
               'Show the resistor from the short leg row to GND rail.', 'Show pin 9 to the long leg row.',
               'Show the rail to UNO GND jumper.', 'Show USB connected with the wiring untouched.',
               'Show the pin 9 Blink upload and LED.'),
    'ee_resistor': ('Show the resistor bands.', 'Show red red brown for 220 ohms.'),
    'ee_polarity': ('Show the long plus leg and flat minus rim.', 'Show package markings if polarity is unclear.'),
}
TARGETS = {'ee_led': ('LED', 'LED', 'resistor', 'pin 9', 'GND', 'resistor', 'LED'),
           'ee_resistor': ('resistor', 'resistor'), 'ee_polarity': ('LED', 'LED')}


def refuse(text):
    text = text.casefold()
    if re.search(r'\b(mains|wall (outlet|socket)|120\s*v|230\s*v|240\s*v)\b', text):
        return MAINS
    if re.search(r'\b(touch|grab|hold)\w*\b', text) and re.search(r'\b(hot|soldering)\b', text) and 'tip' in text:
        return HOT
    if re.search(r'\b(no|without|missing|skip|remove|removed|bypass)\b.{0,30}\bresistor\b|'
                 r'\bresistor\b.{0,25}\b(not in series|missing|removed|bypassed)\b', text):
        return SERIES
    if re.search(r'\busb\b.{0,25}\b(connected|plugged in|on)\b|'
                 r'\b(powered|plugged in)\b.{0,20}\busb\b', text):
        return USB
    return None


@dataclass
class ElectronicsGuide(HomeGuide):
    task: str = 'ee_led'
    usb_off: bool = False
    series: bool = False
    target: str | None = None

    def stop(self, line):
        self.target = None
        if line == USB:
            self.usb_off = False
        if line == SERIES:
            self.series = False
            # A missing resistor after power-on returns to the pre-power checkpoint.
            if self.task == 'ee_led' and self.index >= 5:
                self.index, self.completed = 4, False
        self.stopped()
        return line

    def reply(self, text, store=None):
        self.target = None
        # Hazards never select a task, advance a step, or grant a confirmation.
        stop = refuse(text) or safety.before(text)
        if stop:
            return self.stop(stop)
        chosen = ('ee_resistor' if re.search(r'\b(bands?|colou?rs?)\b', text, re.I) else
                  'ee_polarity' if re.search(r'\bpolarity\b', text, re.I) else
                  'ee_led' if re.search(r'\b(light an? led|arduino|start|practice|blink)\b', text, re.I) else None)
        if ((chosen and (chosen != self.task or self.completed)) or
                (self.growth and self.epoch != self.growth.epoch)):
            # Power state survives task changes and forgetting progress.
            fresh = ElectronicsGuide(task=chosen or self.task, growth=self.growth, usb_off=self.usb_off)
            self.__dict__.update(fresh.__dict__)
        if not self.run_id and self.growth:
            self.level = self.growth.level(self.task)
            self.epoch = self.growth.epoch
            self.started = monotonic()
            self.run_id = self.growth.start(self.task)
        clean = text.strip().lower().rstrip('.!?')
        if re.fullmatch(r'(?:usb unplugged|i unplugged (?:the )?usb)(?:,? next)?', clean):
            self.usb_off = True
        if re.fullmatch(r'(?:the )?resistor is in series(?:,? next)?', clean):
            if not self.usb_off:
                return self.stop(USB)
            self.series = True
        if not self.usb_off and re.search(r'\b(rewir\w*|move (?:the )?(?:led|wire|resistor)|change (?:the )?wir\w*)\b', clean):
            self.stopped()
            return USB
        if self.completed:
            return ''
        if clean == 'walk me through it':
            self.full_steps = True
        if clean in ('retry', 'try again'):
            self.tries += 1
        advancing = self.introduced and bool(re.fullmatch(
            r'(?:(?:usb unplugged|i unplugged (?:the )?usb|(?:the )?resistor is in series),? )?'
            r'(next|done|continue|ready)', clean))
        index = self.index + int(advancing)
        if self.task == 'ee_led':
            if 1 <= index <= 4 and not self.usb_off:
                self.stopped()
                return USB
            if index >= 5 and not self.series:
                self.stopped()
                return SERIES
        if index == len(TASKS[self.task]):
            self.completed = True
            self.save()
            return self.growth.comparison(self.task, self.run_id) if self.growth else ''
        self.index = index
        if self.task == 'ee_led' and index == 5:
            self.usb_off = False  # instructions to power on conservatively mark it powered
        self.introduced = True
        level = 1 if self.full_steps else self.level
        line = TASKS[self.task][index] if level == 1 else HINTS[self.task][index] if level == 2 else ''
        if line:
            self.hints += len(re.split(r'(?<=[.!?])\s+', line))
            self.target = TARGETS[self.task][index]
        self.save()
        return f'Step {index + 1}: {line}' if line else ''


PACK = Pack('engineering', 'Engineering', 'USB-only Arduino electronics practice.' + STYLE,
            ('LED', 'resistor'), False, refuse)
