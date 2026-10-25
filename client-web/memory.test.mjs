import test from "node:test";
import assert from "node:assert/strict";
import {
  agoLine, arrowTo, cleanSettings, countAt, countLine, createMemoryApi, liveTarget, matchLive, parseTag, placeFor, retentionLine, roomAllowed,
} from "./memory.js";

test("answer line says how long ago and where", () => {
  assert.equal(agoLine(10, 140, "blue bowl"), "Seen 2:10 ago · in the blue bowl");
  assert.equal(agoLine(10, 140, "in the blue bowl"), "Seen 2:10 ago · in the blue bowl");
  assert.equal(agoLine(100, 102, ""), "Seen just now");
  assert.equal(agoLine(200, 100), "Seen just now"); // never negative
});

test("live match and arrow", () => {
  const live = [{ id: "a", label: "cup", bbox: [0, 0, 0.1, 0.1], conf: 0.9 }, { id: "b", label: "car keys", bbox: [0.6, 0.2, 0.2, 0.2], conf: 0.7 }];
  assert.equal(matchLive([{ label: "keys" }], live).id, "b");
  assert.equal(matchLive([{ label: "wallet" }], live), null);
  assert.equal(matchLive([], live), null);
  const { from, to } = arrowTo([0.6, 0.2, 0.2, 0.2]);
  assert.deepEqual(from, { x: 0.5, y: 0.96 });
  assert.ok(Math.abs(to.x - 0.7) < 1e-9 && Math.abs(to.y - 0.42) < 1e-9);
});

test("Now panel: server live box first, tracker label match only without it", () => {
  const tracked = [{ id: "o2", label: "cup", bbox: [0.1, 0.1, 0.1, 0.1], conf: 0.8 }];
  // keys in view per the server, though no tracker box is labelled keys
  const hit = { item: "keys", objects: [{ label: "keys" }], live: { object_id: "o7", bbox: [0.5, 0.5, 0.1, 0.1] } };
  assert.deepEqual(liveTarget(hit, tracked), { id: "o7", label: "keys", bbox: [0.5, 0.5, 0.1, 0.1], conf: 1 });
  // a fresh tracker box with the same id moves the arrow
  const moved = liveTarget(hit, [{ id: "o7", label: "thing", bbox: [0.4, 0.4, 0.1, 0.1], conf: 0.9 }]);
  assert.deepEqual(moved.bbox, [0.4, 0.4, 0.1, 0.1]);
  assert.equal(moved.label, "keys");
  // no live field: old label match
  assert.equal(liveTarget({ objects: [{ label: "cup" }] }, tracked).id, "o2");
  assert.equal(liveTarget({ objects: [{ label: "keys" }] }, tracked), null);
});

test("caregiver homes answer where", () => {
  const items = [{ item: "keys", home: "blue bowl" }, { item: "glasses", home: "hall table" }];
  assert.equal(placeFor("where did I put my key", items), "blue bowl");
  assert.equal(placeFor("glasses", items), "hall table");
  assert.equal(placeFor("phone", items), "");
});

test("count badge and its countdown", () => {
  assert.equal(countLine(12), "Remembering 12 things today");
  assert.equal(countLine(1), "Remembering 1 thing today");
  assert.equal(countLine(0), "Nothing remembered today");
  assert.equal(countAt(12, 0, 0), 12);
  assert.equal(countAt(12, 0, 600), 0);
  const mid = countAt(12, 0, 300);
  assert.ok(mid > 0 && mid < 12);
});

test("tags, refused rooms, retention and consent", () => {
  assert.deepEqual(parseTag("keys -> blue bowl"), { item: "keys", home: "blue bowl" });
  assert.deepEqual(parseTag("glasses: hall table"), { item: "glasses", home: "hall table" });
  assert.equal(parseTag("keys"), null);
  assert.equal(roomAllowed("Bedroom"), false);
  assert.equal(roomAllowed("bathroom "), false);
  assert.equal(roomAllowed("kitchen"), true);
  assert.equal(retentionLine(7, 42.4e6), "Kept 7 days, about 42 MB");
  assert.equal(retentionLine(1, 0), "Kept 1 day, about 0 MB");
  const one = cleanSettings({ consent: { person: true }, remembering: true, rooms: ["Kitchen", "bedroom", "bathroom"] });
  assert.equal(one.remembering, false); // the caregiver has not agreed
  assert.deepEqual(one.rooms, ["kitchen"]);
  assert.equal(cleanSettings({ consent: { person: true, caregiver: true }, remembering: true }).remembering, true);
});

function fakeFetch(routes) {
  const calls = [];
  const fn = async (url, init = {}) => {
    calls.push([url, init.method || "GET", init.body ? JSON.parse(init.body) : null, init.headers]);
    const key = `${init.method || "GET"} ${url.split("?")[0]}`;
    const hit = routes[key];
    if (!hit) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => hit(init.body ? JSON.parse(init.body) : null) };
  };
  fn.calls = calls;
  return fn;
}

test("api uses /api/memory when the server has it", async () => {
  const fetch = fakeFetch({
    "GET /api/status": () => ({ ok: true, features: { memory: true } }),
    "POST /api/call-owner": () => ({ owner: "synthetic-owner" }),
    "GET /api/memory/status": () => ({ on: true, count: 12, bytes: 5e6 }),
    "POST /api/memory/forget": (b) => ({ count: b.scope === "today" ? 0 : 11 }),
  });
  const api = createMemoryApi({ fetch, search: "?token=t" });
  assert.equal((await api.status("s1")).count, 12);
  assert.equal((await api.forget("s1", "last")).count, 11);
  assert.equal((await api.forget("s1", "today")).count, 0);
  assert.equal(fetch.calls[2][0], "/api/memory/status?session_id=s1");
  assert.equal(fetch.calls[2][3].Authorization, "Bearer t");
  assert.equal(fetch.calls[2][3]["X-Aeye-Owner"], "synthetic-owner");
});

test("without memory routes: Rewind count and forget, settings only previewed", async () => {
  const fetch = fakeFetch({
    "GET /api/status": () => ({ ok: true, deps: {} }),
    "GET /api/rewind/status": () => ({ on: true, clips: 3 }),
    "POST /api/rewind/forget": () => ({ deleted: 3 }),
  });
  const api = createMemoryApi({ fetch, search: "" });
  assert.deepEqual(await api.status("s1"), { on: true, count: 3, source: "rewind" });
  assert.deepEqual(await api.forget("s1", "today"), { count: 0 });
  assert.equal(fetch.calls.at(-1)[0], "/api/rewind/forget");
  const saved = await api.save({ consent: { person: true, caregiver: true }, remembering: true, items: [{ item: "keys", home: "blue bowl" }] });
  assert.equal(saved.preview, true);
  assert.deepEqual((await api.settings()).items, [{ item: "keys", home: "blue bowl" }]);
  assert.ok(fetch.calls.every(([url]) => !url.startsWith("/api/memory")), "no calls to missing routes");
  // Rewind was never on for this call: nothing to delete, and no 404.
  const quiet = fakeFetch({ "GET /api/status": () => ({ ok: true }) });
  assert.deepEqual(await createMemoryApi({ fetch: quiet, search: "" }).forget("s2", "last"), { count: 0 });
  assert.equal(quiet.calls.length, 1);
});
