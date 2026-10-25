// Microphone loudness for the listening waveform. Port of ios/AEyeCore/Sources/AEyeCore/AudioLevel.swift
// (same floor, ceiling, release and voice threshold). Only a number per audio buffer is used; no audio is kept.

// Below this the room is treated as silent, at or above LEVEL_CEILING_DB the bar is full.
const LEVEL_FLOOR_DB = -55;
const LEVEL_CEILING_DB = -10;
// Share of the old level kept per buffer while the sound falls (about 20 ms per buffer).
const LEVEL_RELEASE = 0.82;
// Normalised level that counts as the user talking rather than room noise.
const VOICE_THRESHOLD = 0.3;
// iOS gets one tap buffer about every 20 ms. Browser capture callbacks arrive every 3 to 50 ms, so readings are
// pooled into 20 ms windows and the waveform moves at the phone's pace.
export const LEVEL_WINDOW_MS = 20;
// SpeechIn.levelHistoryCount
export const LEVEL_HISTORY_COUNT = 28;

// RMS amplitude (0...1) to 0...1 on a decibel scale, so quiet speech still moves the bars.
export function normalizedLevel(rms) {
  const value = Number(rms);
  if (!Number.isFinite(value) || value <= 0) return 0;
  const db = 20 * Math.log10(value);
  return Math.min(1, Math.max(0, (db - LEVEL_FLOOR_DB) / (LEVEL_CEILING_DB - LEVEL_FLOOR_DB)));
}

// Jumps up at once and falls off gently, so bars do not flicker between syllables.
export function smoothedLevel(previous, next) {
  return next >= previous ? next : previous * LEVEL_RELEASE + next * (1 - LEVEL_RELEASE);
}

// A new history with `level` at the end and only the newest `capacity` values. `history` is left unchanged.
export function appendingLevel(history, level, capacity) {
  const cap = Math.max(0, capacity);
  return cap ? [...history, level].slice(-cap) : [];
}

export function isVoiceLevel(level) {
  return level >= VOICE_THRESHOLD;
}

// Pools raw RMS readings into LEVEL_WINDOW_MS windows. Returns the next pool and, once a window closes, the
// window's RMS in `rms` (else null).
export function poolLevel(pool, rms, nowMs, windowMs = LEVEL_WINDOW_MS) {
  const value = Number.isFinite(Number(rms)) ? Math.max(0, Number(rms)) : 0;
  const p = pool && pool.count ? pool : { startMs: nowMs, sumSq: 0, count: 0 };
  const next = { startMs: p.startMs, sumSq: p.sumSq + value * value, count: p.count + 1 };
  if (nowMs - next.startMs < windowMs) return { pool: next, rms: null };
  return { pool: { startMs: nowMs, sumSq: 0, count: 0 }, rms: Math.sqrt(next.sumSq / next.count) };
}

// ListeningStrip.swift: the line next to the bars and how it looks.
export function listeningLine({ muted, heard, levels }) {
  const text = String(heard || "");
  const last = levels && levels.length ? levels[levels.length - 1] : 0;
  const talking = text !== "" || isVoiceLevel(last);
  if (muted) return { text: "MIC OFF", tone: "muted", mono: true, active: false, label: "Microphone off" };
  if (text) return { text, tone: "heard", mono: false, active: true, label: `Heard: ${text}` };
  return talking
    ? { text: "HEARING YOU…", tone: "talking", mono: true, active: true, label: "Listening" }
    : { text: "LISTENING", tone: "idle", mono: true, active: false, label: "Listening" };
}

// Mirrored bars, newest on the right; missing history drawn flat so the width never changes (WaveformBars).
export function waveBars(levels, count, width, height, barWidth = 2, minHeight = 2) {
  const list = (levels || []).slice(-count);
  const padded = Array(Math.max(0, count - list.length)).fill(0).concat(list);
  const gap = (width - count * barWidth) / Math.max(1, count - 1);
  return padded.map((level, i) => {
    const h = minHeight + level * (height - minHeight);
    return { x: i * (barWidth + gap), y: (height - h) / 2, w: barWidth, h };
  });
}
