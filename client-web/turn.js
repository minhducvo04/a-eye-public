// Pipeline times use the local clock; server epoch clocks may differ.
export const EARLY_FINAL_MS = 700;
const STAGES = ['received', 'transcribing', 'thinking', 'backup', 'speaking', 'done', 'failed', 'cancelled'];

export function nextTurnState(state, event, nowMs) {
  const s = state || { stage: 'idle', since: nowMs, timestamps: {}, retries: 0, turnId: '', micAt: null };
  const e = event || {};
  if (e.type === 'turn' && e.stage === 'expert') return { ...s, expertAt: nowMs, expertId: e.turn_id };
  if (e.type === 'turn' && e.stage === 'expert_done') return e.turn_id === s.expertId ? { ...s, expertAt: null, expertId: null } : s;
  if (e.type === 'sent') return { expertAt: s.expertAt, expertId: s.expertId, stage: 'sent', since: nowMs, timestamps: { sent: nowMs }, retries: 0, ackAt: nowMs, turnId: '', text: e.text || '', micAt: null };
  if (e.type === 'mic_start') {
    if (s.micAt != null || ['sent', 'received', 'transcribing', 'thinking', 'backup', 'speaking'].includes(s.stage)) return s;
    return { ...s, stage: 'hearing', since: nowMs, micAt: nowMs, text: '' };
  }
  if (e.type === 'mic_stop') return { ...s, micAt: null, ...(s.stage === 'hearing' ? { stage: 'muted', since: nowMs } : {}) };
  // A known mic or recognizer problem replaces the generic "didn't catch that".
  if (e.type === 'cause') return { ...s, cause: e.detail || '' };
  if (e.type === 'interim' && e.text) return { ...s, stage: 'hearing', since: s.stage === 'hearing' ? s.since : nowMs, text: e.text, micAt: null, cause: '' };
  if (e.type === 'closed') return { ...s, stage: 'disconnected', expertAt: null, expertId: null, since: nowMs, micAt: null };
  if (e.type === 'connected' && s.stage === 'disconnected') return { ...s, stage: s.timestamps.sent != null && s.timestamps.received == null ? 'sent' : 'idle', since: nowMs, ackAt: nowMs };
  if (e.type === 'turn' && STAGES.includes(e.stage)) {
    // A received event binds the server-generated id. Ignore late stages from older turns.
    if (e.stage !== 'received' && s.turnId && e.turn_id && e.turn_id !== s.turnId) return s;
    const timestamps = { ...s.timestamps };
    if (e.stage === 'received' && timestamps.received == null) timestamps.received = nowMs;
    return { ...s, stage: e.stage, since: nowMs, turnId: e.turn_id || s.turnId, detail: e.detail || '', text: e.text || s.text, typicalMs: e.typical_ms ?? s.typicalMs, timestamps, micAt: null };
  }
  if (e.type === 'first_text' || e.type === 'first_sound') {
    if (s.timestamps[e.type] != null) return s;
    return { ...s, timestamps: { ...s.timestamps, [e.type]: nowMs } };
  }
  if (e.type === 'tick') {
    if (s.micAt != null && nowMs - s.micAt >= 8000) return { ...s, stage: 'failed', detail: s.cause || "Didn't catch that, try again", since: nowMs, micAt: null };
    if (['sent', 'retrying'].includes(s.stage) && s.timestamps.received == null && nowMs - s.ackAt >= 2000) {
      return { ...s, stage: s.retries ? 'connection_problem' : 'retrying', retries: 1, ackAt: nowMs, since: nowMs };
    }
  }
  return s;
}

export function statusLine(state, nowMs) {
  const s = state || { stage: 'idle' };
  if (s.expertAt != null) return `EXPERT ${(Math.max(0, nowMs - s.expertAt) / 1000).toFixed(1)} s`;
  const labels = { idle: 'Ready', muted: 'Mic muted', hearing: 'Hearing', sent: 'Sent', received: 'Received', transcribing: 'Transcribing', thinking: 'Thinking', backup: 'Brain slow, trying backup', speaking: 'Speaking', done: 'Done', cancelled: 'Cancelled', failed: s.detail || "Didn't catch that", retrying: 'Not received, retrying', connection_problem: 'Connection problem', disconnected: 'Connection lost, retrying' };
  const label = labels[s.stage] || 'Ready';
  if (['idle', 'muted'].includes(s.stage)) return s.cause || label;
  const elapsed = `${(Math.max(0, nowMs - s.since) / 1000).toFixed(1)} s`;
  const typical = s.stage === 'thinking' && s.typicalMs != null ? ` · usually ${(s.typicalMs / 1000).toFixed(1)} s` : '';
  // The server names the part of a finished turn that went over budget ("Slow: brain 1.3 s").
  const slow = s.stage === 'done' && s.detail ? ` · ${s.detail}` : '';
  return `${label} ${elapsed}${typical}${slow}${s.stage === 'hearing' && s.text ? ` · ${s.text}` : ''}`;
}

// Recognition result ids distinguish a late final from a genuinely new utterance.
// Keep the early version even when the browser later rewrites multiple words.
export function createEarlyFinal(onFinal, clock = globalThis) {
  const entries = new Map();
  function entry(id) {
    if (!entries.has(id)) entries.set(id, { sent: false, timer: null });
    if (entries.size > 100) {
      for (const [key, item] of entries) {
        if (key !== id && item.sent) { entries.delete(key); break; }
      }
    }
    return entries.get(id);
  }
  function finish(text, item) {
    clock.clearTimeout(item.timer);
    if (item.sent || !text.trim()) return;
    item.sent = true;
    onFinal(text.trim());
  }
  return {
    hasSent(id) { return Boolean(entries.get(id)?.sent); },
    interim(text, id) {
      const item = entry(id);
      if (item.sent) return false;
      clock.clearTimeout(item.timer);
      item.timer = clock.setTimeout(() => finish(text, item), EARLY_FINAL_MS);
      return true;
    },
    final(text, id) { finish(text, entry(id)); },
    reset() {
      for (const item of entries.values()) clock.clearTimeout(item.timer);
      entries.clear();
    },
  };
}
