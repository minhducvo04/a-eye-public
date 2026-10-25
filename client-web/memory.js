// Memory aid: the "where did I leave it" answer, the remembered-things count with Forget, and the caregiver
// settings. Pure helpers first (memory.test.mjs), then one small API client.
//
// Server routes. Live today: /api/rewind/status, /api/rewind/forget (this call's clips).
// TODO(rewind builder): the local-memory routes below are what this client expects; match them or tell the
// client package, then advertise them in /api/status (see createMemoryApi) and the preview store goes away.
//   GET  /api/memory/status?session_id=     -> { on, count, kept_days, bytes }
//   POST /api/memory/forget {session_id, scope: "last"|"today"} -> { count }
//   GET  /api/memory/settings               -> settings (shape: emptySettings())
//   PUT  /api/memory/settings  settings     -> settings (server checks consents and refused rooms again)
//   find answers: v2 `rewind_hit` today; a local `find_item` later with the same fields plus `place`.

import { fmtCallTime } from "./rewind.js";

export const REFUSED_ROOMS = Object.freeze(["bedroom", "bathroom"]);
export const ROOMS = Object.freeze(["kitchen", "living room", "hallway", "entryway", "office", "garage", "bedroom", "bathroom"]);
export const DEFAULT_KEPT_DAYS = 7;

export function emptySettings() {
  return {
    consent: { person: false, caregiver: false },
    items: [],
    rooms: ["kitchen", "living room", "entryway"],
    kept_days: DEFAULT_KEPT_DAYS,
    remembering: false,
  };
}

// "Seen 2:10 ago · in the blue bowl". callNowS and hit.t_s are seconds since the call started.
export function agoLine(hitS, callNowS, place) {
  const ago = Math.max(0, Math.round((Number(callNowS) || 0) - (Number(hitS) || 0)));
  const when = ago < 5 ? "Seen just now" : `Seen ${fmtCallTime(ago)} ago`;
  const where = String(place || "").trim();
  return where ? `${when} · in the ${where.replace(/^(in|on|at)\s+the\s+/i, "").replace(/^the\s+/i, "")}` : when;
}

const words = (text) => String(text || "").toLowerCase().match(/[a-z0-9]+/g) || [];

// The live box for the remembered thing: the tracked object sharing a word with its label, most confident first.
export function matchLive(hitObjects, liveObjects) {
  const wanted = new Set((hitObjects || []).flatMap((o) => words(o.label)));
  if (!wanted.size) return null;
  const hits = (liveObjects || []).filter((o) => words(o.label).some((w) => wanted.has(w)));
  hits.sort((a, b) => (Number(b.conf) || 0) - (Number(a.conf) || 0));
  return hits.length ? hits[0] : null;
}

// The Now panel's target. The server's find_item.live (grounded, guarded against false boxes) wins; a fresh
// tracker box with the same object id moves it. Without live, fall back to a label match on the tracker boxes.
export function liveTarget(hit, liveObjects) {
  const live = hit && hit.live;
  if (live && Array.isArray(live.bbox) && live.bbox.length === 4) {
    const label = String(hit.item || ((hit.objects || [])[0] || {}).label || "it");
    const tracked = (liveObjects || []).find((o) => o.id && o.id === live.object_id);
    return { id: live.object_id, label, bbox: tracked ? tracked.bbox : live.bbox, conf: tracked ? tracked.conf : 1 };
  }
  return matchLive(hit && hit.objects, liveObjects);
}

// Where the caregiver said a thing lives, for a question or the boxed labels ("keys" -> "blue bowl").
export function placeFor(text, items) {
  const said = new Set(words(text));
  for (const item of items || []) {
    if (words(item.item).some((w) => said.has(w) || said.has(`${w}s`) || said.has(w.replace(/s$/, "")))) return item.home;
  }
  return "";
}

// An arrow from the pane's bottom centre to the box centre, in 0..1 pane coordinates.
export function arrowTo(bbox) {
  const [x, y, w, h] = (bbox || [0, 0, 0, 0]).map((v) => Math.min(1, Math.max(0, Number(v) || 0)));
  const to = { x: x + w / 2, y: Math.min(1, y + h + 0.02) };
  return { from: { x: 0.5, y: 0.96 }, to };
}

export function countLine(n) {
  const count = Math.max(0, Math.round(Number(n) || 0));
  if (count === 0) return "Nothing remembered today";
  return `Remembering ${count} ${count === 1 ? "thing" : "things"} today`;
}

// The shown count while it animates from `from` to `to` over `ms` (ease out).
export function countAt(from, to, elapsedMs, ms = 600) {
  const t = Math.min(1, Math.max(0, elapsedMs / ms));
  const eased = 1 - (1 - t) ** 3;
  return Math.round(from + (to - from) * eased);
}

// "keys -> blue bowl", "keys: blue bowl" or "keys = blue bowl".
export function parseTag(text) {
  const m = String(text || "").match(/^\s*(.+?)\s*(?:->|→|:|=)\s*(.+?)\s*$/);
  if (!m) return null;
  return { item: m[1].slice(0, 40), home: m[2].slice(0, 60) };
}

export function roomAllowed(room) {
  return !REFUSED_ROOMS.includes(String(room || "").trim().toLowerCase());
}

export function retentionLine(days, bytes) {
  const d = Math.max(1, Math.round(Number(days) || DEFAULT_KEPT_DAYS));
  const mb = Math.max(0, Math.round((Number(bytes) || 0) / 1e6));
  return `Kept ${d} ${d === 1 ? "day" : "days"}, about ${mb} MB`;
}

// Remembering needs both consents. Refused rooms are dropped before anything is sent.
export function cleanSettings(s) {
  const base = { ...emptySettings(), ...(s || {}) };
  const consent = { person: Boolean(base.consent && base.consent.person), caregiver: Boolean(base.consent && base.consent.caregiver) };
  const rooms = [...new Set((base.rooms || []).map((r) => String(r).trim().toLowerCase()).filter((r) => r && roomAllowed(r)))];
  const items = (base.items || []).filter((i) => i && i.item && i.home).map((i) => ({ item: String(i.item), home: String(i.home) }));
  const both = consent.person && consent.caregiver;
  return { consent, items, rooms, kept_days: Math.min(30, Math.max(1, Math.round(Number(base.kept_days) || DEFAULT_KEPT_DAYS))), remembering: both && Boolean(base.remembering) };
}

// API client. Until the server advertises memory in GET /api/status (`features.memory` or `deps.memory`), no
// /api/memory route is called (a 404 would also be a console error): the count is this call's Rewind clips,
// Forget is this call's Rewind forget, and settings live in an in-page preview store (`preview: true`).
// TODO(rewind builder): add `features: { memory: true }` to /api/status when the routes above exist.
import { ownerHeaders } from './call-owner.js';

export function createMemoryApi({ fetch: doFetch = globalThis.fetch, search = globalThis.location ? globalThis.location.search : "" } = {}) {
  const token = new URLSearchParams(search).get("token");
  const headers = { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  let preview = null;
  let present = null;
  let rewindOn = false;
  const call = async (path, init = {}) => doFetch(path, { ...init, headers: { ...headers, ...await ownerHeaders(search, doFetch) } });
  async function json(reply) {
    if (!reply.ok) throw Object.assign(new Error(`memory ${reply.status}`), { status: reply.status });
    return reply.json();
  }
  function available() {
    present = present || call("/api/status").then(json).then(
      (s) => Boolean((s.features && s.features.memory) || (s.deps && s.deps.memory)), () => false);
    return present;
  }
  return {
    available,
    async status(sessionId) {
      const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
      if (await available()) return { ...(await json(await call(`/api/memory/status${q}`))), source: "memory" };
      const r = await json(await call(`/api/rewind/status${q}`));
      rewindOn = Boolean(r.on);
      return { on: rewindOn, count: Number(r.clips) || 0, source: "rewind" };
    },
    // rewindActive: the page knows Rewind is on for this call (a forget for a call without Rewind would 404).
    async forget(sessionId, scope, rewindActive = false) {
      if (await available()) {
        return json(await call("/api/memory/forget", { method: "POST", body: JSON.stringify({ session_id: sessionId, scope }) }));
      }
      // Rewind forgets the whole call; "Forget that" and "Forget today" both mean that until memory exists.
      if (rewindOn || rewindActive) {
        const reply = await call("/api/rewind/forget", { method: "POST", body: JSON.stringify({ session_id: sessionId }) });
        if (!reply.ok && reply.status !== 404) throw new Error(`forget ${reply.status}`);
      }
      rewindOn = false;
      return { count: 0 };
    },
    async settings() {
      if (!(await available())) {
        preview = preview || emptySettings();
        return { ...cleanSettings(preview), preview: true };
      }
      return { ...cleanSettings(await json(await call("/api/memory/settings"))), preview: false };
    },
    async save(settings) {
      const clean = cleanSettings(settings);
      if (!(await available())) {
        preview = clean;
        return { ...clean, preview: true };
      }
      const reply = await call("/api/memory/settings", { method: "PUT", body: JSON.stringify(clean) });
      return { ...cleanSettings(await json(reply)), preview: false };
    },
  };
}

// Split answer: the earlier frame boxed with its time, beside the live view with an arrow to the thing now.
// Returns update(liveObjects) so the arrow follows fresh `track` boxes while the sheet is open.
export function renderSplit(doc, body, { hit, line, stream }) {
  body.replaceChildren();
  const split = doc.createElement("div");
  split.className = "memory-split";
  const then = doc.createElement("figure");
  then.className = "memory-pane";
  const thenLabel = doc.createElement("figcaption");
  thenLabel.className = "memory-tag";
  thenLabel.textContent = `Then · ${fmtCallTime(hit.t_s)}`;
  then.append(thenLabel);
  if (hit.frame_b64) {
    const img = doc.createElement("img");
    img.decoding = "async"; // decode the kept frame off the paint path
    img.alt = `Frame at ${fmtCallTime(hit.t_s)} into the call`;
    img.src = `data:image/jpeg;base64,${hit.frame_b64}`;
    then.append(img);
  }
  for (const obj of hit.objects || []) {
    const box = doc.createElement("span");
    box.className = "rewind-box";
    const [x, y, w, h] = obj.bbox.map((v) => Math.min(1, Math.max(0, Number(v) || 0)) * 100);
    Object.assign(box.style, { left: `${x}%`, top: `${y}%`, width: `${w}%`, height: `${h}%` });
    box.textContent = String(obj.label || "");
    then.append(box);
  }
  const now = doc.createElement("figure");
  now.className = "memory-pane";
  const nowLabel = doc.createElement("figcaption");
  nowLabel.className = "memory-tag live";
  nowLabel.textContent = "Now";
  now.append(nowLabel);
  if (stream) {
    const video = doc.createElement("video");
    video.muted = true;
    video.autoplay = true;
    video.playsInline = true;
    video.setAttribute("playsinline", "");
    video.srcObject = stream;
    now.append(video);
  } else {
    const off = doc.createElement("p");
    off.className = "memory-off";
    off.textContent = "Camera is off";
    now.append(off);
  }
  const svgNs = "http://www.w3.org/2000/svg";
  const svg = doc.createElementNS(svgNs, "svg");
  svg.setAttribute("class", "memory-arrow");
  svg.setAttribute("viewBox", "0 0 100 100");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  const path = doc.createElementNS(svgNs, "path");
  svg.append(path);
  now.append(svg);
  const hint = doc.createElement("p");
  hint.className = "memory-hint";
  now.append(hint);
  split.append(then, now);
  const text = doc.createElement("p");
  text.className = "memory-line";
  text.textContent = line;
  body.append(text, split); // the answer first: on a phone the panes stack below it
  function update(liveObjects) {
    const live = liveTarget(hit, liveObjects);
    if (!live) {
      path.setAttribute("d", "");
      hint.textContent = stream ? "Not in view now. Look around slowly." : "";
      return null;
    }
    const { from, to } = arrowTo(live.bbox);
    const fx = from.x * 100, fy = from.y * 100, tx = to.x * 100, ty = to.y * 100;
    const ang = Math.atan2(ty - fy, tx - fx);
    const head = (a) => `${tx - 6 * Math.cos(ang + a)},${ty - 6 * Math.sin(ang + a)}`;
    path.setAttribute("d", `M${fx},${fy} L${tx},${ty} M${head(0.5)} L${tx},${ty} L${head(-0.5)}`);
    hint.textContent = `${live.label} · here now`;
    return live;
  }
  update([]);
  return update;
}
