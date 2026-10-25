import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { clampRect, coverTransform } from "./overlay.js";
import {
  DRAW_IN_MS,
  applyDraw,
  opToPixels,
  roleColor,
  splitByTargets,
  targetLabels,
  viewTransform,
  visibleOps,
} from "./draw.js";

const examplesPath = join(dirname(fileURLToPath(import.meta.url)), "..", "contracts", "examples.json");
const examples = JSON.parse(readFileSync(examplesPath, "utf8"));
const drawMsg = examples.v2_draw[0];

function close(actual, expected, label) {
  assert.ok(Math.abs(actual - expected) < 1e-6, `${label}: ${actual} vs ${expected}`);
}

function expectPoint(nx, ny, spec, t) {
  return {
    x: nx * spec.srcW * t.scale + t.offsetX,
    y: ny * spec.srcH * t.scale + t.offsetY,
  };
}

function box(id, agentLabel) {
  return {
    kind: "box",
    id,
    bbox: [0.2, 0.2, 0.1, 0.1],
    role: "info",
    label: agentLabel,
  };
}

test("every v2_draw example op maps through cover on a wide and a tall view", () => {
  const views = [
    { name: "wide", srcW: 640, srcH: 480, viewW: 1280, viewH: 720 },
    { name: "tall", srcW: 640, srcH: 480, viewW: 390, viewH: 844 },
  ];
  const state = applyDraw(null, drawMsg, 1000);
  const visible = visibleOps(state, 1000);
  assert.deepEqual(
    visible.map((item) => item.op.kind),
    ["box", "arrow", "circle", "path", "label", "pin", "highlight"],
  );
  const ys = [];
  for (const spec of views) {
    const t = viewTransform(spec.srcW, spec.srcH, spec.viewW, spec.viewH);
    const cover = coverTransform(spec.srcW, spec.srcH, spec.viewW, spec.viewH);
    assert.equal(t.scale, cover.scale);
    assert.equal(t.offsetX, cover.offsetX);
    assert.equal(t.offsetY, cover.offsetY);
    for (const item of visible) {
      const px = opToPixels(item.op, t);
      assert.equal(px.kind, item.op.kind);
      if (px.kind === "box" || px.kind === "highlight") {
        const rawX = item.op.bbox[0] * spec.srcW * t.scale + t.offsetX;
        const rawY = item.op.bbox[1] * spec.srcH * t.scale + t.offsetY;
        const rawW = item.op.bbox[2] * spec.srcW * t.scale;
        const rawH = item.op.bbox[3] * spec.srcH * t.scale;
        const rect = clampRect(rawX, rawY, rawW, rawH, spec.viewW, spec.viewH);
        close(px.rect.x, rect.x, `${spec.name} ${px.kind} x`);
        close(px.rect.y, rect.y, `${spec.name} ${px.kind} y`);
        close(px.rect.w, rect.w, `${spec.name} ${px.kind} w`);
        close(px.rect.h, rect.h, `${spec.name} ${px.kind} h`);
        assert.ok(Number.isFinite(px.rect.x) && Number.isFinite(px.rect.w));
      } else if (px.kind === "circle") {
        const at = expectPoint(item.op.center[0], item.op.center[1], spec, t);
        close(px.center.x, at.x, `${spec.name} circle x`);
        close(px.center.y, at.y, `${spec.name} circle y`);
        close(px.r, item.op.r * Math.min(spec.srcW, spec.srcH) * t.scale, `${spec.name} r`);
        assert.ok(px.r > 0);
      } else if (px.kind === "arrow") {
        const from = expectPoint(item.op.from[0], item.op.from[1], spec, t);
        const to = expectPoint(item.op.to[0], item.op.to[1], spec, t);
        close(px.from.x, from.x, `${spec.name} from x`);
        close(px.from.y, from.y, `${spec.name} from y`);
        close(px.to.x, to.x, `${spec.name} to x`);
        close(px.to.y, to.y, `${spec.name} to y`);
      } else if (px.kind === "path") {
        assert.equal(px.points.length, item.op.points.length);
        item.op.points.forEach((point, index) => {
          const at = expectPoint(point[0], point[1], spec, t);
          close(px.points[index].x, at.x, `${spec.name} path ${index} x`);
          close(px.points[index].y, at.y, `${spec.name} path ${index} y`);
        });
      } else if (px.kind === "label" || px.kind === "pin") {
        const at = expectPoint(item.op.at[0], item.op.at[1], spec, t);
        close(px.at.x, at.x, `${spec.name} ${px.kind} x`);
        close(px.at.y, at.y, `${spec.name} ${px.kind} y`);
      }
    }
    const boxPx = opToPixels(visible[0].op, t);
    ys.push(boxPx.rect.y);
  }
  assert.notEqual(ys[0], ys[1]);
  const wide = viewTransform(640, 480, 1280, 720);
  const boxPx = opToPixels(visible[0].op, wide);
  close(boxPx.rect.x, 512, "wide box x");
  close(boxPx.rect.y, 408, "wide box y");
  close(boxPx.rect.w, 76.8, "wide box w");
  close(boxPx.rect.h, 28.8, "wide box h");
  for (const role of ["target", "info", "warn", "ok"]) {
    assert.match(roleColor(role), /^#[0-9A-Fa-f]{6}$/);
  }
});

test("clear removes one agent and leaves the other", () => {
  let state = applyDraw(null, {
    v: 2,
    type: "draw",
    agent: "helper",
    seq: 1,
    ops: [box("h1", "helper")],
  }, 0);
  state = applyDraw(state, {
    v: 2,
    type: "draw",
    agent: "shopper",
    seq: 1,
    ops: [box("s1", "shopper")],
  }, 0);
  state = applyDraw(state, {
    v: 2,
    type: "draw",
    agent: "helper",
    seq: 2,
    ops: [{ kind: "clear", id: "c0" }, box("h2", "next")],
  }, 50);
  const left = visibleOps(state, 50);
  assert.deepEqual(left.map((item) => item.op.id), ["h2", "s1"]);
  assert.deepEqual(left.map((item) => item.agent), ["helper", "shopper"]);
  state = applyDraw(state, {
    v: 2,
    type: "draw",
    agent: "shopper",
    seq: 2,
    ops: [{ kind: "clear", id: "c1" }],
  }, 60);
  assert.deepEqual(visibleOps(state, 60).map((item) => item.op.id), ["h2"]);
});

test("ttl_ms hides an op after it elapses and a draw-on finishes at 250 ms", () => {
  const state = applyDraw(null, drawMsg, 1000);
  assert.equal(visibleOps(state, 1000).some((item) => item.op.kind === "label"), true);
  assert.equal(visibleOps(state, 1000 + 3999).some((item) => item.op.kind === "label"), true);
  assert.equal(visibleOps(state, 1000 + 4000).some((item) => item.op.kind === "label"), false);
  assert.equal(visibleOps(state, 1000 + 4000).length, 6);
  const born = visibleOps(state, 1000).find((item) => item.op.kind === "box");
  const done = visibleOps(state, 1000 + DRAW_IN_MS).find((item) => item.op.kind === "box");
  assert.equal(born.progress, 0);
  assert.equal(done.progress, 1);
  assert.equal(visibleOps(state, 5000).find((item) => item.op.kind === "box").fade, 1);
});

test("target labels come from target boxes only, longest first, and split the caption around them", () => {
  const msg = { type: "draw", agent: "helper", seq: 1, ops: [
    { kind: "box", id: "a", bbox: [0.1, 0.1, 0.2, 0.2], role: "target", label: "mug" },
    { kind: "box", id: "b", bbox: [0.1, 0.1, 0.2, 0.2], role: "target", label: "blue mug" },
    { kind: "box", id: "c", bbox: [0.5, 0.5, 0.2, 0.2], role: "info", label: "pill bottle" },
  ] };
  const state = applyDraw(null, msg, 0);
  assert.deepEqual(targetLabels(state, 10), ["blue mug", "mug"]);
  assert.deepEqual(splitByTargets("That's your Blue mug. Pills left of it.", targetLabels(state, 10)), [
    { text: "That's your ", target: false },
    { text: "Blue mug", target: true },
    { text: ". Pills left of it.", target: false },
  ]);
  assert.deepEqual(splitByTargets("Nothing here", []), [{ text: "Nothing here", target: false }]);
  assert.deepEqual(splitByTargets("", ["mug"]), []);
  assert.deepEqual(splitByTargets("a (b) c", ["(b)"]), [{ text: "a ", target: false }, { text: "(b)", target: true }, { text: " c", target: false }]);
});
