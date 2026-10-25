import test from 'node:test';
import assert from 'node:assert/strict';
import { showFlipCamera, switchVideoTrack } from './flip-camera.js';
const devices = [{ kind: 'videoinput' }, { kind: 'videoinput' }];
test('only touch/coarse phones and tablets with two cameras show flip', () => {
  assert.equal(showFlipCamera({ userAgent: 'Android', maxTouchPoints: 1 }, true, devices), true);
  assert.equal(showFlipCamera({ userAgent: 'Macintosh', maxTouchPoints: 5 }, false, devices), true);
  assert.equal(showFlipCamera({ userAgent: 'Windows NT', maxTouchPoints: 10 }, true, devices), false);
  assert.equal(showFlipCamera({ userAgent: 'Macintosh', maxTouchPoints: 0 }, false, devices), false);
  assert.equal(showFlipCamera({ userAgent: 'Android', maxTouchPoints: 1 }, true, devices.slice(0, 1)), false);
});
function fixture() {
  const old = { kind: 'video', stop() { this.stopped = true; } };
  const mic = { kind: 'audio', stop() { this.stopped = true; } };
  const tracks = [old, mic];
  const stream = { getVideoTracks: () => tracks.filter(t => t.kind === 'video'), removeTrack: t => tracks.splice(tracks.indexOf(t), 1), addTrack: t => tracks.push(t) };
  return { old, mic, stream, tracks };
}
test('switch replaces video only, releases old camera before requesting front', async () => {
  const f = fixture();
  const next = { kind: 'video', stop() { this.stopped = true; } };
  await switchVideoTrack(f.stream, 'user', async constraints => {
    assert.equal(f.old.stopped, true);
    assert.equal(constraints.audio, false);
    assert.equal(constraints.video.facingMode, 'user');
    return { getVideoTracks: () => [next] };
  });
  assert.deepEqual(f.tracks, [f.mic, next]);
  assert.equal(f.mic.stopped, undefined);
});
test('ending call while permission is pending disposes new video', async () => {
  const f = fixture();
  const next = { stop() { this.stopped = true; } };
  assert.equal(await switchVideoTrack(f.stream, 'user', async () => ({ getVideoTracks: () => [next] }), () => false), false);
  assert.equal(next.stopped, true);
  assert.equal(f.mic.stopped, undefined);
});
