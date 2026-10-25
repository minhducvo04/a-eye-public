"""Home-safety gates for every pack and every brain (Claude or local), enforced in code.

Local models told a user that heavily smoking oil is fine for searing (docs/results/local-brain.md). So:
- before(): a question about one of these hazards gets a fixed line, and no model is asked;
- after(): a spoken reply that contradicts a gate (calls the hazard fine, or gives the dangerous step) is
  replaced by that gate's fixed line, whatever the question was.
Wired for all packs in server/packs/__init__.get().
"""
import re
from dataclasses import dataclass

_OK = r"(fine|ok|okay|safe|good|great|perfect|normal|ideal|no problem|nothing to worry|not a problem|totally fine)"


@dataclass(frozen=True)
class Gate:
    id: str
    ask: re.Pattern       # the user's question is about this hazard
    topic: re.Pattern     # a reply talks about this hazard
    wrong: re.Pattern     # inside that reply: approval, or the dangerous step
    line: str


def _re(pattern):
    return re.compile(pattern, re.I)


# Conservative text backstops, not a substitute for visual hazard detection.
_WATER = r"\b(water|wet|leak\w*|drip\w*|puddle)\b"
_ELECTRIC = r"\b(outlet|socket|live cord|powered cord|plugged.in cord|extension cord)\b"
_PANEL = r"\b(electrical panel|electric panel|breaker (box|panel)|fuse box)\b"
_OPEN = r"\b(open\w*|remov\w*|unscrew\w*|take off|inside)\b"
_LADDER = r"\b(ladder|stepladder)\b"
_UNSAFE = r"\b(top (rung|step)|wobbl\w*|unstable|uneven|overreach\w*|lean\w* sideways)\b"
_WATER_ON = r"\bwater\b.{0,20}\b(still on|is on|running|not off)\b"
WATER_OFF_LINE = ("Stop here until the step checker confirms the water is off. "
                  "Show the shutoff valve and the tap with no water flowing.")


def _pair(a, b):
    return _re(a + r"[\s\S]{0,100}" + b + '|' + b + r"[\s\S]{0,100}" + a)


def plumbing_stop(water_off_passed):
    """Only a trusted checker boolean can unlock a plumbing step; missing is not passed."""
    return None if water_off_passed is True else WATER_OFF_LINE


def _stop(id, pattern, line):
    # For these out-of-scope actions, any proposed instruction is stopped.
    return Gate(id, pattern, pattern, _re(r"[\s\S]+"), line)


GATES = (
    _stop('water_electric', _pair(_WATER, _ELECTRIC),
          "Stop and move away from the wet area; don't touch cords, outlets or switches. "
          "Keep others away and call an electrician before continuing."),
    _stop('electrical_panel', _pair(_OPEN, _PANEL),
          "Stop; don't open or touch the electrical panel. Call a qualified electrician."),
    _stop('gas_line', _re(r"\b(gas|propane)\s+(line|pipe|hose|fitting)s?\b"),
          "Stop; don't touch or repair a gas line. Call the gas company or a qualified gas technician."),
    _stop('unsafe_ladder', _pair(_LADDER, _UNSAFE),
          "Stop and don't reach or climb higher. Get help to come down safely and use stable access."),
    _stop('water_on', _re(_WATER_ON), WATER_OFF_LINE),
    Gate('hot_oil',
         _re(r"\b(oil|grease|fat|pan)\b.{0,40}\b(smok\w*|on fire|fire|flames?|flaming|burning)\b|"
             r"\b(smok\w*|fire|flames?|flaming)\b.{0,30}\b(oil|grease|fat|pan)\b|\bgrease fire\b"),
         _re(r"\b(oil|grease|fat|pan)\b.{0,60}\b(smok\w*|fire|flames?)\b|\b(smok\w*|fire|flames?)\b.{0,60}\b(oil|grease|pan)\b"),
         _re(r"\b(is|it's|that's|its|are|be)\s+(\w+\s+)?" + _OK + r"\b|\bgood for (searing|frying)\b|\bkeep (cooking|searing|frying)\b|"
             r"(?<!never )(?<!don't )(?<!do not )\b(use|pour|throw|add|put|splash)\s+(some\s+|a little\s+)?water\b"),
         "Turn the heat off now and slide the pan off the burner. Smoking oil can catch fire. If it flames, "
         "cover the pan with a lid or a baking sheet, never water. If the fire spreads, leave and call 911."),
    Gate('gas_smell',
         _re(r"\b(smell\w*|smells like|odou?r)\b.{0,30}\b(gas|rotten eggs?|propane)\b|\bgas\s+(leak\w*|smell\w*)\b"),
         _re(r"\bgas\b.{0,40}\b(leak\w*|smell\w*)\b|\b(smell\w*)\b.{0,30}\bgas\b"),
         _re(r"\b(is|it's|that's|probably)\s+(\w+\s+)?" + _OK + r"\b|\b(turn|switch|flip)\s+(on|off)\s+(the\s+)?(light|switch|stove|burner)s?\b|"
             r"\b(light|strike)\s+(a\s+)?(match|lighter|candle)\b|\bfind the leak\b"),
         "Leave now and get everyone out. Don't touch light switches or anything electric, and don't light "
         "anything. Once you're outside, call 911 or the gas company."),
    Gate('live_power',
         _re(r"\b(live|hot|powered|power (is )?(still )?on|while (it's|it is) (on|plugged in)|without (turning|switching|shutting) "
             r"(the )?(power|breaker|it) off|plugged in)\b.{0,50}\b(wire\w*|wiring|outlet|socket|breaker|panel|fuse|electric\w*|"
             r"psu|power supply|light fixture|switch)\b|\b(wire\w*|wiring|outlet|socket|breaker|panel|fuse|psu|power supply|light "
             r"fixture)\b.{0,50}\b(live|power (is )?(still )?on|while (it's|it is) on|plugged in|without (turning|switching|shutting) "
             r"(the )?(power|breaker|it) off)\b"),
         _re(r"\b(live|power (is )?(still )?on)\b.{0,60}\b(wire\w*|outlet|socket|breaker|panel|psu|electric\w*)\b|"
             r"\b(wire\w*|outlet|socket|breaker|panel|psu)\b.{0,60}\b(live|power (is )?(still )?on)\b"),
         _re(r"\b(is|it's|that's)\s+(\w+\s+)?" + _OK + r"\b|\bno need to (turn|switch|shut)\b|\b(leave|keep) (it|the power) on\b"),
         "Turn the power off first, at the breaker or by unplugging it, and check it's dead before you touch "
         "anything. If you're not sure, stop and call an electrician."),
    Gate('burning_device',
         _re(r"\b(burn\w*|smok\w*|melt\w*|scorch\w*|sizzl\w*)\b.{0,40}\b(pc|computer|laptop|charger|power supply|psu|cable|"
             r"cord|plug|outlet|socket|battery|power strip|adapter)\b|\b(pc|computer|laptop|charger|power supply|psu|cable|cord|"
             r"plug|outlet|socket|battery|power strip|adapter)\b.{0,40}\b(burn\w*|smok\w*|melt\w*|scorch\w*|sparks?)\b"),
         _re(r"\b(burn\w*|smok\w*|melt\w*)\b.{0,60}\b(pc|computer|charger|psu|power supply|cable|plug|battery|outlet)\b|"
             r"\b(pc|computer|charger|psu|power supply|cable|plug|battery|outlet)\b.{0,60}\b(burn\w*|smok\w*|melt\w*)\b"),
         _re(r"\b(is|it's|that's|probably)\s+(\w+\s+)?" + _OK + r"\b|\b(new|normal) (electronics |pc )?smell\b|\b(ignore|keep using|keep it running)\b"),
         "Unplug it now, or switch it off at the wall if it's hot to touch, and move it away from anything that "
         "burns. Don't use it again until it's checked. If you see flames or a lot of smoke, leave and call 911."),
    Gate('chemical_mix',
         _re(r"\bbleach\b.{0,60}\b(ammonia|vinegar|acid\w*|toilet (bowl )?cleaner|rubbing alcohol|windex|glass cleaner)\b|"
             r"\b(ammonia|vinegar|acid\w*|toilet (bowl )?cleaner|rubbing alcohol|windex|glass cleaner)\b.{0,60}\bbleach\b"),
         _re(r"\bbleach\b.{0,80}\b(ammonia|vinegar|acid\w*|cleaner|alcohol)\b|\b(ammonia|vinegar|acid\w*|alcohol)\b.{0,80}\bbleach\b"),
         _re(r"\b(is|it's|that's|are)\s+(\w+\s+)?" + _OK + r"\b|\byou can (mix|combine|use them together)\b|"
             r"\bsafe to (mix|combine|use together)\b"),
         "Don't mix them. Bleach with ammonia, vinegar or other acids makes a toxic gas. If you already did, "
         "leave the room, open a window on your way out, and call Poison Control at your local poison control service."),
)

LINES = {gate.line for gate in GATES}


def before(question):
    """The fixed line for a hazard question, or None. Runs before any model."""
    ordered = sorted(GATES, key=lambda g: g.id != 'gas_smell')
    for gate in ordered:
        if gate.ask.search(question or ''):
            return gate.line
    return None


def after(text, question=''):
    """A reply that contradicts a gate becomes that gate's line. Fixed lines pass through unchanged."""
    if not text or text in LINES:
        return text
    for gate in GATES:
        about = gate.topic.search(text) or gate.ask.search(question or '')
        if about and gate.wrong.search(text):
            return gate.line
    return text
