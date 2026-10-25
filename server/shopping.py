"""Voice-first shopping. Offers are data; ranking and follow-up count are code."""
import asyncio
from dataclasses import asdict, dataclass
from jsonschema import ValidationError
import math
import re
from time import monotonic
from typing import Protocol
from urllib.parse import urlsplit

from contracts import validate
from server.crops import pick_target
from server.privacy import claude_allowed, local_only

INTENT = re.compile(r"\b(buy|buying|cheaper|cheapest|price|prices|cost|costs|deal|shopping)\b|\bhow much\b", re.I)
COMPAT = re.compile(r'\b(cables?|chargers?|adapters?|usb|ram|ssd|gpu|cpu|motherboard|electronics|software|laptop|monitor)\b', re.I)


@dataclass(frozen=True)
class Offer:
    store: str
    price: float                 # total including known shipping, before tax
    currency: str
    delivery_days: int | None
    pickup_today: bool
    url: str
    in_stock: bool
    notes: str


class OfferSource(Protocol):
    async def search(self, product: str, location: str | None = None) -> list[Offer]: ...


class FixtureSource:
    """Explicit dependency injection only: never substitute demo prices for live prices."""
    def __init__(self, offers=()):
        self.offers = list(offers)
        self.calls = []

    async def search(self, product, location=None):
        self.calls.append((product, location))
        return list(self.offers)


class WebSearchSource:
    def __init__(self, client=None):
        from server.llm import LLMClient
        self.client = client or LLMClient()

    async def search(self, product, location=None):
        if not claude_allowed():
            return []
        rows = await self.client.shopping_offers(product, location)
        offers = []
        for row in rows:
            try:
                offer = Offer(**row)
                validate('shopping_offer', dict(asdict(offer), selected=False))
                if math.isfinite(offer.price) and urlsplit(offer.url).scheme in ('http', 'https'):
                    offers.append(offer)
            except (TypeError, ValueError, ValidationError):
                continue
        return offers


def days(offer):
    return 0 if offer.pickup_today else offer.delivery_days


def arrival(offer):
    if offer.pickup_today:
        return 'ready for pickup today'
    if offer.delivery_days is None:
        return 'delivery timing unconfirmed'
    return f'arrives in about {offer.delivery_days} ' + ('day' if offer.delivery_days == 1 else 'days')


def money(offer):
    amount = f'{offer.price:g}'
    return amount + (' dollars' if offer.currency == 'USD' else f' {offer.currency}')


def recommend(product, offers, *, setup_known=False, urgent=False):
    available = [o for o in offers if o.in_stock and math.isfinite(o.price) and o.price >= 0]
    if not available:
        return [], "I couldn't find verified offers for this product."
    # Never compare currencies without an exchange-rate source. Prefer the source's first currency.
    available = [o for o in available if o.currency == available[0].currency]
    # Transparent demo policy: each waiting day costs 3% of the cheapest price.
    # Unknown delivery gets a 30-day penalty, never an invented arrival promise.
    daily = max(min(o.price for o in available) * .03, .01)
    ranked = sorted(available, key=lambda o: (
        (days(o) if days(o) is not None else 30) if urgent else
        o.price + daily * (days(o) if days(o) is not None else 30), o.price))
    chosen = ranked[0]
    cheapest = min(available, key=lambda o: o.price)
    fastest = min(available, key=lambda o: days(o) if days(o) is not None else math.inf)
    selected = [chosen]
    for candidate in (cheapest, fastest):
        if candidate not in selected:
            selected.append(candidate)
    def store(offer):
        return offer.store.replace('?', '').replace('\n', ' ').strip()
    line = f"{'Fastest option' if urgent else 'Best value'} is {store(chosen)} at {money(chosen)}, {arrival(chosen)}."
    for other in selected[1:]:
        saving = chosen.price - other.price
        comparison = f', {saving:g} {"dollars" if other.currency == "USD" else other.currency} cheaper' if saving > 0 else ''
        line += f' Or {store(other)} at {money(other)}{comparison}, {arrival(other)}.'
    question = None
    if COMPAT.search(product) and not setup_known:
        question = 'What device or setup does it need to work with?'
    elif (not urgent and cheapest != fastest and days(cheapest) is not None and days(fastest) is not None
          and days(cheapest) - days(fastest) >= 3 and fastest.price - cheapest.price >= cheapest.price * .15):
        question = 'How soon do you need it?'
    if question:
        line += ' ' + question
    return selected, line


async def shopping_turn(session, text, vision, source=None):
    if local_only():
        return None, 'Shopping needs the cloud, so it is unavailable in Private mode.'
    if not claude_allowed():
        return None, 'Shopping needs the Claude cloud switch enabled.'
    if not session.camera or not session.frame or monotonic() - session.frame_time > 1.5:
        return None, 'Point the camera at the product label so I can identify it.'
    scene = session.scene
    if scene is None or session.scene_seq != session.frame['seq']:
        try:
            scene = await asyncio.wait_for(vision.call('scene', dict(
                image_b64=session.frame['jpeg_b64'], queries=['product brand and model on the label'], caption=True)), 2)
            validate('scene_response', scene)
        except Exception:
            return None, "I couldn't read the product. Bring its label closer."
    target = pick_target(scene['objects'], text)
    if target is None or target.get('conf', 0) < .6:
        return None, 'Bring the product label closer so I can read it.'
    product = target['label']
    query = f"{product}. Visible label and scene: {scene['caption']}"
    try:
        offers = await asyncio.wait_for((source or WebSearchSource()).search(query), 8)
    except Exception:
        return None, "I couldn't check live offers just now. Try again in a moment."
    # Recheck after awaits: a mid-call cloud switch must not publish a late result.
    if not claude_allowed():
        return None, 'Shopping needs the Claude cloud switch enabled.'
    user_context = ' '.join(h.removeprefix('user: ') for h in session.history if h.startswith('user: ')) + ' ' + text
    setup_known = bool(re.search(r'\b(usb-c|lightning|windows 11|macos|iphone \d+)\b', user_context, re.I))
    urgent = bool(re.search(r'\b(need it today|need this today|right now|urgent)\b', user_context, re.I))
    chosen, line = recommend(product, offers, setup_known=setup_known, urgent=urgent)
    result = dict(v=1, product=product, items=[dict(asdict(o), selected=i == 0) for i, o in enumerate(chosen)],
                  note='Prices include known shipping, before tax. Compatibility is unverified; confirm the exact model.')
    validate('shopping_response', result)
    return result, line
