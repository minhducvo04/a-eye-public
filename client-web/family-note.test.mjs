import test from 'node:test';
import assert from 'node:assert/strict';
import { confirmation, createApproval, setupFamily } from './family-note.js';

test('only exact spoken send or Send tap confirms; anything else cancels', () => {
  assert.equal(confirmation(' SEND '), 'send');
  assert.equal(confirmation('Send.'), 'send');
  for (const text of ['yes', 'send it', 'no', '', 'do not send', 'Zend.', 'Send? no']) assert.equal(confirmation(text), 'cancel');
});

test('completion never suggests sharing without a contact; removing the last contact disables it', async () => {
  const saved = new Map();
  const put = (key, value) => { saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key)); Object.defineProperty(globalThis, key, {value, configurable: true}); };
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {value: '', textContent: '', open: false, hidden: false, disabled: false,
      replaceChildren() {}, add() {}});
    return nodes.get(id);
  };
  let refresh, rows = [], sid = 'one';
  put('document', {getElementById: node});
  put('Option', class {});
  put('window', {setInterval(fn) { refresh = fn; }, addEventListener() {}, clearInterval() {}});
  try {
    const flow = setupFamily({session: () => sid, speak: async () => {},
      request: async () => ({contacts: rows, tasks: {}, replies: []})});
    await refresh();
    flow.complete('ee_led');
    assert.equal(node('family-panel').open, false);
    assert.equal(node('family-heading').textContent, 'People');
    assert.equal(node('family-sharing').hidden, true);
    assert.equal(node('family-draft').disabled, true);
    rows = [{id: 'friend', name: 'Fixture', relation: 'friend'}];
    await refresh();
    flow.complete('ee_led');
    assert.equal(node('family-panel').open, true);
    assert.equal(node('family-heading').textContent, 'Want to show someone?');
    assert.equal(node('family-sharing').hidden, false);
    rows = [];
    await refresh();
    assert.equal(node('family-sharing').hidden, true);
    assert.equal(node('family-send').disabled, true);
    sid = 'two';
    await refresh();
    flow.complete('ee_led');
    assert.equal(node('family-panel').open, false);
  } finally {
    for (const [key, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
  }
});
test('approval waits for completed read aloud; cancelled drafts cannot send', async () => {
  const calls = [];
  const a = createApproval(async (action, data) => { calls.push([action, data]); return {}; });
  a.start({ id: 'fixture' });
  assert.equal(await a.confirm('send'), false);
  assert.equal(calls[0][1].confirmation, 'cancel');
  a.start({ id: 'fixture2' });
  await a.heard();
  assert.equal(await a.confirm('send'), true);
  assert.equal(await a.confirm('send'), false);
  assert.deepEqual(calls.map(x => x[0]), ['send', 'heard', 'send']);
});
