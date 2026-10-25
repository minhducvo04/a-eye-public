"""Pure session logic. Returns (messages, optional (operation, payload)).

The caller supplies time, board data and vision results. Images live only in memory.
"""
from dataclasses import dataclass, field

from contracts import ALLOWED_PARTS, HEADER_MIN_CONF, PASS_MIN_CONF, STALE_FRAME_MS
from server.gates import PART_REFUSAL, part_allowed, power_off_ok, refusal_for_text, voice_power_off


def error(code, message):
    return dict(v=1, type='error', code=code, message=message)


@dataclass
class Session:
    session_id: str
    board_id: str
    board: dict = field(repr=False)
    state: str = 'SAFETY'
    power_off: bool = False
    item: str | None = None
    target_header: str | None = None
    image: dict | None = field(default=None, repr=False)
    utterances: int = 0

    def say(self, text):
        self.utterances += 1
        return dict(v=1, type='say', utterance_id=f'{self.session_id}-{self.utterances}', text=text)

    def step(self, state):
        if state != 'SAFETY' and not power_off_ok(self):
            raise ValueError('Power-off confirmation required')
        self.state = state
        result = dict(v=1, type='step', step_id='1', state=state)
        if self.item:
            result.update(item=self.item, target_header=self.target_header)
        return result

    def start(self):
        return [self.step('SAFETY'), self.say(
            'Switch the PSU off and unplug it. Tap Power is off when done.')]

    def stale(self, now_ms):
        return (self.image is not None and self.image['type'] == 'frame'
                and now_ms - self.image['ts_ms'] > STALE_FRAME_MS)

    def request(self, operation):
        payload = {'image_b64': self.image['jpeg_b64']}
        if operation == 'identify':
            payload['candidates'] = list(ALLOWED_PARTS)
        else:
            payload.update(board_id=self.board_id, header=self.target_header)
            if operation == 'verify':
                payload['expect'] = {'item': self.item}
        return operation, payload

    def handle(self, message, now_ms):
        kind = message['type']
        if kind == 'market':
            query = dict(v=1, category=message['category'], owned_parts=[dict(
                category='board', model=self.board['model'],
                specs=self.board.get('specs', {}), confirmed=True, source='fixture')])
            if 'budget_usd' in message:
                query['budget_usd'] = message['budget_usd']
            return [], ('market', query)
        if kind in {'parts_scan', 'project_open', 'audio'}:
            # TODO: connect project memory, part scanning and voice adapters.
            return [error('internal', 'not wired yet')], None
        if kind == 'user_text':
            refusal = refusal_for_text(message['text'])
            if refusal:
                return [self.say(refusal)], None
            if voice_power_off(message['text']):
                message = dict(type='confirm', what='power_off', value=True)
                kind = 'confirm'
            else:
                return [], ('text', dict(
                    system=('Answer in one or two plain sentences, only about the current step '
                            'and board. Never advise opening a PSU or mains wiring. '
                            'Say "check the manual page" when unsure.'),
                    user=(f'Board: {self.board_id} ({self.board["model"]})\n'
                          f'Step state: {self.state}\nItem: {self.item}\n'
                          f'Target header: {self.target_header}\n'
                          f'Board headers: {", ".join(h["name"] for h in self.board["headers"])}\n'
                          f'Question: {message["text"]}')))
        if kind == 'confirm' and message['what'] == 'power_off':
            self.power_off = message['value']
            if not self.power_off:
                self.image = None
                self.item = self.target_header = None
                return self.start(), None
            if self.state == 'SAFETY':
                return [self.step('IDENTIFY'), self.say('Hold up the part you want to connect.')], None
            return [], None
        if kind in {'frame', 'photo'}:
            if not power_off_ok(self):
                return [error('power_off_required', 'Confirm that power is off first.')], None
            if kind == 'frame' and now_ms - message['ts_ms'] > STALE_FRAME_MS:
                return [error('stale_frame', 'Frame is too old.')], None
            self.image = message
            if self.state == 'IDENTIFY':
                return [], self.request('identify')
            if self.state == 'LOCATE':
                return [], self.request('locate')
        check = (kind == 'photo' or (kind == 'confirm' and message['value']
                 and message['what'] in {'seated', 'check_again'}))
        if self.state == 'AWAIT' and check:
            if self.image is None:
                return [self.say('Send a photo so I can check.')], None
            if self.stale(now_ms):
                return [error('stale_frame', 'Send a fresh frame to check.')], None
            return [self.step('VERIFY')], self.request('verify')
        return [], None

    def accept(self, operation, result, now_ms):
        if not power_off_ok(self):
            return [error('power_off_required', 'Confirm that power is off first.')], None
        if self.stale(now_ms):
            messages = [error('stale_frame', 'Frame expired while checking.')]
            if self.state == 'VERIFY':
                messages.append(self.step('AWAIT'))
            return messages, None
        if operation == 'identify':
            item = result['item']
            if item != 'unknown' and not part_allowed(item):
                return [error('refused_part', 'Unsupported part.'), self.say(PART_REFUSAL)], None
            if item == 'unknown' or result['conf'] < HEADER_MIN_CONF:
                return [self.say("I can't tell what that is. Hold it closer.")], None
            header = next((h for h in self.board['headers'] if item in h['accepts']), None)
            if header is None:
                return [self.say('There is no checked header for that part on this board.')], None
            self.item, self.target_header = item, header['name']
            return [self.step('LOCATE')], self.request('locate')
        if operation == 'locate':
            if (not result['found'] or result['conf'] < HEADER_MIN_CONF
                    or result.get('blur') or result.get('glare') or 'bbox' not in result):
                return [self.say('Move closer so I can see the board.')], None
            box = dict(id='target', header=self.target_header, bbox=result['bbox'],
                       role='target', conf=result['conf'])
            pins = result.get('pins', {})
            pin_ok = pins.get('pin_ok') is True
            if pin_ok:
                box['pins'] = [dict(n=n, x=p[0], y=p[1], role='target')
                               for n, p in enumerate(pins['grid'], 1)]
            boxes = [box] + [dict(id=f'other-{n}', role='other', **other)
                             for n, other in enumerate(result.get('others', []))
                             if other['conf'] >= HEADER_MIN_CONF]
            overlay = dict(v=1, type='overlay', seq=self.image['seq'],
                           level='pin' if pin_ok else 'header', boxes=boxes)
            return [overlay, self.step('GUIDE'), self.say(
                f"That is the {self.item.replace('_', ' ')}. It goes on {self.target_header}."
            ), self.step('AWAIT')], None
        if operation == 'verify':
            abstain = bool(result.get('abstain') or result.get('blur') or result.get('glare')
                           or (result['pass'] and result['conf'] < PASS_MIN_CONF))
            passed = result['pass'] and result['conf'] >= PASS_MIN_CONF and not abstain
            verdict = dict(v=1, type='verdict', step_id='1', **{
                'pass': passed, 'abstain': abstain, 'conf': result['conf'], 'reason': result['reason']})
            if 'offset_pins' in result:
                verdict['offset_pins'] = result['offset_pins']
            if abstain:
                return [verdict, self.step('AWAIT'), self.say("I can't tell. Hold still and move closer.")], None
            if passed:
                return [verdict, self.step('DONE'), self.say('That is seated correctly. This step is done.')], None
            return [verdict, self.step('RETRY'), self.step('LOCATE'),
                    self.say(result['reason'] or 'Check the connection and try again.')], None
        raise ValueError('Unknown vision operation')

    def vision_failed(self):
        messages = [error('vision_unavailable', 'Vision is unavailable. Try again.')]
        if self.state == 'VERIFY':
            messages.append(self.step('AWAIT'))
        return messages
