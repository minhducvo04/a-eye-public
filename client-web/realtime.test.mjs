import assert from "node:assert/strict";
import test from "node:test";
import {
  DEV_HUD_H,
  ORB_H,
  ORB_W,
  TRACK_GAP_MS,
  bargeSpeech,
  devHudPosition,
  initialHud,
  nextTrackerPlan,
  noteFinalWords,
  noteFirstSound,
  noteFrameReply,
  noteFrameSent,
  noteMetrics,
  notePaint,
  pushSay,
  shouldTrack,
} from "./realtime.js";

function say(fields) {
  return {
    v: 2,
    type: "say",
    agent: "helper",
    utterance_id: "u",
    text: "Line.",
    ...fields,
  };
}

function anchor(id, bbox) {
  return {
    kind: "box",
    id: `op-${id}`,
    bbox,
    anchor: { object_id: id, bbox },
  };
}

function overlaps(box, pose) {
  return box.x < pose.x + ORB_W
    && box.x + box.w > pose.x
    && box.y < pose.y + ORB_H
    && box.y + box.h > pose.y;
}

test("speech queue speaks parts in order and grows the caption", () => {
  let result = pushSay(null, say({ turn_id: "t4", part: 1, utterance_id: "u4.1", text: "Second." }));
  assert.deepEqual(result.speak, []);
  assert.equal(result.caption, "");

  result = pushSay(result.state, say({ turn_id: "t4", part: 2, utterance_id: "u4.2", text: "Third." }));
  assert.deepEqual(result.speak, []);

  result = pushSay(result.state, say({ turn_id: "t4", part: 0, utterance_id: "u4.0", text: "First.", final: false }));
  assert.deepEqual(result.speak.map((line) => line.text), ["First.", "Second.", "Third."]);
  assert.deepEqual(result.speak.map((line) => line.part), [0, 1, 2]);
  assert.equal(result.caption, "First. Second. Third.");

  const again = pushSay(result.state, say({ turn_id: "t4", part: 0, utterance_id: "u4.0", text: "First." }));
  assert.deepEqual(again.speak, []);
  assert.equal(again.caption, "First. Second. Third.");
});

test("speech queue ignores an old turn and clears the queue on barge-in", () => {
  let result = pushSay(null, say({ turn_id: "a", part: 0, utterance_id: "a0", text: "Alpha." }));
  result = pushSay(result.state, say({ turn_id: "a", part: 2, utterance_id: "a2", text: "Later." }));
  assert.equal(result.caption, "Alpha.");

  result = pushSay(result.state, say({ turn_id: "b", part: 0, utterance_id: "b0", text: "Beta." }));
  assert.equal(result.reset, true);
  assert.equal(result.caption, "Beta.");
  assert.deepEqual(result.speak.map((line) => line.text), ["Beta."]);

  const late = pushSay(result.state, say({ turn_id: "a", part: 1, utterance_id: "a1", text: "Dropped." }));
  assert.equal(late.ignored, true);
  assert.deepEqual(late.speak, []);
  assert.equal(late.caption, "Beta.");

  const closed = bargeSpeech(result.state);
  const more = pushSay(closed, say({ turn_id: "b", part: 1, utterance_id: "b1", text: "Still beta." }));
  assert.equal(more.ignored, true);
  assert.deepEqual(more.speak, []);
  assert.equal(more.caption, "Beta.");

  const next = pushSay(more.state, say({ turn_id: "c", part: 0, utterance_id: "c0", text: "Gamma." }));
  assert.deepEqual(next.speak.map((line) => line.text), ["Gamma."]);
  assert.equal(next.caption, "Gamma.");
});

test("a say without parts still speaks and retires the previous turn", () => {
  let result = pushSay(null, say({ turn_id: "t4", part: 0, utterance_id: "u4.0", text: "Okay, I see the pan." }));
  result = pushSay(result.state, say({ utterance_id: "u3", text: "That goes on the header with the arrow." }));
  assert.equal(result.caption, "That goes on the header with the arrow.");
  assert.equal(result.speak[0].utteranceId, "u3");
  const late = pushSay(result.state, say({ turn_id: "t4", part: 1, utterance_id: "u4.1", text: "Too late." }));
  assert.equal(late.ignored, true);
  assert.equal(late.caption, "That goes on the header with the arrow.");
});

test("tracker seeds from draw anchors and track boxes, and the server box wins", () => {
  let plan = nextTrackerPlan(null, {
    camera: true,
    kind: "draw",
    seq: 7,
    ops: [anchor("o1", [0.4, 0.55, 0.06, 0.03]), anchor("o2", [0.1, 0.1, 0.2, 0.2])],
  });
  assert.deepEqual(plan.reseed.map((item) => item.id), ["o1", "o2"]);
  assert.equal(plan.reseed[0].source, "draw");
  assert.deepEqual(plan.reseed[0].bbox, [0.4, 0.55, 0.06, 0.03]);

  plan = nextTrackerPlan(plan.state, {
    camera: true,
    kind: "track",
    seq: 12,
    ops: [anchor("o1", [0.4, 0.55, 0.06, 0.03]), anchor("o2", [0.1, 0.1, 0.2, 0.2])],
    objects: [
      { id: "o1", label: "CPU_FAN", bbox: [0.41, 0.55, 0.06, 0.03], conf: 0.8 },
      { id: "o9", label: "pan", bbox: [0.5, 0.5, 0.1, 0.1], conf: 0.7 },
    ],
  });
  const byId = Object.fromEntries(plan.reseed.map((item) => [item.id, item]));
  assert.equal(byId.o1.source, "track");
  assert.deepEqual(byId.o1.bbox, [0.41, 0.55, 0.06, 0.03]);
  assert.equal(byId.o2.source, "draw");
  assert.deepEqual(byId.o2.bbox, [0.1, 0.1, 0.2, 0.2]);
  assert.equal(byId.o9.source, "track");

  plan = nextTrackerPlan(plan.state, {
    camera: true,
    kind: "draw",
    seq: 12,
    ops: [anchor("o1", [0.2, 0.2, 0.2, 0.2]), anchor("o2", [0.1, 0.1, 0.2, 0.2])],
    changed: [anchor("o1", [0.2, 0.2, 0.2, 0.2])],
  });
  const held = Object.fromEntries(plan.reseed.map((item) => [item.id, item]));
  assert.deepEqual(held.o1.bbox, [0.41, 0.55, 0.06, 0.03]);
  assert.equal(held.o1.source, "track");
  assert.equal(held.o9, undefined);
  assert.deepEqual(plan.drop, ["o9"]);
});

test("unreferenced trackers are dropped and a stopped camera clears them", () => {
  let plan = nextTrackerPlan(null, {
    camera: true,
    kind: "draw",
    seq: 1,
    ops: [anchor("o1", [0.1, 0.1, 0.2, 0.2]), anchor("o2", [0.3, 0.3, 0.2, 0.2])],
  });
  plan = nextTrackerPlan(plan.state, {
    camera: true,
    kind: "draw",
    seq: 2,
    ops: [anchor("o2", [0.31, 0.3, 0.2, 0.2])],
  });
  assert.deepEqual(plan.drop, ["o1"]);
  assert.deepEqual(plan.state.ids, ["o2"]);
  assert.deepEqual(plan.reseed[0].bbox, [0.31, 0.3, 0.2, 0.2]);

  plan = nextTrackerPlan(plan.state, { camera: false, kind: "stop" });
  assert.deepEqual(plan.drop, ["o2"]);
  assert.deepEqual(plan.reseed, []);
  assert.deepEqual(plan.state.ids, []);
});

test("an older track message does not replace a newer box", () => {
  let plan = nextTrackerPlan(null, {
    camera: true,
    kind: "draw",
    seq: 13,
    ops: [anchor("o1", [0.3, 0.3, 0.2, 0.2])],
  });
  plan = nextTrackerPlan(plan.state, {
    camera: true,
    kind: "track",
    seq: 12,
    ops: [anchor("o1", [0.3, 0.3, 0.2, 0.2])],
    objects: [{ id: "o1", label: "CPU_FAN", bbox: [0.9, 0.1, 0.05, 0.05], conf: 0.4 }],
  });
  assert.deepEqual(plan.reseed[0].bbox, [0.3, 0.3, 0.2, 0.2]);
  assert.equal(plan.reseed[0].source, "draw");
});

test("HUD fps counts paints in the last second", () => {
  let hud = initialHud();
  for (let t = 0; t <= 1000; t += 100) hud = notePaint(hud, t);
  assert.equal(hud.fps, 10);
  hud = notePaint(hud, 1000 + 100);
  assert.equal(hud.fps, 10);
});

test("frame round trip waits for a draw or track at that seq or newer", () => {
  let hud = noteFrameSent(initialHud(), 4, 1000);
  hud = noteFrameReply(hud, { type: "metrics", seq: 4 }, 1100);
  assert.equal(hud.rttMs, null);
  hud = noteFrameReply(hud, { type: "draw", seq: 3 }, 1200);
  assert.equal(hud.rttMs, null);
  assert.equal(hud.pending.length, 1);
  hud = noteFrameReply(hud, { type: "track", seq: 4 }, 1500);
  assert.equal(hud.rttMs, 500);
  assert.deepEqual(hud.pending, []);

  hud = noteFrameSent(hud, 5, 2000);
  hud = noteFrameSent(hud, 6, 2100);
  hud = noteFrameReply(hud, { type: "draw", seq: 9 }, 2500);
  assert.equal(hud.rttMs, 400);
  assert.deepEqual(hud.pending, []);
});

test("HUD keeps the last metrics and the voice turn time", () => {
  let hud = noteMetrics(initialHud(), {
    type: "metrics",
    vlm_ms: 900,
    brain_first_token_ms: 420,
    first_audio_ms: 1100,
    turn_ms: 2300,
  });
  assert.deepEqual(hud.metrics, {
    vlm_ms: 900,
    brain_first_token_ms: 420,
    first_audio_ms: 1100,
    turn_ms: 2300,
  });
  hud = noteFinalWords(hud, 10);
  assert.equal(hud.voiceMs, null);
  hud = noteFirstSound(hud, 80);
  assert.equal(hud.voiceMs, 70);
  hud = noteFirstSound(hud, 120);
  assert.equal(hud.voiceMs, 70);
});

test("tracker ticks are capped at 30 Hz and skipped while one is running", () => {
  assert.equal(shouldTrack(null, 0, false), true);
  assert.equal(shouldTrack(0, TRACK_GAP_MS - 1, false), false);
  assert.equal(shouldTrack(0, TRACK_GAP_MS, false), true);
  assert.equal(shouldTrack(0, TRACK_GAP_MS, true), false);
});

test("dev HUD stays off the presence orbs", () => {
  const view = { w: 390, h: 844 };
  const home = devHudPosition(view, {
    helper: { x: 18, y: 18, opacity: 1 },
    shopper: { x: 318, y: 18, opacity: 0 },
  });
  assert.equal(overlaps(home, { x: 18, y: 18 }), false);
  assert.ok(home.y >= 18 + ORB_H || home.x >= 18 + ORB_W);
  assert.equal(home.h, DEV_HUD_H);

  const helper = { x: 12, y: 100 };
  const shopper = { x: 318, y: 18 };
  const moved = devHudPosition(view, {
    helper: { ...helper, opacity: 1 },
    shopper: { ...shopper, opacity: 1 },
  });
  assert.equal(overlaps(moved, helper), false);
  assert.equal(overlaps(moved, shopper), false);
});

test("barge gate: duck on start, stop on real start, unduck on misfire, flush on end", async () => {
  const { initialBarge, nextBarge, BARGE_VAD } = await import("./realtime.js");
  let r = nextBarge(initialBarge(), "start", true);
  assert.equal(r.action, "duck");
  assert.equal(nextBarge(r.state, "misfire", true).action, "unduck");
  assert.equal(nextBarge(r.state, "real_start", true).action, "stop");
  r = nextBarge(initialBarge(), "start", false);
  assert.equal(r.action, null);
  r = nextBarge(r.state, "real_start", false);
  assert.equal(nextBarge(r.state, "end", false).action, "flush");
  assert.ok(BARGE_VAD.minSpeechMs <= 150);
});

test("vadSettings: sensitivity loosens both VADs; the energy gate stays above ambient", async () => {
  const { vadSettings, dbfs } = await import("./realtime.js");
  const soft = vadSettings(9, 0);
  const strict = vadSettings(2, 0);
  assert.ok(soft.energyThreshold < strict.energyThreshold);
  assert.ok(soft.positiveSpeechThreshold < strict.positiveSpeechThreshold);
  assert.ok(soft.minSpeechMs < strict.minSpeechMs);
  assert.ok(vadSettings(10, 0.05).energyThreshold >= 0.125);
  assert.equal(dbfs(0.1), -20);
});
