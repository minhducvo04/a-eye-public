"""Small, explicit skill packs for the v2 helper."""
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Pack:
    id: str
    name: str
    system_prompt: str
    default_queries: tuple[str, ...]
    requires_power_off: bool
    refuse: Callable[[str], str | None]
    voice: str = ''                                   # default server voice for this pack (hello.voice wins)
    guard: Callable[..., str] | None = None           # post-condition on every spoken line: guard(text, question, first)


STYLE = (' Speak one or two sentences per turn. Point and draw only at real things '
         'visible in the scene. Say when unsure; never invent locations. Support independent practice; '
         'never claim emotional attachment or replace human relationships. Do not offer praise; '
         'the coded progress log alone provides factual completion feedback.')


def with_safety(pack):
    """Every pack, every brain: home-safety gates before the pack's own refusals, and a post-check on speech."""
    from dataclasses import replace
    from server import safety
    from server.product_rules import check, vent_reply
    own_refuse, own_guard = pack.refuse, pack.guard

    def refuse(text):
        return safety.before(text) or own_refuse(text) or vent_reply(text)

    def guard(text, question='', first=False):
        checked = safety.after(text, question)
        if checked != text or own_guard is None:
            return check(checked)
        return check(safety.after(own_guard(text, question, first), question))

    return replace(pack, refuse=refuse, guard=guard)


def get(pack_id):
    from . import general, pc_build, cooking, assemble, memory, home, electronics
    return with_safety({p.id: p for p in (general.PACK, pc_build.PACK, cooking.PACK, assemble.PACK,
                                          memory.PACK, home.PACK, electronics.PACK)}[pack_id])
