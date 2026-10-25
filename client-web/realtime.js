// Pure fast-loop decisions for the agent page: speech parts, tracker seeds, HUD math.
// The page applies these with the camera, the tracker and speechSynthesis. No DOM here.

export const TRACK_GAP_MS = 1000 / 30;
export const HUD_WINDOW_MS = 1000;
export const DEV_HUD_W = 210;
export const DEV_HUD_H = 136;
export const ORB_W = 54;
export const ORB_H = 74;

const METRIC_KEYS = ["vlm_ms", "brain_first_token_ms", "first_audio_ms", "turn_ms"];

export function shouldTrack(lastAt, now, busy, gapMs = TRACK_GAP_MS) {
  if (busy) return false;
  if (lastAt != null && Number(now) - Number(lastAt) < gapMs) return false;
  return true;
}

export function emptySpeech() {
  return {
    turnId: null,
    nextPart: 0,
    buffer: {},
    lines: [],
    agent: "helper",
    closed: false,
    retired: [],
  };
}

function turnKey(msg) {
  if (msg && typeof msg.turn_id === "string" && msg.turn_id) return `turn:${msg.turn_id}`;
  if (msg && typeof msg.utterance_id === "string" && msg.utterance_id) return `say:${msg.utterance_id}`;
  return "say:";
}

function partIndex(msg) {
  return Number.isInteger(msg && msg.part) ? msg.part : 0;
}

function agentOf(msg) {
  return msg && msg.agent === "shopper" ? "shopper" : "helper";
}

function captionOf(state) {
  return (state.lines || []).map((line) => line.text).join(" ");
}

function copySpeech(state) {
  return {
    turnId: state.turnId,
    nextPart: state.nextPart,
    buffer: { ...state.buffer },
    lines: state.lines.slice(),
    agent: state.agent,
    closed: state.closed,
    retired: state.retired.slice(),
  };
}

function startTurn(prev, key, msg) {
  const retired = prev.retired.slice();
  if (prev.turnId) retired.push(prev.turnId);
  while (retired.length > 32) retired.shift();
  return {
    turnId: key,
    nextPart: 0,
    buffer: {},
    lines: [],
    agent: agentOf(msg),
    closed: false,
    retired,
  };
}

// Speak one turn's sentences in part order. A later turn retires the previous one.
// Parts that arrive early wait. Barge-in closes the current turn so the rest is dropped.
export function pushSay(state, msg) {
  const prev = state || emptySpeech();
  if (!msg || msg.type !== "say" || typeof msg.text !== "string" || !msg.text) {
    return { state: prev, speak: [], caption: captionOf(prev), agent: prev.agent, reset: false, ignored: true };
  }
  const key = turnKey(msg);
  if (key !== prev.turnId && prev.retired.includes(key)) {
    return { state: prev, speak: [], caption: captionOf(prev), agent: prev.agent, reset: false, ignored: true };
  }
  let next = prev;
  let reset = false;
  if (key !== prev.turnId) {
    next = startTurn(prev, key, msg);
    reset = true;
  } else if (prev.closed) {
    return { state: prev, speak: [], caption: captionOf(prev), agent: prev.agent, reset: false, ignored: true };
  } else {
    next = copySpeech(prev);
  }
  const part = partIndex(msg);
  if (part < next.nextPart || Object.prototype.hasOwnProperty.call(next.buffer, part)) {
    return { state: next, speak: [], caption: captionOf(next), agent: next.agent, reset, ignored: false };
  }
  next.buffer[part] = {
    utteranceId: typeof msg.utterance_id === "string" ? msg.utterance_id : "",
    text: msg.text,
    agent: agentOf(msg),
    part,
  };
  const speak = [];
  while (Object.prototype.hasOwnProperty.call(next.buffer, next.nextPart)) {
    const line = next.buffer[next.nextPart];
    delete next.buffer[next.nextPart];
    next.lines.push(line);
    next.agent = line.agent;
    speak.push(line);
    next.nextPart += 1;
  }
  return { state: next, speak, caption: captionOf(next), agent: next.agent, reset, ignored: false };
}

export function bargeSpeech(state) {
  const prev = state || emptySpeech();
  return {
    turnId: prev.turnId,
    nextPart: prev.nextPart,
    buffer: {},
    lines: prev.lines.slice(),
    agent: prev.agent,
    closed: true,
    retired: prev.retired.slice(),
  };
}

export function initialTrackerBook() {
  return { ids: [], boxes: {}, seq: {}, kind: {} };
}

function cleanBBox(bbox) {
  if (!Array.isArray(bbox) || bbox.length < 4) return null;
  let x = Number(bbox[0]);
  let y = Number(bbox[1]);
  let w = Number(bbox[2]);
  let h = Number(bbox[3]);
  if (![x, y, w, h].every(Number.isFinite)) return null;
  if (w < 0) {
    x += w;
    w = -w;
  }
  if (h < 0) {
    y += h;
    h = -h;
  }
  if (w <= 0 || h <= 0) return null;
  if (x < 0) {
    w += x;
    x = 0;
  }
  if (y < 0) {
    h += y;
    y = 0;
  }
  if (x >= 1 || y >= 1) return null;
  if (x + w > 1) w = 1 - x;
  if (y + h > 1) h = 1 - y;
  if (w <= 1e-6 || h <= 1e-6) return null;
  return [x, y, w, h];
}

function opOf(item) {
  if (!item || typeof item !== "object") return null;
  if (item.op && typeof item.op === "object" && item.op.kind) return item.op;
  return item;
}

function anchorMap(ops) {
  const out = {};
  const list = Array.isArray(ops) ? ops : [];
  for (const item of list) {
    const op = opOf(item);
    const anchor = op && op.anchor;
    if (!anchor || typeof anchor.object_id !== "string" || !anchor.object_id) continue;
    const bbox = cleanBBox(anchor.bbox);
    if (!bbox) continue;
    out[anchor.object_id] = bbox;
  }
  return out;
}

function copyBox(bbox) {
  return bbox.slice();
}

function blankBook() {
  return initialTrackerBook();
}

function incomingBoxes(objects) {
  const out = {};
  for (const obj of objects || []) {
    if (!obj || typeof obj.id !== "string" || !obj.id) continue;
    const bbox = cleanBBox(obj.bbox);
    if (!bbox) continue;
    out[obj.id] = bbox;
  }
  return out;
}

// Draw anchors seed objects. A track message overwrites those boxes (server wins),
// including when it ties the draw seq. An older seq does not replace a newer box.
// Ids no longer anchored, and absent from this track message, are dropped.
export function nextTrackerPlan(state, event) {
  const prev = state || blankBook();
  const camera = !event || event.camera !== false;
  if (!camera || (event && event.kind === "stop")) {
    return { state: blankBook(), reseed: [], drop: prev.ids.slice() };
  }
  const kind = event.kind === "track" ? "track" : "draw";
  const seq = typeof event.seq === "number" ? event.seq : 0;
  const anchors = anchorMap(event.ops);
  const boxes = {};
  const seqs = {};
  const kinds = {};

  if (kind === "draw") {
    // `changed` is this message's ops. Other anchors stay as last seeded.
    const changed = event.changed ? anchorMap(event.changed) : anchors;
    for (const id of Object.keys(anchors)) {
      if (!changed[id]) {
        if (prev.boxes[id]) {
          boxes[id] = copyBox(prev.boxes[id]);
          seqs[id] = prev.seq[id];
          kinds[id] = prev.kind[id] || "draw";
        } else {
          boxes[id] = copyBox(anchors[id]);
          seqs[id] = seq;
          kinds[id] = "draw";
        }
        continue;
      }
      const serverHolds = prev.kind[id] === "track"
        && typeof prev.seq[id] === "number"
        && prev.seq[id] >= seq
        && prev.boxes[id];
      if (serverHolds) {
        boxes[id] = copyBox(prev.boxes[id]);
        seqs[id] = prev.seq[id];
        kinds[id] = "track";
      } else {
        boxes[id] = copyBox(changed[id]);
        seqs[id] = seq;
        kinds[id] = "draw";
      }
    }
  } else {
    const incoming = incomingBoxes(event.objects);
    const referenced = new Set([...Object.keys(anchors), ...Object.keys(incoming)]);
    for (const id of referenced) {
      const prevNewer = prev.boxes[id] && typeof prev.seq[id] === "number" && prev.seq[id] > seq;
      if (prevNewer) {
        boxes[id] = copyBox(prev.boxes[id]);
        seqs[id] = prev.seq[id];
        kinds[id] = prev.kind[id] || "draw";
      } else if (incoming[id]) {
        boxes[id] = copyBox(incoming[id]);
        seqs[id] = seq;
        kinds[id] = "track";
      } else if (prev.boxes[id]) {
        boxes[id] = copyBox(prev.boxes[id]);
        seqs[id] = prev.seq[id];
        kinds[id] = prev.kind[id] || "draw";
      } else if (anchors[id]) {
        boxes[id] = copyBox(anchors[id]);
        seqs[id] = seq;
        kinds[id] = "draw";
      }
    }
  }

  const ids = Object.keys(boxes).sort();
  const drop = prev.ids.filter((id) => !boxes[id]).sort();
  const reseed = ids.map((id) => ({ id, bbox: copyBox(boxes[id]), source: kinds[id] }));
  return {
    state: { ids, boxes, seq: seqs, kind: kinds },
    reseed,
    drop,
  };
}

export function initialHud() {
  return {
    paints: [],
    fps: 0,
    trackerMs: null,
    pending: [],
    rttMs: null,
    metrics: {
      vlm_ms: null,
      brain_first_token_ms: null,
      first_audio_ms: null,
      turn_ms: null,
    },
    voiceAwaiting: null,
    voiceMs: null,
  };
}

export function notePaint(hud, now, windowMs = HUD_WINDOW_MS) {
  const prev = hud || initialHud();
  const t = Number(now) || 0;
  const paints = [];
  for (const stamp of prev.paints) {
    if (t - stamp < windowMs && t - stamp >= 0) paints.push(stamp);
  }
  paints.push(t);
  return { ...prev, paints, fps: paints.length };
}

export function noteTrackerMs(hud, ms) {
  const prev = hud || initialHud();
  const n = Number(ms);
  return { ...prev, trackerMs: Number.isFinite(n) ? n : prev.trackerMs };
}

export function noteFrameSent(hud, seq, now) {
  const prev = hud || initialHud();
  if (typeof seq !== "number") return prev;
  const pending = prev.pending.slice();
  pending.push({ seq, at: Number(now) || 0 });
  while (pending.length > 20) pending.shift();
  return { ...prev, pending };
}

// Round trip is the newest frame send whose seq is <= this draw or track seq.
export function noteFrameReply(hud, message, now) {
  const prev = hud || initialHud();
  if (!message || (message.type !== "draw" && message.type !== "track")) return prev;
  if (typeof message.seq !== "number") return prev;
  const t = Number(now) || 0;
  let rttMs = prev.rttMs;
  let bestSeq = -1;
  let bestAt = null;
  const pending = [];
  for (const item of prev.pending) {
    if (item.seq <= message.seq) {
      if (item.seq >= bestSeq) {
        bestSeq = item.seq;
        bestAt = item.at;
      }
    } else {
      pending.push(item);
    }
  }
  if (bestAt != null) rttMs = t - bestAt;
  return { ...prev, pending, rttMs };
}

export function noteMetrics(hud, message) {
  const prev = hud || initialHud();
  if (!message || message.type !== "metrics") return prev;
  const metrics = {
    vlm_ms: null,
    brain_first_token_ms: null,
    first_audio_ms: null,
    turn_ms: null,
  };
  for (const key of METRIC_KEYS) {
    const n = Number(message[key]);
    metrics[key] = Number.isFinite(n) ? n : null;
  }
  return { ...prev, metrics };
}

export function noteFinalWords(hud, now) {
  const prev = hud || initialHud();
  return { ...prev, voiceAwaiting: Number(now) || 0 };
}

export function noteFirstSound(hud, now) {
  const prev = hud || initialHud();
  if (prev.voiceAwaiting == null) return prev;
  return {
    ...prev,
    voiceAwaiting: null,
    voiceMs: (Number(now) || 0) - prev.voiceAwaiting,
  };
}

function msText(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return String(Math.round(value));
}

export function formatHud(hud) {
  const src = hud || initialHud();
  const metrics = src.metrics || {};
  const fps = typeof src.fps === "number" ? String(src.fps) : "-";
  return [
    `fps ${fps}`,
    `track ${msText(src.trackerMs)} ms`,
    `rtt ${msText(src.rttMs)} ms`,
    `vlm ${msText(metrics.vlm_ms)}`,
    `brain ${msText(metrics.brain_first_token_ms)}`,
    `audio ${msText(metrics.first_audio_ms)}`,
    `turn ${msText(metrics.turn_ms)}`,
    `voice ${msText(src.voiceMs)} ms`,
  ].join("\n");
}

function overlap(a, b) {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

function orbRects(poses) {
  const rects = [];
  for (const id of ["helper", "shopper"]) {
    const pose = poses && poses[id];
    if (!pose || pose.opacity <= 0.02) continue;
    const x = Number(pose.x);
    const y = Number(pose.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    rects.push({ x, y, w: ORB_W, h: ORB_H });
  }
  return rects;
}

function clampBox(x, y, w, h, vw, vh) {
  if (!(vw > 0) || !(vh > 0)) return { x, y, w, h };
  const width = Math.min(w, vw);
  const height = Math.min(h, vh);
  return {
    x: Math.min(Math.max(0, vw - width), Math.max(0, x)),
    y: Math.min(Math.max(0, vh - height), Math.max(0, y)),
    w: width,
    h: height,
  };
}

// Top-left by default, shifted when that box would cover a presence orb.
export function devHudPosition(view, poses) {
  const vw = Number(view && view.w) || 0;
  const vh = Number(view && view.h) || 0;
  const w = Math.max(120, Math.min(DEV_HUD_W, vw > 0 ? Math.max(120, vw - 24) : DEV_HUD_W));
  const h = DEV_HUD_H;
  const orbs = orbRects(poses);
  const candidates = [
    { x: 12, y: 100 },
    { x: 12 + ORB_W + 16, y: 18 },
  ];
  let lowest = 100;
  for (const orb of orbs) lowest = Math.max(lowest, orb.y + orb.h + 8);
  candidates.push({ x: 12, y: lowest });
  if (vh > h + 40) candidates.push({ x: 12, y: Math.max(12, vh - h - 180) });
  let fallback = clampBox(12, 100, w, h, vw, vh);
  for (const candidate of candidates) {
    const box = clampBox(candidate.x, candidate.y, w, h, vw, vh);
    fallback = box;
    if (!orbs.some((orb) => overlap(box, orb))) return box;
  }
  return fallback;
}

// Neural VAD barge-in, in two steps so speaker echo cannot cut the reply by itself:
// the first speech frame ducks playback at once; speech that lasts minSpeechMs stops it;
// a misfire (too short, usually echo or a click) restores it. Pure: the caller applies actions.
export const BARGE_VAD = Object.freeze({
  model: "v5",
  positiveSpeechThreshold: 0.5,
  negativeSpeechThreshold: 0.35,
  minSpeechMs: 120,
  redemptionMs: 450,
  preSpeechPadMs: 200,
});

export function initialBarge() {
  return { phase: "idle", ducked: false };
}

// event: "start" | "real_start" | "misfire" | "end"; speaking: is the AI talking right now.
export function nextBarge(state, event, speaking) {
  const prev = state || initialBarge();
  if (event === "start") {
    if (speaking && !prev.ducked) return { state: { phase: "maybe", ducked: true }, action: "duck" };
    return { state: { ...prev, phase: "maybe" }, action: null };
  }
  if (event === "real_start") {
    if (speaking) return { state: { phase: "user", ducked: false }, action: "stop" };
    return { state: { phase: "user", ducked: false }, action: prev.ducked ? "unduck" : null };
  }
  if (event === "misfire") {
    return { state: initialBarge(), action: prev.ducked ? "unduck" : null };
  }
  if (event === "end") {
    return { state: initialBarge(), action: prev.ducked ? "unduck" : (prev.phase === "user" ? "flush" : null) };
  }
  return { state: prev, action: null };
}

// Noisy halls: one sensitivity knob (1 = only loud close speech, 10 = catch soft far speech) drives both VADs.
// The energy gate never sits below the measured ambient noise times AMBIENT_MARGIN.
export const DEFAULT_SENSITIVITY = 5;
export const AMBIENT_MARGIN = 2.5;
// Ambient RMS above this (about -34 dBFS after browser noise suppression) counts as a loud room.
export const NOISY_AMBIENT = 0.02;

export function vadSettings(sensitivity, ambient) {
  const s = Math.min(10, Math.max(1, Number.isFinite(Number(sensitivity)) ? Number(sensitivity) : DEFAULT_SENSITIVITY));
  const shift = DEFAULT_SENSITIVITY - s; // positive = stricter
  const base = 0.02 * Math.pow(2, shift / 2.5);
  const floor = Number.isFinite(ambient) && ambient > 0 ? ambient * AMBIENT_MARGIN : 0;
  const positive = Math.min(0.85, Math.max(0.2, BARGE_VAD.positiveSpeechThreshold + shift * 0.06));
  return {
    sensitivity: s,
    energyThreshold: Math.max(base, floor),
    positiveSpeechThreshold: positive,
    negativeSpeechThreshold: Math.max(0.1, positive - 0.15),
    minSpeechMs: Math.min(250, Math.max(60, BARGE_VAD.minSpeechMs + shift * 20)),
  };
}

export function dbfs(rms) {
  const n = Number(rms);
  if (!Number.isFinite(n) || n <= 0) return -100;
  return Math.max(-100, Math.round(20 * Math.log10(n)));
}
