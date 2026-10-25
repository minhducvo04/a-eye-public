import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { triggerClient } from './trigger.js';

for (const micOn of [false, true]) {
  test(`agent wake preserves micOn=${micOn} and still speaks the line`, async () => {
    const source = readFileSync(new URL('./agent.js', import.meta.url), 'utf8');
    const start = source.indexOf('  function startTrigger() {');
    const end = source.indexOf('\n  async function onMic()', start);
    assert.ok(start >= 0 && end > start);
    let wake;
    let micStarts = 0;
    const spoken = [];
    runInNewContext(`${source.slice(start, end)}\nstartTrigger();`, {
      stopTrigger: null,
      triggerClient: options => { wake = options.wake; },
      ui: { getState: () => ({ micOn }) },
      voice: { isListening: () => false },
      micRoute: { phone: false },
      startMic: async () => { micStarts++; },
      onServer: message => { spoken.push(message); },
      randomId: () => 'wake-test',
    });
    await wake('Keys are in the bowl.');
    assert.equal(micStarts, micOn ? 1 : 0);
    assert.equal(spoken.length, 1);
    assert.equal(spoken[0].type, 'say');
    assert.equal(spoken[0].text, 'Keys are in the bowl.');
  });
}

test('wake starts listening once, acknowledges it, and leaves camera off', async () => {
  const state = { camera: false, listening: false, spoken: [] };
  const requests = [];
  let next;
  const stop = triggerClient({
    session: () => 's', available: () => true, listening: () => state.listening,
    request: async (_, body) => {
      requests.push(body);
      return { wake: { id: 'one', line: 'Keys are in the bowl.' } };
    },
    wake: async line => { state.listening = true; state.spoken.push(line); },
    timer: fn => { next = fn; },
  });
  await new Promise(resolve => setImmediate(resolve));
  await next();
  stop();
  assert.equal(state.camera, false);
  assert.deepEqual(state.spoken, ['Keys are in the bowl.']);
  assert.equal(requests[1].ack, 'one');
  assert.equal(requests[1].listening, true);
});
