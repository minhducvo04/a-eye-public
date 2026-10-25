import assert from "node:assert/strict";
import test from "node:test";
import { createVad, createVoice, downsample, floatToPcm16, isEcho, pcm16ToFloat32, wordCount } from "./voice.js";

function pcm16Base64(samples) {
  const bytes = Buffer.alloc(samples.length * 2);
  for (let i = 0; i < samples.length; i++) bytes.writeInt16LE(samples[i], i * 2);
  return bytes.toString("base64");
}

function fakeRecognition() {
  return {
    continuous: false,
    interimResults: false,
    lang: "",
    starts: 0,
    stops: 0,
    start() {
      this.starts += 1;
    },
    stop() {
      this.stops += 1;
    },
  };
}

function fakeSynth() {
  return {
    cancels: 0,
    spoken: [],
    cancel() {
      this.cancels += 1;
    },
    speak(utterance) {
      this.spoken.push(utterance);
      this.current = utterance;
    },
  };
}

function fakeContext() {
  const ctx = {
    state: "running",
    currentTime: 0,
    destination: {},
    starts: [],
    stops: 0,
    resumes: 0,
    createBuffer(_channels, length, sampleRate) {
      const data = new Float32Array(length);
      return {
        length,
        sampleRate,
        duration: length / sampleRate,
        getChannelData() {
          return data;
        },
      };
    },
    createBufferSource() {
      const src = {
        buffer: null,
        connect() {},
        start(when) {
          ctx.starts.push(when);
        },
        stop() {
          ctx.stops += 1;
          src.stopped = true;
        },
      };
      return src;
    },
    resume() {
      ctx.resumes += 1;
      ctx.state = "running";
      return Promise.resolve();
    },
  };
  return ctx;
}

function harness(extra = {}) {
  const rec = extra.rec || fakeRecognition();
  const synth = extra.synth || fakeSynth();
  const ctx = extra.ctx || fakeContext();
  const events = {
    finals: [],
    interims: [],
    states: [],
    barges: [],
  };
  let clock = extra.t0 ?? 0;
  const voice = createVoice({
    onFinal: (text) => events.finals.push(text),
    onInterim: (text) => events.interims.push(text),
    onState: (state) => events.states.push(state),
    onBargeIn: (id, atMs) => {
      events.barges.push([id, atMs, voice.isSpeaking()]);
    },
    recognitionFactory: () => rec,
    synth,
    audioContextFactory: () => ctx,
    now: () => clock,
    ...extra.options,
  });
  return {
    rec,
    synth,
    ctx,
    events,
    voice,
    setTime(value) {
      clock = value;
    },
  };
}

function result(transcript, isFinal) {
  return {
    results: [{ 0: { transcript }, isFinal }],
  };
}

test("word count, echo, and a known PCM16 buffer", () => {
  assert.equal(wordCount("  one   two  three "), 3);
  assert.equal(wordCount("hello, world!"), 2);
  assert.equal(wordCount(""), 0);
  assert.equal(isEcho("Switch the PSU off.", "switch the psu off and unplug it"), true);
  assert.equal(isEcho("stop that now", "switch the psu off"), false);
  assert.equal(isEcho("", "hello there"), true);

  const encoded = pcm16Base64([0, 16384, -32768]);
  const decoded = pcm16ToFloat32(encoded);
  assert.equal(decoded.length, 3);
  assert.equal(decoded[0], 0);
  assert.equal(decoded[1], 16384 / 32768);
  assert.equal(decoded[2], -1);
});

test("final and interim callbacks", () => {
  const { rec, voice, events } = harness();
  assert.equal(voice.supported.stt, true);
  assert.equal(voice.supported.tts, true);
  voice.startListening();
  rec.onresult(result("place the fan", false));
  rec.onresult(result("place the fan here", true));
  assert.deepEqual(events.interims, ["place the fan"]);
  assert.deepEqual(events.finals, ["place the fan here"]);
});

test("recognition restarts after end while listening", () => {
  const { rec, voice, events } = harness();
  voice.startListening();
  assert.equal(rec.continuous, true);
  assert.equal(rec.interimResults, true);
  assert.equal(rec.lang, "en-US");
  assert.equal(rec.starts, 1);
  assert.deepEqual(events.states, ["listening"]);
  rec.onend();
  assert.equal(rec.starts, 2);
  voice.stopListening();
  assert.equal(rec.stops, 1);
  rec.onend();
  assert.equal(rec.starts, 2);
  assert.deepEqual(events.states, ["listening", "idle"]);
});

test("speak is speaking until the utterance ends", () => {
  const { synth, voice, events } = harness();
  voice.speak("say-1", "Switch the PSU off.");
  assert.equal(voice.isSpeaking(), true);
  assert.deepEqual(events.states, ["speaking"]);
  assert.equal(synth.spoken.length, 1);
  assert.equal(synth.spoken[0].text, "Switch the PSU off.");
  synth.current.onend();
  assert.equal(voice.isSpeaking(), false);
  assert.deepEqual(events.states, ["speaking", "idle"]);
});

test("cancel stops synth and audio", () => {
  const { synth, ctx, voice, events } = harness();
  voice.speak("say-2", "Check the fan header.");
  voice.playPcm("say-2", pcm16Base64(new Array(1600).fill(0)), false);
  const cancels = synth.cancels;
  voice.cancel();
  assert.equal(synth.cancels, cancels + 1);
  assert.equal(ctx.stops, 1);
  assert.equal(voice.isSpeaking(), false);
  assert.equal(events.states.at(-1), "idle");
});

test("barge-in fires once with id and elapsed ms and stops playback", () => {
  const { rec, synth, ctx, voice, events, setTime } = harness({ t0: 1000 });
  voice.startListening();
  voice.speak("utt-9", "Turn the screw clockwise now.");
  setTime(1000);
  voice.playPcm("utt-9", pcm16Base64(new Array(800).fill(0)), false);
  const cancelsAfterPlay = synth.cancels;
  setTime(1250);
  rec.onresult(result("wait", false));
  assert.equal(events.barges.length, 0);
  assert.equal(voice.isSpeaking(), true);
  rec.onresult(result("wait stop that", false));
  assert.deepEqual(events.barges, [["utt-9", 250, false]]);
  assert.equal(synth.cancels, cancelsAfterPlay + 1);
  assert.equal(ctx.stops, 1);
  assert.equal(voice.isSpeaking(), false);
  assert.deepEqual(events.interims, ["wait", "wait stop that"]);
  rec.onresult(result("wait stop that please", true));
  assert.equal(events.barges.length, 1);
  assert.deepEqual(events.finals, ["wait stop that please"]);
  assert.equal(events.states.at(-1), "listening");
});

test("an echo of the spoken line does not barge in", () => {
  const { rec, synth, voice, events } = harness({ t0: 0 });
  voice.speak("utt-3", "Switch the PSU off and unplug it.");
  const cancels = synth.cancels;
  rec.onresult(result("switch the psu off", false));
  rec.onresult(result("Switch the PSU off and unplug it.", true));
  assert.equal(events.barges.length, 0);
  assert.equal(synth.cancels, cancels);
  assert.equal(voice.isSpeaking(), true);
  assert.deepEqual(events.interims, []);
  assert.deepEqual(events.finals, []);
});

test("PCM16 chunks queue back to back", () => {
  const { ctx, voice } = harness();
  const first = new Array(800).fill(0);
  const second = new Array(1600).fill(1000);
  voice.playPcm("aud-1", pcm16Base64(first), false);
  voice.playPcm("aud-1", pcm16Base64(second), true);
  assert.equal(ctx.starts.length, 2);
  assert.equal(ctx.starts[0], 0);
  assert.equal(ctx.starts[1] - ctx.starts[0], first.length / 16000);
  assert.equal(ctx.starts[1], first.length / 16000);
});

test("unlockAudio resumes a suspended context", async () => {
  const ctx = fakeContext();
  ctx.state = "suspended";
  const { voice } = harness({ ctx });
  const ok = await voice.unlockAudio();
  assert.equal(ok, true);
  assert.equal(ctx.resumes, 1);
  assert.equal(ctx.state, "running");
});

test("downsample 48 kHz to 16 kHz length and values", () => {
  const second = new Float32Array(48000);
  second.fill(0.25);
  const full = downsample(second, 48000, 16000);
  assert.equal(full.length, 16000);
  assert.equal(full[0], 0.25);
  assert.equal(full[15999], 0.25);

  const ramp = new Float32Array(48);
  for (let i = 0; i < ramp.length; i++) ramp[i] = i;
  const out = downsample(ramp, 48000, 16000);
  assert.equal(out.length, 16);
  assert.equal(out[0], 1);
  assert.equal(out[1], 4);
  assert.equal(out[15], 46);
});

test("floatToPcm16 clamps to int16", () => {
  const pcm = floatToPcm16(new Float32Array([-1.5, -1, -0.5, 0, 0.5, 1, 1.5]));
  assert.equal(pcm[0], -32768);
  assert.equal(pcm[1], -32768);
  assert.equal(pcm[2], -16384);
  assert.equal(pcm[3], 0);
  assert.equal(pcm[4], 16384);
  assert.equal(pcm[5], 32767);
  assert.equal(pcm[6], 32767);
});

test("VAD start, hang, end, and adaptive floor on a fake clock", () => {
  const vad = createVad({ threshold: 0.01, hangMs: 400 });
  assert.equal(vad.push(0.2, 0), "silence");
  assert.equal(vad.push(0.05, 20), "silence");
  assert.equal(vad.push(0.5, 40), "start");
  assert.equal(vad.push(0.5, 80), "speech");
  assert.equal(vad.push(0, 80 + 399), "speech");
  assert.equal(vad.push(0, 80 + 400), "end");
  assert.equal(vad.push(0, 900), "silence");

  const quiet = createVad({ threshold: 0.01, hangMs: 400 });
  assert.equal(quiet.push(0.001, 0), "silence");
  assert.equal(quiet.push(0.05, 30), "start");
});

function serverCapture() {
  const ctx = fakeContext();
  ctx.sampleRate = 48000;
  let proc = null;
  ctx.createMediaStreamSource = () => ({ connect() {} });
  ctx.createScriptProcessor = () => {
    proc = { connect() {} };
    return proc;
  };
  const stops = [];
  const constraints = [];
  return {
    ctx,
    stops,
    constraints,
    proc: () => proc,
    getUserMedia: async (asked) => {
      constraints.push(asked);
      return { getTracks: () => [{ stop() { stops.push(1); } }] };
    },
    feed(n, amp, at, clock) {
      clock.t = at;
      const data = new Float32Array(n);
      data.fill(amp);
      proc.onaudioprocess({
        inputBuffer: {
          sampleRate: 48000,
          numberOfChannels: 1,
          getChannelData() {
            return data;
          },
        },
      });
    },
  };
}

test("server mode sends 200 ms chunks then a final chunk", async () => {
  const cap = serverCapture();
  const chunks = [];
  const clock = { t: 0 };
  const voice = createVoice({
    sttMode: "server",
    audioContextFactory: () => cap.ctx,
    getUserMedia: cap.getUserMedia,
    now: () => clock.t,
    onAudioChunk(b64, seq, final) {
      chunks.push({ b64, seq, final, n: pcm16ToFloat32(b64).length });
    },
  });
  await voice.startListening();
  assert.deepEqual(cap.constraints, [{ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1, sampleRate: { ideal: 16000 } } }]);
  cap.feed(48, 0, 0, clock);
  // 21000 samples at 48 kHz in ten frames; the first 400 ms wait in the preroll, then all of it is sent.
  for (let i = 0; i < 10; i++) cap.feed(2100, 0.5, 10 + i * 50, clock);
  cap.feed(48, 0, 900, clock);
  assert.equal(chunks.length, 3);
  assert.deepEqual(
    chunks.map((chunk) => [chunk.seq, chunk.final, chunk.n]),
    [
      [0, false, 3200],
      [1, false, 3200],
      [2, true, 616], // includes the 16 preroll samples from before speech
    ],
  );
  assert.equal(pcm16ToFloat32(chunks[0].b64)[16], 0.5);
  voice.stopListening();
  assert.equal(cap.stops.length, 1);
  cap.feed(21000, 0.5, 2000, clock);
  assert.equal(chunks.length, 3);
});

test("server mode barge-in fires once", async () => {
  const cap = serverCapture();
  const synth = fakeSynth();
  const clock = { t: 1000 };
  const events = { barges: [] };
  const voice = createVoice({
    sttMode: "server",
    synth,
    audioContextFactory: () => cap.ctx,
    getUserMedia: cap.getUserMedia,
    now: () => clock.t,
    onBargeIn(id, atMs) {
      events.barges.push([id, atMs, voice.isSpeaking()]);
    },
    onAudioChunk() {},
  });
  voice.speak("utt-9", "Turn the screw clockwise now.");
  voice.playPcm("utt-9", pcm16Base64(new Array(800).fill(0)), false);
  assert.equal(voice.isSpeaking(), true);
  const cancels = synth.cancels;
  await voice.startListening();
  cap.feed(48, 0, 1250, clock);
  cap.feed(480, 0.5, 1250, clock);
  assert.deepEqual(events.barges, []); // under MIN_UTTERANCE_MS: noise until proven speech
  cap.feed(480, 0.5, 1650, clock);
  assert.deepEqual(events.barges, [["utt-9", 650, false]]);
  assert.equal(synth.cancels, cancels + 1);
  assert.equal(cap.ctx.stops, 1);
  assert.equal(voice.isSpeaking(), false);
  cap.feed(480, 0.5, 1700, clock);
  assert.equal(events.barges.length, 1);
});

test("missing speech APIs report unsupported and do not throw", () => {
  const voice = createVoice({
    recognitionFactory: () => null,
    synth: null,
    onFinal() {},
    onInterim() {},
    onState() {},
    onBargeIn() {},
  });
  assert.deepEqual(voice.supported, { stt: false, tts: false });
  voice.startListening();
  voice.speak("x", "hi");
  voice.cancel();
  assert.equal(voice.isSpeaking(), false);
});

test('browser early send is once per result, and muting cancels pending speech', () => {
  let task = null;
  const clock = { setTimeout(fn, ms) { assert.equal(ms, 700); task = fn; return 1; }, clearTimeout() { task = null; } };
  const { rec, voice, events } = harness({ options: { clock } });
  voice.startListening();
  rec.onresult(result('place the fan', false));
  task();
  rec.onresult(result('place the fan here please', true));
  assert.deepEqual(events.finals, ['place the fan']);
  rec.onend();
  rec.onresult(result('new question', false));
  assert.ok(task);
  voice.stopListening();
  assert.equal(task, null);
  rec.onresult(result('new question', true));
  assert.deepEqual(events.finals, ['place the fan']);
});

test('late early-final recognition cannot barge into the reply; sound timing waits for playback', () => {
  let fire;
  const starts = [];
  const { rec, voice, synth, events } = harness({ options: {
    clock: { setTimeout(fn) { fire = fn; return 1; }, clearTimeout() {} },
    onSpeakStart: id => starts.push(id),
  } });
  voice.startListening();
  rec.onresult(result('where is the fan', false));
  fire();
  voice.speak('reply', 'Look at the top edge.');
  assert.deepEqual(starts, []);
  synth.current.onstart();
  assert.deepEqual(starts, ['reply']);
  rec.onresult(result('where is the fan header', true));
  assert.deepEqual(events.barges, []);
  assert.equal(voice.isListening(), true);
  voice.stopListening();
  assert.equal(voice.isListening(), false);
});

function fakeVadLib() {
  const lib = { options: null, started: 0, destroyed: 0 };
  lib.MicVAD = {
    async new(options) {
      lib.options = options;
      return {
        start() { lib.started += 1; },
        destroy() { lib.destroyed += 1; },
      };
    },
  };
  return lib;
}

function manualClock() {
  const timers = new Map();
  let next = 1;
  return {
    setTimeout(fn, ms) { timers.set(next, fn); return next++; },
    clearTimeout(id) { timers.delete(id); },
    runAll() { for (const [id, fn] of [...timers]) { timers.delete(id); fn(); } },
    size() { return timers.size; },
  };
}

test("neural VAD loads pinned v5 settings and stops on stop listening", async () => {
  const lib = fakeVadLib();
  const h = harness({ options: { neuralVad: true, vadLoader: async () => lib } });
  h.voice.startListening();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(lib.started, 1);
  assert.equal(lib.options.model, "v5");
  assert.equal(lib.options.minSpeechMs, 120);
  assert.match(lib.options.baseAssetPath, /vad-web@0\.0\.31\/dist\/$/);
  assert.match(lib.options.onnxWASMBasePath, /onnxruntime-web@1\.22\.0\/dist\/$/);
  assert.equal(h.voice.neuralVadActive(), true);
  h.voice.stopListening();
  assert.equal(lib.destroyed, 1);
  assert.equal(h.voice.neuralVadActive(), false);
});

test("VAD speech start pauses speech, real start barges at once, misfire resumes", () => {
  const synth = fakeSynth();
  synth.pauses = 0;
  synth.resumes = 0;
  synth.pause = () => { synth.pauses += 1; };
  synth.resume = () => { synth.resumes += 1; };
  const h = harness({ synth, options: { neuralVad: false } });
  h.voice.startListening();
  h.voice.speak("u1", "The cable goes in the CPU fan header.");
  h.voice.vadEvent("start");
  assert.equal(synth.pauses, 1);
  h.voice.vadEvent("misfire");
  assert.equal(synth.resumes, 1);
  assert.equal(h.events.barges.length, 0);
  assert.equal(h.voice.isSpeaking(), true);
  h.voice.vadEvent("start");
  h.setTime(130);
  h.voice.vadEvent("real_start");
  assert.equal(h.events.barges.length, 1);
  assert.equal(h.events.barges[0][0], "u1");
  assert.equal(h.voice.isSpeaking(), false);
  assert.ok(synth.cancels >= 1);
  assert.equal(synth.resumes, 2); // resumed before cancel so the next line is not stuck paused
});

test("VAD end of speech sends the latest words before the recognizer's final", () => {
  const clock = manualClock();
  const h = harness({ options: { neuralVad: false, clock } });
  h.voice.startListening();
  h.voice.vadEvent("start");
  h.voice.vadEvent("real_start");
  h.rec.onresult(result("where does this go", false));
  h.voice.vadEvent("end");
  h.rec.onresult(result("where does this go now", false));
  clock.runAll();
  assert.deepEqual(h.events.finals, ["where does this go now"]);
  h.rec.onresult({ results: [{ 0: { transcript: "where does this go now" }, isFinal: true }] });
  assert.deepEqual(h.events.finals, ["where does this go now"]);
});

test("ducked Web Audio playback stays ducked for chunks that arrive mid-duck", () => {
  const ctx = fakeContext();
  ctx.gains = [];
  ctx.createGain = () => {
    const node = { gain: { value: 1 }, connect() {}, disconnect() {} };
    ctx.gains.push(node);
    return node;
  };
  const h = harness({ ctx, options: { neuralVad: false } });
  h.voice.startListening();
  h.voice.playPcm("u2", pcm16Base64([1000, 2000, 3000, 4000]), false);
  const out = ctx.gains[0];
  h.voice.vadEvent("start");
  assert.ok(out.gain.value < 0.2);
  h.voice.playPcm("u2", pcm16Base64([1000, 2000]), false);
  assert.ok(out.gain.value < 0.2);
  h.voice.vadEvent("misfire");
  assert.equal(out.gain.value, 1);
});

test("Brave-style recognizer: network error or quick empty ends switch to server STT", () => {
  const errors = [];
  const h = harness({ options: { neuralVad: false, onMicError: (kind) => errors.push(kind) } });
  h.voice.startListening();
  h.rec.onerror({ error: "network" });
  assert.deepEqual(errors, ["unsupported"]);
  assert.equal(h.voice.isListening(), false);

  const quick = [];
  const q = harness({ options: { neuralVad: false, onMicError: (kind) => quick.push(kind) } });
  q.voice.startListening();
  for (let i = 0; i < 3; i++) {
    q.setTime(100 * (i + 1));
    q.rec.onend();
  }
  assert.deepEqual(quick, ["unsupported"]);

  const fine = [];
  const f = harness({ options: { neuralVad: false, onMicError: (kind) => fine.push(kind) } });
  f.voice.startListening();
  f.rec.onresult(result("hello there", false));
  for (let i = 0; i < 3; i++) f.rec.onend();
  f.rec.onerror({ error: "no-speech" });
  assert.deepEqual(fine, ["no_speech"]);
  assert.equal(f.voice.isListening(), true);
});

test("hold to talk: VAD ignored, press opens, release sends one final utterance", async () => {
  const cap = serverCapture();
  const chunks = [];
  const clock = { t: 0 };
  const voice = createVoice({
    sttMode: "server",
    holdToTalk: true,
    audioContextFactory: () => cap.ctx,
    getUserMedia: cap.getUserMedia,
    now: () => clock.t,
    onAudioChunk(b64, seq, final) {
      chunks.push({ final, n: pcm16ToFloat32(b64).length });
    },
  });
  await voice.startListening();
  cap.feed(9600, 0.5, 10, clock); // loud talk without the button: nothing is sent
  assert.equal(chunks.length, 0);
  assert.equal(voice.holdStart(), true);
  cap.feed(9600, 0.01, 100, clock); // soft, far speech still goes out while held
  assert.equal(voice.holdEnd(), true);
  assert.deepEqual(chunks.map((c) => c.final), [false, true]);
  assert.equal(chunks.reduce((n, c) => n + c.n, 0), 3200);
  voice.stopListening();
});

test("ambient calibration: first second sets the noise floor and flags a loud room", async () => {
  const cap = serverCapture();
  const seen = [];
  const clock = { t: 0 };
  const voice = createVoice({
    sttMode: "server",
    audioContextFactory: () => cap.ctx,
    getUserMedia: cap.getUserMedia,
    now: () => clock.t,
    onAmbient: (a) => seen.push(a),
  });
  await voice.startListening();
  for (let t = 0; t <= 1100; t += 100) cap.feed(480, 0.05, t, clock);
  assert.equal(seen.length >= 1, true);
  assert.equal(seen[0].calibrated, true);
  assert.equal(seen[0].noisy, true);
  assert.ok(Math.abs(voice.ambient() - 0.05) < 0.005);
  assert.equal(voice.setSensitivity(42), 10);
  voice.stopListening();
});

test("createVad minMs: a short noise burst never starts speech; sustained speech does", () => {
  const vad = createVad({ threshold: 0.02, hangMs: 400, minMs: 300 });
  vad.push(0, 0);
  assert.equal(vad.push(0.2, 10), "silence");
  assert.equal(vad.push(0.2, 200), "silence");
  assert.equal(vad.push(0, 250), "silence"); // burst over: reset
  assert.equal(vad.push(0.2, 300), "silence");
  assert.equal(vad.push(0.2, 610), "start");
});

test("chosen mic: exact deviceId, live switch restarts capture, missing device reports missing", async () => {
  const cap = serverCapture();
  const errors = [];
  const voice = createVoice({
    sttMode: "server",
    micDeviceId: "pods",
    audioContextFactory: () => cap.ctx,
    getUserMedia: cap.getUserMedia,
    now: () => 0,
    onMicError: (kind) => errors.push(kind),
  });
  await voice.startListening();
  assert.deepEqual(cap.constraints[0].audio.deviceId, { exact: "pods" });
  assert.equal(cap.constraints[0].audio.noiseSuppression, true);
  voice.setMicDevice("mac");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(cap.stops.length, 1, "old track stopped");
  assert.deepEqual(cap.constraints[1].audio.deviceId, { exact: "mac" });
  voice.stopListening();
  assert.equal(cap.stops.length, 2);

  const gone = createVoice({
    sttMode: "server",
    micDeviceId: "pods",
    audioContextFactory: () => cap.ctx,
    getUserMedia: async () => { throw Object.assign(new Error("x"), { name: "OverconstrainedError" }); },
    now: () => 0,
    onMicError: (kind) => errors.push(kind),
  });
  await gone.startListening();
  assert.deepEqual(errors, ["missing"]);
});

test("loud room: a short noise burst sends nothing; barge-in needs 700 ms of sustained speech", async () => {
  const cap = serverCapture();
  const chunks = [];
  const barges = [];
  const clock = { t: 0 };
  const voice = createVoice({
    sttMode: "server",
    synth: fakeSynth(),
    audioContextFactory: () => cap.ctx,
    getUserMedia: cap.getUserMedia,
    now: () => clock.t,
    onBargeIn: (id) => barges.push(id),
    onAudioChunk: (b64, seq, final) => chunks.push(final),
  });
  await voice.startListening();
  for (let t = 0; t <= 1100; t += 100) cap.feed(480, 0.03, t, clock); // calibrates a loud floor
  voice.playPcm("u1", pcm16Base64(new Array(800).fill(0)), false);
  cap.feed(480, 0.5, 1200, clock);
  cap.feed(480, 0.5, 1450, clock);   // 250 ms burst
  cap.feed(480, 0.03, 1500, clock);
  cap.feed(480, 0.03, 2000, clock);
  assert.deepEqual(chunks, []);
  assert.deepEqual(barges, []);
  for (let t = 2100; t <= 2600; t += 100) cap.feed(480, 0.5, t, clock); // 500 ms: an utterance, no barge yet
  assert.deepEqual(barges, []);
  cap.feed(480, 0.5, 2800, clock);   // 700 ms sustained
  assert.deepEqual(barges, ["u1"]);
  voice.stopListening();
});

test("speech in 2.7 ms worklet-sized frames still opens an utterance (ambient moves while talking)", async () => {
  // Seen with the phone mic: each ambient change rebuilt the VAD mid-speech, so 400 ms was never reached.
  const cap = serverCapture();
  const chunks = [];
  const clock = { t: 0 };
  const voice = createVoice({
    sttMode: "server",
    audioContextFactory: () => cap.ctx,
    getUserMedia: cap.getUserMedia,
    now: () => clock.t,
    onAudioChunk: (b64, seq, final) => chunks.push(final),
  });
  await voice.startListening();
  let t = 0;
  for (; t <= 1200; t += 2.7) cap.feed(128, 0.001, t, clock); // quiet room calibrates
  for (; t <= 3200; t += 2.7) cap.feed(128, 0.3, t, clock);   // two seconds of talk
  for (; t <= 4000; t += 2.7) cap.feed(128, 0.001, t, clock); // silence ends it
  assert.ok(chunks.length >= 2, `chunks ${chunks.length}`);
  assert.equal(chunks.at(-1), true);
  voice.stopListening();
});

test("speech with syllable dips in 2.7 ms frames still reaches the 400 ms floor", () => {
  // Real replay (2026-10-24): every dip under the gate restarted the 400 ms wait, so a spoken question
  // never opened an utterance in server STT mode.
  const vad = createVad({ threshold: 0.02, hangMs: 400, minMs: 400 });
  vad.push(0.001, 0);
  let verdict = "silence";
  let t = 10;
  for (; t < 600 && verdict !== "start"; t += 2.7) {
    const inDip = Math.floor(t / 60) % 2 === 1 && (t % 60) < 20; // 20 ms dips every 120 ms
    verdict = vad.push(inDip ? 0.005 : 0.2, t);
  }
  assert.equal(verdict, "start");
  assert.ok(t >= 400 && t < 470, `started at ${t}`);
  // A burst followed by a long gap is still noise.
  const burst = createVad({ threshold: 0.02, hangMs: 400, minMs: 400 });
  burst.push(0.001, 0);
  for (let u = 10; u < 260; u += 2.7) burst.push(0.2, u);
  for (let u = 260; u < 700; u += 2.7) assert.equal(burst.push(0.001, u), "silence");
});


test("family local speech blocks commands and VAD until completion, then accepts input", async () => {
  const h = harness({ options: { neuralVad: false } });
  const local = { localService: true, lang: "en-US" };
  h.synth.getVoices = () => [local];
  h.voice.startListening();
  const done = h.voice.speak("family", "Friend replied. stop", { localOnly: true });
  h.rec.onresult(result("stop", true));
  h.rec.onresult(result("find a replacement part", true));
  h.voice.vadEvent("start");
  h.voice.vadEvent("real_start");
  assert.deepEqual(h.events.finals, []);
  assert.deepEqual(h.events.barges, []);
  assert.equal(h.synth.current.voice, local);
  h.synth.current.onend();
  await done;
  h.rec.onresult(result("find a replacement part", true));
  assert.deepEqual(h.events.finals, ["find a replacement part"]);
  h.voice.stopListening();
});

test("family local speech drops server mic audio, including preroll, until completion", async () => {
  const cap = serverCapture(), chunks = [], clock = { t: 0 }, synth = fakeSynth();
  synth.getVoices = () => [{ localService: true, lang: "en-US" }];
  const voice = createVoice({
    sttMode: "server", synth, neuralVad: false,
    audioContextFactory: () => cap.ctx, getUserMedia: cap.getUserMedia,
    now: () => clock.t, onAudioChunk: (...args) => chunks.push(args),
  });
  await voice.startListening();
  for (let t = 0; t <= 1100; t += 100) cap.feed(480, 0.001, t, clock);
  const done = voice.speak("family", "Friend replied. stop", { localOnly: true });
  for (let t = 1200; t < 2700; t += 50) cap.feed(2400, 0.5, t, clock);
  assert.equal(chunks.length, 0);
  assert.equal(voice.holdStart(), false);
  synth.current.onend();
  await done;
  cap.feed(48, 0, 2800, clock);
  assert.deepEqual(chunks, []);
  for (let t = 2900; t < 3900; t += 50) cap.feed(2400, 0.5, t, clock);
  cap.feed(48, 0, 4400, clock);
  assert.ok(chunks.length > 0);
  assert.equal(chunks.at(-1)[2], true);
  voice.stopListening();
});


test("family read aloud fails closed on missing local voice, error, or cancellation", async () => {
  const h = harness();
  h.synth.getVoices = () => [{ localService: false, lang: "en-US" }];
  await assert.rejects(h.voice.speak("family", "note", { localOnly: true }), /on-device/);
  assert.equal(h.synth.spoken.length, 0);
  h.synth.getVoices = () => [{ localService: true, lang: "en-US" }];
  for (const finish of [() => h.synth.current.onerror(), () => h.voice.cancel()]) {
    const done = h.voice.speak("family", "note", { localOnly: true });
    const rejected = assert.rejects(done, /did not finish/);
    finish();
    await rejected;
    assert.equal(h.voice.isInputBlocked(), false);
  }
});
