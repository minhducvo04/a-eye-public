import test from 'node:test';
import assert from 'node:assert/strict';
import { isHeadset, requestLocalMedia } from './headset.js';

const safari = 'Mozilla/5.0 (Apple Vision Pro; CPU visionOS 2_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15';
test('explicit headset flag and visionOS Safari; desktop Safari stays a laptop', () => {
  assert.equal(isHeadset('', safari), true);
  assert.equal(isHeadset('?device=headset', 'desktop'), true);
  assert.equal(isHeadset('?device=phone', safari), true);
  assert.equal(isHeadset('', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Version/18.0 Safari/605.1.15'), false);
  assert.equal(isHeadset('?pair=123456', 'iPhone Safari'), false);
});
test('headset video requests never reach getUserMedia, including combined permission requests', async () => {
  const calls = [];
  const media = { getUserMedia: async c => { calls.push(c); return 'stream'; } };
  for (const video of [true, { facingMode: 'environment' }, { deviceId: { exact: 'persona' } }]) {
    await assert.rejects(requestLocalMedia(media, true, { video, audio: true }), /phone camera/);
  }
  assert.deepEqual(calls, []);
  assert.equal(await requestLocalMedia(media, true, { audio: true }), 'stream');
  assert.equal(await requestLocalMedia(media, false, { video: true }), 'stream');
  assert.deepEqual(calls, [{ audio: true }, { video: true }]);
});
