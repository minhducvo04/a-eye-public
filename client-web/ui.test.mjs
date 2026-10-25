import assert from "node:assert/strict";
import test from "node:test";
import {
  AUDIO_FALLBACK_MS,
  CAPTION_FADE_MS,
  ERROR_HTTPS,
  ERROR_MIC,
  LABEL,
  NOTICE_BROWSER_VOICE,
  NOTICE_SERVER_STT,
  captionModel,
  createCallUi,
  emptyCaption,
  fallbackDecision,
  initialCallState,
  nextCallState,
  readAudioPrefs,
  stateLabel,
  transcriptModel,
  writeAudioPrefs,
} from "./ui.js";

function memoryStorage(seed = {}) {
  const data = { ...seed };
  return {
    getItem(key) {
      return Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null;
    },
    setItem(key, value) {
      data[key] = String(value);
    },
    dump: data,
  };
}

test("nextCallState covers pack, tap, mic, and the state label", () => {
  let state = nextCallState(null, { type: "pack", pack: "pc_build" });
  assert.equal(state.pack, "pc_build");
  assert.equal(state.showStart, true);
  assert.equal(state.started, false);
  assert.equal(state.label, LABEL.idle);
  assert.equal(stateLabel(state), "Tap the mic to talk");

  state = nextCallState(state, { type: "tap_start" });
  assert.equal(state.started, true);
  assert.equal(state.showStart, false);
  state = nextCallState(state, { type: "pack", pack: "cooking" });
  assert.equal(state.showStart, false);

  state = nextCallState(state, { type: "agent_state", agent: "helper", state: "thinking" });
  assert.equal(state.label, "Thinking…");
  assert.equal(state.phase, "thinking");

  state = nextCallState(state, { type: "mic_start" });
  assert.equal(state.micOn, true);
  assert.equal(state.label, "Thinking…");
  state = nextCallState(state, { type: "interim", text: "wait", now: 5 });
  assert.equal(state.userTalking, true);
  assert.equal(state.label, "Listening…");
  assert.equal(stateLabel(state), "Listening…");
  state = nextCallState(state, { type: "final", text: "wait", now: 20 });
  assert.equal(state.userTalking, false);
  assert.equal(state.label, "Thinking…");

  state = nextCallState(state, { type: "mic_stop" });
  assert.equal(state.micOn, false);
  assert.equal(state.label, "Thinking…");

  state = nextCallState(state, { type: "agent_state", agent: "shopper", state: "speaking" });
  assert.equal(state.label, "Speaking…");
  state = nextCallState(state, { type: "agent_state", agent: "shopper", state: "idle" });
  state = nextCallState(state, { type: "agent_state", agent: "helper", state: "idle" });
  assert.equal(state.label, "Tap the mic to talk");
  assert.equal(stateLabel(initialCallState()), LABEL.idle);

  state = nextCallState(state, { type: "agent_state", agent: "helper", state: "listening" });
  assert.equal(state.label, "Listening…");
  state = nextCallState(state, { type: "agent_state", agent: "nope", state: "speaking" });
  assert.equal(state.helperPhase, "listening");
  assert.equal(state.label, "Listening…");
});

test("caption model types interim text, replaces it with the final, then fades", () => {
  let caption = captionModel(emptyCaption(), { type: "interim", text: "place the", now: 10 });
  assert.equal(caption.kind, "interim");
  assert.equal(caption.text, "place the");
  assert.equal(caption.visible, true);
  caption = captionModel(caption, { type: "interim", text: "place the fan", now: 40 });
  assert.equal(caption.text, "place the fan");
  assert.equal(caption.kind, "interim");
  caption = captionModel(caption, { type: "tick", now: 40 + CAPTION_FADE_MS });
  assert.equal(caption.visible, true);

  caption = captionModel(caption, { type: "final", text: "place the fan here", now: 100 });
  assert.equal(caption.kind, "final");
  assert.equal(caption.text, "place the fan here");
  assert.equal(caption.visible, true);
  const early = captionModel(caption, { type: "tick", now: 100 + CAPTION_FADE_MS - 1 });
  assert.equal(early.visible, true);
  assert.equal(early, caption);
  const faded = captionModel(caption, { type: "tick", now: 100 + CAPTION_FADE_MS });
  assert.equal(faded.visible, false);
  assert.equal(faded.text, "place the fan here");
  assert.equal(captionModel(faded, { type: "tick", now: 5000 }), faded);

  let state = nextCallState(null, { type: "interim", text: "wait", now: 1 });
  assert.equal(state.caption.kind, "interim");
  assert.equal(state.transcript.length, 0);
  state = nextCallState(state, { type: "final", text: "wait stop", now: 50 });
  assert.equal(state.caption.kind, "final");
  assert.equal(state.caption.text, "wait stop");
  state = nextCallState(state, { type: "tick", now: 50 + CAPTION_FADE_MS });
  assert.equal(state.caption.visible, false);
});

test("transcript keeps the newest 50 lines and names You, Helper, and Shopper", () => {
  let lines = transcriptModel([], { type: "final", text: "hello" });
  assert.equal(lines[0].speaker, "You");
  lines = transcriptModel(lines, { type: "say", agent: "helper", text: "Switch it off." });
  lines = transcriptModel(lines, { type: "say", agent: "shopper", text: "This fan fits." });
  assert.deepEqual(
    lines.map((line) => line.speaker),
    ["You", "Helper", "Shopper"],
  );
  assert.equal(transcriptModel(lines, { type: "final", text: "   " }).length, 3);

  let state = initialCallState();
  for (let i = 1; i <= 60; i += 1) {
    state = nextCallState(state, { type: "final", text: `line ${i}`, now: i });
  }
  assert.equal(state.transcript.length, 50);
  assert.equal(state.transcript[0].text, "line 11");
  assert.equal(state.transcript[0].speaker, "You");
  assert.equal(state.transcript[49].text, "line 60");
  state = nextCallState(state, {
    type: "say",
    agent: "shopper",
    utteranceId: "s1",
    text: "Offer",
    part: 0,
    now: 61,
  });
  assert.equal(state.transcript.length, 50);
  assert.equal(state.transcript[49].speaker, "Shopper");
  assert.equal(state.transcript[49].text, "Offer");
  assert.equal(state.replies[0].speaker, "Shopper");
  assert.equal(state.replies[0].active, false);
});

test("say parts highlight when audio starts, and fallback waits 1.5 s", () => {
  let state = nextCallState(null, {
    type: "say",
    agent: "helper",
    utteranceId: "u1",
    part: 0,
    turnId: "turn:1",
    text: "First sentence.",
    now: 1000,
  });
  state = nextCallState(state, {
    type: "say",
    agent: "helper",
    utteranceId: "u2",
    part: 1,
    turnId: "turn:1",
    text: "Second sentence.",
    now: 1100,
  });
  assert.equal(state.replies.length, 2);
  assert.equal(state.replies[0].speaker, "Helper");
  assert.equal(fallbackDecision(1000, null, 1000 + AUDIO_FALLBACK_MS - 1).fallback, false);
  assert.equal(fallbackDecision(1000, null, 1000 + AUDIO_FALLBACK_MS).fallback, true);
  assert.equal(fallbackDecision(1000, 1200, 9000).fallback, false);

  const early = nextCallState(state, { type: "audio_timeout", utteranceId: "u1", now: 1000 + 1499 });
  assert.equal(early.waits.u1.fellBack, false);
  assert.equal(early.voiceNotice, "");

  const heard = nextCallState(state, { type: "audio_started", utteranceId: "u1", now: 1300 });
  assert.equal(heard.replies[0].active, true);
  assert.equal(heard.replies[1].active, false);
  assert.equal(heard.waits.u1.audioSeenMs, 1300);
  const noFallback = nextCallState(heard, { type: "audio_timeout", utteranceId: "u1", now: 9000 });
  assert.equal(noFallback.waits.u1.fellBack, false);
  assert.equal(noFallback.voiceNotice, "");

  const timed = nextCallState(state, { type: "audio_timeout", utteranceId: "u2", now: 1100 + AUDIO_FALLBACK_MS });
  assert.equal(timed.waits.u2.fellBack, true);
  assert.equal(timed.replies[1].active, true);
  assert.equal(timed.voiceNotice, NOTICE_BROWSER_VOICE);
  const again = nextCallState(timed, { type: "audio_error", utteranceId: "u2", now: 4000 });
  assert.equal(again.voiceNotice, NOTICE_BROWSER_VOICE);
  assert.equal(again.waits.u1.fellBack, false);

  const broken = nextCallState(state, { type: "audio_error", utteranceId: "u1", now: 1010 });
  assert.equal(broken.waits.u1.fellBack, true);
  assert.equal(broken.voiceNotice, NOTICE_BROWSER_VOICE);
  assert.equal(broken.replies[0].active, true);

  const nextTurn = nextCallState(broken, {
    type: "say",
    agent: "helper",
    utteranceId: "u3",
    part: 0,
    turnId: "turn:2",
    text: "New turn.",
    now: 5000,
  });
  assert.equal(nextTurn.replies.length, 1);
  assert.equal(nextTurn.replies[0].text, "New turn.");
  assert.equal(nextTurn.waits.u1, undefined);
});

test("permission, https, and missing speech recognition are visible", () => {
  let state = nextCallState(null, { type: "mic_start" });
  state = nextCallState(state, { type: "permission_denied" });
  assert.equal(state.localListening, false);
  assert.equal(state.error, ERROR_MIC);
  assert.equal(state.label, "Tap the mic to talk");

  state = nextCallState(state, { type: "https" });
  assert.equal(state.error, ERROR_HTTPS);

  state = nextCallState(initialCallState(), { type: "stt_unsupported" });
  assert.equal(state.sttMode, "server");
  assert.equal(state.needsHello, true);
  assert.equal(state.sttNotice, NOTICE_SERVER_STT);
  state = nextCallState(state, { type: "hello_sent" });
  assert.equal(state.needsHello, false);
  assert.equal(state.sttNotice, NOTICE_SERVER_STT);
});

test("mute and volume persist, and a throwing storage stays quiet", () => {
  const mem = memoryStorage();
  const ui = createCallUi({ storage: mem });
  assert.equal(ui.getState().muted, false);
  assert.equal(ui.getState().volume, 1);
  ui.dispatch({ type: "mute", muted: true });
  ui.dispatch({ type: "volume", volume: 1.4 });
  assert.equal(ui.getState().muted, true);
  assert.equal(ui.getState().volume, 1);
  ui.dispatch({ type: "volume", volume: 0.35 });
  assert.deepEqual(readAudioPrefs(mem), { muted: true, volume: 0.35 });
  const again = createCallUi({ storage: mem });
  assert.equal(again.getState().muted, true);
  assert.equal(again.getState().volume, 0.35);

  const broken = {
    getItem() {
      throw new Error("denied");
    },
    setItem() {
      throw new Error("denied");
    },
  };
  const quiet = createCallUi({ storage: broken });
  assert.equal(quiet.getState().muted, false);
  assert.equal(quiet.getState().volume, 1);
  assert.doesNotThrow(() => quiet.dispatch({ type: "mute", muted: true }));
  assert.doesNotThrow(() => quiet.dispatch({ type: "volume", volume: 0.2 }));
  assert.equal(quiet.getState().muted, true);
  assert.equal(quiet.getState().volume, 0.2);
  assert.equal(readAudioPrefs(broken).volume, 1);
  assert.equal(writeAudioPrefs(broken, { muted: true, volume: 0.2 }), false);
  assert.equal(writeAudioPrefs(null, { muted: true, volume: 0.5 }), false);
});

test('reply deltas grow before audio, preserve the unfinished suffix, and retain sentence highlighting', async () => {
  const { replyLines } = await import('./ui.js');
  let s = nextCallState(null, { type: 'reply_delta', turnId: 'turn:a', text: 'First.' });
  s = nextCallState(s, { type: 'reply_delta', turnId: 'turn:a', text: ' Second' });
  assert.equal(replyLines(s)[0].text, 'First. Second');
  s = nextCallState(s, { type: 'say', turnId: 'turn:a', utteranceId: 'a0', text: 'First.', part: 0 });
  s = nextCallState(s, { type: 'audio_started', utteranceId: 'a0', now: 200 });
  assert.deepEqual(replyLines(s).map(l => [l.text, l.active]), [['First.', true], ['Second', false]]);
  s = nextCallState(s, { type: 'reply_delta', turnId: 'turn:a', text: '.' });
  s = nextCallState(s, { type: 'say', turnId: 'turn:a', utteranceId: 'a1', text: 'Second.', part: 1 });
  assert.equal(replyLines(s).length, 2);
  s = nextCallState(s, { type: 'reply_delta', turnId: 'turn:b', text: 'New reply' });
  assert.deepEqual(replyLines(s).map(l => l.text), ['New reply']);
});

test('turn timestamps update the user entry without changing older entries', () => {
  let s = nextCallState(null, { type: 'final', text: 'one' });
  s = nextCallState(s, { type: 'turn_times', timestamps: { sent: 10, received: 20 } });
  s = nextCallState(s, { type: 'final', text: 'two' });
  s = nextCallState(s, { type: 'say', text: 'reply' });
  s = nextCallState(s, { type: 'turn_times', timestamps: { sent: 30, first_text: 40 } });
  assert.deepEqual(s.transcript[0].timestamps, { sent: 10, received: 20 });
  assert.deepEqual(s.transcript[1].timestamps, { sent: 30, first_text: 40 });
});
