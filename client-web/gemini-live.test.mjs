import test from 'node:test';
import assert from 'node:assert/strict';
import { liveSettings } from './gemini-live.js';

test('Live and General use Google unless opted out or Private', () => {
  assert.equal(liveSettings('general', true, false).allowed, true);
  assert.equal(liveSettings('gemini-live', false, false).allowed, false);
  assert.equal(liveSettings('gemini-live', true, true).allowed, false);
  assert.equal(liveSettings('gemini-live', true, false).allowed, true);
  assert.match(liveSettings('gemini-live', true, false).notice, /audio.*video.*Google/);
});

test('specialist flows retain their pipeline', () => {
  for (const mode of ['home', 'memory', 'pc_build']) assert.equal(liveSettings(mode, true, false).allowed, false);
  assert.equal(liveSettings('general', false, false).allowed, false);
  assert.equal(liveSettings('general', true, true).allowed, false);
});
