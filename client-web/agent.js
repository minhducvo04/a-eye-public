import { wireFlipCamera } from "./flip-camera.js";
import { liveSettings } from './gemini-live.js';
import { triggerClient } from './trigger.js';
import { setupFamily } from './family-note.js';
import { createValveCard } from './step-check.js';
import { isHeadset, requestLocalMedia } from './headset.js';
import { bootPairCamera, phoneMicRoute, setupPairCall } from './pair.js';
import { callOwner, ownerHeaders, ownerQuery, resetCallOwner } from './call-owner.js';
// Agent call page. Speaks the v2 WebSocket contract on /ws2.
// Frames exist only as the JPEG about to be sent. Nothing is written to storage.

import { paintDraw, applyDraw, pruneDraw, viewTransform, visibleOps, targetLabels, splitByTargets } from "./draw.js";
import { writeFrame } from "./live.js";
import { agoLine, countAt, countLine, createMemoryApi, placeFor, renderSplit } from "./memory.js";
import { fetchRecap, forgetRewind, initialRewind, nextRewind, NOTICE as REWIND_NOTICE, PROMPTS as REWIND_PROMPTS, renderRecap, showsRewindChip } from "./rewind.js";
import { sharpCanvasSize } from "./overlay.js";
import {
  AUDIO_FALLBACK_MS,
  ERROR_HTTPS,
  TEST_LINE,
  createCallUi,
  fallbackDecision,
  replyLines,
} from "./ui.js";
import {
  bargeSpeech,
  dbfs,
  devHudPosition,
  emptySpeech,
  formatHud,
  initialHud,
  initialTrackerBook,
  nextTrackerPlan,
  noteFinalWords,
  noteFirstSound,
  noteFrameReply,
  noteFrameSent,
  noteMetrics,
  notePaint,
  noteTrackerMs,
  pushSay,
  shouldTrack,
} from "./realtime.js";
import { applyAnchors, createTracker, toGray } from "./track.js";

import { nextTurnState, statusLine } from "./turn.js";
import { appendingLevel, LEVEL_HISTORY_COUNT, listeningLine, normalizedLevel, poolLevel, smoothedLevel, waveBars } from "./listen.js";
import { capitalized, visibleWait, waitAfterAgentState, waitAfterTurn, waitCopy, waitPercent, waitProgress } from "./wait.js";
import {
  chooseDevice,
  createLevelPreview,
  deviceLabel,
  deviceOptions,
  devicesLocked,
  isMissingDevice,
  loadChoice,
  micLine,
  saveChoice,
  sinkSupported,
  videoConstraints,
} from "./devices.js";

// Same names and order as ios/AEyeCore Modes.all; Live is centred at launch.
const MODES = [
  { id: "general", title: "General", pack: "general" },
  { id: "pc_build", title: "PC Build", pack: "pc_build" },
  { id: "cooking", title: "Cooking", pack: "cooking" },
  { id: "assemble", title: "Assembly", pack: "assemble" },
  { id: "memory", title: "Memory", pack: "memory" },
  { id: "mechanic", title: "Mechanic", pack: null },
  { id: "plumbing", title: "Sink", pack: "home" },
  { id: "engineering", title: "Engineering", pack: "engineering" },
  { id: "gemini-live", title: "Gemini Live", pack: "gemini-live" },
];
const DEFAULT_MODE = MODES.findIndex((m) => m.id === "gemini-live");
const RING_SLOT = 84;
const PACK_LABEL = Object.fromEntries(MODES.filter((m) => m.pack).map((m) => [m.pack, m.title]));
const PRESENCE_STATES = ["idle", "listening", "thinking", "speaking"];

const FRAME_MS = 1000;
const BOARD_ID = "demo-b650";
const TRACK_W = 320;

function boot() {
  const headset = isHeadset(location.search, navigator.userAgent);
  if (!headset && bootPairCamera()) return;
  document.body?.classList.toggle("headset", headset);
  const headsetPanel = document.getElementById("headset-panel");
  const headsetStatus = document.getElementById("headset-status");
  headsetPanel?.classList.toggle("hidden", !headset);
  const picker = document.getElementById("picker");
  const stage = document.getElementById("stage");
  const video = document.getElementById("camera");
  const still = document.getElementById("still");
  const drawCanvas = document.getElementById("draw");
  const callEl = document.getElementById("call");
  // Controls fold into one button after a short idle time so the caption has the lower screen.
  const CONTROLS_HIDE_MS = 3000;
  const controlsBar = document.getElementById("controls");
  const controlsToggle = document.getElementById("controls-toggle");
  let controlsTimer = 0;
  function setControls(collapsed) {
    callEl.classList.toggle("controls-collapsed", collapsed);
    controlsToggle.setAttribute("aria-expanded", String(!collapsed));
    controlsToggle.setAttribute("aria-label", collapsed ? "Show controls" : "Hide controls");
    controlsToggle.setAttribute("title", collapsed ? "Show controls" : "Hide controls");
  }
  function armControls() {
    window.clearTimeout(controlsTimer);
    setControls(false);
    controlsTimer = window.setTimeout(() => setControls(true), CONTROLS_HIDE_MS);
  }
  function stopControls() {
    window.clearTimeout(controlsTimer);
    setControls(false);
  }
  controlsToggle.addEventListener("click", (event) => {
    event.stopPropagation();
    if (callEl.classList.contains("controls-collapsed")) armControls();
    else { window.clearTimeout(controlsTimer); setControls(true); }
  });
  // Using any control keeps the bar open a little longer; it folds again 3 s after the last touch.
  for (const type of ["pointerdown", "focusin"]) {
    controlsBar.addEventListener(type, (event) => {
      if (event.target === controlsToggle || event.target?.closest?.("#controls-toggle")) return;
      if (!callEl.classList.contains("controls-collapsed")) armControls();
    });
  }
  const ringEl = document.getElementById("ring");
  const ringTitle = document.getElementById("ring-title");
  const ringHint = document.getElementById("ring-hint");
  const dotEl = document.getElementById("presence-dot");
  const elapsedEl = document.getElementById("elapsed");
  const linkEl = document.getElementById("link");
  const captionEl = document.getElementById("caption");
  const captionEyebrow = document.getElementById("caption-eyebrow");
  const captionSpeaker = document.getElementById("caption-speaker");
  const powerGate = document.getElementById("power-gate");
  const cameraIcon = document.getElementById("camera-icon");
  const micIcon = document.getElementById("mic-icon");
  const backdropEl = document.getElementById("backdrop");
  const settingsEl = document.getElementById("settings");
  const marketTitle = document.getElementById("market-title");
  const permissionEl = document.getElementById("permission");
  const permissionTitle = document.getElementById("permission-title");
  const permissionText = document.getElementById("permission-text");
  const connStatusEl = document.getElementById("conn-status");
  const serverHostEl = document.getElementById("server-host");
  const packLabel = document.getElementById("pack-label");
  const errorEl = document.getElementById("error");
  const noticeEl = document.getElementById("notice");
  const marketEl = document.getElementById("market");
  const marketBody = document.getElementById("market-body");
  const rewindEl = document.getElementById("rewind-sheet");
  const rewindBody = document.getElementById("rewind-body");
  const rewindToggle = document.getElementById("rewind-toggle");
  const rewindBadge = document.getElementById("rewind-badge");
  const repliesEl = document.getElementById("replies");
  const interimEl = document.getElementById("interim");
  const listenEl = document.getElementById("listen");
  const waitEl = document.getElementById("wait-card");
  const waitArc = document.getElementById("wait-arc");
  const waitPct = document.getElementById("wait-pct");
  const waitEyebrow = document.getElementById("wait-eyebrow");
  const waitLine = document.getElementById("wait-line");
  const scanEl = document.getElementById("scan");
  const scanBand = scanEl.querySelector(".scan-band");
  const scanPath = document.getElementById("scan-path");
  const typedEl = document.getElementById("typed");
  const micBtn = document.getElementById("mic");
  const holdBtn = document.getElementById("hold");
  const sensEl = document.getElementById("sensitivity");
  const ambientEl = document.getElementById("ambient");
  // Hold to talk is the reliable mode in a loud hall; ?hold=1 forces it, a noisy calibration turns it on.
  let holdMode = new URLSearchParams(location.search).get("hold") === "1";
  let holdPressed = false;
  const startBtn = document.getElementById("start-camera");
  const powerBtn = document.getElementById("power-off");
  const overlayEl = document.getElementById("start-overlay");
  const transcriptBtn = document.getElementById("transcript-toggle");
  const transcriptList = document.getElementById("transcript-list");
  const muteBtn = document.getElementById("mute");
  const volumeEl = document.getElementById("volume");
  const waveEl = document.getElementById("wave");
  const grab = document.createElement("canvas");
  const grayCanvas = document.createElement("canvas");
  const devOn = new URLSearchParams(location.search).get("dev") === "1";
  const devEl = devOn ? document.createElement("p") : null;
  if (devOn && document.body && document.body.classList) document.body.classList.add("dev");
  if (devEl) {
    devEl.className = "dev-hud";
    devEl.setAttribute("aria-hidden", "true");
    const hudRoot = stage.querySelector(".hud");
    if (hudRoot) hudRoot.append(devEl);
  }

  let sessionId = randomId();
  // The People panel is not on the call page any more; without it the family feature is a no-op.
  const family = document.getElementById('family-panel') ? setupFamily({ session: () => sessionId,
    speak: text => voice.speak("family-note", text, { localOnly: true }), request: async (path, data) => {
    const result = await apiFetch(path, data);
    const body = await result.json();
    if (!result.ok) throw new Error(body.detail || 'Family note unavailable.');
    return body;
  } }) : { consume: () => false, complete() {} };
  const params = new URLSearchParams(location.search);
  // Server voice (Cartesia when keyed, else Kokoro on the box) by default; the browser voice is the last resort.
  // ?tts=cartesia means server TTS with a Cartesia voice from /api/voices.
  const storedVoice = (() => { try { return safeStorage()?.getItem("aeye.voice") || ""; } catch { return ""; } })();
  const wantCartesia = params.get("tts") === "cartesia";
  let tts = !wantCartesia && (params.get("tts") === "browser" || storedVoice === "browser") ? "browser" : "server";
  let ttsVoice = storedVoice && storedVoice !== "browser" ? storedVoice : "";
  const voicePick = document.getElementById("voice-pick");
  let stt = params.get("stt") === "server" ? "server" : "browser";
  let beforeLiveAudio = null;
  // Device choice (Settings > Devices), remembered per browser. "" means the browser default.
  const micLineEl = document.getElementById("mic-line");
  const micLevelEl = document.getElementById("mic-level");
  const micPick = document.getElementById("mic-pick");
  const cameraPick = document.getElementById("camera-pick");
  const speakerPick = document.getElementById("speaker-pick");
  const speakerRow = document.getElementById("speaker-row");
  const devicesAllow = document.getElementById("devices-allow");
  let mediaDevices = [];
  let micChoice = loadChoice(safeStorage(), "audioinput");
  let cameraChoice = loadChoice(safeStorage(), "videoinput");
  let speakerChoice = loadChoice(safeStorage(), "audiooutput");
  let deviceNotice = "";
  let deviceNoticeTimer = 0;
  // Browser recognition always hears the system default mic, so a chosen mic is heard through Whisper.
  let sttForMic = false;
  const micPreview = createLevelPreview({
    getUserMedia: (c) => navigator.mediaDevices.getUserMedia(c),
    makeContext: () => new (window.AudioContext || window.webkitAudioContext)(),
    onLevel: (level) => showMicLevel(level),
    base: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  if (micChoice && stt === "browser" && !params.get("stt")) {
    stt = "server";
    sttForMic = true;
  }
  const sttLabel = document.getElementById("stt-mode");
  const reducedQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  let ui = createCallUi({ storage: safeStorage() });

  let pack = "";
  let homeStartPending = false;
  let socket = null;
  let retryTimer = 0;
  let reconnectDelay = 500;
  let turn = nextTurnState(null, {}, Date.now());
  // Proof counters are no longer shown on the call page; keep them when a page still has the nodes.
  const setText = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  async function refreshProof() {
    const proofSession = sessionId;
    try {
      const response = await apiFetch('/api/proof?session_id=' + encodeURIComponent(sessionId));
      if (!response.ok) throw new Error('Proof unavailable');
      const proof = await response.json();
      if (proofSession !== sessionId) return;
      setText('proof-pictures', `Pictures saved: ${proof.pictures_saved ?? 'unknown'}`);
      setText('proof-pictures-sent', `Pictures sent: ${proof.pictures_sent ?? 'unknown'}`);
      setText('proof-cloud-calls', `Cloud AI calls: ${proof.cloud_ai_calls ?? 'unknown'}`);
      setText('proof-cloud-bytes', `Bytes to cloud: ${proof.bytes_to_cloud ?? 'unknown'}`);
    } catch {
      setText('proof-pictures', 'Pictures saved: unknown');
      setText('proof-pictures-sent', 'Pictures sent: unknown');
      setText('proof-cloud-calls', 'Cloud AI calls: unknown');
      setText('proof-cloud-bytes', 'Bytes to cloud: unknown');
    }
  }
  refreshProof();
  window.setInterval(refreshProof, 2000);
  let pendingText = null;
  let seq = 0;
  let cameraOn = false;
  let rewindOn = false; // off by default; the switch sends clips to TwelveLabs
  let rewind = initialRewind(); // one state for the bottom-bar button and the Settings switch
  let rewindNoticeTimer = 0;
  const rewindBtn = document.getElementById("rewind-btn");
  const privateToggle = document.getElementById("private-toggle");
  const privateBadge = document.getElementById("private-badge");
  // Private mode: hello.private makes the server keep everything local; the page uses server STT (browser
  // recognition can send audio to Google), the server voice (Kokoro) and keeps Rewind off.
  let privateMode = (() => { try { return safeStorage()?.getItem("aeye.private") === "1"; } catch { return false; } })();
  // Home first: four cloud switches. Claude, Cartesia and Google default on; TwelveLabs stays off.
  // The TwelveLabs switch and the Rewind toggle are one consent.
  const CLOUD_DEFAULTS = { claude: true, cartesia: true, twelvelabs: false, google: true };
  let cloud = (() => {
    try {
      const saved = JSON.parse(safeStorage()?.getItem("aeye.cloud") || "null");
      return { ...CLOUD_DEFAULTS, ...(saved && typeof saved === "object" ? saved : {}), twelvelabs: false };
    } catch {
      return { ...CLOUD_DEFAULTS };
    }
  })();
  const cloudBoxes = {
    google: document.getElementById("cloud-google"),
    claude: document.getElementById("cloud-claude"),
    cartesia: document.getElementById("cloud-cartesia"),
    twelvelabs: document.getElementById("cloud-twelvelabs"),
  };
  const leavesList = document.getElementById("leaves-list");
  const rewindAsk = document.getElementById("rewind-ask");
  const rewindChip = document.getElementById("rewind-chip");
  const rewindPrompts = document.getElementById("rewind-prompts");
  let starting = false;
  let frameTimer = 0;
  let photoUrl = "";
  let drawState = applyDraw(null, null, 0);
  // Presence as CallState.reduce does it: thinking or speaking by one agent makes the other idle.
  let agents = { helper: "idle", shopper: "idle" };
  let link = "connecting";
  let ended = false;
  let callStartedAt = 0;
  // Memory aid (memory.js): live boxes for the split answer's arrow, caregiver homes, the remembered count.
  const memoryApi = createMemoryApi();
  let liveObjects = [];
  let splitUpdate = null;
  let memoryItems = [];
  let memoryCount = 0;
  let memoryShown = 0;
  let memoryTimer = 0;
  let memorySource = ""; // "memory" once /api/memory answers; until then the Rewind clip count stands in
  const memoryEl = document.getElementById("memory");
  const memoryCountEl = document.getElementById("memory-count");
  const memoryActions = document.getElementById("memory-actions");
  let powerConfirmed = false;
  // ?mode=memory centres a mode at launch (demo and caregiver links); otherwise PC Build, as iOS.
  // Shared-token calls have private, temporary owners; cross-call caregiver settings need personal sign-in.
  const caregiverLink = document.getElementById("caregiver-link");
  if (caregiverLink && params.get("token")) {
    caregiverLink.removeAttribute('href');
    caregiverLink.setAttribute('aria-disabled', 'true');
    caregiverLink.textContent = 'Caregiver settings require personal sign-in';
  }
  const askedMode = MODES.findIndex((m) => m.id === params.get("mode"));
  let ringIndex = askedMode >= 0 ? askedMode : DEFAULT_MODE;
  let voice = null;
  let listening = false;
  let raf = 0;
  let speech = emptySpeech();
  const talkQueue = [];
  let talkBusy = false;
  const fallbackTimers = new Map();
  // ListeningStrip: smoothed 0..1 levels, oldest first (SpeechIn.levels). Cleared when the mic stops.
  let waveLevels = [];
  let levelPool = null;
  // CallState.wait: set while an agent works on an answer (wait.js).
  let wait = null;
  // A paired phone is the camera and the mic: the laptop mic is off meanwhile (pair.js phoneMicRoute).
  let micRoute = { phone: false, resume: false };
  let waitShown = "";
  let lastUiTick = 0;
  const tracker = createTracker();
  let tracks = new Map();
  let book = initialTrackerBook();
  let pendingPlan = null;
  let trackBusy = false;
  let lastTrackAt = null;
  let hud = initialHud();

  function voiceOptions() {
    return {
      micDeviceId: micChoice,
      holdToTalk: holdMode,
      sensitivity: Number(sensEl && sensEl.value) || 5,
      onAmbient: (reading) => {
        if (ambientEl) ambientEl.textContent = `Noise ${dbfs(reading.rms)} dB${reading.noisy ? " (loud)" : ""}`;
        if (reading.noisy && reading.calibrated && !holdMode) void enableHold();
      },
      onFinal: (text) => {
        if (voice?.isInputBlocked()) return;
        const clean = String(text || "").trim();
        if (!clean) return;
        ui.dispatch({ type: "final", text: clean, now: Date.now() });
        renderCall();
        sendUser(clean, stt === "server" ? "server_stt" : "browser_stt");
      },
      onInterim: (text) => {
        turnEvent({ type: "interim", text });
        ui.dispatch({ type: "interim", text: String(text || "").trim(), now: Date.now() });
        renderCall();
      },
      onState: (state) => {
        if (state === "listening") {
          listening = true;
          ui.dispatch({ type: "mic_start" });
          turnEvent({ type: "mic_start" });
        } else if (state === "idle" || state === "stopped") {
          listening = false;
          waveLevels = [];
          levelPool = null;
          ui.dispatch({ type: "mic_stop" });
          turnEvent({ type: "mic_stop" });
        }
        renderMic();
        renderCall();
      },
      onBargeIn: (id, ms) => {
        speech = bargeSpeech(speech);
        talkQueue.length = 0;
        talkBusy = false;
        clearAllFallbacks();
        ui.dispatch({ type: "audio_ended", utteranceId: "", now: Date.now() });
        send({
          v: 2,
          type: "barge_in",
          session_id: sessionId,
          utterance_id: id,
          at_ms: Math.max(0, Math.round(Number(ms) || 0)),
        });
        renderCall();
      },
      onLevel: (level, rms) => {
        const n = Number(level);
        const value = Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 0;
        showMicLevel(value);
        // The waveform uses the raw RMS on the phone's decibel scale, one value per 20 ms window.
        const pooled = poolLevel(levelPool, rms, performance.now());
        levelPool = pooled.pool;
        if (pooled.rms == null) return;
        const next = smoothedLevel(waveLevels.length ? waveLevels[waveLevels.length - 1] : 0, normalizedLevel(pooled.rms));
        waveLevels = appendingLevel(waveLevels, next, LEVEL_HISTORY_COUNT);
        renderListen(ui.getState());
      },
      onMicError: (kind) => {
        if (kind === "missing") {
          void micMissing();
          return;
        }
        if (kind === "unsupported") {
          turnEvent({ type: "cause", detail: "Browser can't do speech recognition: using server" });
          void switchToServerStt();
          return;
        }
        if (kind === "no_speech") {
          turnEvent({ type: "cause", detail: "No speech detected" });
          return;
        }
        if (kind === "denied") {
          turnEvent({ type: "cause", detail: "Mic blocked" });
          ui.dispatch({ type: "permission_denied" });
          renderCall();
        }
      },
      onSpeakStart: (id) => {
        if (id !== "test-voice") markSound();
      },
      onSpeakEnd: () => {
        talkBusy = false;
        ui.dispatch({ type: "audio_ended", utteranceId: "", now: Date.now() });
        renderCall();
        pumpTalk();
      },
      onAudioError: (id) => {
        failAudio(typeof id === "string" ? id : "");
      },
      sttMode: stt,
      onAudioChunk: (pcm16Base64, audioSeq, final) => {
        if (voice?.isInputBlocked()) return;
        if (turn.stage === "hearing") turnEvent({ type: "interim", text: "Speech detected" });
        if (final) turnEvent({ type: "sent" });
        send({
          v: 2,
          type: "audio_in",
          session_id: sessionId,
          seq: audioSeq,
          sample_rate: 16000,
          pcm16_b64: pcm16Base64,
          final: Boolean(final),
        });
      },
    };
  }

  function randomId() {
    if (globalThis.crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }

  function wsUrl() {
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    const token = new URLSearchParams(location.search).get("token");
    const base = `${scheme}//${location.host}/ws2${token ? `?token=${encodeURIComponent(token)}${ownerQuery()}` : ""}`;
    if (pack === 'engineering') return `${base}${base.includes('?') ? '&' : '?'}mode=engineering`;
    return liveSettings(pack, cloud.google, privateMode).allowed ? `${base}${base.includes("?") ? "&" : "?"}mode=gemini-live&google=${cloud.google && !privateMode ? "1" : "0"}` : base;
  }

  function reducedMotion() {
    return headset || Boolean(reducedQuery && reducedQuery.matches);
  }

  function send(message) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(message));
    return true;
  }

  function nextSeq() {
    seq += 1;
    return seq;
  }

  function setLink(next) {
    link = next;
    if (next === "reconnecting") wait = null; // CallState: .disconnected ends the wait
    renderLink();
  }

  function renderLink() {
    linkEl.textContent = link === "reconnecting" ? "Reconnecting…" : "Connecting…";
    linkEl.classList.toggle("hidden", link === "live" || !pack);
    const labels = { connecting: "Connecting…", live: "Connected", reconnecting: "Reconnecting…" };
    if (connStatusEl) connStatusEl.textContent = pack ? labels[link] : "Not in a call";
  }

  function applyAgentState(agent, state) {
    if (agent !== "helper" && agent !== "shopper") return;
    if (!PRESENCE_STATES.includes(state)) return;
    agents = { ...agents, [agent]: state };
    if (state === "thinking" || state === "speaking") agents[agent === "helper" ? "shopper" : "helper"] = "idle";
    renderPresence();
  }

  function renderPresence() {
    const active = agents.shopper !== "idle" ? "shopper" : agents.helper !== "idle" ? "helper" : "";
    const state = active ? agents[active] : "idle";
    dotEl.setAttribute("data-agent", active);
    dotEl.setAttribute("data-state", state);
    const who = active === "shopper" ? "Shopper" : "Helper";
    dotEl.setAttribute("aria-label", active ? `${who} is ${state}` : "");
  }

  function showProblem(text) {
    ui.dispatch({ type: "error", message: text || "" });
    renderCall();
  }

  function turnEvent(event) {
    const before = turn.timestamps;
    turn = nextTurnState(turn, event, Date.now());
    if (turn.timestamps !== before) {
      ui.dispatch({ type: "turn_times", timestamps: turn.timestamps });
      renderTranscript(ui.getState());
    }
    const pipelineEl = document.getElementById("pipeline");
    pipelineEl.textContent = statusLine(turn, Date.now());
    // Styling hooks only: the line shows for problems (or ?dev=1); Expert shows as a badge.
    if (pipelineEl.dataset) pipelineEl.dataset.stage = turn.stage || "idle";
    document.getElementById("expert-badge")?.classList?.toggle("hidden", turn.expertAt == null);
  }

  const pipelineTimer = window.setInterval(() => {
    const retries = turn.retries;
    turnEvent({ type: "tick" });
    if (turn.retries > retries && pendingText) send(pendingText);
  }, 100);
  window.addEventListener("pagehide", () => window.clearInterval(pipelineTimer));

  function sendUser(text, source) {
    const clean = String(text || "").trim();
    if (!clean) return false;
    if (family.consume(clean, source)) return true;
    pendingText = {
      v: 2,
      type: "user_text",
      session_id: sessionId,
      text: clean,
      final: true,
      source,
    };
    turnEvent({ type: "sent", text: clean });
    const ok = send(pendingText);
    if (ok) hud = noteFinalWords(hud, Date.now());
    if (!ok) showProblem("Not connected.");
    return ok;
  }

  function sendHello() {
    if (!pack) return false;
    const hello = {
      v: 2,
      type: "hello",
      session_id: sessionId,
      pack: ["gemini-live", "engineering"].includes(pack) ? "general" : pack,
      client: "web",
      tts,
      stt,
      camera: cameraOn,
    };
    if (tts === "server" && ttsVoice) hello.voice = ttsVoice;
    if (pack === "pc_build") hello.board_id = BOARD_ID;
    if (rewindOn && !privateMode) hello.rewind = true;
    if (privateMode) hello.private = true;
    else hello.cloud = { claude: cloud.claude, cartesia: cloud.cartesia, twelvelabs: cloud.twelvelabs && rewindOn };
    const ok = send(hello);
    if (ok) ui.dispatch({ type: "hello_sent" });
    const keepBox = document.getElementById("rewind-keep");
    if (ok && rewindOn && keepBox && keepBox.checked) sendRewind();
    return ok;
  }

  function connect() {
    valveCard.reset();
    if (!pack) return;
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
    const ws = new WebSocket(wsUrl());
    socket = ws;
    ws.addEventListener("open", () => {
      if (socket !== ws) return;
      reconnectDelay = 500;
      // A fresh socket is a fresh server session: the power-off gate, drawing and presence start over.
      powerConfirmed = false;
      drawState = applyDraw(null, null, 0);
      agents = { helper: "idle", shopper: "idle" };
      wait = null;
      // The old session's phone left with it: the laptop mic comes back if it was on.
      if (micRoute.phone) routeMic({ type: "pair_status", role: "call", state: "left" });
      rewind = nextRewind(rewind, { type: "reset" });
      ui.dispatch({ type: "error", message: "" });
      setLink("live");
      renderPresence();
      sendHello();
      startTrigger();
      turnEvent({ type: "connected" });
      renderCall();
    });
    ws.addEventListener("message", (event) => {
      if (socket !== ws || typeof event.data !== "string") return;
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      onServer(message);
    });
    ws.addEventListener('close', () => { if (socket === ws) stopTrigger?.(); });
    ws.addEventListener("close", (event) => {
      if (socket !== ws) return;
      valveCard.reset();
      socket = null;
      window.clearTimeout(retryTimer);
      setLink("reconnecting");
      turnEvent({ type: "closed" });
      if (event?.code === 4401) {
        ended = true;
        showProblem("Please sign in again");
        window.clearInterval(pipelineTimer);
        linkEl.textContent = "Please sign in again";
        if (connStatusEl) connStatusEl.textContent = "Please sign in again";
        document.getElementById("pipeline").textContent = "Please sign in again";
        const reload = document.createElement("button");
        reload.textContent = "Reload to sign in";
        reload.addEventListener("click", () => location.reload());
        errorEl.append(reload);
        return;
      }
      if (ended) return;
      retryTimer = window.setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(10000, reconnectDelay * 2);
    });
  }

  // A getter: sessionId changes when a new call starts, and a captured value sent stale ids (seen in the two-page run).
  const onPair = setupPairCall(send, () => sessionId);
  const valveCard = createValveCard(document.getElementById('valve-check'));

  async function presenter(action) {
    const response = await apiFetch('/api/dad-demo?session_id=' + encodeURIComponent(sessionId) + (action?.state ? '&action=verdict' : ''),
      action);
    if (!response.ok) throw new Error('Presenter controls unavailable.');
    const data = await response.json();
    document.getElementById('guide-presenter')?.classList.remove('hidden');
    const toggle = document.getElementById('guide-test');
    if (toggle) toggle.checked = data.enabled;
    document.getElementById('guide-test-label')?.classList.toggle('hidden', !data.enabled);
    const pass = document.getElementById('guide-pass');
    if (pass) pass.disabled = !data.enabled;
    return true;
  }
  document.getElementById('guide-test')?.addEventListener('change', async event => {
    try { await presenter({ enabled: event.target.checked }); }
    catch { showProblem('Presenter controls unavailable.'); }
  });
  document.getElementById('guide-pass')?.addEventListener('click', async () => {
    try { if (await presenter({ state: 'closed' })) sendUser('next', 'typed'); }
    catch { showProblem('Presenter check unavailable.'); }
  });
  document.getElementById('guide-stop')?.addEventListener('click', () => sendUser('water near the outlet', 'typed'));

  function onServer(message) {
    if (!message || typeof message !== "object") return;
    if (message.detail === 'Presenter available') void presenter().catch(() => {});
    valveCard.event(message);
    onPair(message);
    if (message.type === "pair_status") routeMic(message);
    const now = Date.now();
    if (message.type === "turn") {
      if (message.stage === 'received') document.getElementById('proof-safety')?.classList.add('hidden');
      if (message.detail === 'Stopped by code') document.getElementById('proof-safety')?.classList.remove('hidden');
      if (message.detail === 'Home: complete') family.complete('sink');
      if (message.detail === 'Engineering: complete') family.complete('ee_led');
      if (message.detail === 'Home: sink') { pack = 'home'; packLabel.textContent = 'Sink'; }
      if (message.detail?.startsWith('Brain: ')) setText('proof-brain', message.detail);
      wait = waitAfterTurn(wait, message, now);
      if (message.stage === "failed" && message.detail) ui.dispatch({ type: "error", message: String(message.detail) });
      if (message.stage === "received") pendingText = null;
      if (message.text && (stt === "server" || micRoute.phone) && !turn.text) {
        ui.dispatch({ type: "final", text: message.text, now });
      }
      turnEvent(message);
      renderCall();
      return;
    }
    if (message.type === "reply_delta") {
      turnEvent({ type: "first_text" });
      ui.dispatch({ type: "reply_delta", turnId: `turn:${message.turn_id}`, agent: message.agent, text: message.text });
      renderCall();
      return;
    }
    if (message.type === "draw") {
      hud = noteFrameReply(hud, message, now);
      const before = drawState;
      drawState = applyDraw(drawState, message, now);
      if (drawState !== before) {
        const changed = Array.isArray(message.ops) ? message.ops : [];
        commitTracker("draw", message.seq, null, changed);
        renderCall();
      }
      return;
    }
    if (message.type === "track") {
      hud = noteFrameReply(hud, message, now);
      commitTracker("track", message.seq, message.objects);
      liveObjects = Array.isArray(message.objects) ? message.objects : [];
      if (splitUpdate && !rewindEl.classList.contains("hidden")) splitUpdate(liveObjects);
      return;
    }
    if (message.type === "metrics") {
      hud = noteMetrics(hud, message);
      return;
    }
    if (message.type === "agent_state") {
      if (homeStartPending && message.state === 'listening') {
        homeStartPending = false;
        sendUser(pack === 'engineering' ? 'light an LED' : 'sink', 'typed');
      }
      // Phone speech barged in on the server: stop this laptop's playback as its own VAD would.
      if (micRoute.phone && message.state === "listening") stopLocalPlayback();
      wait = waitAfterAgentState(wait, message.agent, message.state, now);
      applyAgentState(message.agent, message.state);
      ui.dispatch({ type: "agent_state", agent: message.agent, state: message.state });
      renderCall();
      return;
    }
    if (message.type === "say") {
      turnEvent({ type: "first_text" });
      const result = pushSay(speech, message);
      speech = result.state;
      if (result.reset) {
        talkQueue.length = 0;
        talkBusy = false;
        clearAllFallbacks();
        if (voice) voice.cancel();
      }
      for (const line of result.speak) {
        ui.dispatch({
          type: "say",
          agent: line.agent,
          utteranceId: line.utteranceId,
          text: line.text,
          part: line.part,
          turnId: speech.turnId,
          now,
        });
        utteranceTurn.set(line.utteranceId, speech.turnId);
        // Server audio is synthesized sentence by sentence, so later sentences can wait longer than 1.5 s.
        // Arm the browser fallback only until the first audio of this turn arrives.
        if (tts === "server" && !audioTurns.has(speech.turnId)) armFallback(line.utteranceId);
      }
      if (tts === "browser" && result.speak.length) enqueueTalk(result.speak);
      renderCall();
      return;
    }
    if (message.type === "audio" && voice) {
      const id = typeof message.utterance_id === "string" ? message.utterance_id : "";
      const turnOfAudio = utteranceTurn.get(id);
      if (turnOfAudio != null) {
        audioTurns.add(turnOfAudio);
        for (const [uid, tid] of utteranceTurn) if (tid === turnOfAudio) clearFallback(uid);
      }
      clearFallback(id);
      ui.dispatch({ type: "audio_started", utteranceId: id, now });
      renderCall();
      try {
        voice.playPcm(message.utterance_id, message.pcm16_b64, Boolean(message.final));
      } catch {
        failAudio(id);
      }
      return;
    }
    if (message.type === "market_results") {
      showMarket(message.result);
      return;
    }
    if (message.type === "rewind_state") {
      rewind = nextRewind(rewind, { type: "server", on: message.on, clips: message.clips });
      if (memorySource !== "memory" && Number.isInteger(message.clips)) setMemoryCount(message.clips);
      else void refreshMemory();
      showRewind(Boolean(message.on));
      return;
    }
    if (message.type === "rewind_recap") {
      rewindEl.classList.remove("wide");
      renderRecap(document, rewindBody, message);
      openSheet(rewindEl);
      return;
    }
    // A find answer. TODO(rewind builder): a local `find_item` with the same fields (+ `place`) lands here too.
    if (message.type === "rewind_hit" || message.type === "find_item") {
      showFind(message);
      return;
    }
    if (message.type === "error") {
      wait = null;
      showProblem(message.message || "Something went wrong.");
    }
  }

  function fitsLine(item) {
    const compat = item && item.compat;
    const status = compat && compat.status;
    if (status === "unverified") {
      const unknown = compat.unknown && compat.unknown.length ? `: ${compat.unknown.join(", ")}` : "";
      return { text: `Unverified${unknown}`, kind: "unverified" };
    }
    if (status === "no") {
      const why = compat.reasons && compat.reasons.length ? compat.reasons.join("; ") : "";
      return { text: why ? `Does not fit: ${why}` : "Does not fit", kind: "no" };
    }
    if (item && item.explain) return { text: item.explain, kind: status || "" };
    if (status === "fits") {
      const why = compat.reasons && compat.reasons.length ? compat.reasons.join("; ") : "";
      return { text: why ? `Fits because: ${why}` : "Fits", kind: "fits" };
    }
    return { text: "Unverified", kind: "unverified" };
  }

  function money(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "";
    return `$${n.toFixed(2)}`;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  // Only web links open; anything else the server sends is dropped (as MarketSheet.safeURL).
  function safeLink(url) {
    try {
      const parsed = new URL(String(url || ""));
      return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : "";
    } catch {
      return "";
    }
  }

  const CHIP = { fits: "Fits", no: "Does not fit", unverified: "Unverified" };

  function showMarket(result) {
    marketBody.replaceChildren();
    const category = String((result && (result.product || result.category)) || "");
    marketTitle.textContent = category ? category.charAt(0).toUpperCase() + category.slice(1) : "Options";
    const items = result && Array.isArray(result.items) ? result.items.slice(0, 3) : [];
    if (!items.length) marketBody.append(el("p", "market-empty", "No matching offers."));
    for (const item of items) {
      const status = item.compat && CHIP[item.compat.status] ? item.compat.status : "unverified";
      const card = el("article", "offer glass");
      if (item.selected) {
        card.style.border = "2px solid var(--accent, #81d8ad)";
        card.append(el("p", "offer-why", "Recommended"));
      }
      const head = el("div", "offer-head");
      head.append(el("p", "offer-title", item.title || result.product || "Offer"), el("span", `status-chip ${status}`, CHIP[status]));
      card.append(head);
      if (item.retailer || item.store) card.append(el("p", "offer-retailer", item.retailer || item.store));
      let price = item.currency ? new Intl.NumberFormat(undefined, { style: "currency", currency: item.currency }).format(item.price) : money(item.price_usd);
      if (item.currency) {
        const delivery = item.pickup_today ? "Pickup today" : item.delivery_days == null ? "Delivery unconfirmed" : `Delivery in about ${item.delivery_days} ${item.delivery_days === 1 ? "day" : "days"}`;
        card.append(el("p", "offer-why", delivery));
        if (item.notes) card.append(el("p", "offer-why", item.notes));
      }
      if (price && item.shipping_usd != null && money(item.shipping_usd)) price += ` + ${money(item.shipping_usd)} shipping`;
      if (price) card.append(el("p", "offer-price", price));
      const line = fitsLine(item);
      if (line.text && !Object.values(CHIP).includes(line.text)) card.append(el("p", "offer-why", line.text));
      const href = safeLink(item.url);
      if (href) {
        const a = el("a", "", `View at ${item.retailer || item.store || "store"} ↗`);
        a.href = href;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        card.append(a);
      }
      marketBody.append(card);
    }
    if (result && result.note) marketBody.append(el("p", "market-note", String(result.note)));
    openSheet(marketEl);
  }

  function showRewind(on) {
    rewindOn = on;
    if (rewindToggle) rewindToggle.checked = on;
    if (rewindBadge) rewindBadge.classList.toggle("hidden", !on);
    if (rewindBtn) {
      rewindBtn.setAttribute("aria-pressed", on ? "true" : "false");
      rewindBtn.setAttribute("aria-label", on ? "Rewind on" : "Rewind off");
    }
    const chip = showsRewindChip(rewind);
    rewindAsk.classList.toggle("hidden", !chip);
    if (!chip) togglePrompts(false);
    renderCall();
  }

  // Either control: the same state, the same message, and the upload notice on the first enable.
  function setRewind(on) {
    if (on && privateMode) on = false; // Rewind uploads clips to TwelveLabs: never in private mode
    cloud.twelvelabs = Boolean(on);
    rewind = nextRewind(rewind, { type: "switch", on });
    if (rewind.notice) {
      window.clearTimeout(rewindNoticeTimer);
      rewindNoticeTimer = window.setTimeout(() => {
        rewind = nextRewind(rewind, { type: "notice_seen" });
        renderCall();
      }, 8000);
    }
    showRewind(on);
    sendRewind();
    renderPrivate();
  }

  function sendRewind() {
    const keep = Boolean(document.getElementById("rewind-keep") && document.getElementById("rewind-keep").checked);
    if (socket && socket.readyState === WebSocket.OPEN) send({ v: 2, type: "rewind", session_id: sessionId, on: rewindOn, keep });
  }

  function togglePrompts(open) {
    rewindPrompts.classList.toggle("hidden", !open);
    rewindChip.setAttribute("aria-expanded", open ? "true" : "false");
  }

  // Recap over HTTP: the Recap chip during a call, and the end-of-call sheet when Rewind was on.
  async function showRecap(id) {
    rewindEl.classList.remove("wide");
    renderRecap(document, rewindBody, { pending: true });
    openSheet(rewindEl);
    try {
      renderRecap(document, rewindBody, await fetchRecap(id));
    } catch {
      renderRecap(document, rewindBody, { steps: [], say: "The recap isn't available right now." });
    }
  }

  function showFind(hit) {
    const labels = (hit.objects || []).map((o) => o.label).join(" ");
    const place = hit.place || placeFor(`${hit.query || ""} ${labels}`, memoryItems);
    const callNow = callStartedAt ? (Date.now() - callStartedAt) / 1000 : hit.t_s;
    const line = hit.sure === false ? `Best guess · ${agoLine(hit.t_s, callNow, place)}` : agoLine(hit.t_s, callNow, place);
    const stream = cameraOn && video.srcObject ? video.srcObject : null;
    splitUpdate = renderSplit(document, rewindBody, { hit, line, stream });
    splitUpdate(liveObjects);
    rewindEl.classList.add("wide");
    openSheet(rewindEl);
  }

  // "Remembering 12 things today"; the number counts down when something is forgotten.
  function setMemoryCount(next) {
    const from = memoryShown;
    memoryCount = Math.max(0, Number(next) || 0);
    const started = performance.now();
    const step = () => {
      memoryShown = countAt(from, memoryCount, performance.now() - started);
      memoryCountEl.textContent = countLine(memoryShown);
      if (memoryShown !== memoryCount) requestAnimationFrame(step);
      else memoryEl.classList.toggle("hidden", memoryCount === 0 && memoryActions.classList.contains("hidden"));
    };
    memoryEl.classList.remove("hidden");
    step();
  }

  async function refreshMemory() {
    if (!pack) return;
    try {
      const s = await memoryApi.status(sessionId);
      memorySource = s.source;
      if (s.count !== memoryCount) setMemoryCount(s.count);
    } catch {
      // the badge keeps its last number
    }
  }

  async function forgetMemory(scope) {
    try {
      const r = await memoryApi.forget(sessionId, scope, rewindOn || rewind.clips > 0);
      setMemoryCount(r.count);
      if (scope === "today" || r.count === 0) rewind = nextRewind(rewind, { type: "reset" });
    } catch {
      showProblem("Nothing to forget.");
    }
  }

  function openSheet(sheet) {
    if (sheet !== rewindEl && rewindEl) rewindEl.classList.remove("wide");
    for (const other of [marketEl, settingsEl, rewindEl]) if (other && other !== sheet) other.classList.add("hidden");
    sheet.classList.remove("hidden");
    if (backdropEl) backdropEl.classList.remove("hidden");
    if (typeof sheet.focus === "function") sheet.focus({ preventScroll: true }); // arrows, Page Down and Space scroll it
  }

  function closeSheets() {
    marketEl.classList.add("hidden");
    marketBody.replaceChildren();
    if (settingsEl) settingsEl.classList.add("hidden");
    if (rewindEl) rewindEl.classList.add("hidden");
    if (backdropEl) backdropEl.classList.add("hidden");
    micPreview.stop();
    showMicLevel(0);
  }

  function showPermission(title, text) {
    permissionTitle.textContent = title;
    permissionText.textContent = text;
    permissionEl.classList.remove("hidden");
  }

  function currentOps() {
    const ops = [];
    for (const agent of ["helper", "shopper"]) {
      for (const item of drawState.ops[agent] || []) ops.push(item.op);
    }
    return ops;
  }

  function commitTracker(kind, seq, objects, changedOps) {
    const plan = nextTrackerPlan(book, {
      camera: kind === "stop" ? false : cameraOn,
      kind,
      seq,
      ops: currentOps(),
      changed: changedOps || (kind === "draw" ? currentOps() : null),
      objects,
    });
    book = plan.state;
    if (!cameraOn || kind === "stop") {
      for (const id of plan.drop) tracker.drop(id);
      tracks = new Map();
      pendingPlan = null;
      return;
    }
    const shot = captureGray();
    if (!shot) {
      pendingPlan = plan;
      return;
    }
    applyPlan(plan, shot.gray);
  }

  function applyPlan(plan, gray) {
    for (const id of plan.drop) {
      tracker.drop(id);
      tracks.delete(id);
    }
    for (const item of plan.reseed) {
      try {
        tracker.reseed(item.id, item.bbox, gray);
        tracks.set(item.id, { bbox: item.bbox.slice(), score: 1, lost: false });
      } catch {
        tracker.drop(item.id);
        tracks.delete(item.id);
      }
    }
    pendingPlan = null;
  }

  function reseedKnown(gray) {
    for (const id of book.ids) {
      const bbox = book.boxes[id];
      if (!bbox) continue;
      try {
        tracker.reseed(id, bbox, gray);
        tracks.set(id, { bbox: bbox.slice(), score: 1, lost: false });
      } catch {
        tracker.drop(id);
        tracks.delete(id);
      }
    }
  }

  function captureGray() {
    const vw = video.videoWidth;
    const vh = video.videoHeight;
    if (!(vw > 0) || !(vh > 0)) return null;
    const w = TRACK_W;
    const h = Math.max(1, Math.round((vh / vw) * w));
    const resized = grayCanvas.width !== w || grayCanvas.height !== h;
    if (resized) {
      grayCanvas.width = w;
      grayCanvas.height = h;
    }
    const ctx = grayCanvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return null;
    try {
      ctx.drawImage(video, 0, 0, w, h);
      const image = ctx.getImageData(0, 0, w, h);
      return { gray: toGray(image.data, image.width, image.height, 1), resized };
    } catch {
      return null;
    }
  }

  function stepTracker(now) {
    if (!cameraOn || !shouldTrack(lastTrackAt, now, trackBusy)) return;
    if (!video.videoWidth || !video.videoHeight) return;
    trackBusy = true;
    try {
      const shot = captureGray();
      if (!shot) return;
      if (pendingPlan) applyPlan(pendingPlan, shot.gray);
      else if (shot.resized) reseedKnown(shot.gray);
      const started = Date.now();
      tracks = tracker.update(shot.gray) || new Map();
      hud = noteTrackerMs(hud, Date.now() - started);
    } catch {
      /* keep the last good tracks */
    } finally {
      lastTrackAt = now;
      trackBusy = false;
    }
  }

  function dropUnanchored() {
    if (!cameraOn || !book.ids.length) return;
    const live = new Set();
    for (const op of currentOps()) {
      const id = op && op.anchor && op.anchor.object_id;
      if (typeof id === "string" && id) live.add(id);
    }
    if (book.ids.every((id) => live.has(id))) return;
    const plan = nextTrackerPlan(book, {
      camera: true,
      kind: "draw",
      seq: 0,
      ops: currentOps(),
      changed: [],
    });
    book = plan.state;
    for (const id of plan.drop) {
      tracker.drop(id);
      tracks.delete(id);
    }
  }

  function anchoredState(state, live, now) {
    if (!live || live.size === 0) return state;
    const vis = visibleOps(state, now);
    if (!vis.length) return state;
    const moved = applyAnchors(vis.map((item) => item.op), live);
    const ops = { helper: [], shopper: [] };
    for (let i = 0; i < vis.length; i += 1) {
      const agent = vis[i].agent === "shopper" ? "shopper" : "helper";
      ops[agent].push({ op: moved[i], bornMs: vis[i].bornMs, agent });
    }
    return { seq: state.seq, ops };
  }

  function markSound() {
    turnEvent({ type: "first_sound" });
    hud = noteFirstSound(hud, Date.now());
  }

  function enqueueTalk(lines) {
    for (const line of lines) talkQueue.push(line);
    pumpTalk();
  }

  function pumpTalk() {
    if (talkBusy || tts === "server" || !voice || !talkQueue.length) return;
    const line = talkQueue.shift();
    talkBusy = true;
    ui.dispatch({ type: "audio_started", utteranceId: line.utteranceId, now: Date.now() });
    voice.speak(line.utteranceId, line.text);
    renderCall();
  }

  function clearFallback(id) {
    const timer = fallbackTimers.get(id);
    if (timer) window.clearTimeout(timer);
    fallbackTimers.delete(id);
  }

  function clearAllFallbacks() {
    for (const timer of fallbackTimers.values()) window.clearTimeout(timer);
    fallbackTimers.clear();
  }

  const utteranceTurn = new Map();
  const audioTurns = new Set();

  function armFallback(id) {
    if (tts !== "server") return;
    clearFallback(id);
    const wait = ui.getState().waits[id];
    if (!wait || wait.audioSeenMs != null || wait.fellBack) return;
    const delay = Math.max(0, AUDIO_FALLBACK_MS - (Date.now() - wait.sayTimeMs));
    const timer = window.setTimeout(() => runFallback(id), delay);
    fallbackTimers.set(id, timer);
  }

  function runFallback(id) {
    fallbackTimers.delete(id);
    const wait = ui.getState().waits[id];
    if (!wait || wait.fellBack) return;
    const now = Date.now();
    if (!fallbackDecision(wait.sayTimeMs, wait.audioSeenMs, now).fallback) {
      armFallback(id);
      return;
    }
    ui.dispatch({ type: "audio_timeout", utteranceId: id, now });
    const next = ui.getState().waits[id];
    if (voice && next && next.text) voice.speak(id, next.text);
    renderCall();
  }

  function failAudio(id) {
    const wait = ui.getState().waits[id];
    if (!wait || wait.fellBack) return;
    clearFallback(id);
    ui.dispatch({ type: "audio_error", utteranceId: id, now: Date.now() });
    const next = ui.getState().waits[id];
    if (voice && next && next.text) voice.speak(id, next.text);
    renderCall();
  }

  function placeDevHud(view, poses) {
    if (!devEl) return;
    const box = devHudPosition(view, poses || {});
    devEl.style.left = `${box.x}px`;
    devEl.style.top = `${box.y}px`;
    devEl.style.width = `${box.w}px`;
    const text = formatHud(hud);
    if (devEl.textContent !== text) devEl.textContent = text;
  }

  function sourceSize() {
    if (cameraOn && video.videoWidth && video.videoHeight) {
      return { w: video.videoWidth, h: video.videoHeight };
    }
    if (!still.classList.contains("hidden") && still.naturalWidth && still.naturalHeight) {
      return { w: still.naturalWidth, h: still.naturalHeight };
    }
    return { w: 0, h: 0 };
  }

  function currentView() {
    const cssW = stage.clientWidth || window.innerWidth;
    const cssH = stage.clientHeight || window.innerHeight;
    const source = sourceSize();
    const srcW = source.w > 0 ? source.w : cssW;
    const srcH = source.h > 0 ? source.h : cssH;
    const transform = viewTransform(srcW, srcH, cssW, cssH);
    return { ...transform, w: cssW, h: cssH };
  }

  function fitCanvas(canvas, cssW, cssH) {
    const bitmap = sharpCanvasSize(cssW, cssH, window.devicePixelRatio || 1);
    if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(bitmap.ratio, 0, 0, bitmap.ratio, 0, 0);
    return ctx;
  }

  function paint(now) {
    const view = currentView();
    if (!(view.w > 0) || !(view.h > 0)) return;
    const reduced = reducedMotion();
    drawState = pruneDraw(drawState, now);
    dropUnanchored();
    hud = notePaint(hud, now);
    const drawCtx = fitCanvas(drawCanvas, view.w, view.h);
    const frameState = cameraOn ? anchoredState(drawState, tracks, now) : drawState;
    paintDraw(drawCtx, frameState, view, now, reduced);
    placeDevHud(view, null);
  }

  function renderElapsed(now) {
    if (!callStartedAt) return;
    const seconds = Math.max(0, Math.floor((now - callStartedAt) / 1000));
    const text = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    if (elapsedEl.textContent !== text) elapsedEl.textContent = text;
  }

  function loop() {
    const now = Date.now();
    stepTracker(now);
    if (now - lastUiTick > 200) {
      lastUiTick = now;
      const before = ui.getState().caption;
      ui.dispatch({ type: "tick", now });
      if (ui.getState().caption !== before) renderListen(ui.getState());
      renderElapsed(now);
    }
    paint(now);
    renderWait(now);
    raf = window.requestAnimationFrame(loop);
  }

  function sendLiveFrame() {
    if (!cameraOn || document.hidden) return;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    if (socket.bufferedAmount > 0) return;
    if (!video.videoWidth || !video.videoHeight) return;
    const shot = writeFrame(grab, video, video.videoWidth, video.videoHeight);
    if (!shot || !shot.jpeg_b64) return;
    const sentAt = Date.now();
    const frameSeq = nextSeq();
    const ok = send({
      v: 2,
      type: "frame",
      session_id: sessionId,
      seq: frameSeq,
      ts_ms: sentAt,
      w: shot.w,
      h: shot.h,
      jpeg_b64: shot.jpeg_b64,
    });
    if (ok) hud = noteFrameSent(hud, frameSeq, sentAt);
  }

  function startFrames() {
    if (frameTimer) return;
    frameTimer = window.setInterval(sendLiveFrame, FRAME_MS);
  }

  function stopCamera() {
    valveCard.reset();
    const stream = video.srcObject;
    if (stream && typeof stream.getTracks === "function") {
      for (const track of stream.getTracks()) track.stop();
    }
    video.srcObject = null;
    cameraOn = false;
    window.clearInterval(frameTimer);
    frameTimer = 0;
    renderCamera();
    send({ v: 2, type: "camera", session_id: sessionId, on: false });
    commitTracker("stop");
  }

  function hideStill() {
    still.classList.add("hidden");
    still.removeAttribute("src");
    if (photoUrl) {
      URL.revokeObjectURL(photoUrl);
      photoUrl = "";
    }
  }

  const flipCamera = wireFlipCamera({
    video, button: document.getElementById("flip-camera"),
    active: () => cameraOn && !headset, busy: () => starting,
    onError: showProblem, onSwitch: () => commitTracker("draw"),
  });

  async function startCamera() {
    if (headset || starting || cameraOn) return;
    if (!window.isSecureContext) {
      ui.dispatch({ type: "https" });
      renderCall();
      showPermission("Camera needs HTTPS", ERROR_HTTPS);
      return;
    }
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
      showProblem("No camera in this browser. Use Upload photo.");
      return;
    }
    starting = true;
    let retryDefault = false;
    try {
      const stream = await requestLocalMedia(navigator.mediaDevices, headset, {
        video: flipCamera.constraints(videoConstraints(cameraChoice)),
        audio: false,
      });
      if (!pack) {
        // The call ended while the camera prompt was open: do not leave a live camera on the ring screen.
        for (const track of stream.getTracks()) track.stop();
        return;
      }
      hideStill();
      video.srcObject = stream;
      video.playsInline = true;
      video.muted = true;
      // On before play(): a play() that never settles must not leave a live camera the button can't stop.
      cameraOn = true;
      void flipCamera.refresh();
      await video.play().catch(() => {});
      if (video.srcObject !== stream) return;
      renderCamera();
      send({ v: 2, type: "camera", session_id: sessionId, on: true });
      commitTracker("draw");
      sendLiveFrame();
      startFrames();
    } catch (err) {
      stopCamera();
      if (cameraChoice && isMissingDevice(err)) {
        // The chosen camera is gone: forget it and open the default one.
        retryDefault = true;
        dropCameraChoice();
        return;
      }
      const name = err && err.name;
      if (name === "NotAllowedError" || name === "PermissionDeniedError" || name === "SecurityError") {
        showPermission("Camera access is off",
          "A-eye looks through the camera to point at parts while you work. Allow the camera for this site in your browser settings, or upload a photo from Settings.");
      } else {
        showProblem("The camera is not available. You can use your phone or a photo from Settings.");
      }
    } finally {
      starting = false;
      if (retryDefault) void startCamera();
    }
  }

  function loadImage(url) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error("image"));
      image.src = url;
    });
  }

  async function onUpload(event) {
    const file = event.target.files && event.target.files[0];
    event.target.value = "";
    if (!file) return;
    const url = URL.createObjectURL(file);
    try {
      const image = await loadImage(url);
      const shot = writeFrame(grab, image, image.naturalWidth, image.naturalHeight);
      if (!shot || !shot.jpeg_b64) {
        URL.revokeObjectURL(url);
        showProblem("Could not read that photo.");
        return;
      }
      if (cameraOn) {
        URL.revokeObjectURL(url);
      } else {
        if (photoUrl) URL.revokeObjectURL(photoUrl);
        photoUrl = url;
        still.src = url;
        still.classList.remove("hidden");
      }
      const sentAt = Date.now();
      const frameSeq = nextSeq();
      const ok = send({
        v: 2,
        type: "frame",
        session_id: sessionId,
        seq: frameSeq,
        ts_ms: sentAt,
        w: shot.w,
        h: shot.h,
        jpeg_b64: shot.jpeg_b64,
      });
      if (ok) hud = noteFrameSent(hud, frameSeq, sentAt);
      if (!ok) showProblem("Not connected.");
    } catch {
      if (photoUrl !== url) URL.revokeObjectURL(url);
      showProblem("Could not read that photo.");
    }
  }

  function renderCamera() {
    startBtn.setAttribute("aria-pressed", cameraOn ? "true" : "false");
    startBtn.setAttribute("aria-label", cameraOn ? "Camera off" : "Camera on");
    cameraIcon.setAttribute("href", cameraOn ? "#i-camera" : "#i-camera-off");
  }

  // CallScreen.micButton: mic when listening, struck-through mic in the warn colour when muted.
  function renderMic() {
    const ready = Boolean(voice && voice.supported && voice.supported.stt);
    micBtn.disabled = !ready || micRoute.phone;
    const on = ui.getState().micOn;
    micBtn.setAttribute("aria-pressed", on ? "true" : "false");
    micBtn.setAttribute("data-muted", ready && !on ? "true" : "false");
    micIcon.setAttribute("href", on ? "#i-mic" : "#i-mic-off");
    micBtn.setAttribute("aria-label", on ? "Stop microphone" : "Start microphone");
    micBtn.title = on ? "Mic is on. Tap to turn it off." : "Mic is off. Tap to turn it on.";
    renderMicLine();
    renderListen(ui.getState());
  }

  function applyAudioPrefs() {
    const st = ui.getState();
    if (!voice) return;
    if (typeof voice.setMuted === "function") voice.setMuted(st.muted);
    if (typeof voice.setVolume === "function") voice.setVolume(st.volume);
    applySpeaker();
  }

  function securePage() {
    return Boolean(window.isSecureContext);
  }

  async function startMic() {
    if (micRoute.phone) return;
    micPreview.stop();
    await voice.startListening();
    if (micRoute.phone) { voice.stopListening(); return; }
    if (voice.isListening()) {
      ui.dispatch({ type: "mic_start" });
      turnEvent({ type: "mic_start" });
      renderCall();
    }
  }

  let stopTrigger = null;
  function startTrigger() {
    stopTrigger?.();
    stopTrigger = triggerClient({
      request: async (path, body) => {
        const token = new URLSearchParams(location.search).get('token');
        const owner = await callOwner();
        const response = await fetch(path, { method: 'POST', headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}`, 'X-Aeye-Owner': owner } : {}),
        }, body: JSON.stringify(body) });
        if (!response.ok) throw new Error('Wake unavailable');
        return response.json();
      },
      session: () => sessionId,
      available: () => Boolean(pack && !ended && socket?.readyState === WebSocket.OPEN),
      listening: () => Boolean(micRoute.phone || voice?.isListening()),
      wake: async (line) => {
        if (!voice) voice = await rebuildVoice();
        if (ui.getState().micOn && !micRoute.phone && !voice.isListening()) await startMic();
        onServer({ v: 2, type: 'say', agent: 'helper', text: line,
          utterance_id: randomId(), final: true });
      },
    });
  }

  async function onMic() {
    voice = voice || (await rebuildVoice());
    if (!voice || !voice.supported || !voice.supported.stt) return;
    if (!securePage()) {
      ui.dispatch({ type: "https" });
      renderCall();
      return;
    }
    void voice.unlockAudio();
    if (ui.getState().micOn) {
      voice.stopListening();
      ui.dispatch({ type: "mic_stop" });
      turnEvent({ type: "mic_stop" });
      renderCall();
    }
    else void startMic();
    pumpTalk();
  }

  function onStartTap() {
    ui.dispatch({ type: "tap_start" });
    renderCall();
    if (!voice) return;
    voice.primeSpeech();
    void voice.unlockAudio();
    applyAudioPrefs();
    if (!securePage()) {
      ui.dispatch({ type: "https" });
      renderCall();
      return;
    }
    if (voice.supported && voice.supported.stt) void startMic();
  }

  async function onTestVoice() {
    if (!voice) return;
    void voice.unlockAudio();
    applyAudioPrefs();
    if (tts === "server") {
      try {
        const reply = await apiFetch(`/api/voice_sample?voice=${encodeURIComponent(ttsVoice)}&session_id=${encodeURIComponent(sessionId)}`);
        if (reply.ok) {
          const sample = await reply.json();
          voice.playPcm("test-voice", sample.pcm16_b64, true);
          return;
        }
      } catch {
        // fall through to the browser voice
      }
    }
    voice.speak("test-voice", TEST_LINE);
  }

  async function loadVoices() {
    if (!voicePick) return;
    let listed = [];
    try {
      const reply = await apiFetch("/api/voices");
      if (reply.ok) listed = (await reply.json()).voices || [];
    } catch {
      listed = [];
    }
    voicePick.textContent = "";
    for (const item of listed) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.label + (item.default ? " (default)" : "");
      voicePick.append(option);
    }
    const browserOption = document.createElement("option");
    browserOption.value = "browser";
    browserOption.textContent = "Browser voice (robotic)";
    voicePick.append(browserOption);
    const fallback = (listed.find((item) => item.default) || listed[0] || { id: "browser" }).id;
    if (wantCartesia && !String(ttsVoice).startsWith("cartesia:")) {
      const cartesia = listed.find((item) => item.engine === "cartesia" && item.default)
        || listed.find((item) => item.engine === "cartesia");
      if (cartesia) {
        ttsVoice = cartesia.id;
        sendHello();
      }
    }
    voicePick.value = tts === "browser" ? "browser" : (ttsVoice || fallback);
  }

  function onVoicePick() {
    const picked = voicePick.value;
    if (beforeLiveAudio && picked === "browser") return;
    tts = picked === "browser" ? "browser" : "server";
    ttsVoice = picked === "browser" ? "" : picked;
    try {
      safeStorage()?.setItem("aeye.voice", picked);
    } catch {
      // per-viewer convenience only
    }
    sendHello();
    void onTestVoice();
  }

  async function choosePack(next) {
    if (!PACK_LABEL[next] || pack) return;
    if (next === 'gemini-live' && !cloud.google && !privateMode) {
      const notice = document.getElementById('live-start-notice');
      notice.textContent = 'To start Gemini Live, turn on Gemini Live (Google) below. It sends microphone audio and downscaled camera video to Google. You can also choose General with Google off.';
      notice.classList.remove('hidden');
      openSheet(settingsEl);
      settingsEl.scrollTop = 0;
      return;
    }
    document.getElementById('live-start-notice').classList.add('hidden');
    pack = next;
    renderLeaves();
    homeStartPending = next === 'home' || next === 'engineering';
    document.getElementById('guide-presenter')?.classList.add('hidden');
    ended = false;
    sessionId = randomId();
    resetCallOwner();
    try { await callOwner(); }
    catch { pack = null; showProblem('Could not create a private call. Please try again.'); return; }
    if (liveSettings(next, cloud.google, privateMode).allowed) {
      beforeLiveAudio = { stt, tts };
      stt = "server"; tts = "server";
      renderLeaves();
    }
    ui = createCallUi({ storage: safeStorage() });
    turn = nextTurnState(null, {}, Date.now());
    speech = emptySpeech();
    powerConfirmed = false;
    agents = { helper: "idle", shopper: "idle" };
    callStartedAt = Date.now();
    elapsedEl.textContent = "0:00";
    memoryShown = 0;
    setMemoryCount(0);
    window.clearInterval(memoryTimer);
    memoryTimer = window.setInterval(() => void refreshMemory(), 10000);
    void memoryApi.settings().then((s) => { memoryItems = s.items; }, () => {});
    packLabel.textContent = PACK_LABEL[next];
    if (document.body && document.body.dataset) document.body.dataset.mode = next; // calm Memory look in CSS
    link = "connecting";
    renderPresence();
    renderCamera();
    ui.dispatch({ type: "pack", pack: next });
    picker.classList.add("hidden");
    callEl.classList.remove("hidden");
    armControls();
    voice = await rebuildVoice();
    if (voice && !voice.supported.stt && stt !== "server") {
      stt = "server";
      ui.dispatch({ type: "stt_unsupported" });
      voice = await rebuildVoice();
    }
    applyAudioPrefs();
    renderCall();
    connect();
    paint(Date.now());
  }

  async function enableHold() {
    holdMode = true;
    renderHold();
    if (stt !== "server") await switchToServerStt();
    if (voice && typeof voice.setHoldMode === "function") voice.setHoldMode(true);
    if (voice && !voice.isListening() && voice.supported && voice.supported.stt) await startMic();
    renderHold();
  }

  function renderHold() {
    if (!holdBtn) return;
    holdBtn.classList.toggle("hold-default", holdMode);
    holdBtn.setAttribute("aria-pressed", holdPressed ? "true" : "false");
    holdBtn.setAttribute("aria-label", holdPressed ? "Listening, release to send" : "Hold to talk");
  }

  // CallController.end: back to the mode ring with the same mode selected.
  function endCall() {
    valveCard.reset();
    stopTrigger?.();
    if (!pack) return;
    window.clearInterval(memoryTimer);
    memoryEl.classList.add("hidden");
    ended = true;
    if (cameraOn || video.srcObject) stopCamera();
    holdPressed = false;
    if (voice) {
      voice.stopListening();
      if (typeof voice.cancel === "function") voice.cancel();
    }
    talkQueue.length = 0;
    talkBusy = false;
    clearAllFallbacks();
    window.clearTimeout(retryTimer);
    const ws = socket;
    socket = null;
    if (ws) ws.close();
    if (beforeLiveAudio) {
      stt = privateMode ? "server" : beforeLiveAudio.stt;
      tts = privateMode ? "server" : beforeLiveAudio.tts;
      beforeLiveAudio = null;
    }
    pack = "";
    if (document.body && document.body.dataset) delete document.body.dataset.mode;
    callStartedAt = 0;
    drawState = applyDraw(null, null, 0);
    agents = { helper: "idle", shopper: "idle" };
    wait = null;
    micRoute = { phone: false, resume: false };
    if (headset) {
      headsetPanel.classList.remove("connected");
      document.getElementById("headset-sound").textContent = "Enable sound";
      headsetStatus.textContent = "Waiting for your phone camera";
      document.getElementById("headset-help").classList.remove("hidden");
    }
    document.getElementById("phone-chip")?.classList.add("hidden");
    renderWait(Date.now());
    hideStill();
    closeSheets();
    permissionEl.classList.add("hidden");
    overlayEl.classList.add("hidden");
    stopControls();
    callEl.classList.add("hidden");
    picker.classList.remove("hidden");
    renderLink();
    paint(Date.now());
    if (rewindOn) void showRecap(sessionId);   // end-of-call recap; the server uploads the last clips first
  }

  function ringButtons() {
    return ringEl && typeof ringEl.querySelectorAll === "function" ? [...ringEl.querySelectorAll("button[data-mode]")] : [];
  }

  // ModeRing.swift: centred item 78 px with a white ring, neighbours 52 / 42 / 34 and fading.
  function renderRing() {
    ringButtons().forEach((button, i) => {
      const dist = Math.min(Math.abs(i - ringIndex), 3);
      button.setAttribute("data-dist", String(dist));
      button.setAttribute("aria-current", dist === 0 ? "true" : "false");
    });
    const mode = MODES[ringIndex];
    ringTitle.textContent = mode.title;
    ringHint.textContent = mode.pack ? "tap to start" : "coming soon";
    ringHint.disabled = !mode.pack;
  }

  function scrollRingTo(i, smooth) {
    if (!ringEl || typeof ringEl.scrollTo !== "function") return;
    ringEl.scrollTo({ left: i * RING_SLOT, behavior: smooth && !reducedMotion() ? "smooth" : "auto" });
  }

  function onRingScroll() {
    const i = Math.max(0, Math.min(MODES.length - 1, Math.round(ringEl.scrollLeft / RING_SLOT)));
    if (i === ringIndex) return;
    ringIndex = i;
    renderRing();
  }

  function shake(button) {
    if (reducedMotion()) return;
    button.classList.remove("shake");
    void button.offsetWidth;
    button.classList.add("shake");
  }

  async function onHoldDown() {
    if (holdPressed) return;
    holdPressed = true;
    renderHold();
    if (voice) void voice.unlockAudio();
    if (!holdMode || stt !== "server" || !voice || !voice.isListening()) await enableHold();
    if (holdPressed && voice && typeof voice.holdStart === "function") {
      voice.holdStart();
      turnEvent({ type: "interim", text: "Holding to talk" });
    }
  }

  function onHoldUp() {
    if (!holdPressed) return;
    holdPressed = false;
    if (voice && typeof voice.holdEnd === "function") voice.holdEnd();
    renderHold();
  }

  function renderPrivate() {
    if (privateToggle) privateToggle.checked = privateMode;
    if (privateBadge) privateBadge.classList.toggle("hidden", !privateMode);
    for (const [name, box] of Object.entries(cloudBoxes)) {
      if (box) box.checked = !privateMode && Boolean(cloud[name]);
    }
    renderLeaves();
  }

  // Plain words for a caregiver: what is leaving the home with the switches as they are right now.
  function renderLeaves() {
    if (!leavesList) return;
    const on = (name) => !privateMode && Boolean(cloud[name]);
    const lines = [];
    if (on("google")) lines.push(liveSettings(pack, true, privateMode).notice);
    if (on("claude")) lines.push("Your question and a written note about the scene go to Claude. Sometimes one small close-up photo.");
    if (on("cartesia")) lines.push("A-eye's reply sentence goes to Cartesia, which turns it into speech.");
    if (on("twelvelabs") && rewindOn) lines.push("Short silent video clips go to TwelveLabs. They are deleted when the call ends.");
    if (stt !== "server") lines.push("Your speech goes to your browser's speech service (in Chrome, that is Google).");
    if (!lines.length) lines.push("Nothing leaves your home right now.");
    leavesList.textContent = "";
    for (const text of lines) {
      const item = document.createElement("li");
      item.textContent = text;
      leavesList.append(item);
    }
    const home = document.createElement("li");
    home.className = "home";
    home.textContent = on("google") ? "The memory log stays home. Only requested tool result text goes to Google." : "Always stays home: the camera picture, the memory log, and (with server speech) your voice.";
    leavesList.append(home);
  }

  function setCloud(name, on) {
    if (name === "twelvelabs") {
      if (on && privateMode) {
        privateMode = false;
        sendHello();
      }
      setRewind(on); // the same consent as the Rewind toggle; sends its own message
      return;
    }
    cloud[name] = Boolean(on);
    if (on && privateMode) privateMode = false; // switching a cloud feature on leaves Private mode
    try {
      safeStorage()?.setItem("aeye.cloud", JSON.stringify({ claude: cloud.claude, cartesia: cloud.cartesia, google: cloud.google }));
      safeStorage()?.setItem("aeye.private", privateMode ? "1" : "0");
    } catch {
      // per-viewer convenience only
    }
    renderPrivate();
    if (name === "google" && liveSettings(pack, true, false).selected) {
      endCall(); // the next call rebuilds both microphone and playback transport
    } else sendHello();
  }

  async function setPrivate(on) {
    if (pack) endCall(); // rebuild transport under the new privacy policy
    privateMode = Boolean(on);
    try {
      safeStorage()?.setItem("aeye.private", privateMode ? "1" : "0");
    } catch {
      // per-viewer convenience only
    }
    renderPrivate();
    if (privateMode) {
      if (rewindOn) setRewind(false);
      tts = "server";
      if (stt !== "server") {
        await switchToServerStt(); // sends the new hello
        return;
      }
    }
    sendHello();
  }

  async function switchToServerStt() {
    if (stt === "server") return;
    const micWasOn = ui.getState().micOn;
    stt = "server";
    ui.dispatch({ type: "stt_unsupported" });
    voice = await rebuildVoice();
    applyAudioPrefs();
    sendHello();
    renderCall();
    renderLeaves();
    // The mic was on when browser recognition failed: keep listening through server STT.
    if (voice && micWasOn && voice.supported && voice.supported.stt) void startMic();
  }

  function renderCall() {
    const st = ui.getState();
    errorEl.textContent = st.error || "";
    errorEl.classList.toggle("hidden", !st.error);
    powerGate.classList.toggle("hidden", pack !== "pc_build" || powerConfirmed);
    renderLink();
    const notes = [sttForMic || !devOn ? "" : st.sttNotice /* which recognizer runs: dev only */, st.voiceNotice, deviceNotice, rewind.notice ? REWIND_NOTICE : ""].filter(Boolean);
    noticeEl.textContent = notes.join(" ");
    noticeEl.classList.toggle("hidden", notes.length === 0);
    renderReplies(st);
    renderListen(st);
    renderTranscript(st);
    muteBtn.setAttribute("aria-pressed", st.muted ? "true" : "false");
    muteBtn.textContent = st.muted ? "Unmute" : "Mute";
    if (document.activeElement !== volumeEl) volumeEl.value = String(st.volume);
    overlayEl.classList.toggle("hidden", headset || !st.showStart);
    if (sttLabel) sttLabel.textContent = stt === "server" ? "Whisper" : "Browser";
    renderMic();
  }

  // CaptionCard.swift: eyebrow A-EYE or SHOPPER, then the reply. The web streams a turn sentence by sentence,
  // so the card holds the whole current turn (clamped to 8 lines) instead of only the last sentence.
  function renderReplies(st) {
    repliesEl.replaceChildren();
    const lines = replyLines(st);
    // Words that name a boxed object take the box colour, so the reader links the sentence to the outline.
    const targets = targetLabels(drawState, Date.now());
    lines.forEach((line, i) => {
      if (i) repliesEl.append(document.createTextNode(" "));
      const cls = line.active ? "line active" : "line";
      const runs = splitByTargets(line.text, targets);
      if (!runs.some((run) => run.target)) {
        repliesEl.append(el("span", cls, line.text));
        return;
      }
      const span = el("span", cls, "");
      for (const run of runs) span.append(run.target ? el("em", "target", run.text) : document.createTextNode(run.text));
      repliesEl.append(span);
    });
    const last = lines[lines.length - 1];
    const shopper = Boolean(last && last.speaker === "Shopper");
    captionEl.setAttribute("data-agent", shopper ? "shopper" : "helper");
    captionEyebrow.textContent = shopper ? "SHOPPER" : "A-EYE";
    captionSpeaker.textContent = shopper ? "Shopper" : "Helper";
    captionEl.classList.toggle("hidden", !lines.length && !st.error);
  }

  // ListeningStrip.swift: waveform and heard line while voice input is available; "MIC OFF" while the mic is off.
  function renderListen(st) {
    const ready = Boolean(voice && voice.supported && voice.supported.stt) && !micRoute.phone;
    listenEl.classList.toggle("hidden", !ready);
    if (!ready) return;
    if (!st.micOn && waveLevels.length) {
      waveLevels = [];
      levelPool = null;
    }
    // Only what the recogniser hears now, as SpeechIn.interim; sent and typed words live in the transcript.
    const cap = st.caption || {};
    const heard = cap.kind === "interim" && cap.visible ? String(cap.text || "") : "";
    const line = listeningLine({ muted: !st.micOn, heard, levels: waveLevels });
    interimEl.replaceChildren(document.createTextNode(line.text));
    if (line.tone === "heard") {
      const caret = document.createElement("span");
      caret.className = "caret";
      caret.setAttribute("aria-hidden", "true");
      interimEl.append(caret);
    }
    interimEl.classList.toggle("mono", line.mono);
    interimEl.setAttribute("data-tone", line.tone);
    if (listenEl.getAttribute("aria-label") !== line.label) listenEl.setAttribute("aria-label", line.label);
    drawWave(line.active && st.micOn);
  }

  // WaitCard.swift and ScanSweep.swift, every frame while a wait is visible.
  function renderWait(now) {
    const shown = pack ? visibleWait(wait, now) : null;
    waitEl.classList.toggle("hidden", !shown);
    scanEl.classList.add("hidden"); // The model always sees the whole frame; no sweep animation.
    if (!shown) {
      waitShown = "";
      return;
    }
    const elapsed = now - shown.startedAtMs;
    const progress = waitProgress(elapsed, shown.typicalMs);
    const pct = waitPercent(progress);
    const copy = waitCopy(shown, elapsed);
    const key = `${shown.agent}|${pct}|${copy.eyebrow}|${copy.line}`;
    if (key !== waitShown) {
      waitShown = key;
      waitEl.setAttribute("data-agent", shown.agent);
      waitArc.setAttribute("stroke-dasharray", `${(progress * 100).toFixed(2)} 100`);
      waitPct.textContent = `${pct}%`;
      waitEyebrow.textContent = copy.eyebrow;
      waitLine.textContent = copy.line;
      waitEl.setAttribute("aria-label", `${capitalized(copy.eyebrow)}. ${copy.line}`);
      waitEl.setAttribute("aria-valuenow", String(pct));
      waitEl.setAttribute("aria-valuetext", `${pct} percent`);
    } else {
      waitArc.setAttribute("stroke-dasharray", `${(progress * 100).toFixed(2)} 100`);
    }
    if (shown.agent === "helper") renderScan(now);
  }

  // ScanSweep: region inset 28 px from the sides, 16% to 58% of the height; band 90 px, 1.8 s per sweep.
  function renderScan(now) {
    const w = scanEl.clientWidth;
    const h = scanEl.clientHeight;
    const x = 28;
    const top = h * 0.08;
    const rw = Math.max(0, w - 56);
    const rh = h * (0.88 - 0.08);
    const len = Math.min(26, rw / 3, rh / 3);
    const d = `M${x},${top + len}L${x},${top}L${x + len},${top}` +
      `M${x + rw},${top + len}L${x + rw},${top}L${x + rw - len},${top}` +
      `M${x},${top + rh - len}L${x},${top + rh}L${x + len},${top + rh}` +
      `M${x + rw},${top + rh - len}L${x + rw},${top + rh}L${x + rw - len},${top + rh}`;
    if (scanPath.getAttribute("d") !== d) scanPath.setAttribute("d", d);
    if (reducedMotion()) return;
    const phase = ((now / 1000) % 1.8) / 1.8;
    const edge = top + rh * phase;
    const bandTop = Math.max(top, edge - 90);
    scanBand.style.left = `${x}px`;
    scanBand.style.width = `${rw}px`;
    scanBand.style.top = `${bandTop}px`;
    scanBand.style.height = `${Math.max(0, edge - bandTop)}px`;
  }

  function renderTranscript(st) {
    transcriptList.replaceChildren();
    for (const line of st.transcript) {
      const item = document.createElement("li");
      const who = document.createElement("span");
      who.className = "who";
      who.style.color = line.color || "#f4f7fb";
      who.textContent = line.speaker;
      item.append(who, document.createTextNode(line.text));
      if (line.timestamps) {
        const times = document.createElement("small");
        times.textContent = " · " + ["sent", "received", "first_text", "first_sound"].map(key => {
          const at = line.timestamps[key];
          return `${key.replaceAll("_", " ")}: ${at == null ? "—" : new Date(at).toISOString()}`;
        }).join(" · ");
        item.append(times);
      }
      transcriptList.append(item);
    }
    if (transcriptBtn.getAttribute("aria-expanded") === "true") {
      transcriptList.scrollTop = transcriptList.scrollHeight;
    }
  }

  // WaveformBars: 28 bars 2 px wide, at least 2 px tall, helper blue while the user talks, else white 0.4.
  function drawWave(active) {
    if (!waveEl) return;
    const ctx = fitCanvas(waveEl, 96, 28);
    ctx.clearRect(0, 0, 96, 28);
    ctx.fillStyle = active ? "#8EB6FF" : "rgba(255, 255, 255, 0.4)";
    for (const bar of waveBars(waveLevels, LEVEL_HISTORY_COUNT, 96, 28)) {
      ctx.beginPath();
      if (typeof ctx.roundRect === "function") ctx.roundRect(bar.x, bar.y, bar.w, bar.h, bar.w / 2);
      else ctx.rect(bar.x, bar.y, bar.w, bar.h);
      ctx.fill();
    }
  }

  // Tap the centred mode to start; a coming-soon mode shakes. With a mouse there is no swipe, so tapping a side
  // mode that works starts it directly (iOS only scrolls it to the centre).
  ringHint.addEventListener('click', () => void choosePack(MODES[ringIndex].pack));
  picker.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-mode]");
    if (!button) return;
    const i = MODES.findIndex((m) => m.id === button.getAttribute("data-mode"));
    const next = button.getAttribute("data-pack");
    if (i >= 0 && i !== ringIndex) {
      ringIndex = i;
      renderRing();
      scrollRingTo(i, true);
    }
    if (!next) {
      shake(button);
      return;
    }
    choosePack(next);
  });
  if (ringEl) {
    ringEl.addEventListener("scroll", onRingScroll, { passive: true });
    ringEl.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const i = Math.max(0, Math.min(MODES.length - 1, ringIndex + (event.key === "ArrowRight" ? 1 : -1)));
      ringIndex = i;
      renderRing();
      scrollRingTo(i, true);
      const target = ringButtons()[i];
      if (target) target.focus();
    });
    ringEl.addEventListener("animationend", (event) => {
      const button = event.target.closest && event.target.closest("button");
      if (button) button.classList.remove("shake");
    });
  }
  document.getElementById("end-call").addEventListener("click", endCall);
  // One tap to pair a phone camera: open Settings at the top and ask for a pairing code.
  document.getElementById("pair-call")?.addEventListener?.("click", () => {
    openSheet(settingsEl);
    void onSettingsOpen();
    settingsEl.scrollTop = 0;
    document.getElementById("pair-request")?.click?.();
  });
  document.getElementById("headset-sound")?.addEventListener("click", () => {
    if (!voice) return;
    voice.primeSpeech();
    void voice.unlockAudio();
    ui.dispatch({ type: "tap_start" });
    applyAudioPrefs();
    document.getElementById("headset-sound").textContent = "Sound enabled";
  });
  document.getElementById("headset-pair")?.addEventListener("click", () => {
    document.getElementById("pair-call").click();
  });
  if (headset) {
    startBtn.disabled = true;
    cameraPick.disabled = true;
  }
  // Wheel and trackpad: if the browser did not scroll natively (seen in Brave), scroll it ourselves next frame.
  // Passive listeners only, no preventDefault; deltaX and deltaY both count. The ring turns a vertical wheel sideways.
  function wheelScroll(el, horizontal) {
    el?.addEventListener?.("wheel", (event) => {
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? el.clientHeight : 1;
      const delta = (horizontal && Math.abs(event.deltaX) < Math.abs(event.deltaY) ? event.deltaY : horizontal ? event.deltaX : event.deltaY) * unit;
      const before = horizontal ? el.scrollLeft : el.scrollTop;
      requestAnimationFrame(() => {
        if ((horizontal ? el.scrollLeft : el.scrollTop) !== before) return;
        if (horizontal) el.scrollLeft += delta;
        else el.scrollTop += delta;
      });
    }, { passive: true });
  }
  wheelScroll(ringEl, true);
  // An open sheet takes every wheel: over the sheet, scroll it if the browser did not; anywhere else (backdrop,
  // camera, chips), send the scroll to the sheet. Passive, so no browser can block it.
  const openSheetEl = () => [settingsEl, marketEl, rewindEl].find((el) => el && !el.classList.contains("hidden"));
  window.addEventListener("wheel", (event) => {
    const sheet = openSheetEl();
    if (!sheet) return;
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? sheet.clientHeight : 1;
    const delta = event.deltaY * unit;
    const inside = sheet.contains(event.target);
    const before = sheet.scrollTop;
    requestAnimationFrame(() => {
      if (inside && sheet.scrollTop !== before) return;
      sheet.scrollTop += delta;
    });
  }, { passive: true });
  // Mouse or pen drag on the phone-width ring (touch scrolls natively); a drag never counts as a tap.
  if (ringEl && typeof ringEl.addEventListener === "function") {
    let drag = null;
    ringEl.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "touch" || ringEl.scrollWidth <= ringEl.clientWidth) return;
      drag = { x: event.clientX, left: ringEl.scrollLeft, moved: false };
    });
    window.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const dx = event.clientX - drag.x;
      if (Math.abs(dx) > 5) drag.moved = true;
      if (drag.moved) ringEl.scrollLeft = drag.left - dx;
    });
    window.addEventListener("pointerup", (event) => {
      if (drag && drag.moved) {
        if (ringEl.contains(event.target)) ringEl.addEventListener("click", (event) => { event.stopPropagation(); event.preventDefault(); }, { capture: true, once: true });
        scrollRingTo(Math.round(ringEl.scrollLeft / RING_SLOT), true);
      }
      drag = null;
    });
  }
  document.addEventListener?.("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    if (picker.classList.contains("hidden") || (ringEl && ringEl.contains(event.target))) return;
    if (event.target && /^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName || "")) return;
    if (!settingsEl.classList.contains("hidden")) return;
    event.preventDefault();
    ringIndex = Math.max(0, Math.min(MODES.length - 1, ringIndex + (event.key === "ArrowRight" ? 1 : -1)));
    renderRing();
    scrollRingTo(ringIndex, true);
  });
  const openers = typeof document.querySelectorAll === "function" ? document.querySelectorAll(".open-settings") : [];
  for (const opener of openers) opener.addEventListener("click", () => openSheet(settingsEl));
  for (const opener of openers) opener.addEventListener("click", () => void onSettingsOpen());
  wireDevices();
  document.getElementById("settings-done").addEventListener("click", closeSheets);
  if (privateToggle) privateToggle.addEventListener("change", () => { void setPrivate(privateToggle.checked); });
  for (const [name, box] of Object.entries(cloudBoxes)) {
    if (box) box.addEventListener("change", () => setCloud(name, box.checked));
  }
  renderPrivate();
  if (privateMode) {
    stt = "server";
    tts = "server";
  }
  if (rewindEl) document.getElementById("rewind-dismiss").addEventListener("click", closeSheets);
  const keepBox = document.getElementById("rewind-keep");
  if (keepBox) keepBox.addEventListener("change", sendRewind);
  memoryCountEl.addEventListener("click", () => {
    const open = memoryActions.classList.contains("hidden");
    memoryActions.classList.toggle("hidden", !open);
    memoryCountEl.setAttribute("aria-expanded", open ? "true" : "false");
  });
  document.getElementById("forget-last").addEventListener("click", () => void forgetMemory("last"));
  // Disconnect the paired phone; a remembered phone then waits for the next call (server: Call.kick).
  document.getElementById("phone-kick").addEventListener("click", () => {
    send({ v: 2, type: "pair_request", session_id: sessionId, kick: true });
  });
  document.getElementById("forget-today").addEventListener("click", () => void forgetMemory("today"));
  const forgetBtn = document.getElementById("rewind-forget");
  if (forgetBtn) forgetBtn.addEventListener("click", () => void forgetRewind(sessionId).then(
    (r) => showProblem(`Forgot ${r.deleted} clip${r.deleted === 1 ? "" : "s"}.`), () => showProblem("Nothing to forget.")));
  const recapBtn = document.getElementById("rewind-recap");
  if (recapBtn) recapBtn.addEventListener("click", () => void showRecap(sessionId));
  if (rewindToggle) {
    rewindToggle.addEventListener("change", () => setRewind(rewindToggle.checked));
  }
  rewindBtn.addEventListener("click", () => setRewind(!rewindOn));
  rewindChip.addEventListener("click", () => togglePrompts(rewindPrompts.classList.contains("hidden")));
  for (const prompt of REWIND_PROMPTS) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = prompt.label;
    button.addEventListener("click", () => {
      togglePrompts(false);
      if (!typedEl) {
        ui.dispatch({ type: "final", text: prompt.text, now: Date.now() });
        renderCall();
        sendUser(prompt.text, "typed");
        return;
      }
      typedEl.value = prompt.text;
      typedEl.focus();
      typedEl.setSelectionRange(prompt.text.length, prompt.text.length);
    });
    rewindPrompts.append(button);
  }
  if (backdropEl) backdropEl.addEventListener("click", closeSheets);
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSheets();
  });
  document.getElementById("permission-retry").addEventListener("click", () => {
    permissionEl.classList.add("hidden");
    void startCamera();
  });
  document.getElementById("permission-close").addEventListener("click", () => {
    permissionEl.classList.add("hidden");
  });
  startBtn.addEventListener("click", () => {
    if (cameraOn || video.srcObject) stopCamera();
    else void startCamera();
  });
  powerBtn.addEventListener("click", () => {
    const ok = send({
      v: 2,
      type: "confirm",
      session_id: sessionId,
      what: "power_off",
      value: true,
    });
    if (!ok) {
      showProblem("Not connected.");
      return;
    }
    powerConfirmed = true;
    renderCall();
  });
  document.getElementById("upload").addEventListener("change", (event) => {
    onUpload(event);
  });
  document.getElementById("composer")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = typedEl.value;
    typedEl.value = "";
    const clean = String(text || "").trim();
    if (!clean) return;
    ui.dispatch({ type: "final", text: clean, now: Date.now() });
    renderCall();
    sendUser(clean, "typed");
  });
  micBtn.addEventListener("click", () => {
    onMic();
  });
  if (holdBtn) {
    holdBtn.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      if (typeof holdBtn.setPointerCapture === "function") holdBtn.setPointerCapture(event.pointerId);
      void onHoldDown();
    });
    for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) holdBtn.addEventListener(name, onHoldUp);
    holdBtn.addEventListener("contextmenu", (event) => event.preventDefault());
  }
  const typingTarget = (el) => el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
  window.addEventListener("keydown", (event) => {
    if (event.code !== "Space" || event.repeat || typingTarget(document.activeElement)) return;
    if (backdropEl && !backdropEl.classList.contains("hidden")) return; // a sheet is open: Space scrolls it
    event.preventDefault();
    void onHoldDown();
  });
  window.addEventListener("keyup", (event) => {
    if (event.code !== "Space" || typingTarget(document.activeElement)) return;
    event.preventDefault();
    onHoldUp();
  });
  if (sensEl) {
    sensEl.addEventListener("input", () => {
      if (voice && typeof voice.setSensitivity === "function") voice.setSensitivity(Number(sensEl.value));
    });
  }
  overlayEl.addEventListener("click", () => {
    onStartTap();
  });
  muteBtn.addEventListener("click", () => {
    ui.dispatch({ type: "mute", muted: !ui.getState().muted });
    applyAudioPrefs();
    renderCall();
  });
  volumeEl.addEventListener("input", () => {
    ui.dispatch({ type: "volume", volume: Number(volumeEl.value) });
    applyAudioPrefs();
  });
  if (voicePick) {
    voicePick.addEventListener("change", onVoicePick);
    void loadVoices();
  }
  document.getElementById("test-voice").addEventListener("click", () => {
    onTestVoice();
  });
  transcriptBtn.addEventListener("click", () => {
    const open = transcriptBtn.getAttribute("aria-expanded") === "true";
    const next = !open;
    transcriptBtn.setAttribute("aria-expanded", next ? "true" : "false");
    transcriptList.classList.toggle("hidden", !next);
    transcriptBtn.textContent = next ? "Hide transcript" : "Transcript";
    if (next) transcriptList.scrollTop = transcriptList.scrollHeight;
  });
  document.getElementById("market-dismiss").addEventListener("click", closeSheets);
  window.addEventListener("resize", () => paint(Date.now()));
  video.addEventListener("loadedmetadata", () => paint(Date.now()));
  if (reducedQuery && typeof reducedQuery.addEventListener === "function") {
    reducedQuery.addEventListener("change", () => paint(Date.now()));
  }

  volumeEl.value = String(ui.getState().volume);
  if (serverHostEl) serverHostEl.textContent = location.host;
  renderRing();
  scrollRingTo(ringIndex, false);
  renderPresence();
  renderCamera();
  renderCall();
  raf = window.requestAnimationFrame(loop);
  void raf;

  // ---- Device choice: lists, live mic line, level meter, hot swap, unplug fallback. ----

  function wireDevices() {
    const md = navigator.mediaDevices;
    if (!md || typeof md.enumerateDevices !== "function") {
      for (const pick of [micPick, cameraPick]) if (pick) pick.disabled = true;
      return;
    }
    if (speakerRow) speakerRow.classList.toggle("hidden", !sinkSupported(window));
    if (micPick) micPick.addEventListener("change", () => void onMicPick(micPick.value));
    if (cameraPick) cameraPick.addEventListener("change", () => void onCameraPick(cameraPick.value));
    if (speakerPick) speakerPick.addEventListener("change", () => onSpeakerPick(speakerPick.value));
    if (devicesAllow) devicesAllow.addEventListener("click", () => void allowDevices());
    if (typeof md.addEventListener === "function") md.addEventListener("devicechange", () => void refreshDevices());
    void refreshDevices();
  }

  async function refreshDevices() {
    try {
      mediaDevices = await navigator.mediaDevices.enumerateDevices();
    } catch {
      mediaDevices = [];
    }
    const mic = chooseDevice(mediaDevices, "audioinput", micChoice);
    if (mic.gone) await micMissing();
    const cam = chooseDevice(mediaDevices, "videoinput", cameraChoice);
    if (cam.gone) {
      dropCameraChoice();
      if (cameraOn) void swapCamera();
    }
    const spk = chooseDevice(mediaDevices, "audiooutput", speakerChoice);
    if (spk.gone) {
      speakerChoice = "";
      saveChoice(safeStorage(), "audiooutput", "");
      setDeviceNotice("Chosen speaker is gone. Using the default.");
      applySpeaker();
    }
    renderDeviceLists();
  }

  function fillPick(pick, kind, chosen) {
    if (!pick) return;
    const options = deviceOptions(mediaDevices, kind);
    pick.replaceChildren(...options.map((o) => {
      const opt = document.createElement("option");
      opt.value = o.id;
      opt.textContent = o.label;
      return opt;
    }));
    pick.value = options.some((o) => o.id === chosen) ? chosen : "";
  }

  function renderDeviceLists() {
    const locked = devicesLocked(mediaDevices, "audioinput") || devicesLocked(mediaDevices, "videoinput");
    if (devicesAllow) devicesAllow.classList.toggle("hidden", !locked);
    fillPick(micPick, "audioinput", micChoice);
    fillPick(cameraPick, "videoinput", cameraChoice);
    fillPick(speakerPick, "audiooutput", speakerChoice);
    const foot = document.getElementById("devices-foot");
    if (foot) {
      foot.textContent = sttForMic
        ? "Speak and watch the level bar. A chosen mic is heard through Whisper on this server, since browser recognition only hears the system default."
        : "Speak and watch the level bar to see which mic hears you.";
    }
    renderMicLine();
  }

  // Labels appear only after the browser grants access: one tap asks, then the lists fill in.
  async function allowDevices() {
    if (micRoute.phone) return;
    const md = navigator.mediaDevices;
    for (const ask of (headset ? [{ audio: true }] : [{ audio: true, video: true }, { audio: true }, { video: true }])) {
      try {
        const stream = await requestLocalMedia(md, headset, ask);
        for (const track of stream.getTracks()) track.stop();
        break;
      } catch (err) {
        if (err && err.name === "NotAllowedError") {
          setDeviceNotice("Device access is blocked. Allow the mic and camera for this site.");
          break;
        }
      }
    }
    await refreshDevices();
    if (settingsEl && !settingsEl.classList.contains("hidden")) void startPreviewIfIdle();
  }

  function currentMicLabel() {
    const live = voice && listening && typeof voice.micLabel === "function" ? voice.micLabel() : "";
    if (live) return live;
    if (micPreview.active() && micPreview.label()) return micPreview.label();
    return deviceLabel(mediaDevices, "audioinput", micChoice);
  }

  function routeMic(message) {
    const { route, action } = phoneMicRoute(micRoute, message, ui.getState().micOn);
    micRoute = route;
    if (micRoute.phone) micPreview.stop();
    if (headset) {
      headsetPanel.classList.toggle("connected", micRoute.phone);
      headsetStatus.textContent = micRoute.phone
        ? (message.state === "camera" && !message.camera_on ? "Phone camera paused" : "Phone camera connected")
        : "Waiting for your phone camera";
      document.getElementById("headset-help").classList.toggle("hidden", micRoute.phone);
      if (message.state === "left") document.getElementById("pair-status").textContent = "Phone disconnected. Waiting for your phone camera.";
    }
    const phoneChip = document.getElementById("phone-chip");
    if (phoneChip) phoneChip.classList.toggle("hidden", !micRoute.phone);
    if (action === "stop_local" && voice) {
      voice.stopListening();
      ui.dispatch({ type: "mic_stop" });
      turnEvent({ type: "mic_stop" });
    } else if (action === "start_local" && voice && voice.supported && voice.supported.stt) {
      void startMic();
    }
    renderCall();
  }

  function stopLocalPlayback() {
    speech = bargeSpeech(speech);
    talkQueue.length = 0;
    talkBusy = false;
    clearAllFallbacks();
    if (voice && typeof voice.cancel === "function") voice.cancel();
    ui.dispatch({ type: "audio_ended", utteranceId: "", now: Date.now() });
  }

  function renderMicLine() {
    if (!micLineEl) return;
    const text = micRoute.phone ? "Mic: phone" : micLine(currentMicLabel(), listening);
    if (micLineEl.textContent !== text) micLineEl.textContent = text;
  }

  function showMicLevel(level) {
    if (!micLevelEl) return;
    const pct = Math.round(Math.max(0, Math.min(1, Number(level) || 0)) * 100);
    micLevelEl.style.width = `${pct}%`;
    const meter = micLevelEl.parentElement;
    if (meter) meter.setAttribute("aria-valuenow", String(pct));
    if (listening) renderMicLine();
  }

  function setDeviceNotice(text) {
    deviceNotice = text;
    window.clearTimeout(deviceNoticeTimer);
    deviceNoticeTimer = window.setTimeout(() => {
      deviceNotice = "";
      renderCall();
    }, 6000);
    renderCall();
  }

  async function onSettingsOpen() {
    await refreshDevices();
    await startPreviewIfIdle();
  }

  // The Settings meter: the call mic feeds it while listening; otherwise a short preview of the chosen mic.
  async function startPreviewIfIdle() {
    if (micRoute.phone || listening || !settingsEl || settingsEl.classList.contains("hidden")) return;
    if (devicesLocked(mediaDevices, "audioinput")) return;
    try {
      await micPreview.start(micChoice);
      if (micRoute.phone) micPreview.stop();
      renderMicLine();
    } catch (err) {
      micPreview.stop();
      if (micChoice && isMissingDevice(err)) await micMissing();
    }
  }

  async function onMicPick(id) {
    micChoice = String(id || "");
    saveChoice(safeStorage(), "audioinput", micChoice);
    await syncSttToMic();
    if (voice && typeof voice.setMicDevice === "function") voice.setMicDevice(micChoice);
    if (micPreview.active() || !listening) await startPreviewIfIdle();
    renderMicLine();
    // the new track's label shows once it opens
    window.setTimeout(renderMicLine, 800);
  }

  async function micMissing() {
    if (!micChoice) return;
    micChoice = "";
    saveChoice(safeStorage(), "audioinput", "");
    setDeviceNotice("Chosen mic is gone. Using the default mic.");
    await syncSttToMic();
    if (voice && typeof voice.setMicDevice === "function") voice.setMicDevice("");
    if (voice && ui.getState().micOn && !voice.isListening()) void startMic();
    renderDeviceLists();
  }

  // Whisper for a chosen mic; back to browser recognition when the default is picked again.
  async function syncSttToMic() {
    const want = micChoice && stt === "browser" ? "server" : !micChoice && sttForMic ? "browser" : stt;
    if (want === stt) return;
    sttForMic = want === "server";
    const micWasOn = ui.getState().micOn;
    stt = want;
    if (!voice) {
      renderCall();
      return;
    }
    voice = await rebuildVoice();
    applyAudioPrefs();
    sendHello();
    renderCall();
    if (voice && micWasOn && voice.supported && voice.supported.stt) void startMic();
  }

  function dropCameraChoice() {
    cameraChoice = "";
    saveChoice(safeStorage(), "videoinput", "");
    setDeviceNotice("Chosen camera is gone. Using the default camera.");
    if (cameraPick) cameraPick.value = "";
  }

  async function onCameraPick(id) {
    cameraChoice = String(id || "");
    saveChoice(safeStorage(), "videoinput", cameraChoice);
    if (cameraOn) await swapCamera();
  }

  // Live camera switch: open the new device, then stop the old tracks. The call and frames keep going.
  async function swapCamera() {
    if (headset || !cameraOn || starting || flipCamera.busy()) return;
    starting = true;
    try {
      const stream = await requestLocalMedia(navigator.mediaDevices, headset, { video: flipCamera.constraints(videoConstraints(cameraChoice)), audio: false });
      if (!cameraOn || !pack) {
        for (const track of stream.getTracks()) track.stop();
        return;
      }
      const old = video.srcObject;
      video.srcObject = stream;
      flipCamera.preview();
      if (old && typeof old.getTracks === "function") for (const track of old.getTracks()) track.stop();
      await video.play().catch(() => {});
      paint(Date.now());
    } catch (err) {
      if (cameraChoice && isMissingDevice(err)) {
        dropCameraChoice();
        starting = false;
        return swapCamera();
      }
      showProblem("Could not switch the camera.");
    } finally {
      starting = false;
    }
  }

  function onSpeakerPick(id) {
    speakerChoice = String(id || "");
    saveChoice(safeStorage(), "audiooutput", speakerChoice);
    applySpeaker();
  }

  function applySpeaker() {
    if (!voice || typeof voice.setSinkId !== "function" || !sinkSupported(window)) return;
    voice.setSinkId(speakerChoice).catch(() => {
      if (!speakerChoice) return;
      speakerChoice = "";
      saveChoice(safeStorage(), "audiooutput", "");
      setDeviceNotice("Could not use that speaker. Using the default.");
      if (speakerPick) speakerPick.value = "";
    });
  }

  async function rebuildVoice() {
    try {
      const mod = await import("./voice.js");
      if (voice && typeof voice.stopListening === "function") voice.stopListening();
      if (!mod || typeof mod.createVoice !== "function") return null;
      const next = mod.createVoice(voiceOptions());
      return next;
    } catch {
      return null;
    }
  }
}

function safeStorage() {
  try {
    return globalThis.localStorage;
  } catch {
    return null;
  }
}

// Same-origin API calls carry the page token when there is one (LAN, quick tunnel). Behind Cloudflare Access the
// cookie rides along by itself. Seen live: /api/voices returned 401 on a token server because no token was sent.
async function apiFetch(path, data) {
  const token = new URLSearchParams(location.search).get("token");
  const headers = await ownerHeaders();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (data !== undefined) headers['Content-Type'] = 'application/json';
  return fetch(path, { headers, ...(data !== undefined ? { method: 'POST', body: JSON.stringify(data) } : {}) });
}

if (typeof document !== "undefined") boot();
