import assert from "node:assert/strict";
import test from "node:test";
import {
  GLIDE_MS,
  ORB_SIZE,
  SCREEN_MARGIN,
  SHOPPER_FADE_MS,
  agentOpacity,
  cornerFor,
  focusPixels,
  glideTarget,
  layoutOrbs,
  nextPresence,
  presenceLook,
} from "./presence.js";

function stateMsg(agent, state, focus) {
  const msg = { v: 2, type: "agent_state", agent, state };
  if (focus) msg.focus = focus;
  return msg;
}

function inside(goal, view) {
  assert.ok(goal.x >= SCREEN_MARGIN - 1e-6, `x ${goal.x}`);
  assert.ok(goal.y >= SCREEN_MARGIN - 1e-6, `y ${goal.y}`);
  assert.ok(goal.x + ORB_SIZE <= view.w - SCREEN_MARGIN + 1e-6, `right ${goal.x}`);
  assert.ok(goal.y + ORB_SIZE <= view.h - SCREEN_MARGIN + 1e-6, `bottom ${goal.y}`);
}

function covers(goal, point) {
  const cx = goal.x + ORB_SIZE / 2;
  const cy = goal.y + ORB_SIZE / 2;
  return Math.hypot(cx - point.x, cy - point.y) <= ORB_SIZE / 2;
}

test("presence moves through each state and idle returns to the corner", () => {
  let presence = nextPresence(null, null, 0);
  assert.equal(presence.helper.state, "idle");
  assert.equal(presence.shopper.state, "idle");
  const view = { w: 800, h: 600, srcW: 800, srcH: 600 };
  for (const name of ["listening", "thinking", "speaking", "idle"]) {
    presence = nextPresence(presence, stateMsg("helper", name, [0.4, 0.4]), 100);
    assert.equal(presence.helper.state, name);
    const look = presenceLook(presence.helper, 100, presence.level, false);
    assert.equal(look.state, name);
  }
  const listening = nextPresence(null, stateMsg("helper", "listening"), 0);
  assert.equal(presenceLook(listening.helper, 0, null, false).ripple, 0.45);
  assert.equal(presenceLook(listening.helper, 0, 0.25, false).ripple, 0.25);
  const idleSlot = nextPresence(null, null, 0).helper;
  const hearing = presenceLook(idleSlot, 0, 0.4, false, true);
  assert.equal(hearing.hearing, true);
  assert.equal(hearing.ripple, 0.4);
  assert.equal(presenceLook(idleSlot, 0, 0, true, true).ripple, 0);
  assert.equal(presenceLook(listening.helper, 0, null, false).dim, 1);
  const idle = presence.helper;
  assert.equal(presenceLook(idle, 0, null, false).dim, 0.62);
  assert.equal(presenceLook(idle, 0, null, true).breathe, 0);
  const thinking = nextPresence(null, stateMsg("helper", "thinking"), 0).helper;
  assert.notEqual(
    presenceLook(thinking, 0, null, false).orbit,
    presenceLook(thinking, 900, null, false).orbit,
  );
  assert.equal(presenceLook(thinking, 900, null, true).orbit, -Math.PI / 2);
  const speaking = nextPresence(null, stateMsg("helper", "speaking"), 0).helper;
  assert.notEqual(presenceLook(speaking, 0, null, false).pulse, presenceLook(speaking, 80, null, false).pulse);

  presence = nextPresence(presence, stateMsg("helper", "speaking", [0.2, 0.3]), 200);
  assert.deepEqual(presence.helper.focus, [0.2, 0.3]);
  presence = nextPresence(presence, stateMsg("helper", "idle", [0.2, 0.3]), 300);
  assert.equal(presence.helper.focus, null);
  const home = glideTarget("helper", presence.helper, view);
  const corner = cornerFor("helper", view);
  assert.equal(home.mode, "corner");
  assert.equal(home.x, corner.x);
  assert.equal(home.y, corner.y);
});

test("focus glide target stays on screen, beside the point", () => {
  const roomy = { w: 800, h: 600, srcW: 800, srcH: 600 };
  let presence = nextPresence(null, stateMsg("helper", "thinking", [0.5, 0.5]), 0);
  const helperGoal = glideTarget("helper", presence.helper, roomy);
  const shopper = nextPresence(null, stateMsg("shopper", "speaking", [0.5, 0.5]), 0);
  const shopperGoal = glideTarget("shopper", shopper.shopper, roomy);
  const mid = focusPixels([0.5, 0.5], roomy);
  inside(helperGoal, roomy);
  inside(shopperGoal, roomy);
  assert.equal(covers(helperGoal, mid), false);
  assert.equal(covers(shopperGoal, mid), false);
  assert.ok(helperGoal.x + ORB_SIZE / 2 < mid.x);
  assert.ok(shopperGoal.x + ORB_SIZE / 2 > mid.x);

  const tight = { w: 220, h: 220, srcW: 220, srcH: 220 };
  for (const focus of [[0.02, 0.02], [1, 1], [0.43, 0.56]]) {
    presence = nextPresence(null, stateMsg("helper", "speaking", focus), 0);
    const goal = glideTarget("helper", presence.helper, tight);
    const point = focusPixels(focus, tight);
    inside(goal, tight);
    assert.equal(covers(goal, point), false, `covered ${focus}`);
  }

  let laid = layoutOrbs(nextPresence(null, stateMsg("helper", "idle"), 0), {}, roomy, 0, false);
  const start = { x: laid.poses.helper.x, y: laid.poses.helper.y };
  presence = nextPresence(null, stateMsg("helper", "speaking", [0.7, 0.6]), 1000);
  laid = layoutOrbs(presence, laid.motion, roomy, 1000, false);
  assert.ok(Math.abs(laid.poses.helper.x - start.x) < 1e-6);
  laid = layoutOrbs(presence, laid.motion, roomy, 1000 + GLIDE_MS, false);
  assert.ok(Math.abs(laid.poses.helper.x - laid.poses.helper.goal.x) < 1e-6);
  assert.ok(Math.abs(laid.poses.helper.y - laid.poses.helper.goal.y) < 1e-6);
  const snapped = layoutOrbs(presence, { helper: null, shopper: null }, roomy, 1000, true);
  assert.equal(snapped.poses.helper.x, snapped.poses.helper.goal.x);
  assert.equal(snapped.poses.helper.y, snapped.poses.helper.goal.y);
});

test("the shopper orb appears only while speaking, then fades", () => {
  let presence = nextPresence(null, stateMsg("shopper", "thinking"), 0);
  assert.equal(agentOpacity("shopper", presence.shopper, 0), 0);
  assert.equal(agentOpacity("helper", presence.helper, 0), 1);
  presence = nextPresence(presence, stateMsg("shopper", "speaking"), 500);
  assert.equal(agentOpacity("shopper", presence.shopper, 500), 1);
  presence = nextPresence(presence, stateMsg("shopper", "idle"), 800);
  assert.equal(agentOpacity("shopper", presence.shopper, 800), 1);
  const mid = agentOpacity("shopper", presence.shopper, 800 + SHOPPER_FADE_MS / 2);
  assert.ok(mid > 0 && mid < 1);
  assert.equal(agentOpacity("shopper", presence.shopper, 800 + SHOPPER_FADE_MS), 0);
  assert.equal(agentOpacity("helper", presence.helper, 800 + SHOPPER_FADE_MS), 1);
  presence = nextPresence(presence, stateMsg("shopper", "speaking"), 3000);
  assert.equal(agentOpacity("shopper", presence.shopper, 3000), 1);
});
