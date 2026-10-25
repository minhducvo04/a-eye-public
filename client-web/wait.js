// The wait card while an agent works on an answer. Port of ios/AEyeCore Wait.swift, the wait part of
// CallState.reduce, and the copy in ios/AEye/App/Views/WaitCard.swift. Clocks are the client's (Date.now()).

// Tuning.defaultTypicalWaitMs: expected wait before the server has measured any turns (typical_ms).
export const DEFAULT_TYPICAL_WAIT_MS = 3000;
// Tuning.maxWaitMs: a wait older than this is hidden, in case the server never sends the turn's end.
export const MAX_WAIT_MS = 30000;

const WAITING = ["received", "transcribing", "thinking", "backup"];
const STAGES = [...WAITING, "speaking", "done", "failed", "cancelled"];

// The agent has not answered yet: the user should keep the camera on the thing and wait.
export function isWaitingStage(stage) {
  return WAITING.includes(stage);
}

// A `turn` message: a new turn id restarts the clock, a later stage of the same turn keeps it.
// Unknown stages are ignored (iOS decodes them as `.ignored`).
export function waitAfterTurn(wait, message, nowMs) {
  const m = message || {};
  if (!STAGES.includes(m.stage) || typeof m.turn_id !== "string") return wait;
  if (!isWaitingStage(m.stage)) return null;
  const same = Boolean(wait && wait.turnId === m.turn_id);
  const typical = Number.isInteger(m.typical_ms) ? m.typical_ms : null;
  return {
    turnId: m.turn_id,
    agent: same ? wait.agent || "helper" : "helper",
    stage: m.stage,
    startedAtMs: same ? wait.startedAtMs : nowMs,
    typicalMs: typical ?? (same ? wait.typicalMs : null),
  };
}

// `agent_state`: thinking starts a wait if none is running (the shopper sends no `turn`), speaking ends it.
export function waitAfterAgentState(wait, agent, state, nowMs) {
  if (agent !== "helper" && agent !== "shopper") return wait;
  if (state === "thinking") {
    if (wait && wait.agent === agent) return wait;
    return { turnId: null, agent, stage: "thinking", startedAtMs: nowMs, typicalMs: null };
  }
  if (state === "speaking") return null;
  return wait;
}

// The wait to show at nowMs, or null once it is older than MAX_WAIT_MS.
export function visibleWait(wait, nowMs) {
  if (!wait || nowMs - wait.startedAtMs > MAX_WAIT_MS) return null;
  return wait;
}

// Share of the wait that has passed, 0 to just under 1. Linear to 0.9 at the typical time, then creeps towards
// 0.99 so a slow turn keeps moving instead of sitting at 100% looking stuck.
export function waitProgress(elapsedMs, typicalMs) {
  const typical = Number(typicalMs) > 0 ? Number(typicalMs) : DEFAULT_TYPICAL_WAIT_MS;
  const elapsed = Math.max(0, Number(elapsedMs) || 0);
  if (elapsed <= typical) return (0.9 * elapsed) / typical;
  return 0.9 + 0.09 * (1 - Math.exp(-(elapsed - typical) / typical));
}

export function waitPercent(progress) {
  return Math.floor(progress * 100);
}

// WaitCard.copy: eyebrow and line for the card.
export function waitCopy(wait, elapsedMs) {
  if (wait.agent === "shopper") return { eyebrow: "SHOPPER", line: "Finding parts that fit…" };
  const isSlow = elapsedMs > (wait.typicalMs ?? DEFAULT_TYPICAL_WAIT_MS);
  if (wait.stage === "received") return { eyebrow: "GOT IT", line: "Hold the camera steady…" };
  if (wait.stage === "transcribing") return { eyebrow: "LISTENING", line: "Catching your words…" };
  if (wait.stage === "backup") return { eyebrow: "LOOKING CLOSER", line: "Taking a bit longer. Keep holding." };
  return { eyebrow: "LOOKING", line: isSlow ? "Almost there. Keep holding." : "Hold the camera steady…" };
}

// Swift's String.capitalized, for the spoken label: "LOOKING CLOSER" -> "Looking Closer".
export function capitalized(text) {
  return String(text).toLowerCase().replace(/(^|\s)(\S)/g, (_, space, ch) => space + ch.toUpperCase());
}
