import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  coverTransform,
  drawOverlay,
  drawOverlayCover,
  mapCoverBox,
  reduceOverlay,
  roleColor,
  sharpCanvasSize,
  toPixels,
  visibleOverlay,
} from "./overlay.js";

const examplesPath = join(dirname(fileURLToPath(import.meta.url)), "..", "contracts", "examples.json");
const examples = JSON.parse(readFileSync(examplesPath, "utf8"));
const [headerOverlay, pinOverlay] = examples.overlay;

function fakeCtx() {
  const calls = [];
  const ctx = new Proxy(
    {},
    {
      get(_target, prop) {
        if (prop === "calls") return calls;
        if (typeof prop !== "string") return undefined;
        return (...args) => {
          calls.push([prop, ...args]);
        };
      },
      set(_target, prop, value) {
        calls.push(["set", prop, value]);
        return true;
      },
    },
  );
  return ctx;
}

function methods(ctx, name) {
  return ctx.calls.filter((call) => call[0] === name);
}

test("toPixels scales normalized bbox onto the frame", () => {
  const bbox = headerOverlay.boxes[0].bbox;
  const rect = toPixels(bbox, 640, 480);
  assert.deepEqual(rect, {
    x: bbox[0] * 640,
    y: bbox[1] * 480,
    w: bbox[2] * 640,
    h: bbox[3] * 480,
  });
  assert.equal(rect.x, 256);
  assert.equal(rect.y, 264);
  assert.deepEqual(toPixels([0.25, 0.5, 0.5, 0.25], 200, 100), {
    x: 50,
    y: 50,
    w: 100,
    h: 25,
  });
});

test("toPixels clamps boxes that start outside or run past the frame", () => {
  assert.deepEqual(toPixels([0.8, -0.2, 0.5, 0.5], 100, 100), {
    x: 80,
    y: 0,
    w: 20,
    h: 30,
  });
  assert.deepEqual(toPixels([-0.5, 0.5, 0.25, 2], 100, 50), {
    x: 0,
    y: 25,
    w: 0,
    h: 25,
  });
});

test("roleColor covers target, other, wrong and ok", () => {
  assert.equal(roleColor("target"), "#3DDC97");
  assert.equal(roleColor("other"), "#8B909A");
  assert.equal(roleColor("wrong"), "#FF4B4B");
  assert.equal(roleColor("ok"), "#4C9AFF");
});

test("drawOverlay strokes each example box, draws the arrow, and dots only pin level", () => {
  const width = 200;
  const height = 100;
  const headerCtx = fakeCtx();
  drawOverlay(headerCtx, headerOverlay, width, height);
  const headerRects = methods(headerCtx, "strokeRect");
  assert.equal(headerRects.length, headerOverlay.boxes.length);
  assert.deepEqual(headerRects[0].slice(1), Object.values(toPixels(headerOverlay.boxes[0].bbox, width, height)));
  assert.equal(methods(headerCtx, "arc").length, 0);
  assert.ok(methods(headerCtx, "stroke").length >= 1);
  const [tx, ty] = headerOverlay.arrow.to;
  const arrowTip = methods(headerCtx, "lineTo").some(
    (call) => Math.abs(call[1] - tx * width) < 1e-9 && Math.abs(call[2] - ty * height) < 1e-9,
  );
  assert.equal(arrowTip, true);
  const labels = methods(headerCtx, "fillText").map((call) => call[1]);
  assert.ok(labels.some((text) => text.includes("CPU_FAN") && text.includes("86%")));
  assert.ok(labels.some((text) => text.includes("SYS_FAN1") && text.includes("74%")));

  const pinCtx = fakeCtx();
  drawOverlay(pinCtx, pinOverlay, width, height);
  assert.equal(methods(pinCtx, "strokeRect").length, pinOverlay.boxes.length);
  const pinCount = pinOverlay.boxes.reduce((sum, box) => sum + (box.pins ? box.pins.length : 0), 0);
  assert.equal(methods(pinCtx, "arc").length, pinCount);
  assert.equal(methods(pinCtx, "stroke").length, 0);

  const headerWithPins = {
    ...pinOverlay,
    level: "header",
    arrow: undefined,
  };
  const gated = fakeCtx();
  drawOverlay(gated, headerWithPins, width, height);
  assert.equal(methods(gated, "arc").length, 0);
  assert.equal(methods(gated, "strokeRect").length, 1);
});

test("coverTransform crops a wider source on the sides and a taller source on the top", () => {
  const wide = coverTransform(640, 360, 320, 320);
  assert.equal(wide.scale, 320 / 360);
  assert.equal(wide.offsetY, 0);
  assert.ok(wide.offsetX < 0);
  assert.equal(wide.offsetX, (320 - 640 * wide.scale) / 2);

  const tall = coverTransform(360, 640, 320, 320);
  assert.equal(tall.scale, 320 / 360);
  assert.equal(tall.offsetX, 0);
  assert.ok(tall.offsetY < 0);
  assert.equal(tall.offsetY, (320 - 640 * tall.scale) / 2);
});

test("mapCoverBox places a normalized box through the cover crop", () => {
  const box = mapCoverBox([0.25, 0.25, 0.5, 0.5], 640, 360, 320, 320);
  assert.equal(box.y, 80);
  assert.equal(box.h, 160);
  assert.ok(Math.abs(box.x - 160 / 9) < 1e-9);
  assert.ok(Math.abs(box.w - 2560 / 9) < 1e-9);

  const wideCenter = mapCoverBox([0.5, 0.5, 0, 0], 640, 360, 320, 320);
  const tallCenter = mapCoverBox([0.5, 0.5, 0, 0], 360, 640, 320, 320);
  assert.ok(Math.abs(wideCenter.x - 160) < 1e-9);
  assert.ok(Math.abs(wideCenter.y - 160) < 1e-9);
  assert.ok(Math.abs(tallCenter.x - 160) < 1e-9);
  assert.ok(Math.abs(tallCenter.y - 160) < 1e-9);

  const ctx = fakeCtx();
  drawOverlayCover(
    ctx,
    {
      type: "overlay",
      seq: 1,
      level: "header",
      boxes: [{ id: "a", label: "CPU_FAN", bbox: [0.25, 0.25, 0.5, 0.5], role: "target", conf: 0.86 }],
    },
    640,
    360,
    320,
    320,
  );
  assert.deepEqual(methods(ctx, "strokeRect")[0].slice(1), [box.x, box.y, box.w, box.h]);
});

test("sharpCanvasSize uses devicePixelRatio", () => {
  assert.deepEqual(sharpCanvasSize(100, 50, 2), { width: 200, height: 100, ratio: 2 });
  assert.deepEqual(sharpCanvasSize(10, 10, 0), { width: 10, height: 10, ratio: 1 });
});

test("an older overlay is dropped and a quiet one clears after 3 s", () => {
  let state = { seq: -1, overlay: null, atMs: 0 };
  state = reduceOverlay(state, { type: "overlay", seq: 2, level: "header", boxes: [] }, 1000);
  assert.equal(visibleOverlay(state, 1000).seq, 2);
  const stale = { type: "overlay", seq: 1, level: "header", boxes: [{ id: "old" }] };
  const kept = reduceOverlay(state, stale, 1200);
  assert.equal(kept, state);
  assert.equal(visibleOverlay(state, 1000 + 2999).seq, 2);
  assert.equal(visibleOverlay(state, 1000 + 3000), null);
  const fresh = reduceOverlay(state, { type: "overlay", seq: 3, level: "header", boxes: [] }, 2000);
  assert.equal(fresh.seq, 3);
  assert.equal(visibleOverlay(fresh, 4999).seq, 3);
  assert.equal(visibleOverlay(fresh, 5000), null);
});
