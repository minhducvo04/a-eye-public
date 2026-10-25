// Live frame loop for the Safari call page. Scheduling is pure so tests can pass a clock.
// This module does not touch localStorage, does not download, and does not log images.

export const MAX_LONG_SIDE = 640;
export const JPEG_QUALITY = 0.7;
export const MAX_FPS = 2;
export const MIN_FRAME_GAP_MS = 1000 / MAX_FPS;
export const REPLY_TIMEOUT_MS = 1500;
export const STATS_WINDOW_MS = 1000;

const ORIENTATION_BY_TYPE = {
  "portrait-primary": "portrait",
  "portrait-secondary": "portrait_upside_down",
  "landscape-primary": "landscape_left",
  "landscape-secondary": "landscape_right",
};

export function initialLiveState() {
  return {
    cameraOn: false,
    paused: false,
    hidden: false,
    socketOpen: false,
    bufferedAmount: 0,
    pendingSeq: null,
    pendingSince: null,
    lastSentAt: null,
    lastRttMs: null,
    sendTimes: [],
  };
}

export function scaledSize(srcW, srcH, maxLongSide = MAX_LONG_SIDE) {
  const w0 = Math.max(0, Math.round(Number(srcW) || 0));
  const h0 = Math.max(0, Math.round(Number(srcH) || 0));
  if (w0 < 1 || h0 < 1) return { w: 0, h: 0 };
  const longSide = Math.max(w0, h0);
  if (longSide <= maxLongSide) return { w: w0, h: h0 };
  const scale = maxLongSide / longSide;
  return {
    w: Math.max(1, Math.round(w0 * scale)),
    h: Math.max(1, Math.round(h0 * scale)),
  };
}

// screen.orientation.type when the browser has it, otherwise portrait vs landscape from the window.
export function frameOrientation(type, viewW, viewH) {
  if (type && Object.prototype.hasOwnProperty.call(ORIENTATION_BY_TYPE, type)) {
    return ORIENTATION_BY_TYPE[type];
  }
  const w = Number(viewW) || 0;
  const h = Number(viewH) || 0;
  if (w > h) return "landscape_left";
  return "portrait";
}

export function buildFrameMessage(fields) {
  return {
    v: 1,
    type: "frame",
    session_id: fields.sessionId,
    seq: fields.seq,
    ts_ms: fields.tsMs,
    w: fields.w,
    h: fields.h,
    orientation: fields.orientation,
    jpeg_b64: fields.jpeg_b64,
  };
}

// Draw the camera frame into a canvas with the long side capped. Returns the JPEG payload only.
export function writeFrame(canvas, source, srcW, srcH) {
  const size = scaledSize(srcW, srcH, MAX_LONG_SIDE);
  if (size.w < 1 || size.h < 1) return null;
  canvas.width = size.w;
  canvas.height = size.h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.drawImage(source, 0, 0, size.w, size.h);
  const dataUrl = canvas.toDataURL("image/jpeg", JPEG_QUALITY);
  const comma = dataUrl.indexOf(",");
  if (comma < 0) return null;
  return { w: size.w, h: size.h, jpeg_b64: dataUrl.slice(comma + 1) };
}

export function pushSendTimes(times, nowMs, windowMs = STATS_WINDOW_MS) {
  const now = Number(nowMs) || 0;
  const kept = [];
  for (const t of times || []) {
    if (now - t < windowMs && now - t >= 0) kept.push(t);
  }
  kept.push(now);
  return kept;
}

export function sentFps(sendTimes, nowMs, windowMs = STATS_WINDOW_MS) {
  const now = Number(nowMs) || 0;
  let count = 0;
  for (const t of sendTimes || []) {
    if (now - t < windowMs && now - t >= 0) count += 1;
  }
  return count;
}

export function liveStats(state, nowMs) {
  return {
    fps: sentFps(state && state.sendTimes, nowMs),
    rttMs: state ? state.lastRttMs : null,
  };
}

export function markFrameSent(state, seq, nowMs) {
  const now = Number(nowMs) || 0;
  return {
    ...state,
    pendingSeq: seq,
    pendingSince: now,
    lastSentAt: now,
    sendTimes: pushSendTimes(state.sendTimes, now),
  };
}

// Any server message whose seq is at least the outstanding frame releases the slot.
export function noteServerSeq(state, message, nowMs) {
  if (!state || state.pendingSeq == null) return state;
  if (!message || typeof message.seq !== "number") return state;
  if (message.seq < state.pendingSeq) return state;
  const now = Number(nowMs) || 0;
  const rtt = state.pendingSince == null ? state.lastRttMs : now - state.pendingSince;
  return {
    ...state,
    pendingSeq: null,
    pendingSince: null,
    lastRttMs: rtt,
  };
}

// Fake-clock gate: rate limit, in-flight backpressure, 1.5 s timeout, buffer, pause, hidden tab.
export function decideFrame(state, nowMs) {
  const now = Number(nowMs) || 0;
  let next = state;
  let released = null;
  if (
    next.pendingSeq != null &&
    next.pendingSince != null &&
    now - next.pendingSince >= REPLY_TIMEOUT_MS
  ) {
    next = { ...next, pendingSeq: null, pendingSince: null };
    released = "timeout";
  }
  const deny = (reason) => ({ ok: false, reason, released, state: next });
  if (!next.cameraOn) return deny("camera");
  if (next.paused) return deny("paused");
  if (next.hidden) return deny("hidden");
  if (!next.socketOpen) return deny("socket");
  if (next.bufferedAmount > 0) return deny("buffer");
  if (next.pendingSeq != null) return deny("awaiting");
  if (next.lastSentAt != null && now - next.lastSentAt < MIN_FRAME_GAP_MS) return deny("rate");
  return { ok: true, reason: "send", released, state: next };
}

export function createFrameLoop(hooks) {
  let state = initialLiveState();
  let timer = 0;
  const gap = hooks.intervalMs || 100;

  function publish() {
    if (typeof hooks.onStats === "function") hooks.onStats(liveStats(state, hooks.now()));
  }

  function tick() {
    state = {
      ...state,
      cameraOn: Boolean(hooks.cameraOn()),
      hidden: typeof hooks.hidden === "function" ? Boolean(hooks.hidden()) : state.hidden,
      socketOpen: Boolean(hooks.socketOpen()),
      bufferedAmount: Number(hooks.bufferedAmount()) || 0,
    };
    const decision = decideFrame(state, hooks.now());
    state = decision.state;
    if (decision.ok) {
      const sent = hooks.captureAndSend(hooks.now());
      if (sent && typeof sent.seq === "number") {
        state = markFrameSent(state, sent.seq, sent.tsMs != null ? sent.tsMs : hooks.now());
      }
    }
    publish();
  }

  return {
    start() {
      if (timer) return;
      const setTimer = hooks.setInterval || ((fn, ms) => setInterval(fn, ms));
      timer = setTimer(tick, gap);
    },
    stop() {
      if (!timer) return;
      const clearTimer = hooks.clearInterval || ((id) => clearInterval(id));
      clearTimer(timer);
      timer = 0;
    },
    setPaused(paused) {
      state = { ...state, paused: Boolean(paused) };
    },
    togglePaused() {
      state = { ...state, paused: !state.paused };
      return state.paused;
    },
    isPaused() {
      return state.paused;
    },
    setHidden(hidden) {
      state = { ...state, hidden: Boolean(hidden) };
    },
    noteServer(message) {
      state = noteServerSeq(state, message, hooks.now());
      publish();
    },
  };
}
