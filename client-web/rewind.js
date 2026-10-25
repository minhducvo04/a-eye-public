// Rewind: the call-memory switch and the answer card. Video clips go to TwelveLabs only while the switch is on.
export const NOTICE = "When on, short video clips of this call are uploaded to TwelveLabs so you can ask about them later.";

export function fmtCallTime(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function hitCaption(hit) {
  const when = `${fmtCallTime(hit.t_s)} into the call`;
  if (hit.sure) return when;
  const others = (hit.alternatives || []).map(fmtCallTime);
  return `Best guess: ${when}${others.length ? `, or ${others.join(", ")}` : ""}`;
}

export function boxStyle(bbox) {
  const [x, y, w, h] = bbox.map((v) => Math.min(1, Math.max(0, Number(v) || 0)) * 100);
  return { left: `${x}%`, top: `${y}%`, width: `${w}%`, height: `${h}%` };
}

// Build the card: the kept frame with boxes on it (not on the live picture), and the call time.
export function renderHit(doc, body, hit) {
  body.replaceChildren();
  if (hit.frame_b64) {
    const figure = doc.createElement("div");
    figure.className = "rewind-frame";
    const img = doc.createElement("img");
    img.alt = `Frame at ${fmtCallTime(hit.t_s)}`;
    img.src = `data:image/jpeg;base64,${hit.frame_b64}`;
    figure.append(img);
    for (const obj of hit.objects || []) {
      const box = doc.createElement("span");
      box.className = "rewind-box";
      Object.assign(box.style, boxStyle(obj.bbox));
      box.textContent = String(obj.label || "");
      figure.append(box);
    }
    body.append(figure);
  }
  const line = doc.createElement("p");
  line.className = "rewind-when";
  line.textContent = hitCaption(hit);
  body.append(line);
}

// Recap: ordered steps with call times, then what looks skipped for this mode.
export function renderRecap(doc, body, recap) {
  body.replaceChildren();
  const steps = (recap && recap.steps) || [];
  if (recap && recap.pending) {
    const wait = doc.createElement("p");
    wait.className = "rewind-when";
    wait.textContent = "Making your recap from the Rewind clips…";
    body.append(wait);
    return;
  }
  if (!steps.length) {
    const none = doc.createElement("p");
    none.className = "rewind-when";
    none.textContent = (recap && recap.say) || "Nothing to recap yet.";
    body.append(none);
    return;
  }
  const list = doc.createElement("ol");
  list.className = "rewind-steps";
  for (const step of steps) {
    const item = doc.createElement("li");
    const time = doc.createElement("span");
    time.className = "rewind-time";
    time.textContent = fmtCallTime(step.t_s);
    item.append(time, doc.createTextNode(` ${String(step.text || "")}`));
    list.append(item);
  }
  body.append(list);
  const skipped = doc.createElement("p");
  skipped.className = "rewind-skipped";
  skipped.textContent = (recap.skipped || []).length
    ? `Maybe skipped: ${recap.skipped.join(", ")}`
    : "No typical step looks skipped.";
  body.append(skipped);
}

// POST /api/rewind/recap with the page token when there is one (same rule as the other /api calls).
import { ownerHeaders } from './call-owner.js';

export async function fetchRecap(sessionId, search = globalThis.location ? globalThis.location.search : "") {
  const token = new URLSearchParams(search).get("token");
  const headers = { "Content-Type": "application/json", ...await ownerHeaders(search) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const reply = await fetch("/api/rewind/recap", { method: "POST", headers, body: JSON.stringify({ session_id: sessionId }) });
  if (!reply.ok) throw new Error(`recap ${reply.status}`);
  return reply.json();
}

// Call-screen Rewind: one state behind both the bottom-bar button and the Settings switch.
// Starter questions the server's Rewind matcher knows (server/rewind.py _QUESTION); each fills the typed bar.
export const PROMPTS = Object.freeze([
  { label: "When did I…", text: "When did I " },
  { label: "Where did I put…", text: "Where did I put " },
]);

export function initialRewind() {
  return { on: false, clips: 0, noticed: false, notice: false };
}

// switch: the user flipped either control. server: a rewind_state message. The upload notice shows on the
// first switch on in this page, whichever control did it.
export function nextRewind(state, event) {
  const s = state || initialRewind();
  if (!event) return s;
  if (event.type === "switch") {
    const on = Boolean(event.on);
    const first = on && !s.noticed;
    return { ...s, on, clips: on ? s.clips : 0, noticed: s.noticed || on, notice: first };
  }
  if (event.type === "server") {
    const on = Boolean(event.on);
    const clips = Number.isInteger(event.clips) ? event.clips : on ? s.clips : 0;
    return { ...s, on, clips: on ? clips : 0 };
  }
  if (event.type === "notice_seen") return { ...s, notice: false };
  if (event.type === "reset") return { ...s, clips: 0 };
  return s;
}

// The "Rewind" chip above the typed bar: only while Rewind is on and has kept at least one clip.
export function showsRewindChip(state) {
  return Boolean(state && state.on && state.clips > 0);
}

// POST /api/rewind/forget: delete this call's TwelveLabs videos and its local clips.
export async function forgetRewind(sessionId, search = globalThis.location ? globalThis.location.search : "") {
  const token = new URLSearchParams(search).get("token");
  const headers = { "Content-Type": "application/json", ...await ownerHeaders(search) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const reply = await fetch("/api/rewind/forget", { method: "POST", headers, body: JSON.stringify({ session_id: sessionId }) });
  if (!reply.ok) throw new Error(`forget ${reply.status}`);
  return reply.json();
}
