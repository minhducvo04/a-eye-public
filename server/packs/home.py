"""Short home skills for people 16+, with deterministic speech and a closed water gate.

HomeGuide belongs to one Session. Only the trusted step checker may set
water_off_passed; neither user text nor a model response can set it.
"""
import re
from dataclasses import dataclass
from time import monotonic

from server import safety
from . import Pack, STYLE
from .general import refuse

INTRO = 'You can take this one step at a time'
TASKS = {
    'sink': (
        'Show the dripping tap or supply line and the surrounding floor.',
        'Find the sink shutoff without touching any wet electrical items. Show the valve and its handle.',
        'Gently close the sink shutoff; stop if it sticks or leaks. Show the valve and the open tap with no flow.',
        'Show the dry supply connection and its fitting instructions. We need those before choosing a repair.',
        'Only hand-snug a connection if its instructions allow it; otherwise stop for a plumber. '
        'Show the connection without twisting the hose.',
        'For a dripping tap, leave its parts closed and ask a plumber about the correct cartridge. '
        'Show the tap model label for that request.',
        'Keep the water off if the repair is uncertain. Show the connection and a dry towel underneath it.',
    ),
    'breaker': (
        'This is a read-only guide to a tripped breaker; leave the panel untouched. '
        'Show the room and the affected appliance from a safe, dry spot.',
        'A tripped breaker can mean an overload or fault. Show any label already visible without approaching or opening the panel.',
        'Ask an electrician or building maintenance to check and reset it. '
        'Show the affected appliance label to help describe the problem.',
    ),
    'hinge': (
        'Support the cabinet door without climbing. Show the loose hinge and its screws.',
        'With the door supported, gently snug the loose mounting screw with a matching screwdriver. '
        'Show the screw head and stop if it spins freely.',
        'Move your fingers clear and gently test the door. Show the hinge and the gap as the door closes.',
    ),
}
_NEXT = re.compile(r"\b(next|done|continue|ready)\b", re.I)
HINTS = {
    'sink': ('Show the leak and floor.', 'Locate the shutoff.', TASKS['sink'][2],
             'Check the fitting instructions.', 'Hand-snug only if permitted; otherwise stop for a plumber.',
             'Leave tap parts closed; ask a plumber.', 'Keep water off if uncertain; check with a dry towel.'),
    'breaker': ('Leave the panel untouched; show the appliance.', 'Read only an already visible label.',
                'Ask an electrician or maintenance to check it.'),
    'hinge': ('Support the door without climbing.', 'Snug the mounting screw; stop if it spins.',
              'Clear your fingers; test the door.'),
}


@dataclass
class HomeGuide:
    task: str = 'sink'
    index: int = 0
    introduced: bool = False
    water_off_passed: bool = False
    completed: bool = False
    growth: object = None
    level: int = 1
    full_steps: bool = False
    run_id: str = ''
    epoch: int = 0
    started: float = 0
    hints: int = 0
    tries: int = 1
    safety_stop: bool = False

    def save(self):
        if self.growth and self.run_id:
            self.growth.update(self.run_id, hints=self.hints, tries=self.tries,
                               safety_stop=self.safety_stop, completed=self.completed,
                               minutes=round((monotonic() - self.started) / 60, 3))

    def stopped(self, retry=False):
        self.safety_stop = True
        self.tries += int(retry)
        self.save()

    def reply(self, text, store=None):
        if self.growth and self.epoch != self.growth.epoch:
            self.run_id, self.introduced, self.completed = '', False, False
            self.index, self.hints, self.tries = 0, 0, 1
            self.full_steps, self.safety_stop = False, False
        chosen = ('hinge' if re.search(r'\b(hinge|cabinet door)\b', text, re.I) else
                  'breaker' if re.search(r'\b(breaker|tripped)\b', text, re.I) else
                  'sink' if re.search(r'\b(sink|tap|supply line)\b', text, re.I) else None)
        changed = chosen and (chosen != self.task or (self.completed and re.fullmatch(
            r'(?:start |practice |fix (?:the |a )?)?(?:sink|hinge|breaker)[.!]?', text.strip(), re.I)))
        if changed:
            self.save()
            self.task, self.index, self.water_off_passed = chosen, 0, False
            self.completed, self.introduced, self.run_id = False, False, ''
            self.hints, self.tries, self.safety_stop, self.full_steps = 0, 1, False, False
        if not self.run_id and self.growth:
            self.level = self.growth.level(self.task)
            self.epoch = self.growth.epoch
            self.started = monotonic()
            self.run_id = self.growth.start(self.task)
        stop = safety.before(text) or refuse(text)
        if stop:
            # A new hazard invalidates any earlier water check.
            self.water_off_passed = False
            self.stopped()
            return stop
        from server.product_rules import vent_reply
        neutral = vent_reply(text)
        if neutral:
            return neutral
        if self.completed:
            return ''
        if re.search(r'\bwalk me through it\b', text, re.I):
            self.full_steps = True
        if re.fullmatch(r'(try again|retry)[.!]?', text.strip(), re.I):
            self.tries += 1
        if not changed and self.introduced and _NEXT.search(text):
            if self.task == 'sink' and self.index >= 2:
                stop = safety.plumbing_stop(self.water_off_passed)
                if stop:
                    return stop
            if self.index == len(TASKS[self.task]) - 1:
                self.completed = True
                self.save()
                return self.growth.comparison(self.task, self.run_id) if self.growth else ''
            self.index = min(self.index + 1, len(TASKS[self.task]) - 1)
        if self.task == 'sink' and self.index >= 3:
            stop = safety.plumbing_stop(self.water_off_passed)
            if stop:
                return stop
        level = 1 if self.full_steps else self.level
        # The shutoff/no-flow instruction is a coded safety check, never a fading hint.
        valve = self.task == 'sink' and self.index == 2
        line = TASKS[self.task][self.index] if level == 1 or valve else HINTS[self.task][self.index]
        if level == 3 and not valve:
            line = ''
        if level == 1 and self.task == 'sink' and self.index == 1 and store is not None:
            # Read only the consenting caller's existing local text tag, never a picture.
            tag = store.tags().get('shutoff valve', {})
            home = tag.get('home')
            if home:
                home = re.sub(r'[.!?\n\r]+', ' ', str(home))[:60].strip()
                line = f'Your saved shutoff location is {home}. Show the valve there so we can check.'
        if line and not valve:
            # Each spoken instruction sentence is one hint; the separate safety check is excluded.
            self.hints += len([s for s in re.split(r'(?<=[.!?])\s+', line) if s])
        self.save()
        if not self.introduced:
            self.introduced = True
            # Intro is a separate first turn; no step dump.
            return INTRO + '. ' + line if level == 1 else line
        return line


PACK = Pack('home', 'Home skills',
            'Help someone aged 16 or older with a dripping sink supply line or tap, a read-only breaker guide, '
            'or a loose cabinet hinge. Support independent practice. Use local home facts only with consent; '
            'a saved valve location is not proof that water is off. The deterministic guide owns spoken steps.' + STYLE,
            ('the shutoff valve', 'the dripping connection', 'the cabinet hinge'), False, refuse)
