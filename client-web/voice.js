import { createEarlyFinal } from "./turn.js";
import { audioConstraints, isMissingDevice } from "./devices.js";
import { BARGE_VAD, DEFAULT_SENSITIVITY, NOISY_AMBIENT, initialBarge, nextBarge, vadSettings } from "./realtime.js";

// Browser mic, speech-to-text, and talk-back for the call page.
// No recording, no storage, and no logging. Status leaves only through onState.
// Server STT streams 16 kHz PCM16 through onAudioChunk and keeps only the unsent
// tail of the current utterance. Nothing is written to disk or kept after send.
// iOS creates or resumes AudioContext only inside a user gesture: the page calls
// unlockAudio() from the mic button tap.

const PCM_RATE = 16000;
const CHUNK_SAMPLES = PCM_RATE / 5; // 200 ms
const MAX_UTTER_SAMPLES = PCM_RATE * 15;
const VAD_RATIO = 2;
const VAD_UP = 0.2;
const VAD_DOWN = 0.05;
const SPEECH_RMS = 0.02;
const HANG_MS = 400;
// Silero VAD (v5) in the browser, pinned. Loaded lazily from the CDN; any failure falls back to word barge-in.
export const VAD_ORT_BASE = "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/";
export const VAD_WEB_BASE = "https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.31/dist/";
const DUCK_LEVEL = 0.15;
// After the VAD hears the end of speech, send the latest words once the recognizer is quiet this long.
export const VAD_FLUSH_MS = 120;
const QUICK_END_MS = 1500;
const QUICK_END_LIMIT = 3;
// Calibration: the first second of mic level after start is the room's noise.
const CALIBRATE_MS = 1000;
// An utterance needs this much voiced audio above the calibrated floor before anything is sent
// (hall noise bursts opened utterances that Whisper returned empty, 2026-10-24 live log).
export const MIN_UTTERANCE_MS = 400;
const PENDING_GAP_MS = 150;
// In a loud room a barge-in needs sustained speech, so crowd noise cannot cut the reply.
export const NOISY_BARGE_MS = 700;
const PREROLL_SAMPLES = PCM_RATE / 2; // 500 ms kept so a delayed start keeps the first syllable
// Browser speech cleanup on, one channel, 16 kHz when the browser honours it (Chrome, Brave, Safari).
export const MIC_CONSTRAINTS = Object.freeze({
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
  channelCount: 1,
  sampleRate: { ideal: 16000 },
});

export function wordCount(text) {
  return wordsOf(text).length;
}

// True when every heard word already appears in the line being spoken.
// An empty hear is treated as an echo so it cannot trip barge-in.
export function isEcho(heard, speaking) {
  const heardWords = wordsOf(heard);
  if (heardWords.length === 0) return true;
  const spoken = new Set(wordsOf(speaking));
  return heardWords.every((word) => spoken.has(word));
}

// Little-endian PCM16 bytes, base64, to floats in [-1, 1]. A trailing odd byte is dropped.
export function pcm16ToFloat32(base64) {
  const bytes = decodeBase64(base64);
  const n = bytes.length - (bytes.length % 2);
  const view = new DataView(bytes.buffer, bytes.byteOffset, n);
  const out = new Float32Array(n / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = view.getInt16(i * 2, true) / 32768;
  }
  return out;
}

// Box-average when fromRate is an integer multiple of toRate. Trailing samples
// that do not fill a whole output frame are dropped.
export function downsample(float32, fromRate, toRate) {
  const input = asFloat32(float32);
  const from = Number(fromRate);
  const to = Number(toRate);
  if (!input.length || !Number.isFinite(from) || !Number.isFinite(to) || from <= 0 || to <= 0) {
    return new Float32Array(0);
  }
  if (from === to) return new Float32Array(input);
  if (from % to === 0) {
    const step = from / to;
    const outLen = Math.floor(input.length / step);
    const out = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      let sum = 0;
      const base = i * step;
      for (let j = 0; j < step; j++) sum += input[base + j];
      out[i] = sum / step;
    }
    return out;
  }
  const outLen = Math.floor((input.length * to) / from);
  const out = new Float32Array(outLen);
  const scale = from / to;
  for (let i = 0; i < outLen; i++) {
    const pos = i * scale;
    const i0 = Math.floor(pos);
    const i1 = Math.min(i0 + 1, input.length - 1);
    const frac = pos - i0;
    out[i] = input[i0] * (1 - frac) + input[i1] * frac;
  }
  return out;
}

// Floats outside [-1, 1] clamp to int16. Scale matches pcm16ToFloat32 (divide by 32768).
export function floatToPcm16(float32) {
  const input = asFloat32(float32);
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    let s = input[i];
    if (!Number.isFinite(s)) s = 0;
    else if (s > 1) s = 1;
    else if (s < -1) s = -1;
    let v = Math.round(s * 32768);
    if (v > 32767) v = 32767;
    else if (v < -32768) v = -32768;
    out[i] = v;
  }
  return out;
}

// The first frame sets the noise floor and does not open speech.
// Later frames start only above max(threshold, floor * 2). Silence for hangMs ends it.
export function createVad(options = {}) {
  const threshold = Number.isFinite(options.threshold) ? options.threshold : SPEECH_RMS;
  const hangMs = Number.isFinite(options.hangMs) ? options.hangMs : HANG_MS;
  // minMs: speech must stay above the gate this long before it counts (noise bursts in a loud hall).
  const minMs = Number.isFinite(options.minMs) ? options.minMs : 0;
  let pendingSince = null;
  let lastAbove = 0;
  // seed: a rebuilt VAD keeps the old floor; otherwise its first frame (possibly speech) would become it.
  let noise = Number.isFinite(options.seed) ? options.seed : 0;
  let seeded = Number.isFinite(options.seed);
  let active = false;
  let lastVoice = 0;

  function gate() {
    return Math.max(threshold, noise * VAD_RATIO);
  }

  function adapt(energy) {
    const alpha = energy > noise ? VAD_UP : VAD_DOWN;
    noise = noise * (1 - alpha) + energy * alpha;
  }

  return {
    // Speech is open or pending its minMs: a rebuild now would reseed the floor on the speech itself.
    busy() {
      return active || pendingSince != null;
    },
    floor() {
      return seeded ? noise : undefined;
    },
    push(frameEnergy, nowMs) {
      const energy = Number.isFinite(frameEnergy) ? Math.max(0, frameEnergy) : 0;
      const now = Number.isFinite(nowMs) ? nowMs : 0;
      if (!seeded) {
        noise = energy;
        seeded = true;
        return "silence";
      }
      const above = energy > gate();
      if (!active) {
        if (above) {
          if (pendingSince == null) pendingSince = now;
          lastAbove = now;
          if (now - pendingSince < minMs) return "silence";
          pendingSince = null;
          active = true;
          lastVoice = now;
          return "start";
        }
        // Speech energy dips between syllables, and AudioWorklet frames are only 2.7 ms: a short dip inside
        // the pending window is part of the word, not the end of it (and is not room noise to learn).
        if (pendingSince != null && now - lastAbove < PENDING_GAP_MS) return "silence";
        pendingSince = null;
        adapt(energy);
        return "silence";
      }
      if (above) {
        lastVoice = now;
        return "speech";
      }
      if (now - lastVoice >= hangMs) {
        active = false;
        adapt(energy);
        return "end";
      }
      return "speech";
    },
  };
}

export function createVoice(options = {}) {
  const onFinal = options.onFinal || noop;
  const onInterim = options.onInterim || noop;
  const onState = options.onState || noop;
  const onBargeIn = options.onBargeIn || noop;
  // Called at every barge point (same 400/700 ms rules) even when this page is not speaking: a phone mic
  // paired to a laptop call uses it to stop the laptop's reply (pair.js).
  const onBargePoint = options.onBargePoint || noop;
  const onAudioChunk = options.onAudioChunk || noop;
  const onLevel = options.onLevel || noop;
  const onMicError = options.onMicError || noop;
  const onSpeakEnd = options.onSpeakEnd || noop;
  const onSpeakStart = options.onSpeakStart || noop;
  const onAudioError = options.onAudioError || noop;
  const serverMode = options.sttMode === "server";
  const lang = options.lang || "en-US";
  const now = options.now || defaultNow;
  const synth = options.synth !== undefined ? options.synth : globalThis.speechSynthesis || null;
  const recognitionFactory = options.recognitionFactory || defaultRecognitionFactory;
  const audioContextFactory = options.audioContextFactory || defaultAudioContextFactory;
  const customAudio = Object.prototype.hasOwnProperty.call(options, "audioContextFactory");
  const clock = options.clock || globalThis;
  const onVad = options.onVad || noop;
  const neuralWanted = options.neuralVad !== undefined ? Boolean(options.neuralVad) : defaultNeuralWanted();
  const vadLoader = options.vadLoader || loadVadWeb;
  const onAmbient = options.onAmbient || noop;
  let sensitivity = Number.isFinite(options.sensitivity) ? options.sensitivity : DEFAULT_SENSITIVITY;
  let ambient = null;
  let calibration = null;
  let holdMode = Boolean(options.holdToTalk);
  let holding = false;
  let micSettings = null;
  let micDeviceId = String(options.micDeviceId || "");

  const early = createEarlyFinal(onFinal, clock);
  let recGeneration = 0;
  let rec = null;
  try {
    rec = recognitionFactory();
  } catch {
    rec = null;
  }

  const supported = {
    stt: serverMode ? micAvailable() : Boolean(rec),
    tts: Boolean(synth) || customAudio || hasAudioConstructor(),
  };

  let state = "idle";
  let listening = false;
  let recRunning = false;
  let speaking = false;
  let utteranceId = null;
  let spokenText = "";
  let localSpeech = null;
  let playbackStartedAt = 0;
  let bargedUtterance = null;
  let synthActive = false;
  let synthGen = 0;
  let audioActive = false;
  let audioLeft = 0;
  let audioGen = 0;
  let pcmFinal = false;
  let nextTime = null;
  let audioCtx = null;
  const sources = [];
  let captureGen = 0;
  let mediaStream = null;
  const captureNodes = [];
  let workletUrl = "";
  let vad = createVad(energyVadOptions());
  let pieces = [];
  let preroll = [];
  let bargeArmedAt = null;
  let prerollSamples = 0;
  let queued = 0;
  let sentInUtt = 0;
  let utteranceOpen = false;
  let serverHold = false;
  let audioSeq = 0;
  let volume = 1;
  let muted = false;
  let currentUtter = null;
  let outputGain = null;
  let meterGen = 0;
  let meterStream = null;
  let meterTimer = 0;
  let meterSmooth = 0;
  const meterNodes = [];
  let neural = null;
  let neuralGen = 0;
  let barge = initialBarge();
  let lastInterim = null;
  let flushTimer = 0;
  let synthPaused = false;
  let recStartedAt = 0;
  let recHeard = false;
  let quickEnds = 0;

  if (rec) {
    rec.onresult = handleResult;
    rec.onerror = handleRecError;
    rec.onend = handleRecEnd;
  }

  function setState(next) {
    if (next === state) return;
    state = next;
    onState(state);
  }

  function isSpeaking() {
    return speaking;
  }

  function maybeIdle() {
    if (synthActive || audioActive) return;
    const was = speaking;
    const ended = utteranceId;
    speaking = false;
    finishLocalSpeech();
    currentUtter = null;
    setState(listening ? "listening" : "idle");
    if (was) onSpeakEnd(ended);
  }

  function effectiveVolume() {
    if (muted) return 0;
    return clamp01(volume);
  }

  function outputLevel() {
    return barge.ducked ? effectiveVolume() * DUCK_LEVEL : effectiveVolume();
  }

  function applyOutput() {
    const level = outputLevel();
    if (outputGain && outputGain.gain) outputGain.gain.value = level;
    if (currentUtter) currentUtter.volume = level;
  }

  function setVolume(next) {
    const n = Number(next);
    if (!Number.isFinite(n)) return effectiveVolume();
    volume = clamp01(n);
    applyOutput();
    return volume;
  }

  function setMuted(next) {
    muted = Boolean(next);
    applyOutput();
    return muted;
  }

  function haltPlayback() {
    finishLocalSpeech(new Error("Read aloud did not finish. Please try again."));
    audioGen += 1;
    synthGen += 1;
    synthActive = false;
    audioActive = false;
    audioLeft = 0;
    pcmFinal = false;
    nextTime = null;
    speaking = false;
    if (barge.ducked) barge = { ...barge, ducked: false };
    if (outputGain && outputGain.gain) outputGain.gain.value = effectiveVolume();
    resumeSynth();
    if (synth && typeof synth.cancel === "function") {
      try {
        synth.cancel();
      } catch {
        // ignore
      }
    }
    const doomed = sources.splice(0, sources.length);
    for (const src of doomed) {
      try {
        src.stop();
      } catch {
        // already stopped
      }
    }
  }

  function startListening() {
    if (serverMode) return startServerListening();
    if (!rec) return;
    listening = true;
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = lang;
    if (!recRunning) {
      try {
        recStartedAt = now();
        rec.start();
        recRunning = true;
      } catch {
        recRunning = false;
      }
    }
    if (!speaking) setState("listening");
    startBrowserMeter();
    void startNeural();
  }

  function stopListening() {
    listening = false;
    stopNeural();
    early.reset();
    stopBrowserMeter();
    if (serverMode) {
      captureGen += 1;
      if (utteranceOpen) drain(true);
      utteranceOpen = false;
      serverHold = false;
      releaseCapture();
      if (!speaking) setState("idle");
      return;
    }
    if (rec) {
      try {
        rec.stop();
      } catch {
        // not started
      }
    }
    if (!speaking) setState("idle");
  }

  // Brave and some Chromium builds expose SpeechRecognition but it never works: it errors with
  // "network" or ends at once, over and over. Three quick ends without any result means unsupported.
  function recBroken() {
    listening = false;
    early.reset();
    stopBrowserMeter();
    stopNeural();
    onMicError("unsupported"); // before "idle", so the page still knows the mic was on
    if (!speaking) setState("idle");
  }

  function handleRecEnd() {
    recGeneration += 1;
    recRunning = false;
    if (!listening || !rec) return;
    if (!recHeard && now() - recStartedAt < QUICK_END_MS) {
      quickEnds += 1;
      if (quickEnds >= QUICK_END_LIMIT) {
        recBroken();
        return;
      }
    } else {
      quickEnds = 0;
    }
    recStartedAt = now();
    try {
      rec.start();
      recRunning = true;
    } catch {
      recRunning = false;
    }
  }

  function handleRecError(event) {
    const code = event && event.error;
    if (code === "network" || code === "language-not-supported") {
      recBroken();
      return;
    }
    if (code === "no-speech") {
      onMicError("no_speech");
      return;
    }
    if (code === "not-allowed" || code === "service-not-allowed" || code === "audio-capture") {
      listening = false;
      early.reset();
      stopBrowserMeter();
      if (!speaking) setState("idle");
      if (code === "service-not-allowed") onMicError("unsupported");
      else onMicError("denied");
    }
  }

  function handleResult(event) {
    const results = event && event.results;
    if (!results || !listening || localSpeech) return;
    recHeard = true;
    quickEnds = 0;
    const from = Number.isInteger(event.resultIndex) ? event.resultIndex : 0;
    for (let i = from; i < results.length; i++) {
      const row = results[i];
      const text = transcriptOf(row).trim();
      if (!text) continue;
      const resultId = `${recGeneration}:${i}`;
      if (early.hasSent(resultId)) continue;
      // While playback is up, a result whose words all sit inside the spoken line
      // is the speaker heard by the mic. Drop it instead of treating it as the user.
      if (speaking && isEcho(text, spokenText)) continue;
      considerBarge(text);
      if (row && row.isFinal) {
        early.final(text, resultId);
        lastInterim = null;
      } else if (early.interim(text, resultId)) {
        lastInterim = { text, id: resultId };
        if (flushTimer) scheduleFlush();
        onInterim(text);
      }
    }
  }

  function considerBarge(text) {
    if (!speaking || utteranceId == null) return;
    if (wordCount(text) < 2) return;
    if (isEcho(text, spokenText)) return;
    if (bargedUtterance === utteranceId) return;
    fireBarge();
  }

  // Server mode has no transcript yet. Speech onset is the barge, once per utterance.
  function considerServerBarge() {
    onBargePoint();
    if (!speaking || utteranceId == null) return;
    if (bargedUtterance === utteranceId) return;
    fireBarge();
  }

  function fireBarge() {
    const id = utteranceId;
    const atMs = Math.max(0, Math.round(now() - playbackStartedAt));
    bargedUtterance = id;
    haltPlayback();
    setState(listening ? "listening" : "idle");
    onBargeIn(id, atMs);
  }

  function finishLocalSpeech(error) {
    if (!localSpeech) return;
    const pending = localSpeech;
    localSpeech = null;
    if (error) pending.reject(error);
    else pending.resolve();
  }

  function speak(id, text, { localOnly = false } = {}) {
    if (localSpeech) return localOnly
      ? Promise.reject(new Error("Please wait for read aloud to finish.")) : undefined;
    // Family words never go to a remote voice. Fail closed if no local voice is installed.
    const localVoice = localOnly && synth?.getVoices?.().find(v => v.localService && v.lang.startsWith("en"));
    if (localOnly && (!localVoice || typeof synth.speak !== "function")) return Promise.reject(new Error("Enable an on-device English voice, then tap Read aloud."));
    haltPlayback();
    let completion;
    if (localOnly) {
      completion = new Promise((resolve, reject) => { localSpeech = { resolve, reject }; });
      early.reset();
      lastInterim = null;
      dropQueue();
      preroll = [];
      prerollSamples = 0;
      sentInUtt = 0;
      utteranceOpen = false;
      bargeArmedAt = null;
      vad = createVad(energyVadOptions());
    }
    utteranceId = id;
    spokenText = String(text || "");
    bargedUtterance = null;
    playbackStartedAt = now();
    speaking = true;
    setState("speaking");
    if (!synth || typeof synth.speak !== "function") {
      maybeIdle();
      return;
    }
    const utter = makeUtterance(spokenText);
    utter.lang = lang;
    if (localVoice) utter.voice = localVoice;
    utter.volume = effectiveVolume();
    currentUtter = utter;
    const token = ++synthGen;
    synthActive = true;
    const done = (error) => {
      if (token !== synthGen) return;
      finishLocalSpeech(error);
      synthActive = false;
      maybeIdle();
    };
    utter.onstart = () => { if (token === synthGen) onSpeakStart(id); };
    utter.onend = () => done();
    utter.onerror = () => done(new Error("Read aloud did not finish. Please try again."));
    try {
      synth.speak(utter);
    } catch {
      done(new Error("Read aloud did not finish. Please try again."));
    }
    return completion;
  }

  function playPcm(id, pcm16Base64, finalChunk) {
    if (localSpeech) return;
    const same = speaking && utteranceId === id;
    if (!same) {
      const keepText = utteranceId === id ? spokenText : "";
      haltPlayback();
      utteranceId = id;
      spokenText = keepText;
      bargedUtterance = null;
      playbackStartedAt = now();
      speaking = true;
      setState("speaking");
    }
    // Server audio replaces browser TTS for this utterance. Bump the token
    // before cancel so the utterance's onend does not mark playback finished.
    if (synthActive) {
      synthGen += 1;
      synthActive = false;
      if (synth && typeof synth.cancel === "function") {
        try {
          synth.cancel();
        } catch {
          // ignore
        }
      }
    }
    if (finalChunk) pcmFinal = true;
    let samples;
    try {
      samples = pcm16ToFloat32(pcm16Base64);
    } catch {
      samples = new Float32Array(0);
    }
    if (samples.length === 0) {
      if (pcmFinal && audioLeft === 0) {
        audioActive = false;
        maybeIdle();
      }
      return;
    }
    const ctx = ensureAudio();
    if (!ctx || typeof ctx.createBuffer !== "function" || typeof ctx.createBufferSource !== "function") {
      if (pcmFinal && audioLeft === 0) {
        audioActive = false;
        maybeIdle();
      }
      return;
    }
    if (ctx.state === "suspended" && typeof ctx.resume === "function") {
      try {
        ctx.resume();
      } catch {
        // unlocked later from the mic tap
      }
    }
    let src;
    try {
      const buffer = ctx.createBuffer(1, samples.length, PCM_RATE);
      const channel = buffer.getChannelData(0);
      channel.set(samples);
      src = ctx.createBufferSource();
      src.buffer = buffer;
      if (typeof src.connect === "function") src.connect(outputNode(ctx));
      const current = typeof ctx.currentTime === "number" ? ctx.currentTime : 0;
      const startAt = nextTime == null || nextTime < current ? current : nextTime;
      nextTime = startAt + samples.length / PCM_RATE;
      const token = audioGen;
      audioLeft += 1;
      audioActive = true;
      sources.push(src);
      src.onended = () => {
        if (token !== audioGen) return;
        audioLeft = Math.max(0, audioLeft - 1);
        if (audioLeft === 0 && pcmFinal) {
          audioActive = false;
          maybeIdle();
        }
      };
      src.start(startAt);
      if (ctx.state === "running" && startAt === current) onSpeakStart(id);
    } catch (err) {
      onAudioError(id, err);
      if (src) {
        const idx = sources.indexOf(src);
        if (idx >= 0) sources.splice(idx, 1);
      }
      if (pcmFinal && audioLeft === 0) {
        audioActive = false;
        maybeIdle();
      }
    }
  }

  function cancel() {
    haltPlayback();
    utteranceId = null;
    spokenText = "";
    setState(listening ? "listening" : "idle");
  }

  // A chosen mic that was unplugged: the page falls back to the default and says so.
  function micErrorKind(err) {
    return micDeviceId && isMissingDevice(err) ? "missing" : deniedKind(err);
  }

  // Switch the input device; a live mic restarts only its own capture (old tracks stopped first).
  function setMicDevice(id) {
    const next = String(id || "");
    if (next === micDeviceId) return false;
    micDeviceId = next;
    if (!listening) return true;
    if (serverMode) {
      void startServerListening();
    } else {
      stopBrowserMeter();
      startBrowserMeter();
    }
    if (neural) {
      stopNeural();
      void startNeural();
    }
    return true;
  }

  function micLabel() {
    const stream = mediaStream || meterStream;
    try {
      const track = stream && typeof stream.getAudioTracks === "function" ? stream.getAudioTracks()[0] : null;
      return track && track.readyState !== "ended" ? String(track.label || "") : "";
    } catch {
      return "";
    }
  }

  // Speaker output for server voice (AudioContext.setSinkId, Chrome and Brave). Browser speech can't be routed.
  async function setSinkId(id) {
    const ctx = ensureAudio();
    if (!ctx || typeof ctx.setSinkId !== "function") return false;
    await ctx.setSinkId(String(id || ""));
    return true;
  }

  function micAvailable() {
    if (typeof options.getUserMedia === "function") return true;
    const md = globalThis.navigator && globalThis.navigator.mediaDevices;
    return Boolean(md && typeof md.getUserMedia === "function");
  }

  function loudRoom() {
    return ambient != null && ambient > NOISY_AMBIENT;
  }

  function energyVadOptions() {
    return { threshold: vadSettings(sensitivity, ambient).energyThreshold, hangMs: HANG_MS, minMs: MIN_UTTERANCE_MS };
  }

  function noteSettings(stream) {
    try {
      const track = stream && typeof stream.getAudioTracks === "function" ? stream.getAudioTracks()[0] : null;
      const got = track && typeof track.getSettings === "function" ? track.getSettings() : null;
      if (got) {
        micSettings = {};
        for (const key of ["echoCancellation", "noiseSuppression", "autoGainControl", "channelCount", "sampleRate"]) {
          if (key in got) micSettings[key] = got[key];
        }
      }
    } catch {
      // settings are informational
    }
    return stream;
  }

  function requestMic() {
    const constraints = audioConstraints(MIC_CONSTRAINTS, micDeviceId);
    if (typeof options.getUserMedia === "function") return Promise.resolve(options.getUserMedia(constraints));
    const md = globalThis.navigator && globalThis.navigator.mediaDevices;
    if (!md || typeof md.getUserMedia !== "function") return Promise.reject(new Error("no mic"));
    return md.getUserMedia(constraints);
  }

  async function startServerListening() {
    const token = ++captureGen;
    listening = true;
    utteranceOpen = false;
    serverHold = false;
    dropQueue();
    releaseCapture();
    vad = createVad(energyVadOptions());
    calibration = { until: now() + CALIBRATE_MS, samples: [] };
    try {
      const stream = noteSettings(await requestMic());
      if (token !== captureGen || !listening) {
        stopTracks(stream);
        return;
      }
      mediaStream = stream;
      const ctx = ensureAudio();
      if (!ctx) throw new Error("no audio");
      await attachCapture(ctx, stream, token);
      if (token !== captureGen || !listening) {
        releaseCapture();
        return;
      }
      if (!speaking) setState("listening");
    } catch (err) {
      if (token !== captureGen) return;
      listening = false;
      releaseCapture();
      if (!speaking) setState("idle");
      onMicError(micErrorKind(err));
    }
  }

  async function attachCapture(ctx, stream, token) {
    if (typeof ctx.createMediaStreamSource !== "function") throw new Error("no source");
    const source = ctx.createMediaStreamSource(stream);
    captureNodes.push(source);
    const worked = await attachWorklet(ctx, source, token);
    if (worked) return;
    if (typeof ctx.createScriptProcessor !== "function") throw new Error("no capture node");
    const proc = ctx.createScriptProcessor(2048, 1, 1);
    proc.onaudioprocess = (event) => {
      const buffer = event && event.inputBuffer;
      const rate = buffer && buffer.sampleRate ? buffer.sampleRate : ctx.sampleRate || 48000;
      onCapture(monoBuffer(buffer), rate, token);
    };
    if (typeof source.connect === "function") source.connect(proc);
    connectSilent(ctx, proc);
    captureNodes.push(proc);
  }

  async function attachWorklet(ctx, source, token) {
    const worklet = ctx.audioWorklet;
    if (!worklet || typeof worklet.addModule !== "function") return false;
    if (typeof globalThis.AudioWorkletNode !== "function") return false;
    try {
      const url = createWorkletUrl();
      workletUrl = url;
      await worklet.addModule(url);
      if (token !== captureGen || !listening) return false;
      const node = new globalThis.AudioWorkletNode(ctx, "pcm-capture");
      node.port.onmessage = (event) => {
        const samples = event && event.data;
        if (!samples || typeof samples.length !== "number") return;
        onCapture(samples, ctx.sampleRate || 48000, token);
      };
      if (typeof source.connect === "function") source.connect(node);
      connectSilent(ctx, node);
      captureNodes.push(node);
      return true;
    } catch {
      return false;
    }
  }

  function onCapture(samples, sampleRate, token) {
    if (token !== captureGen || !listening || localSpeech) return;
    const down = downsample(samples, sampleRate, PCM_RATE);
    if (down.length === 0) return;
    const energy = rms(down);
    emitLevel(energy);
    if (holdMode) {
      // Hold to talk: the button decides the utterance; the VAD is not asked.
      if (holding) {
        appendSamples(down);
        drain(false);
      }
      return;
    }
    const verdict = vad.push(energy, now());
    if (serverHold) {
      if (verdict === "end" || verdict === "silence") serverHold = false;
      return;
    }
    if (verdict === "silence") {
      preroll.push(down);
      prerollSamples += down.length;
      while (preroll.length > 1 && prerollSamples - preroll[0].length >= PREROLL_SAMPLES) prerollSamples -= preroll.shift().length;
      return;
    }
    if (verdict === "start") {
      dropQueue();
      sentInUtt = 0;
      utteranceOpen = true;
      // Speech began MIN_UTTERANCE_MS ago. In a loud room the barge waits for NOISY_BARGE_MS of it.
      bargeArmedAt = now() - MIN_UTTERANCE_MS;
      if (!loudRoom()) {
        bargeArmedAt = null;
        considerServerBarge();
      }
      for (const frame of preroll) appendSamples(frame);
      preroll = [];
      prerollSamples = 0;
    } else if (verdict === "end") {
      bargeArmedAt = null;
      if (utteranceOpen) drain(true);
      utteranceOpen = false;
      return;
    } else if (!utteranceOpen) {
      return;
    } else if (bargeArmedAt != null && now() - bargeArmedAt >= NOISY_BARGE_MS) {
      bargeArmedAt = null;
      considerServerBarge();
    }
    appendSamples(down);
    drain(false);
  }

  function appendSamples(samples) {
    const pcm = floatToPcm16(samples);
    if (pcm.length === 0) return;
    pieces.push(pcm);
    queued += pcm.length;
  }

  function takeSamples(n) {
    const out = new Int16Array(n);
    let filled = 0;
    while (filled < n && pieces.length) {
      const head = pieces[0];
      const need = n - filled;
      if (head.length <= need) {
        out.set(head, filled);
        filled += head.length;
        pieces.shift();
      } else {
        out.set(head.subarray(0, need), filled);
        pieces[0] = head.subarray(need);
        filled += need;
      }
    }
    queued -= filled;
    return filled === n ? out : out.subarray(0, filled);
  }

  function dropQueue() {
    pieces = [];
    queued = 0;
  }

  function drain(finalChunk) {
    while (queued >= CHUNK_SAMPLES && sentInUtt + CHUNK_SAMPLES < MAX_UTTER_SAMPLES) {
      const took = takeSamples(CHUNK_SAMPLES);
      sentInUtt += took.length;
      emitChunk(took, false);
    }
    const hitCap = utteranceOpen && sentInUtt + queued >= MAX_UTTER_SAMPLES;
    if (hitCap) {
      const room = MAX_UTTER_SAMPLES - sentInUtt;
      const n = Math.min(queued, room > 0 ? room : 0);
      const tail = n > 0 ? takeSamples(n) : new Int16Array(0);
      dropQueue();
      sentInUtt += tail.length;
      emitChunk(tail, true);
      utteranceOpen = false;
      serverHold = true;
      return;
    }
    if (!finalChunk) return;
    const tail = queued > 0 ? takeSamples(queued) : new Int16Array(0);
    if (tail.length > 0 || sentInUtt > 0) emitChunk(tail, true);
    dropQueue();
    sentInUtt = 0;
    utteranceOpen = false;
  }

  function emitChunk(samples, finalChunk) {
    const seq = audioSeq;
    audioSeq += 1;
    onAudioChunk(encodePcm16(samples), seq, Boolean(finalChunk));
  }

  function releaseCapture() {
    const stream = mediaStream;
    mediaStream = null;
    stopTracks(stream);
    const nodes = captureNodes.splice(0, captureNodes.length);
    for (const node of nodes) {
      try {
        if (node && typeof node.disconnect === "function") node.disconnect();
      } catch {
        // already disconnected
      }
    }
    if (workletUrl) {
      try {
        URL.revokeObjectURL(workletUrl);
      } catch {
        // ignore
      }
      workletUrl = "";
    }
  }

  function connectSilent(ctx, node) {
    if (!node || typeof node.connect !== "function") return;
    if (typeof ctx.createGain === "function") {
      const gain = ctx.createGain();
      if (gain.gain) gain.gain.value = 0;
      node.connect(gain);
      if (ctx.destination && typeof gain.connect === "function") gain.connect(ctx.destination);
      captureNodes.push(gain);
      return;
    }
    if (ctx.destination) node.connect(ctx.destination);
  }

  function outputNode(ctx) {
    if (!ctx || typeof ctx.createGain !== "function") return ctx ? ctx.destination : null;
    if (!outputGain) {
      outputGain = ctx.createGain();
      if (outputGain.gain) outputGain.gain.value = outputLevel();
      if (ctx.destination && typeof outputGain.connect === "function") outputGain.connect(ctx.destination);
    } else if (outputGain.gain) {
      outputGain.gain.value = outputLevel();
    }
    return outputGain;
  }

  // Ambient noise: the median of the first second after start, then a slow floor tracker.
  function trackAmbient(e) {
    const t = now();
    if (calibration) {
      calibration.samples.push(e);
      if (t < calibration.until) return;
      const sorted = calibration.samples.slice().sort((a, b) => a - b);
      calibration = null;
      setAmbient(sorted[Math.floor(sorted.length / 2)] || 0, true);
      return;
    }
    if (ambient == null) return;
    // Speech is not room noise: while the VAD hears speech (or is waiting out MIN_UTTERANCE_MS) the floor only
    // falls. Per-frame rising at 2.7 ms worklet frames lifted the gate over the talker within 400 ms.
    if (e > ambient && vad.busy()) return;
    const next = e < ambient ? ambient * 0.9 + e * 0.1 : ambient * 0.998 + e * 0.002;
    if (Math.abs(next - ambient) > ambient * 0.25) setAmbient(next, false);
    else ambient = next;
  }

  function setAmbient(value, calibrated) {
    ambient = value;
    // Not mid-utterance: AudioWorklet frames are 2.7 ms, so the ambient tracker moves often while someone talks,
    // and a fresh VAD would never reach MIN_UTTERANCE_MS (seen with the phone mic). The next ambient change retunes it.
    if (!vad.busy()) vad = createVad({ ...energyVadOptions(), seed: vad.floor() });
    try {
      onAmbient({ rms: ambient, noisy: ambient > NOISY_AMBIENT, calibrated });
    } catch {
      // the page owns the readout
    }
  }

  function emitLevel(energy) {
    const e = Number.isFinite(energy) ? Math.max(0, energy) : 0;
    trackAmbient(e);
    meterSmooth = meterSmooth * 0.72 + e * 0.28;
    const level = Math.min(1, meterSmooth / 0.12);
    try {
      onLevel(level, e);
    } catch {
      // the page owns the meter
    }
  }

  function requestLevelMic() {
    const constraints = audioConstraints(MIC_CONSTRAINTS, micDeviceId);
    if (typeof options.getUserMedia === "function") return Promise.resolve(options.getUserMedia(constraints));
    const md = globalThis.navigator && globalThis.navigator.mediaDevices;
    if (!md || typeof md.getUserMedia !== "function") return Promise.reject(new Error("no mic"));
    return md.getUserMedia(constraints);
  }

  function startBrowserMeter() {
    if (serverMode || !listening) return;
    const token = ++meterGen;
    let pending;
    try {
      pending = requestLevelMic();
    } catch (err) {
      onMicError(deniedKind(err));
      return;
    }
    Promise.resolve(pending).then(
      (stream) => {
        if (token !== meterGen || !listening) {
          stopTracks(stream);
          return;
        }
        meterStream = noteSettings(stream);
        calibration = { until: now() + CALIBRATE_MS, samples: [] };
        const ctx = ensureAudio();
        if (!ctx || typeof ctx.createAnalyser !== "function" || typeof ctx.createMediaStreamSource !== "function") {
          stopBrowserMeter();
          return;
        }
        const source = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 1024;
        if (typeof source.connect === "function") source.connect(analyser);
        const silent = zeroGain(ctx);
        if (silent && typeof analyser.connect === "function") analyser.connect(silent);
        else if (ctx.destination && typeof analyser.connect === "function") analyser.connect(ctx.destination);
        meterNodes.push(source, analyser);
        const size = analyser.fftSize || 1024;
        const bins = new Float32Array(size);
        const tick = () => {
          if (token !== meterGen || !listening) return;
          if (typeof analyser.getFloatTimeDomainData === "function") analyser.getFloatTimeDomainData(bins);
          let sum = 0;
          for (let i = 0; i < bins.length; i += 1) sum += bins[i] * bins[i];
          emitLevel(Math.sqrt(sum / bins.length) || 0);
          meterTimer = setTimeout(tick, 50);
        };
        tick();
      },
      (err) => {
        if (token !== meterGen) return;
        onMicError(micErrorKind(err));
      },
    );
  }

  function stopBrowserMeter() {
    meterGen += 1;
    if (meterTimer) {
      clearTimeout(meterTimer);
      meterTimer = 0;
    }
    const stream = meterStream;
    meterStream = null;
    stopTracks(stream);
    const nodes = meterNodes.splice(0, meterNodes.length);
    for (const node of nodes) {
      try {
        if (node && typeof node.disconnect === "function") node.disconnect();
      } catch {
        // already disconnected
      }
    }
    meterSmooth = 0;
  }

  function zeroGain(ctx) {
    if (!ctx || typeof ctx.createGain !== "function") return null;
    const gain = ctx.createGain();
    if (gain.gain) gain.gain.value = 0;
    if (ctx.destination && typeof gain.connect === "function") gain.connect(ctx.destination);
    meterNodes.push(gain);
    return gain;
  }

  function resumeSynth() {
    if (!synthPaused) return;
    synthPaused = false;
    try {
      if (synth && typeof synth.resume === "function") synth.resume();
    } catch {
      // ignore
    }
  }

  // Applies barge.ducked (already set by nextBarge) to Web Audio gain and speechSynthesis.
  function duck(on) {
    if (outputGain && outputGain.gain) outputGain.gain.value = outputLevel();
    if (on && synthActive && synth && typeof synth.pause === "function") {
      try {
        synth.pause();
        synthPaused = true;
      } catch {
        // ignore
      }
    } else if (!on) {
      resumeSynth();
    }
  }

  function scheduleFlush() {
    if (serverMode) return;
    if (flushTimer) clock.clearTimeout(flushTimer);
    flushTimer = clock.setTimeout(() => {
      flushTimer = 0;
      const pending = lastInterim;
      lastInterim = null;
      if (pending && listening && !early.hasSent(pending.id)) early.final(pending.text, pending.id);
    }, VAD_FLUSH_MS);
  }

  // One VAD event: duck on the first speech frame, stop after minSpeechMs, restore on a misfire,
  // and on the end of speech send the words without waiting for the recognizer's own final.
  function vadEvent(event) {
    if (localSpeech) return;
    const live = speaking && utteranceId != null && bargedUtterance !== utteranceId;
    const result = nextBarge(barge, event, live);
    barge = result.state;
    if (result.action === "duck") duck(true);
    else if (result.action === "unduck") duck(false);
    else if (result.action === "stop") {
      duck(false);
      fireBarge();
    } else if (result.action === "flush") scheduleFlush();
    try {
      onVad(event, now(), result.action);
    } catch {
      // the page owns timing displays
    }
  }

  async function startNeural() {
    if (!neuralWanted || neural || serverMode) return;
    const token = ++neuralGen;
    try {
      const lib = await vadLoader();
      if (token !== neuralGen || !listening || !lib || !lib.MicVAD) return;
      const ctx = ensureAudio();
      const tuned = vadSettings(sensitivity, ambient);
      const instance = await lib.MicVAD.new({
        ...BARGE_VAD,
        positiveSpeechThreshold: tuned.positiveSpeechThreshold,
        negativeSpeechThreshold: tuned.negativeSpeechThreshold,
        minSpeechMs: ambient != null && ambient > NOISY_AMBIENT ? Math.max(tuned.minSpeechMs, NOISY_BARGE_MS) : tuned.minSpeechMs,
        baseAssetPath: VAD_WEB_BASE,
        onnxWASMBasePath: VAD_ORT_BASE,
        ...(ctx ? { audioContext: ctx } : {}),
        getStream: options.vadGetStream || (async () => noteSettings(await requestMic())),
        onSpeechStart: () => vadEvent("start"),
        onSpeechRealStart: () => vadEvent("real_start"),
        onVADMisfire: () => vadEvent("misfire"),
        onSpeechEnd: () => vadEvent("end"),
      });
      if (token !== neuralGen || !listening) {
        destroyVad(instance);
        return;
      }
      neural = instance;
      await instance.start();
    } catch {
      if (token === neuralGen) neural = null;
    }
  }

  function stopNeural() {
    neuralGen += 1;
    const instance = neural;
    neural = null;
    barge = initialBarge();
    lastInterim = null;
    if (flushTimer) {
      clock.clearTimeout(flushTimer);
      flushTimer = 0;
    }
    if (instance) destroyVad(instance);
  }

  function primeSpeech() {
    if (!synth || typeof synth.speak !== "function") return false;
    const utter = makeUtterance(" ");
    utter.volume = 0;
    utter.lang = lang;
    try {
      synth.speak(utter);
      return true;
    } catch {
      return false;
    }
  }

  function ensureAudio() {
    if (audioCtx) return audioCtx;
    try {
      audioCtx = audioContextFactory() || null;
    } catch {
      audioCtx = null;
    }
    return audioCtx;
  }

  function unlockAudio() {
    const ctx = ensureAudio();
    if (!ctx) return Promise.resolve(false);
    if (typeof ctx.resume === "function" && ctx.state !== "running") {
      try {
        return Promise.resolve(ctx.resume()).then(
          () => true,
          () => false,
        );
      } catch {
        return Promise.resolve(false);
      }
    }
    return Promise.resolve(true);
  }

  return {
    supported,
    startListening,
    stopListening,
    speak,
    playPcm,
    cancel,
    isSpeaking,
    isInputBlocked() { return Boolean(localSpeech); },
    isListening() { return listening; },
    neuralVadActive() { return Boolean(neural); },
    setSensitivity(next) {
      sensitivity = vadSettings(next, ambient).sensitivity;
      vad = createVad(energyVadOptions());
      if (neural) {
        stopNeural();
        void startNeural();
      }
      return sensitivity;
    },
    getSensitivity() { return sensitivity; },
    ambient() { return ambient; },
    micSettings() { return micSettings; },
    setMicDevice,
    micDeviceId() { return micDeviceId; },
    micLabel,
    setSinkId,
    setHoldMode(on) {
      holdMode = Boolean(on);
      if (!holdMode && holding) this.holdEnd();
      return holdMode;
    },
    holdMode() { return holdMode; },
    // Press: stop the AI and open an utterance. Release: send it to server Whisper.
    holdStart() {
      if (!serverMode || !listening || localSpeech) return false;
      holding = true;
      dropQueue();
      sentInUtt = 0;
      utteranceOpen = true;
      considerServerBarge();
      return true;
    },
    holdEnd() {
      if (!holding) return false;
      holding = false;
      if (utteranceOpen) drain(true);
      utteranceOpen = false;
      return true;
    },
    vadEvent,
    unlockAudio,
    setVolume,
    setMuted,
    getVolume() {
      return volume;
    },
    getMuted() {
      return muted;
    },
    primeSpeech,
  };
}

function wordsOf(text) {
  const found = String(text || "").toLowerCase().match(/[a-z0-9']+/g);
  return found || [];
}

function transcriptOf(row) {
  if (!row) return "";
  if (row[0] && typeof row[0].transcript === "string") return row[0].transcript;
  if (typeof row.transcript === "string") return row.transcript;
  return "";
}

function makeUtterance(text) {
  const Ctor = globalThis.SpeechSynthesisUtterance;
  if (typeof Ctor === "function") return new Ctor(text);
  return { text };
}

function decodeBase64(base64) {
  const clean = String(base64 || "").replace(/\s+/g, "");
  if (!clean) return new Uint8Array(0);
  const bin = atob(clean);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i) & 0xff;
  return out;
}

function defaultRecognitionFactory() {
  const Ctor = globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition;
  if (typeof Ctor !== "function") return null;
  return new Ctor();
}

// One page-wide context: the one the user's tap unlocked keeps working after the voice is rebuilt
// (for example when browser STT fails and the page switches to server STT without a new tap).
let sharedAudioContext = null;

function defaultAudioContextFactory() {
  if (sharedAudioContext && sharedAudioContext.state !== "closed") return sharedAudioContext;
  const Ctor = globalThis.AudioContext || globalThis.webkitAudioContext;
  if (typeof Ctor !== "function") return null;
  sharedAudioContext = new Ctor();
  return sharedAudioContext;
}

function hasAudioConstructor() {
  return typeof (globalThis.AudioContext || globalThis.webkitAudioContext) === "function";
}

function defaultNow() {
  if (globalThis.performance && typeof performance.now === "function") return performance.now();
  return Date.now();
}

function asFloat32(value) {
  if (value instanceof Float32Array) return value;
  if (!value || typeof value.length !== "number") return new Float32Array(0);
  return Float32Array.from(value);
}

function rms(samples) {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i];
    sum += s * s;
  }
  return Math.sqrt(sum / samples.length);
}

function monoBuffer(buffer) {
  if (!buffer || typeof buffer.getChannelData !== "function") return new Float32Array(0);
  const channels = buffer.numberOfChannels || 1;
  const first = buffer.getChannelData(0);
  if (!first || !first.length) return new Float32Array(0);
  if (channels < 2) return first;
  const out = new Float32Array(first.length);
  for (let c = 0; c < channels; c++) {
    const ch = buffer.getChannelData(c);
    if (!ch) continue;
    for (let i = 0; i < out.length; i++) out[i] += ch[i] || 0;
  }
  const inv = 1 / channels;
  for (let i = 0; i < out.length; i++) out[i] *= inv;
  return out;
}

function encodePcm16(samples) {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < samples.length; i++) view.setInt16(i * 2, samples[i], true);
  let bin = "";
  const step = 8192;
  for (let i = 0; i < bytes.length; i += step) {
    const end = Math.min(bytes.length, i + step);
    let part = "";
    for (let j = i; j < end; j++) part += String.fromCharCode(bytes[j]);
    bin += part;
  }
  return btoa(bin);
}

function createWorkletUrl() {
  const source = `
class PcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0] || input[0].length === 0) return true;
    const ch0 = input[0];
    const channels = input.length;
    if (channels === 1) {
      this.port.postMessage(ch0);
      return true;
    }
    const mixed = new Float32Array(ch0.length);
    for (let c = 0; c < channels; c++) {
      const ch = input[c];
      if (!ch) continue;
      for (let i = 0; i < mixed.length; i++) mixed[i] += ch[i] || 0;
    }
    const inv = 1 / channels;
    for (let i = 0; i < mixed.length; i++) mixed[i] *= inv;
    this.port.postMessage(mixed);
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
`;
  const blob = new Blob([source], { type: "application/javascript" });
  return URL.createObjectURL(blob);
}

function clamp01(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) return 0;
  if (n > 1) return 1;
  return n;
}

function deniedKind(err) {
  const name = err && (err.name || err.code || err.message);
  const text = String(name || "");
  if (
    text === "NotAllowedError" ||
    text === "PermissionDeniedError" ||
    text === "SecurityError" ||
    text === "not-allowed" ||
    text === "audio-capture"
  ) {
    return "denied";
  }
  return "failed";
}

function stopTracks(stream) {
  if (!stream || typeof stream.getTracks !== "function") return;
  for (const track of stream.getTracks()) {
    try {
      track.stop();
    } catch {
      // already stopped
    }
  }
}

function destroyVad(instance) {
  try {
    if (typeof instance.destroy === "function") Promise.resolve(instance.destroy()).catch(noop);
    else if (typeof instance.pause === "function") instance.pause();
  } catch {
    // already gone
  }
}

// On unless the page URL says ?vad=0. Node tests have no location and get the word-based path.
function defaultNeuralWanted() {
  const loc = globalThis.location;
  if (!loc || typeof loc.search !== "string") return false;
  return new URLSearchParams(loc.search).get("vad") !== "0";
}

let vadPromise = null;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const doc = globalThis.document;
    if (!doc) {
      reject(new Error("no document"));
      return;
    }
    const tag = doc.createElement("script");
    tag.src = src;
    tag.crossOrigin = "anonymous";
    tag.onload = () => resolve();
    tag.onerror = () => reject(new Error("load failed"));
    doc.head.appendChild(tag);
  });
}

// vad-web's UMD bundle reads the global `ort`, so onnxruntime-web loads first.
export function loadVadWeb() {
  if (globalThis.vad && globalThis.vad.MicVAD) return Promise.resolve(globalThis.vad);
  if (!vadPromise) {
    vadPromise = loadScript(`${VAD_ORT_BASE}ort.wasm.min.js`)
      .then(() => loadScript(`${VAD_WEB_BASE}bundle.min.js`))
      .then(() => globalThis.vad)
      .catch((err) => {
        vadPromise = null;
        throw err;
      });
  }
  return vadPromise;
}

function noop() {}
