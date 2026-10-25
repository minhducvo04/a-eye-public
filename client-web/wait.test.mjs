// Port of ios/AEyeCore/Tests/AEyeCoreTests/WaitTests.swift, plus the WaitCard copy.
import test from "node:test";
import assert from "node:assert/strict";
import {
  waitAfterTurn, waitAfterAgentState, visibleWait, waitProgress, waitCopy, capitalized,
  MAX_WAIT_MS, DEFAULT_TYPICAL_WAIT_MS,
} from "./wait.js";

const turn = (id, stage, extra = {}) => ({ v: 2, type: "turn", turn_id: id, stage, at_ms: 5, ...extra });
const close = (a, b) => assert.ok(Math.abs(a - b) < 0.001, `${a} != ${b}`);

test("unknown turn stage is ignored", () => {
  const w = waitAfterTurn(null, turn("t1", "thinking"), 0);
  assert.equal(waitAfterTurn(w, turn("t1", "dancing"), 1), w);
});

test("received starts the wait and thinking keeps its start", () => {
  let w = waitAfterTurn(null, turn("t1", "received"), 1000);
  w = waitAfterTurn(w, turn("t1", "thinking", { typical_ms: 3000 }), 1200);
  assert.deepEqual(w, { turnId: "t1", agent: "helper", stage: "thinking", startedAtMs: 1000, typicalMs: 3000 });
});

test("new turn restarts the clock", () => {
  let w = waitAfterTurn(null, turn("t1", "thinking"), 1000);
  w = waitAfterTurn(w, turn("t2", "received"), 5000);
  assert.equal(w.turnId, "t2");
  assert.equal(w.startedAtMs, 5000);
});

test("speaking and done end the wait", () => {
  const base = waitAfterTurn(null, turn("t1", "thinking"), 0);
  assert.equal(waitAfterTurn(base, turn("t1", "speaking"), 1), null);
  assert.equal(waitAfterTurn(base, turn("t1", "done"), 1), null);
  assert.equal(waitAfterTurn(base, turn("t1", "failed", { detail: "Didn't catch that" }), 1), null);
  assert.equal(waitAfterAgentState(base, "helper", "speaking", 1), null);
});

test("thinking presence without a turn still shows a wait", () => {
  assert.deepEqual(waitAfterAgentState(null, "shopper", "thinking", 700),
    { turnId: null, agent: "shopper", stage: "thinking", startedAtMs: 700, typicalMs: null });
});

test("thinking presence during a turn keeps the turn clock", () => {
  let w = waitAfterTurn(null, turn("t1", "thinking", { typical_ms: 2000 }), 100);
  w = waitAfterAgentState(w, "helper", "thinking", 400);
  assert.equal(w.startedAtMs, 100);
  assert.equal(w.typicalMs, 2000);
  assert.equal(waitAfterAgentState(w, "helper", "idle", 500), w);
});

test("stale wait is hidden", () => {
  const w = waitAfterTurn(null, turn("t1", "thinking"), 1000);
  assert.ok(visibleWait(w, 1000 + MAX_WAIT_MS));
  assert.equal(visibleWait(w, 1001 + MAX_WAIT_MS), null);
});

test("progress rises then slows but never finishes", () => {
  assert.equal(waitProgress(0, 2000), 0);
  close(waitProgress(1000, 2000), 0.45);
  close(waitProgress(2000, 2000), 0.9);
  const late = waitProgress(6000, 2000);
  assert.ok(late > 0.9 && late < 0.99);
  assert.ok(waitProgress(1_000_000, 2000) < 1);
});

test("progress uses the default when typical is unknown or zero", () => {
  const fallback = waitProgress(1000, null);
  assert.equal(fallback, waitProgress(1000, DEFAULT_TYPICAL_WAIT_MS));
  assert.equal(waitProgress(1000, 0), fallback);
  assert.equal(waitProgress(-50, 2000), 0);
});

test("card copy matches WaitCard", () => {
  const w = (stage, agent = "helper", typicalMs = null) => ({ stage, agent, typicalMs });
  assert.deepEqual(waitCopy(w("thinking", "shopper"), 0), { eyebrow: "SHOPPER", line: "Finding parts that fit…" });
  assert.deepEqual(waitCopy(w("received"), 0), { eyebrow: "GOT IT", line: "Hold the camera steady…" });
  assert.deepEqual(waitCopy(w("transcribing"), 0), { eyebrow: "LISTENING", line: "Catching your words…" });
  assert.deepEqual(waitCopy(w("backup"), 0), { eyebrow: "LOOKING CLOSER", line: "Taking a bit longer. Keep holding." });
  assert.equal(waitCopy(w("thinking"), 3000).line, "Hold the camera steady…");
  assert.equal(waitCopy(w("thinking"), 3001).line, "Almost there. Keep holding.");
  assert.equal(waitCopy(w("thinking", "helper", 1000), 1500).line, "Almost there. Keep holding.");
  assert.equal(capitalized("LOOKING CLOSER"), "Looking Closer");
});
