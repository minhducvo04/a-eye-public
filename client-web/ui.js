// Call-page state for hearing and for hearing the reply. No DOM.
// The page applies caption, transcript, labels, mute and fallback with these results.

export const AUDIO_FALLBACK_MS = 1500;
export const CAPTION_FADE_MS = 2000;
export const TRANSCRIPT_CAP = 50;
export const TEST_LINE = "I can hear you.";

export const MUTE_KEY = "aeye.muted";
export const VOLUME_KEY = "aeye.volume";

export const LABEL = {
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: "Speaking…",
  idle: "Tap the mic to talk",
};

export const ERROR_MIC = "Microphone blocked. Allow it in Safari settings for this site.";
export const ERROR_HTTPS = "Camera and mic need HTTPS. On a phone use the https address from make phone.";
export const NOTICE_SERVER_STT = "Using server speech recognition";
export const NOTICE_BROWSER_VOICE = "Server voice unavailable, using the browser voice";

export const SPEAKERS = {
  you: { name: "You", color: "#f4f7fb" },
  helper: { name: "Helper", color: "#8eb6ff" },
  shopper: { name: "Shopper", color: "#e8943a" },
};

const PHASES = ["idle", "listening", "thinking", "speaking"];

export function initialCallState() {
  return {
    pack: "",
    started: false,
    showStart: false,
    micOn: false,
    userTalking: false,
    localListening: false,
    helperPhase: "idle",
    shopperPhase: "idle",
    phase: "idle",
    label: LABEL.idle,
    error: "",
    sttNotice: "",
    voiceNotice: "",
    sttMode: "browser",
    needsHello: false,
    muted: false,
    volume: 1,
    caption: emptyCaption(),
    transcript: [],
    replies: [],
    turnId: "",
    replyText: "",
    replyAgent: "helper",
    waits: {},
    seenSays: {},
  };
}

export function emptyCaption(now = 0) {
  return { text: "", kind: "none", at: now, visible: false };
}

export function captionModel(caption, event) {
  const prev = caption && typeof caption === "object" ? caption : emptyCaption();
  if (!event || typeof event !== "object") return prev;
  const now = Number(event.now) || 0;
  if (event.type === "interim") {
    const text = String(event.text || "").trim();
    if (!text) return emptyCaption(now);
    return { text, kind: "interim", at: now, visible: true };
  }
  if (event.type === "final") {
    const text = String(event.text || "").trim();
    if (!text) return prev;
    return { text, kind: "final", at: now, visible: true };
  }
  if (event.type === "tick") {
    if (prev.kind !== "final" || !prev.visible) return prev;
    if (now - prev.at >= CAPTION_FADE_MS) return { ...prev, visible: false };
    return prev;
  }
  if (event.type === "clear") return emptyCaption(now);
  return prev;
}

function speakerOf(agent) {
  if (agent === "shopper") return SPEAKERS.shopper;
  if (agent === "you") return SPEAKERS.you;
  return SPEAKERS.helper;
}

export function transcriptModel(lines, event) {
  const prev = Array.isArray(lines) ? lines.slice() : [];
  if (!event || typeof event !== "object") return prev.slice(-TRANSCRIPT_CAP);
  const text = String(event.text || "").trim();
  if (!text) return prev.slice(-TRANSCRIPT_CAP);
  const fromAgent = event.type === "say";
  const agent = fromAgent ? (event.agent === "shopper" ? "shopper" : "helper") : "you";
  const spec = speakerOf(agent);
  prev.push({ speaker: spec.name, agent, text, color: spec.color });
  while (prev.length > TRANSCRIPT_CAP) prev.shift();
  return prev;
}

export function fallbackDecision(sayTimeMs, audioSeenMs, nowMs) {
  if (audioSeenMs != null && Number.isFinite(Number(audioSeenMs))) {
    return { fallback: false, waitedMs: 0 };
  }
  const say = Number(sayTimeMs);
  const now = Number(nowMs);
  if (!Number.isFinite(say) || !Number.isFinite(now)) return { fallback: false, waitedMs: 0 };
  const waitedMs = now - say;
  return { fallback: waitedMs >= AUDIO_FALLBACK_MS, waitedMs };
}

export function stateLabel(state) {
  const phase = derivePhase(state || {});
  return LABEL[phase] || LABEL.idle;
}

export function testVoicePath(ttsMode) {
  return ttsMode === "server" ? "server" : "browser";
}

export function clampVolume(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 1;
  if (n < 0) return 0;
  if (n > 1) return 1;
  return n;
}

export function readAudioPrefs(storage) {
  const fallback = { muted: false, volume: 1 };
  try {
    if (!storage || typeof storage.getItem !== "function") return fallback;
    let mutedRaw = null;
    let volRaw = null;
    try {
      mutedRaw = storage.getItem(MUTE_KEY);
    } catch {
      return fallback;
    }
    try {
      volRaw = storage.getItem(VOLUME_KEY);
    } catch {
      return fallback;
    }
    const volume = Number(volRaw);
    return {
      muted: mutedRaw === "1" || mutedRaw === "true",
      volume: volRaw == null || volRaw === "" || !Number.isFinite(volume) ? 1 : clampVolume(volume),
    };
  } catch {
    return fallback;
  }
}

export function writeAudioPrefs(storage, prefs) {
  try {
    if (!storage || typeof storage.setItem !== "function") return false;
    const muted = Boolean(prefs && prefs.muted);
    const volume = clampVolume(prefs && prefs.volume);
    storage.setItem(MUTE_KEY, muted ? "1" : "0");
    storage.setItem(VOLUME_KEY, String(volume));
    return true;
  } catch {
    return false;
  }
}

function derivePhase(state) {
  if (state.userTalking) return "listening";
  const phases = [state.helperPhase, state.shopperPhase];
  if (phases.includes("speaking")) return "speaking";
  if (phases.includes("thinking")) return "thinking";
  if (phases.includes("listening") || state.micOn) return "listening";
  return "idle";
}

function finish(state) {
  const phase = derivePhase(state);
  return { ...state, phase, label: LABEL[phase] };
}

function copyWaits(waits) {
  const out = {};
  for (const key of Object.keys(waits || {})) out[key] = { ...waits[key] };
  return out;
}

export function nextCallState(state, event) {
  const base = state && typeof state === "object" ? { ...initialCallState(), ...state } : initialCallState();
  base.caption = base.caption || emptyCaption();
  base.transcript = Array.isArray(base.transcript) ? base.transcript : [];
  base.replies = Array.isArray(base.replies) ? base.replies : [];
  base.waits = base.waits || {};
  base.seenSays = base.seenSays || {};
  return finish(reduce(base, event || {}));
}

function reduce(state, event) {
  const type = event.type;
  if (type === "pack") {
    const pack = typeof event.pack === "string" && event.pack ? event.pack : state.pack;
    return { ...state, pack, showStart: state.started ? false : true };
  }
  if (type === "tap_start") {
    return { ...state, started: true, showStart: false };
  }
  if (type === "mic_start") {
    return { ...state, micOn: true, localListening: true, error: "" };
  }
  if (type === "mic_stop") {
    return { ...state, micOn: false, localListening: false, userTalking: false };
  }
  if (type === "interim") {
    const caption = captionModel(state.caption, event);
    const talking = caption.kind === "interim";
    const error = talking ? "" : state.error;
    return { ...state, caption, error, userTalking: talking, localListening: talking || state.micOn };
  }
  if (type === "final") {
    const caption = captionModel(state.caption, event);
    if (caption === state.caption) return state;
    return {
      ...state,
      caption,
      error: "",
      userTalking: false,
      localListening: state.micOn,
      transcript: transcriptModel(state.transcript, { type: "final", text: event.text }),
    };
  }
  if (type === "tick") {
    const caption = captionModel(state.caption, event);
    if (caption === state.caption) return state;
    return { ...state, caption };
  }
  if (type === "agent_state") {
    const phase = PHASES.includes(event.state) ? event.state : null;
    const who = event.agent === "shopper" ? "shopper" : event.agent === "helper" ? "helper" : null;
    if (!phase || !who) return state;
    if (who === "shopper") return { ...state, shopperPhase: phase };
    return { ...state, helperPhase: phase };
  }
  if (type === "turn_times") {
    const transcript = state.transcript.slice();
    const index = transcript.findLastIndex(line => line.agent === "you");
    if (index >= 0) transcript[index] = { ...transcript[index], timestamps: { ...event.timestamps } };
    return { ...state, transcript };
  }
  if (type === "reply_delta") {
    const newTurn = event.turnId !== state.turnId;
    return { ...state, turnId: event.turnId, replyAgent: event.agent || "helper",
      replies: newTurn ? [] : state.replies, waits: newTurn ? {} : state.waits,
      replyText: (newTurn ? "" : state.replyText) + String(event.text || "") };
  }
  if (type === "say") return reduceSay(state, event);
  if (type === "audio_started") return reduceAudioStarted(state, event);
  if (type === "audio_ended") return reduceAudioEnded(state, event);
  if (type === "audio_timeout") return reduceFallback(state, event, false);
  if (type === "audio_error") return reduceFallback(state, event, true);
  if (type === "permission_denied") {
    return { ...state, micOn: false, localListening: false, userTalking: false, error: ERROR_MIC };
  }
  if (type === "https") {
    return { ...state, micOn: false, localListening: false, userTalking: false, error: ERROR_HTTPS };
  }
  if (type === "stt_unsupported") {
    return {
      ...state,
      sttMode: "server",
      needsHello: true,
      sttNotice: NOTICE_SERVER_STT,
    };
  }
  if (type === "hello_sent") {
    return { ...state, needsHello: false };
  }
  if (type === "mute") {
    return { ...state, muted: Boolean(event.muted) };
  }
  if (type === "volume") {
    if (!Number.isFinite(Number(event.volume))) return state;
    return { ...state, volume: clampVolume(event.volume) };
  }
  if (type === "error") {
    return { ...state, error: String(event.message || "") };
  }
  return state;
}

function reduceSay(state, event) {
  const text = String(event.text || "").trim();
  if (!text) return state;
  const utteranceId = typeof event.utteranceId === "string" ? event.utteranceId : "";
  const part = Number.isInteger(event.part) ? event.part : 0;
  const seenKey = `${utteranceId}:${part}:${text}`;
  if (state.seenSays[seenKey]) return state;
  const seenSays = { ...state.seenSays, [seenKey]: true };
  const seenKeys = Object.keys(seenSays);
  while (seenKeys.length > 80) delete seenSays[seenKeys.shift()];
  const turnId = typeof event.turnId === "string" ? event.turnId : state.turnId;
  const newTurn = Boolean(turnId) && turnId !== state.turnId;
  const agent = event.agent === "shopper" ? "shopper" : "helper";
  const replies = newTurn ? [] : state.replies.slice();
  const waits = newTurn ? {} : copyWaits(state.waits);
  const prior = waits[utteranceId];
  const now = Number(event.now) || 0;
  waits[utteranceId] = {
    sayTimeMs: prior && prior.audioSeenMs != null ? prior.sayTimeMs : now,
    audioSeenMs: prior ? prior.audioSeenMs : null,
    text,
    agent,
    fellBack: prior ? prior.fellBack : false,
  };
  replies.push({
    utteranceId,
    agent,
    text,
    color: speakerOf(agent).color,
    speaker: speakerOf(agent).name,
    active: Boolean(prior && prior.audioSeenMs != null && !prior.fellBack),
  });
  return {
    ...state,
    error: "",
    turnId,
    seenSays,
    replyText: newTurn ? "" : state.replyText,
    replies,
    waits,
    transcript: transcriptModel(state.transcript, { type: "say", agent, text }),
  };
}

function reduceAudioStarted(state, event) {
  const id = typeof event.utteranceId === "string" ? event.utteranceId : "";
  const now = Number(event.now) || 0;
  const waits = copyWaits(state.waits);
  const prior = waits[id] || {
    sayTimeMs: now,
    audioSeenMs: null,
    text: "",
    agent: "helper",
    fellBack: false,
  };
  waits[id] = { ...prior, audioSeenMs: now };
  const replies = state.replies.map((line) => ({ ...line, active: line.utteranceId === id }));
  return { ...state, waits, replies };
}

function reduceAudioEnded(state, event) {
  const id = typeof event.utteranceId === "string" ? event.utteranceId : "";
  const replies = state.replies.map((line) => {
    if (!id || line.utteranceId === id) return { ...line, active: false };
    return line;
  });
  return { ...state, replies };
}

function reduceFallback(state, event, force) {
  const id = typeof event.utteranceId === "string" ? event.utteranceId : "";
  const wait = state.waits[id];
  if (!wait || wait.fellBack) return state;
  const now = Number(event.now) || 0;
  if (!force) {
    const decision = fallbackDecision(wait.sayTimeMs, wait.audioSeenMs, now);
    if (!decision.fallback) return state;
  }
  const waits = copyWaits(state.waits);
  waits[id] = { ...wait, fellBack: true };
  const replies = state.replies.map((line) => ({ ...line, active: line.utteranceId === id }));
  return {
    ...state,
    waits,
    replies,
    voiceNotice: state.voiceNotice || NOTICE_BROWSER_VOICE,
  };
}

export function createCallUi(options = {}) {
  const storage = options.storage;
  const prefs = readAudioPrefs(storage);
  let state = nextCallState(initialCallState(), { type: "volume", volume: prefs.volume });
  state = { ...state, muted: prefs.muted, label: stateLabel({ ...state, muted: prefs.muted }), phase: derivePhase({ ...state, muted: prefs.muted }) };
  return {
    getState() {
      return state;
    },
    dispatch(event) {
      state = nextCallState(state, event);
      if (event && (event.type === "mute" || event.type === "volume")) writeAudioPrefs(storage, state);
      return state;
    },
  };
}

// Already spoken sentences keep their highlight; the unfinished suffix stays visible.
export function replyLines(state) {
  const lines = state.replies.slice();
  const said = lines.map(line => line.text).join(" ");
  const text = state.replyText || "";
  const tail = text.startsWith(said) ? text.slice(said.length).trimStart() : (lines.length ? "" : text);
  if (tail) {
    const spec = speakerOf(state.replyAgent);
    lines.push({ text: tail, speaker: spec.name, color: spec.color, active: false });
  }
  return lines;
}
