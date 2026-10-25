// Port of ios/AEyeCore/Tests/AEyeCoreTests/AudioLevelTests.swift, plus the web pooling and strip copy.
import test from "node:test";
import assert from "node:assert/strict";
import {
  normalizedLevel, smoothedLevel, appendingLevel, isVoiceLevel, poolLevel, listeningLine, waveBars,
} from "./listen.js";

test("normalised level maps decibels to zero one", () => {
  assert.equal(normalizedLevel(0), 0);
  assert.equal(normalizedLevel(1), 1);
  assert.equal(normalizedLevel(0.0001), 0); // -80 dB: room silence
  const speech = normalizedLevel(0.05); // about -26 dB: normal talking
  assert.ok(speech > 0.5);
  assert.ok(speech < 1);
  assert.equal(normalizedLevel(NaN), 0);
});

test("smoothing rises at once and falls slowly", () => {
  assert.equal(smoothedLevel(0.2, 0.8), 0.8);
  const fall = smoothedLevel(0.8, 0);
  assert.ok(fall < 0.8);
  assert.ok(fall > 0.4);
});

test("appending level keeps the newest and does not mutate", () => {
  const history = [0.1, 0.2, 0.3];
  assert.deepEqual(appendingLevel(history, 0.4, 3), [0.2, 0.3, 0.4]);
  assert.deepEqual(history, [0.1, 0.2, 0.3]);
  assert.deepEqual(appendingLevel([], 0.5, 3), [0.5]);
});

test("voice threshold", () => {
  assert.equal(isVoiceLevel(0.05), false);
  assert.equal(isVoiceLevel(0.6), true);
});

test("readings are pooled into 20 ms windows", () => {
  let r = poolLevel(null, 0.1, 0);
  assert.equal(r.rms, null);
  r = poolLevel(r.pool, 0.3, 10);
  assert.equal(r.rms, null);
  r = poolLevel(r.pool, 0.0, 20);
  assert.ok(Math.abs(r.rms - Math.sqrt((0.01 + 0.09) / 3)) < 1e-9);
  // a slow meter (50 ms apart) still gives one value per reading
  const slow = poolLevel(poolLevel(null, 0.2, 0).pool, 0.2, 50);
  assert.ok(Math.abs(slow.rms - 0.2) < 1e-9);
});

test("strip line follows ListeningStrip", () => {
  assert.equal(listeningLine({ muted: true, heard: "hi", levels: [1] }).text, "MIC OFF");
  assert.deepEqual(listeningLine({ muted: false, heard: "open the case", levels: [] }).tone, "heard");
  assert.equal(listeningLine({ muted: false, heard: "", levels: [0.6] }).text, "HEARING YOU…");
  assert.equal(listeningLine({ muted: false, heard: "", levels: [0.1] }).text, "LISTENING");
  assert.equal(listeningLine({ muted: false, heard: "", levels: [0.1] }).active, false);
});

test("bars keep a fixed width, newest on the right", () => {
  const bars = waveBars([1], 28, 96, 28);
  assert.equal(bars.length, 28);
  assert.equal(bars[0].h, 2);
  assert.equal(bars[27].h, 28);
  assert.ok(Math.abs(bars[27].x + 2 - 96) < 1e-9);
});
