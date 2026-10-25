import assert from 'node:assert/strict';
import test from 'node:test';
import { nextTurnState, statusLine, createEarlyFinal } from './turn.js';

test('pipeline stages, timestamps, and server timing', () => {
  let s = nextTurnState(null, { type: 'mic_start' }, 0);
  assert.equal(statusLine(s, 100), 'Hearing 0.1 s');
  s = nextTurnState(s, { type: 'interim', text: 'hello' }, 100);
  assert.match(statusLine(s, 200), /Hearing.*hello/);
  s = nextTurnState(s, { type: 'sent', text: 'hello' }, 300);
  assert.equal(statusLine(s, 400), 'Sent 0.1 s');
  s = nextTurnState(s, { type: 'turn', stage: 'received', turn_id: 'a', at_ms: 99999 }, 500);
  assert.equal(statusLine(s, 600), 'Received 0.1 s');
  for (const [stage, label] of [['transcribing', 'Transcribing'], ['thinking', 'Thinking'], ['backup', 'Brain slow, trying backup'], ['speaking', 'Speaking'], ['done', 'Done'], ['cancelled', 'Cancelled'], ['failed', "Didn't catch that"]]) {
    s = nextTurnState(s, { type: 'turn', stage, turn_id: 'a', at_ms: 99999, typical_ms: 1100 }, 1000);
    assert.ok(statusLine(s, 2800).startsWith(label));
    if (stage === 'thinking') assert.equal(statusLine(s, 2800), 'Thinking 1.8 s · usually 1.1 s');
  }
  s = nextTurnState(s, { type: 'first_text' }, 3000);
  s = nextTurnState(s, { type: 'first_sound' }, 3200);
  s = nextTurnState(s, { type: 'first_sound' }, 3300);
  assert.deepEqual(s.timestamps, { sent: 300, received: 500, first_text: 3000, first_sound: 3200 });
});

test('ack retries once at two seconds and then shows connection problem', () => {
  let s = nextTurnState(null, { type: 'sent', text: 'hello' }, 0);
  s = nextTurnState(s, { type: 'tick' }, 1999);
  assert.equal(s.retries, 0);
  s = nextTurnState(s, { type: 'tick' }, 2000);
  assert.equal(s.retries, 1);
  assert.match(statusLine(s, 2000), /^Not received, retrying/);
  s = nextTurnState(s, { type: 'tick' }, 4000);
  assert.match(statusLine(s, 4000), /^Connection problem/);
  assert.equal(nextTurnState(s, { type: 'tick' }, 9000).retries, 1);
  s = nextTurnState(s, { type: 'closed' }, 9100);
  assert.match(statusLine(s, 9200), /^Connection lost, retrying/);
});

test('no speech timeout, mic stop, and failure details', () => {
  let s = nextTurnState(null, { type: 'mic_start' }, 0);
  assert.match(statusLine(nextTurnState(s, { type: 'tick' }, 8000), 8000), /^Didn't catch that, try again/);
  s = nextTurnState(s, { type: 'mic_stop' }, 100);
  assert.equal(statusLine(nextTurnState(s, { type: 'tick' }, 9000), 9000), 'Mic muted');
  s = nextTurnState(s, { type: 'turn', stage: 'failed', detail: 'Try a shorter question' }, 10000);
  assert.match(statusLine(s, 10000), /^Try a shorter question/);
});

function fakeClock() {
  let now = 0, id = 0;
  const tasks = new Map();
  return {
    setTimeout(fn, ms) { tasks.set(++id, { at: now + ms, fn }); return id; },
    clearTimeout(key) { tasks.delete(key); },
    advance(ms) { now += ms; for (const [key, task] of tasks) if (task.at <= now) { tasks.delete(key); task.fn(); } },
  };
}

test('early final uses latest interim at 700ms and never sends a late correction as another turn', () => {
  const clock = fakeClock(), sent = [];
  const early = createEarlyFinal(text => sent.push(text), clock);
  early.interim('place', 'a');
  clock.advance(600);
  early.interim('place the fan', 'a');
  clock.advance(699);
  assert.deepEqual(sent, []);
  clock.advance(1);
  assert.deepEqual(sent, ['place the fan']);
  early.final('Place the fan.', 'a');
  early.final('put the fan over there instead', 'a');
  early.interim('more late words', 'a');
  clock.advance(1000);
  assert.equal(sent.length, 1);
  early.final('new question', 'b');
  assert.deepEqual(sent, ['place the fan', 'new question']);
  early.interim('do not send', 'c');
  early.reset();
  clock.advance(1000);
  assert.equal(sent.length, 2);
});

for (const [headset, home, live, policy] of [[false, false, false, "engineering"], [false, false], [true, false], [false, true], [false, false, true], [false, false, true, "general"], [false, false, true, "optout"], [false, false, true, "private"]]) test(live ? `${policy || 'live'}: Live defaults to Google and uses existing hello and caption messages` : home ? 'sink selection and presenter controls use real client contracts' : headset ? 'headset call never acquires local video through camera or permission controls' : 'call page toggles camera, keeps voice-only hello, retries text, reconnects, and renders streamed replies', async () => {
  const saved = new Map();
  const put = (key, value) => { saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key)); Object.defineProperty(globalThis, key, { value, configurable: true, writable: true }); };
  const nodes = new Map(), intervals = new Map(), timeouts = new Map();
  let timerId = 0, stops = 0, gets = 0;
  const constraints = [];
  let proofAvailable = true, familyReply = null;
  let recognition;
  const synth = { getVoices: () => [{ localService: true, lang: "en-US" }], cancel() {}, speak(u) { this.current = u; } };
  const proofRequests = [];
  let presenterEnabled = false;
  const presenterRequests = [];
  put('fetch', async (path, options) => {
    if (path.startsWith('/api/dad-demo?')) {
      presenterRequests.push(options);
      const body = options?.body ? JSON.parse(options.body) : null;
      if (body && typeof body.enabled === 'boolean') presenterEnabled = body.enabled;
      return { ok: true, json: async () => ({ enabled: presenterEnabled }) };
    }
    if (path.startsWith('/api/family?') && familyReply) return {
      ok: true, json: async () => ({ contacts: [], tasks: {}, replies: [familyReply] }),
    };
    if (!path.startsWith('/api/proof?')) throw new Error('Fixture unavailable');
    proofRequests.push(path);
    return { ok: proofAvailable, json: async () => ({ pictures_saved: 0, cloud_ai_calls: 3, bytes_to_cloud: 125 }) };
  });
  const context = new Proxy({}, { get: (_obj, key) => key === 'measureText' ? () => ({ width: 20 }) : key === 'createRadialGradient' ? () => ({ addColorStop() {} }) : () => {} });
  class Element {
    constructor() { this.handlers = {}; this.attrs = {}; this.children = []; this.style = {}; this.textContent = ''; this.value = ''; this.clientWidth = 390; this.clientHeight = 844; this.width = 320; this.height = 200; const classes = new Set(); this.classList = { add: k => classes.add(k), remove: k => classes.delete(k), contains: k => classes.has(k), toggle: (k, on) => on ? classes.add(k) : classes.delete(k) }; }
    addEventListener(name, fn) { this.handlers[name] = fn; }
    setAttribute(key, value) { this.attrs[key] = value; }
    getAttribute(key) { return this.attrs[key]; }
    removeAttribute(key) { delete this.attrs[key]; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    querySelector() { return null; }
    getContext() { return context; }
    play() { return Promise.resolve(); }
  }
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  class Socket {
    static OPEN = 1; static CONNECTING = 0; static all = [];
    constructor(url) { this.url = url; this.readyState = 0; this.handlers = {}; this.messages = []; Socket.all.push(this); }
    addEventListener(name, fn) { this.handlers[name] = fn; }
    send(text) { this.messages.push(JSON.parse(text)); }
    open() { this.readyState = 1; this.handlers.open(); }
    message(data) { this.handlers.message({ data: JSON.stringify(data) }); }
    close() { this.readyState = 3; this.handlers.close(); }
  }
  const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
  put('document', { getElementById: node, createElement: () => new Element(), createTextNode: text => ({ textContent: text }), hidden: false });
  put('window', { isSecureContext: true, innerWidth: 390, innerHeight: 844, devicePixelRatio: 1, matchMedia: () => ({ matches: true }), addEventListener() {}, requestAnimationFrame() { return 1; }, setInterval(fn, ms) { intervals.set(++timerId, { fn, ms }); return timerId; }, clearInterval(id) { intervals.delete(id); }, setTimeout(fn, ms) { timeouts.set(++timerId, { fn, ms }); return timerId; }, clearTimeout(id) { timeouts.delete(id); } });
  put('localStorage', policy === 'optout' || policy === 'private' ? {
    getItem: key => key === 'aeye.cloud' && policy === 'optout' ? JSON.stringify({ google: false }) : key === 'aeye.private' && policy === 'private' ? '1' : null,
    setItem() {}, removeItem() {},
  } : null);
  put('location', { search: headset ? '?device=headset' : '', protocol: 'https:', host: 'example.test' });
  put('navigator', { mediaDevices: { enumerateDevices: async () => [], getUserMedia: async c => { constraints.push(c); gets++; return { getTracks: () => [{ stop() { stops++; } }] }; } } });
  put('SpeechRecognition', class { constructor() { recognition = this; } start() {} stop() {} });
  put('speechSynthesis', synth);
  put('SpeechSynthesisUtterance', class { constructor(text) { this.text = text; } });
  put('Option', class {});
  put('WebSocket', Socket);
  const realNow = Date.now;
  let now = 1000;
  Date.now = () => now;
  try {
    await import(`./agent.js?headset-test=${headset}&home=${home}&live=${live}&policy=${policy}`);
    assert.equal(node('ring-title').textContent, 'Gemini Live', 'Live is selected at launch');
    const choose = () => node('picker').handlers.click({ target: { closest: () => ({ getAttribute: name => policy === 'engineering' ? 'engineering' : policy === 'general' ? 'general' : live ? 'gemini-live' : home ? (name === 'data-pack' ? 'home' : 'plumbing') : 'general' }) } });
    if (!live && !home) {
      node('cloud-google').checked = false;
      node('cloud-google').handlers.change();
    }
    choose();
    await settle();
    if (policy === 'optout') {
      assert.equal(Socket.all.length, 0, 'Live opt-out must not silently start a different transport');
      assert.equal(node('settings').classList.contains('hidden'), false);
      assert.equal(node('live-start-notice').classList.contains('hidden'), false);
      assert.match(node('live-start-notice').textContent, /turn on Gemini Live \(Google\)/);
      node('cloud-google').checked = true;
      node('cloud-google').handlers.change();
      node('ring-hint').handlers.click();
      await settle();
      assert.equal(node('live-start-notice').classList.contains('hidden'), true);
    }
    const socket = Socket.all[0];
    assert.ok(socket);
    socket.open();
    assert.equal(socket.messages[0].camera, false);
    if (policy === 'private') {
      assert.equal(new URL(socket.url).searchParams.get('mode'), null);
      assert.equal(socket.messages[0].pack, 'general');
      assert.equal(Boolean(socket.messages[0].private), policy === 'private');
      assert.equal(node('cloud-google').checked, false);
      return;
    }
    if (live) {
      assert.equal(new URL(socket.url).searchParams.get('mode'), 'gemini-live');
      assert.equal(new URL(socket.url).searchParams.get('google'), '1');
      assert.equal(socket.messages[0].pack, 'general');
      assert.equal(socket.messages[0].stt, 'server');
      assert.equal(socket.messages[0].tts, 'server');
      socket.message({ type: 'say', agent: 'helper', utterance_id: 'live', text: 'A blue board.' });
      assert.equal(node('replies').children[0].textContent, 'A blue board.');
      node('cloud-google').checked = false;
      node('cloud-google').handlers.change();
      assert.equal(socket.readyState, 3, 'revoking Google closes its session');
      return;
    }
    if (policy === 'engineering') {
      assert.equal(new URL(socket.url).searchParams.get('mode'), 'engineering');
      assert.equal(socket.messages[0].pack, 'general');
      socket.message({ type: 'agent_state', agent: 'helper', state: 'listening' });
      assert.equal(socket.messages[1].text, 'light an LED');
      socket.message({ type: 'agent_state', agent: 'helper', state: 'listening' });
      assert.equal(socket.messages.length, 2);
      socket.message({ type: 'turn', stage: 'speaking', detail: 'Engineering: complete' });
      assert.equal(Boolean(node('family-panel').open), false);
      return;
    }
    if (home) {
      assert.equal(socket.messages[0].pack, 'home');
      socket.message({ type: 'agent_state', agent: 'helper', state: 'listening' });
      assert.equal(socket.messages[1].text, 'sink');
      assert.equal(socket.messages[1].source, 'typed');
      socket.message({ type: 'agent_state', agent: 'helper', state: 'listening' });
      assert.equal(socket.messages.length, 2);
      socket.message({ type: 'turn', turn_id: 'presenter', stage: 'done', detail: 'Presenter available' });
      await settle();
      assert.equal(presenterRequests[0].method, undefined, 'discovery must be a GET');
      await node('guide-test').handlers.change({ target: { checked: true } });
      assert.deepEqual(JSON.parse(presenterRequests.at(-1).body), { enabled: true });
      assert.equal(node('guide-test-label').classList.contains('hidden'), false);
      await node('guide-pass').handlers.click();
      assert.deepEqual(JSON.parse(presenterRequests.at(-1).body), { state: 'closed' });
      assert.equal(socket.messages.at(-1).text, 'next');
      assert.equal(socket.messages.at(-1).source, 'typed');
      socket.message({ type: 'turn', stage: 'speaking', detail: 'Home: complete' });
      assert.equal(Boolean(node('family-panel').open), false, 'no contact means no sharing suggestion');
      return;
    }
    const proofPoll = [...intervals.values()].find(timer => timer.fn.name === 'refreshProof');
    await proofPoll.fn();
    assert.ok(proofRequests.at(-1).includes(encodeURIComponent(socket.messages[0].session_id)));
    assert.equal(node('proof-cloud-calls').textContent, 'Cloud AI calls: 3');
    assert.equal(node('proof-cloud-bytes').textContent, 'Bytes to cloud: 125');
    proofAvailable = false;
    await proofPoll.fn();
    assert.equal(node('proof-cloud-calls').textContent, 'Cloud AI calls: unknown');
    assert.equal(node('proof-cloud-bytes').textContent, 'Bytes to cloud: unknown');
    socket.message({ type: 'turn', stage: 'speaking', turn_id: 'proof', detail: 'Brain: local / fixture-model' });
    assert.equal(node('proof-brain').textContent, 'Brain: local / fixture-model');
    socket.message({ type: 'turn', stage: 'speaking', turn_id: 'proof', detail: 'Stopped by code' });
    assert.equal(node('proof-safety').classList.contains('hidden'), false);
    socket.message({ type: 'turn', stage: 'received', turn_id: 'next' });
    assert.equal(node('proof-safety').classList.contains('hidden'), true);
    socket.message({ type: 'turn', stage: 'thinking', turn_id: 'next', detail: 'Valve: checking' });
    assert.equal(node('valve-check').textContent, 'Checking the valve...');
    socket.message({ type: 'turn', stage: 'thinking', turn_id: 'next', detail: 'Valve: closed' });
    assert.match(node('valve-check').textContent, /^Water is off/);
    if (headset) {
      node('start-camera').handlers.click();
      node('permission-retry').handlers.click();
      await settle();
      assert.equal(gets, 0);
      node('devices-allow').handlers.click();
      await settle();
      assert.equal(constraints.some(c => c.video), false);
      socket.message({ type: 'pair_status', role: 'call', state: 'joined' });
      assert.equal(node('headset-status').textContent, 'Phone camera connected');
      const before = gets;
      node('devices-allow').handlers.click();
      await settle();
      assert.equal(gets, before);
      socket.message({ type: 'pair_status', role: 'call', state: 'left' });
      assert.equal(node('headset-status').textContent, 'Waiting for your phone camera');
      node('end-call').handlers.click();
      return;
    }
    node('start-camera').handlers.click();
    await settle();
    assert.equal(gets, 1);
    assert.equal(socket.messages.at(-1).type, 'camera');
    assert.equal(socket.messages.at(-1).on, true);
    assert.equal([...intervals.values()].filter(t => t.ms === 1000).length, 1);
    node('start-camera').handlers.click();
    assert.equal(stops, 1);
    assert.equal(socket.messages.at(-1).on, false);
    assert.equal([...intervals.values()].filter(t => t.ms === 1000).length, 0);
    node('start-camera').handlers.click();
    await settle();
    assert.equal(gets, 2);
    node('typed').value = 'hello';
    node('composer').handlers.submit({ preventDefault() {} });
    assert.equal(socket.messages.at(-1).type, 'user_text');
    assert.match(node('pipeline').textContent, /^Sent/);
    now += 2000;
    const tick = [...intervals.values()].find(t => t.ms === 100).fn;
    tick();
    assert.equal(socket.messages.filter(m => m.type === 'user_text').length, 2);
    now += 2000;
    tick();
    assert.match(node('pipeline').textContent, /^Connection problem/);
    socket.message({ type: 'turn', stage: 'received', turn_id: 'a', at_ms: 10 });
    socket.message({ type: 'turn', stage: 'thinking', turn_id: 'a', at_ms: 20, typical_ms: 1100 });
    now += 1800;
    tick();
    assert.equal(node('pipeline').textContent, 'Thinking 1.8 s · usually 1.1 s');
    socket.message({ type: 'reply_delta', turn_id: 'a', text: 'Hello' });
    socket.message({ type: 'reply_delta', turn_id: 'a', text: ' there' });
    assert.equal(node('replies').children[0].textContent, 'Hello there');
    socket.message({ type: 'agent_state', agent: 'shopper', state: 'speaking' });
    assert.equal(node('presence-dot').attrs['data-agent'], 'shopper');
    socket.message({ type: 'agent_state', agent: 'helper', state: 'thinking' });
    assert.equal(node('presence-dot').attrs['data-agent'], 'helper');
    assert.equal(node('presence-dot').attrs['aria-label'], 'Helper is thinking');
    socket.close();
    assert.equal(node('valve-check').classList.contains('hidden'), true);
    assert.match(node('pipeline').textContent, /^Connection lost, retrying/);
    const retry = [...timeouts.values()].at(-1);
    assert.equal(retry.ms, 500);
    retry.fn();
    Socket.all[1].open();
    assert.equal(Socket.all[1].messages[0].type, 'hello');
    assert.equal(Socket.all[1].messages[0].camera, true);
    Socket.all[1].close();
    [...timeouts.values()].at(-1).fn();
    Socket.all[2].close();
    assert.equal([...timeouts.values()].at(-1).ms, 1000);
    [...timeouts.values()].at(-1).fn();
    Socket.all[3].open();
    // Actual call-page wiring: contact commands cannot reach the WebSocket during local speech.
    node('mic').handlers.click();
    await settle();
    familyReply = { id: 'command-reply', name: 'Fixture Friend', text: 'stop' };
    const familyPoll = [...intervals.values()].find(t => t.fn.name === 'refresh');
    const reading = familyPoll.fn();
    await settle();
    assert.equal(synth.current.text, 'Fixture Friend replied. stop');
    const active = Socket.all[3];
    const beforeSpeech = active.messages.length;
    for (const text of ['stop', 'find a replacement part']) {
      recognition.onresult({ results: [{ 0: { transcript: text }, isFinal: true }] });
    }
    assert.equal(active.messages.slice(beforeSpeech).some(m => m.type === 'user_text'), false);
    synth.current.onend();
    await reading;
    recognition.onresult({ results: [{ 0: { transcript: 'find a replacement part' }, isFinal: true }] });
    assert.equal(active.messages.at(-1).type, 'user_text');
    assert.equal(active.messages.at(-1).text, 'find a replacement part');
    const lastTimer = timerId;
    node('end-call').handlers.click();
    assert.equal(Socket.all[3].readyState, 3);
    assert.ok([...timeouts.keys()].every(id => id <= lastTimer), 'End call does not schedule a reconnect');
    assert.equal(node('call').classList.contains('hidden'), true);
  } finally {
    if (home || live || policy === 'engineering') node('end-call').handlers.click();
    Date.now = realNow;
    for (const [key, descriptor] of saved) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  }
});

test('a known mic cause replaces the generic failure and clears on speech', () => {
  let s = nextTurnState(undefined, { type: 'mic_start' }, 0);
  s = nextTurnState(s, { type: 'cause', detail: 'No speech detected' }, 10);
  assert.match(statusLine(nextTurnState(s, { type: 'tick' }, 8000), 8000), /^No speech detected/);
  s = nextTurnState(s, { type: 'interim', text: 'hello' }, 20);
  assert.equal(s.cause, '');
  const muted = nextTurnState(nextTurnState(undefined, { type: 'cause', detail: 'Mic blocked' }, 0), { type: 'mic_stop' }, 1);
  assert.equal(statusLine(muted, 2), 'Mic blocked');
});

test('a finished turn shows the slow part the server named', () => {
  const s = nextTurnState(undefined, { type: 'turn', stage: 'done', turn_id: 't', detail: 'Slow: brain 1.3 s' }, 0);
  assert.match(statusLine(s, 500), /^Done 0\.5 s · Slow: brain 1\.3 s$/);
});

test('expert elapsed state survives new turns and clears only its own completion', () => {
  let s = nextTurnState(null, { type: 'turn', stage: 'expert', turn_id: 'a' }, 100);
  s = nextTurnState(s, { type: 'sent', text: 'hello' }, 200);
  s = nextTurnState(s, { type: 'turn', stage: 'received', turn_id: 'b' }, 300);
  assert.equal(statusLine(s, 2100), 'EXPERT 2.0 s');
  s = nextTurnState(s, { type: 'turn', stage: 'expert_done', turn_id: 'other' }, 2200);
  assert.match(statusLine(s, 2200), /^EXPERT/);
  s = nextTurnState(s, { type: 'turn', stage: 'expert_done', turn_id: 'a' }, 2300);
  assert.equal(s.turnId, 'b');
  assert.match(statusLine(s, 2300), /^Received/);
});
