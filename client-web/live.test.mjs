import assert from "node:assert/strict";
import test from "node:test";
import {
  MIN_FRAME_GAP_MS,
  REPLY_TIMEOUT_MS,
  decideFrame,
  frameOrientation,
  initialLiveState,
  markFrameSent,
  noteServerSeq,
  scaledSize,
  sentFps,
} from "./live.js";

function ready(extra = {}) {
  return {
    ...initialLiveState(),
    cameraOn: true,
    socketOpen: true,
    bufferedAmount: 0,
    ...extra,
  };
}

test("scaledSize caps the long side at 640 px", () => {
  assert.deepEqual(scaledSize(1280, 720), { w: 640, h: 360 });
  assert.deepEqual(scaledSize(720, 1280), { w: 360, h: 640 });
  assert.deepEqual(scaledSize(640, 480), { w: 640, h: 480 });
  assert.deepEqual(scaledSize(100, 50), { w: 100, h: 50 });
});

test("frameOrientation uses screen.orientation, then the window", () => {
  assert.equal(frameOrientation("portrait-primary", 100, 800), "portrait");
  assert.equal(frameOrientation("portrait-secondary", 100, 800), "portrait_upside_down");
  assert.equal(frameOrientation("landscape-primary", 800, 100), "landscape_left");
  assert.equal(frameOrientation("landscape-secondary", 800, 100), "landscape_right");
  assert.equal(frameOrientation("", 390, 844), "portrait");
  assert.equal(frameOrientation(undefined, 844, 390), "landscape_left");
});

test("rate limit allows a frame at 500 ms and not before", () => {
  let state = ready();
  assert.equal(decideFrame(state, 0).ok, true);
  state = markFrameSent(state, 1, 0);
  state = noteServerSeq(state, { type: "overlay", seq: 1 }, 100);
  assert.equal(state.lastRttMs, 100);
  const early = decideFrame(state, MIN_FRAME_GAP_MS - 1);
  assert.equal(early.ok, false);
  assert.equal(early.reason, "rate");
  assert.equal(decideFrame(state, MIN_FRAME_GAP_MS).ok, true);
});

test("backpressure holds until a server message with seq >= the sent seq", () => {
  let state = markFrameSent(ready(), 4, 0);
  const held = decideFrame(state, 400);
  assert.equal(held.ok, false);
  assert.equal(held.reason, "awaiting");
  assert.equal(noteServerSeq(state, { type: "overlay", seq: 3 }, 200).pendingSeq, 4);
  assert.equal(noteServerSeq(state, { type: "say", text: "hi" }, 220).pendingSeq, 4);
  const released = noteServerSeq(state, { type: "error", seq: 4 }, 300);
  assert.equal(released.pendingSeq, null);
  assert.equal(released.lastRttMs, 300);
  assert.equal(decideFrame(released, 300).reason, "rate");
  assert.equal(decideFrame(released, MIN_FRAME_GAP_MS).ok, true);
  const newer = noteServerSeq(markFrameSent(ready(), 7, 0), { type: "overlay", seq: 8 }, 50);
  assert.equal(newer.pendingSeq, null);
  assert.equal(newer.lastRttMs, 50);
});

test("a 1.5 s timeout releases the in-flight frame", () => {
  const state = markFrameSent(ready(), 2, 1000);
  const before = decideFrame(state, 1000 + REPLY_TIMEOUT_MS - 1);
  assert.equal(before.ok, false);
  assert.equal(before.reason, "awaiting");
  assert.equal(before.released, null);
  const after = decideFrame(state, 1000 + REPLY_TIMEOUT_MS);
  assert.equal(after.ok, true);
  assert.equal(after.released, "timeout");
  assert.equal(after.state.pendingSeq, null);
});

test("a non-empty websocket buffer skips the send, including after timeout", () => {
  const idle = decideFrame(ready({ bufferedAmount: 1 }), 0);
  assert.equal(idle.ok, false);
  assert.equal(idle.reason, "buffer");
  const pending = markFrameSent(ready({ bufferedAmount: 8 }), 1, 0);
  const released = decideFrame(pending, REPLY_TIMEOUT_MS);
  assert.equal(released.ok, false);
  assert.equal(released.reason, "buffer");
  assert.equal(released.released, "timeout");
  assert.equal(released.state.pendingSeq, null);
});

test("pause and a hidden tab block frames", () => {
  assert.equal(decideFrame(ready({ paused: true }), 0).reason, "paused");
  assert.equal(decideFrame(ready({ hidden: true }), 0).reason, "hidden");
  assert.equal(decideFrame(ready({ socketOpen: false }), 0).reason, "socket");
  assert.equal(decideFrame(ready({ cameraOn: false }), 0).reason, "camera");
});

test("sent fps counts frames inside the last second", () => {
  let state = markFrameSent(ready(), 1, 0);
  state = markFrameSent({ ...state, pendingSeq: null, pendingSince: null }, 2, 500);
  assert.equal(sentFps(state.sendTimes, 500), 2);
  assert.equal(sentFps(state.sendTimes, 999), 2);
  assert.equal(sentFps(state.sendTimes, 1000), 1);
  assert.equal(sentFps(state.sendTimes, 1499), 1);
  assert.equal(sentFps(state.sendTimes, 1500), 0);
});
