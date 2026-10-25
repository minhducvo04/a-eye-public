import test from 'node:test';
import assert from 'node:assert/strict';
import { pairMode, pairUrl, pairStatus } from './pair.js';
import { qrMatrix } from './qr.js';

test('pair URL never displays the master token and mode is explicit', () => {
  assert.deepEqual(pairMode('?pair=0123&token=a%26b'), { camera: true, code: '0123', token: 'a&b', join: '', mine: false });
  assert.equal(pairMode('?phone=mine').camera, true);
  assert.equal(pairMode('?phone=mine').mine, true);
  assert.equal(pairMode('').camera, false);
  assert.equal(pairMode('?pair=').camera, true);
  assert.equal(pairUrl('https://demo.test', '/agent.html?pair=ABCDEFGH&join=single-use', '?token=a%26b'), 'https://demo.test/agent.html?pair=ABCDEFGH&join=single-use');
  assert.equal(pairUrl('https://demo.test', '/agent.html?pair=0123', ''), 'https://demo.test/agent.html?pair=0123');
});
test('status distinguishes call and camera', () => {
  assert.equal(pairStatus({ state: 'joined' }), 'Phone camera + mic connected');
  assert.equal(pairStatus({ state: 'joined' }, true), 'Camera + mic connected');
  assert.equal(pairStatus({ state: 'camera', camera_on: false }), 'Phone camera off');
  assert.equal(pairStatus({ state: 'expired', detail: 'Code expired.' }), 'Code expired.');
  assert.match(pairStatus({ state: 'left' }), /disconnected/);
});
test('QR dimensions, finder patterns and bounded capacity', () => {
  for (const [text, size] of [['hello', 21], ['https://demo.test/agent.html?pair=0123', 29], ['x'.repeat(106), 37]]) {
    const m = qrMatrix(text);
    assert.equal(m.length, size);
    assert.ok(m.every(row => row.length === size && row.every(v => typeof v === 'boolean')));
    for (const [x, y] of [[0, 0], [size - 7, 0], [0, size - 7]]) {
      for (let dy = 0; dy < 7; dy++) for (let dx = 0; dx < 7; dx++) {
        const d = Math.max(Math.abs(dx - 3), Math.abs(dy - 3));
        assert.equal(m[y + dy][x + dx], d !== 2);
      }
    }
  }
  assert.equal(qrMatrix('x'.repeat(134)).length, 41);
  assert.throws(() => qrMatrix('x'.repeat(135)), /too long/);
});

test('the QR supports a long tunnel hostname with the complete single-use join URL', () => {
  const url = pairUrl('https://example-camera-demonstration-session.trycloudflare.com',
    '/agent.html?pair=ABCD2345&join=abcdefghijklmnopqrstuv', '?token=master');
  assert.equal(qrMatrix(url).length, 41);
});

test('phone entry: camera frames and mic utterances after joined, stops on disconnect', async () => {
  const { bootPairCamera } = await import('./pair.js');
  const saved = new Map();
  const put = (key, value) => { saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key)); Object.defineProperty(globalThis, key, { configurable: true, value }); };
  const children = [], timers = new Map();
  let stopped = 0, requested;
  function element(tag) {
    return { tag, handlers: {}, style: {}, setAttribute() {}, addEventListener(k, fn) { this.handlers[k] = fn; },
      append(...nodes) { (this.children ||= []).push(...nodes); }, videoWidth: 640, videoHeight: 480, play: async () => {},
      getContext: () => ({ drawImage() {} }), toDataURL: () => 'data:image/jpeg;base64,c3ludGhldGlj' };
  }
  class Socket {
    static OPEN = 1;
    constructor(url) { this.url = String(url); this.handlers = {}; this.messages = []; this.readyState = 1; this.bufferedAmount = 0; Socket.current = this; }
    addEventListener(k, fn) { this.handlers[k] = fn; }
    send(raw) { this.messages.push(JSON.parse(raw)); }
    close() { this.handlers.close(); }
    message(msg) { this.handlers.message({ data: JSON.stringify(msg) }); }
  }
  put('document', { hidden: false, createElement: element, body: { style: {}, replaceChildren() { children.length = 0; }, append(...nodes) { children.push(...nodes); } } });
  put('location', { href: 'https://demo.test/agent.html?pair=0123&token=test', search: '?pair=0123&token=test', protocol: 'https:' });
  put('window', { addEventListener() {} });
  put('navigator', { mediaDevices: { getUserMedia: async options => { requested = options; return { getTracks: () => [{ stop() { stopped++; } }] }; } } });
  put('WebSocket', Socket);
  put('setInterval', fn => { timers.set(1, fn); return 1; });
  put('clearInterval', id => timers.delete(id));
  let voiceOptions, listening = 0, unlocked = 0;
  const makeVoice = (options) => {
    voiceOptions = options;
    return { supported: { stt: true }, unlockAudio: async () => { unlocked++; },
      startListening: async () => { listening++; }, stopListening: () => { listening--; } };
  };
  try {
    assert.equal(bootPairCamera({ makeVoice, storage: null }), true);
    const [video, button, meter, status] = children;
    assert.equal(button.textContent, 'Start');
    await button.handlers.click();
    assert.equal(requested.audio, false); // the camera stream; voice.js opens the mic itself
    assert.equal(unlocked, 1);
    assert.equal(listening, 0); // the mic starts once the call takes the phone
    assert.equal(voiceOptions.sttMode, 'server');
    const socket = Socket.current;
    assert.match(socket.url, /token=test/);
    socket.handlers.open();
    assert.equal(socket.messages[0].role, 'camera');
    assert.equal(socket.messages[0].pair_code, '0123');
    timers.get(1)();
    assert.equal(socket.messages.length, 1);
    voiceOptions.onAudioChunk('AAA=', 0, false);
    assert.equal(socket.messages.length, 1); // nothing before joined
    socket.message({ type: 'pair_status', state: 'joined' });
    assert.equal(status.textContent, 'Camera + mic connected');
    await new Promise((r) => setImmediate(r));
    assert.equal(listening, 1);
    voiceOptions.onLevel(0.5);
    assert.equal(meter.children?.[0]?.style.width ?? '50%', '50%');
    voiceOptions.onBargePoint();
    voiceOptions.onAudioChunk('AQA=', 1, true);
    const mic = socket.messages.filter(m => m.type === 'barge_in' || m.type === 'audio_in');
    assert.equal(mic[0].type, 'barge_in');
    assert.deepEqual(mic.slice(1).map(m => [m.seq, m.final]), [[0, false], [1, true]]);
    timers.get(1)();
    assert.equal(socket.messages.at(-1).type, 'frame');
    assert.equal(socket.messages.at(-1).jpeg_b64, 'c3ludGhldGlj');
    await button.handlers.click();
    assert.equal(socket.messages.at(-1).on, false);
    assert.equal(stopped, 1);
    await button.handlers.click();
    assert.equal(socket.messages.at(-1).on, true);
    socket.message({ type: 'pair_status', state: 'left' });
    socket.close();
    assert.equal(stopped, 2);
    assert.equal(video.srcObject, null);
    assert.equal(button.disabled, true);
    assert.equal(timers.size, 0);
    assert.ok(socket.messages.every(msg => ['hello', 'frame', 'camera', 'audio_in', 'barge_in'].includes(msg.type)));
  } finally {
    for (const [key, descriptor] of saved) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  }
});

test("pair request uses the current session id, not the one at setup time", async () => {
  // Seen in the two-page run: agent.js starts a new session id per call, so a captured id was stale.
  const { setupPairCall } = await import("./pair.js");
  const sent = [];
  const listeners = {};
  globalThis.document = { getElementById: (id) => id === "pair-request"
    ? { addEventListener: (_e, fn) => { listeners.click = fn; } } : { textContent: "" } };
  let current = "old";
  setupPairCall((m) => { sent.push(m); return true; }, () => current);
  current = "new";
  listeners.click();
  assert.equal(sent[0].session_id, "new");
  delete globalThis.document;
});

test('phone mic replaces the laptop mic and leaving restores it', async () => {
  const { phoneMicRoute } = await import('./pair.js');
  const joined = { type: 'pair_status', role: 'call', state: 'joined' };
  const left = { type: 'pair_status', role: 'call', state: 'left' };
  let r = phoneMicRoute(null, joined, true);
  assert.deepEqual(r, { route: { phone: true, resume: true }, action: 'stop_local' });
  assert.equal(phoneMicRoute(r.route, { ...joined, state: 'camera' }, false).action, null);
  assert.equal(phoneMicRoute(r.route, left, false).action, 'start_local');
  assert.deepEqual(phoneMicRoute(r.route, left, false).route, { phone: false, resume: false });
  // Laptop mic was off: it stays off after the phone leaves.
  r = phoneMicRoute(null, joined, false);
  assert.equal(r.action, null);
  assert.equal(phoneMicRoute(r.route, left, false).action, null);
  // Messages to the camera device do not move the laptop mic.
  assert.equal(phoneMicRoute(null, { ...joined, role: 'camera' }, true).action, null);
});

test('phone audio waits for its barge point so barge_in never drops the utterance start', async () => {
  const { createPhoneAudio } = await import('./pair.js');
  const sent = [];
  const a = createPhoneAudio((m) => sent.push(m));
  a.chunk('AAA=', 0, false);
  a.chunk('AQA=', 1, false);
  assert.equal(sent.length, 0);
  a.bargePoint();
  a.chunk('AgA=', 2, true);
  assert.deepEqual(sent.map((m) => m.type === 'audio_in' ? m.seq : m.type), ['barge_in', 0, 1, 2]);
  // A short utterance that ends before its barge point is sent without a barge.
  sent.length = 0;
  a.chunk('AAA=', 3, false);
  a.chunk('AQA=', 4, true);
  assert.deepEqual(sent.map((m) => m.seq), [3, 4]);
  assert.ok(sent.every((m) => m.sample_rate === 16000));
});

test('remembered phone waits, joins its next call without a code, and waits again after it', async () => {
  const { bootPairCamera, rememberStep } = await import('./pair.js');
  assert.equal(rememberStep({ state: 'rejected', detail: 'Sign in with your own account', }, true).action, 'stop');
  assert.equal(rememberStep({ state: 'left', retry_s: 3, detail: 'Call ended.' }, false).action, 'stop');
  const saved = new Map();
  const put = (key, value) => { saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key)); Object.defineProperty(globalThis, key, { configurable: true, value }); };
  const children = [], timeouts = [];
  const track = { enabled: true, stop() {} };
  function element(tag) {
    return { tag, handlers: {}, style: {}, setAttribute() {}, addEventListener(k, fn) { this.handlers[k] = fn; },
      append(...nodes) { (this.children ||= []).push(...nodes); }, videoWidth: 640, videoHeight: 480, play: async () => {},
      getContext: () => ({ drawImage() {} }), toDataURL: () => 'data:image/jpeg;base64,c3ludGhldGlj' };
  }
  const sockets = [];
  class Socket {
    static OPEN = 1;
    constructor(url) { this.url = String(url); this.handlers = {}; this.messages = []; this.readyState = 1; this.bufferedAmount = 0; sockets.push(this); }
    addEventListener(k, fn) { this.handlers[k] = fn; }
    send(raw) { this.messages.push(JSON.parse(raw)); }
    close() { this.readyState = 3; this.handlers.close?.({ code: 4404 }); }
    message(msg) { this.handlers.message({ data: JSON.stringify(msg) }); }
  }
  const store = new Map([['aeye.phone.remember', '1']]);
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  put('document', { hidden: false, createElement: element, body: { style: {}, replaceChildren() { children.length = 0; }, append(...nodes) { children.push(...nodes); } } });
  put('location', { href: 'https://demo.test/agent.html?phone=mine', search: '?phone=mine', protocol: 'https:' });
  put('window', { addEventListener() {} });
  put('navigator', { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [track] }) } });
  put('WebSocket', Socket);
  put('setInterval', () => 1);
  put('clearInterval', () => {});
  put('setTimeout', (fn, ms) => { timeouts.push([fn, ms]); return timeouts.length; });
  put('clearTimeout', () => {});
  let listening = 0;
  const makeVoice = () => ({ supported: { stt: true }, unlockAudio: async () => {}, isListening: () => listening > 0,
    startListening: async () => { listening = 1; }, stopListening: () => { listening = 0; } });
  try {
    bootPairCamera({ makeVoice, storage });
    const [, button, , status, label] = children;
    assert.equal(label.children[0].checked, true);
    await button.handlers.click();
    sockets[0].handlers.open();
    assert.equal(sockets[0].messages[0].role, 'camera');
    assert.equal('pair_code' in sockets[0].messages[0], false);
    sockets[0].message({ type: 'pair_status', state: 'rejected', role: 'camera', detail: 'Waiting for your next call.', retry_s: 3 });
    assert.equal(status.textContent, 'Waiting for your next call.');
    assert.equal(button.textContent, 'Join my call');
    assert.equal(track.enabled, false); // nothing captured while waiting
    assert.equal(timeouts.at(-1)[1], 3000);
    sockets[0].close();
    timeouts.at(-1)[0]();
    assert.equal(sockets.length, 2);
    sockets[1].handlers.open();
    assert.equal('pair_code' in sockets[1].messages[0], false);
    sockets[1].message({ type: 'pair_status', state: 'joined', role: 'camera' });
    await new Promise((r) => setImmediate(r));
    assert.equal(track.enabled, true);
    assert.equal(listening, 1);
    sockets[1].message({ type: 'pair_status', state: 'left', role: 'camera', detail: 'Call ended.', retry_s: 3 });
    assert.equal(status.textContent, 'Call ended. Waiting for your next call.');
    assert.equal(listening, 0);
    sockets[1].close();
    timeouts.at(-1)[0]();
    assert.equal(sockets.length, 3); // tries the next call
    // Turning the switch off is remembered for this phone.
    label.children[0].checked = false;
    label.children[0].handlers.change();
    assert.equal(store.get('aeye.phone.remember'), '0');
  } finally {
    for (const [key, descriptor] of saved) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  }
});
