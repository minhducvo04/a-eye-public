import assert from "node:assert/strict";
import test from "node:test";
import { performance } from "node:perf_hooks";
import { toGray, createTracker, applyAnchors } from "./track.js";

const W = 320, H = 240;
function noise(n, seed = 1) {
  const data = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    data[i] = seed >>> 24;
  }
  return data;
}
function frame(objects, seed = 7) {
  const data = noise(W * H, seed);
  for (const { x, y, w, h, patch } of objects) {
    for (let j = 0; j < h; j++) data.set(patch.subarray(j * w, (j + 1) * w), (y + j) * W + x);
  }
  return { data, w: W, h: H };
}
const box = (o) => [o.x / W, o.y / H, o.w / W, o.h / H];
function near(actual, expected, tolerance = 1e-10) {
  actual.forEach((value, i) => assert.ok(Math.abs(value - expected[i]) < tolerance, `${actual} != ${expected}`));
}

test("toGray downsamples RGBA and ignores alpha", () => {
  const rgba = new Uint8Array([255, 0, 0, 0, 0, 255, 0, 255, 0, 0, 255, 255, 255, 255, 255, 255]);
  const gray = toGray(rgba, 2, 2, 0.5);
  assert.equal(gray.w, 1);
  assert.equal(gray.h, 1);
  assert.ok(Math.abs(gray.data[0] - 128) <= 1);
  assert.ok(gray.data instanceof Uint8Array);
});

test("tracks 30 frames of pan and a 12px jump, loses occlusion, reseeds and drops", () => {
  for (const [w, h] of [[36, 28], [80, 60], [19, 13]]) {
    const o = { x: 41, y: 53, w, h, patch: noise(w * h, 19) };
    const tracker = createTracker();
    tracker.seed("part", box(o), frame([o]));
    let last;
    for (let i = 0; i < 30; i++) {
      o.x += i === 15 ? 12 : 2;
      o.y += i % 3 === 0 ? 1 : 0;
      last = tracker.update(frame([o], i + 100)).get("part");
      assert.equal(last.lost, false, `size ${w}, frame ${i}, score ${last.score}`);
      near([last.bbox[0] * W, last.bbox[1] * H], [o.x, o.y], 2);
    }
    const lost = tracker.update(frame([], 321)).get("part");
    assert.equal(lost.lost, true);
    assert.deepEqual(lost.bbox, last.bbox);
    o.x = 8; o.y = 9; o.patch = noise(w * h, 888);
    tracker.reseed("part", box(o), frame([o]));
    near(tracker.update(frame([o])).get("part").bbox, box(o));
    tracker.drop("part");
    assert.equal(tracker.update(frame([])).size, 0);
  }
});

test("flat regions are lost and illumination changes retain a textured target", () => {
  const tracker = createTracker();
  const flat = { data: new Uint8Array(W * H).fill(90), w: W, h: H };
  tracker.seed("flat", [0.1, 0.1, 0.1, 0.1], flat);
  assert.equal(tracker.update(flat).get("flat").lost, true);
  const o = { x: 80, y: 80, w: 40, h: 32, patch: noise(40 * 32) };
  tracker.seed("part", box(o), frame([o]));
  o.patch = o.patch.map(v => Math.round(v * 0.7 + 30));
  o.x += 3;
  const result = tracker.update(frame([o])).get("part");
  assert.equal(result.lost, false);
  near(result.bbox, box(o));
});

test("strong matches refresh the template as appearance changes gradually", () => {
  const tracker = createTracker();
  const original = noise(40 * 32, 101), replacement = noise(40 * 32, 987);
  const o = { x: 80, y: 80, w: 40, h: 32, patch: original };
  tracker.seed("part", box(o), frame([o]));
  for (let i = 1; i <= 60; i++) {
    o.patch = original.map((v, k) => Math.round(v * (1 - i / 60) + replacement[k] * i / 60));
    const result = tracker.update(frame([o], i + 1000)).get("part");
    assert.equal(result.lost, false, `appearance frame ${i}`);
    near(result.bbox, box(o));
  }
});

test("anchors transform every geometry without mutation; arrows move only heads by default", () => {
  const anchor = { object_id: "part", bbox: [0.2, 0.2, 0.2, 0.2] };
  const ops = [
    { kind: "box", bbox: [0.2, 0.2, 0.1, 0.1], anchor },
    { kind: "circle", center: [0.3, 0.3], r: 0.1, anchor },
    { kind: "label", at: [0.3, 0.3], text: "part", anchor },
    { kind: "pin", at: [0.3, 0.3], anchor },
    { kind: "path", points: [[0.2, 0.2], [0.3, 0.3]], anchor },
    { kind: "arrow", from: [0.1, 0.1], to: [0.3, 0.3], anchor },
    { kind: "arrow", from: [0.2, 0.2], to: [0.3, 0.3], anchor, moveFrom: true },
    { kind: "pin", at: [0.1, 0.1] },
  ];
  const before = structuredClone(ops);
  const tracks = new Map([["part", { bbox: [0.4, 0.3, 0.4, 0.2], lost: false }]]);
  const moved = applyAnchors(ops, tracks);
  near(moved[0].bbox, [0.4, 0.3, 0.2, 0.1]);
  near(moved[1].center, [0.6, 0.4]);
  assert.ok(Math.abs(moved[1].r - 0.15) < 1e-10);
  for (const i of [2, 3]) near(moved[i].at, [0.6, 0.4]);
  near(moved[4].points[0], [0.4, 0.3]);
  near(moved[4].points[1], [0.6, 0.4]);
  near(moved[5].from, [0.1, 0.1]);
  near(moved[5].to, [0.6, 0.4]);
  near(moved[6].from, [0.4, 0.3]);
  assert.deepEqual(moved[7], ops[7]);
  assert.deepEqual(ops, before);
  tracks.get("part").lost = true;
  assert.deepEqual(applyAnchors(ops, tracks), ops);
  assert.deepEqual(applyAnchors(ops, new Map()), ops);
});

test("four objects: update median under 8 ms at 320x240", () => {
  const objects = Array.from({ length: 4 }, (_, i) => ({
    x: 30 + (i % 2) * 150, y: 30 + Math.floor(i / 2) * 110,
    w: 48, h: 48, patch: noise(48 * 48, i + 20),
  }));
  const tracker = createTracker();
  const first = frame(objects);
  objects.forEach((o, i) => tracker.seed(i, box(o), first));
  const times = [];
  for (let i = 0; i < 100; i++) {
    objects.forEach(o => { o.x += i % 2 ? -2 : 2; });
    const gray = frame(objects, i + 500);
    const start = performance.now();
    const tracks = tracker.update(gray);
    const elapsed = performance.now() - start;
    if (i >= 20) times.push(elapsed);
    objects.forEach((o, id) => {
      assert.equal(tracks.get(id).lost, false);
      near(tracks.get(id).bbox, box(o));
    });
  }
  times.sort((a, b) => a - b);
  const median = (times[39] + times[40]) / 2;
  console.log(`Tracker median: ${median.toFixed(3)} ms (4 objects, 320x240, 80 measured updates, 20 warmups)`);
  assert.ok(median < 8, `median ${median} ms`);
});
