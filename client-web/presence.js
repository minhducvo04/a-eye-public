// Agent presence: two orbs. nextPresence / glideTarget / layoutOrbs are pure.
// paintPresence is the only function that touches a canvas.

import { coverTransform } from "./overlay.js";

export const ORB_SIZE = 54;
export const SCREEN_MARGIN = 18;
export const FOCUS_GAP = 22;
export const GLIDE_MS = 450;
export const SHOPPER_FADE_MS = 1400;

export const AGENTS = {
  helper: {
    id: "helper",
    name: "Helper",
    core: "#f5f8ff",
    glow: "#8eb6ff",
    caption: "#c5d8ff",
  },
  shopper: {
    id: "shopper",
    name: "Shopper",
    core: "#ffe6c2",
    glow: "#e8943a",
    caption: "#ffc98a",
  },
};

const PRESENCE_STATES = ["idle", "listening", "thinking", "speaking"];

function slot(now) {
  return {
    state: "idle",
    focus: null,
    since: now,
    seenSpeaking: false,
    leftSpeakingAt: null,
  };
}

export function initialPresence(now = 0) {
  return {
    level: null,
    helper: slot(now),
    shopper: slot(now),
  };
}

function cleanFocus(focus) {
  if (!Array.isArray(focus) || focus.length < 2) return null;
  const x = Number(focus[0]);
  const y = Number(focus[1]);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return [x, y];
}

export function nextPresence(state, msg, now) {
  const prev = state || initialPresence(now);
  const t = Number(now) || 0;
  if (!msg || typeof msg !== "object") return prev;
  if (msg.type === "level") {
    const level = Number(msg.level);
    return {
      ...prev,
      level: Number.isFinite(level) ? Math.min(1, Math.max(0, level)) : null,
    };
  }
  if (msg.type !== "agent_state") return prev;
  const id = msg.agent === "shopper" ? "shopper" : msg.agent === "helper" ? "helper" : null;
  if (!id) return prev;
  const cur = prev[id];
  const nextState = PRESENCE_STATES.includes(msg.state) ? msg.state : "idle";
  let seenSpeaking = cur.seenSpeaking;
  let leftSpeakingAt = cur.leftSpeakingAt;
  if (nextState === "speaking") {
    seenSpeaking = true;
    leftSpeakingAt = null;
  } else if (cur.state === "speaking") {
    leftSpeakingAt = t;
  }
  return {
    ...prev,
    [id]: {
      state: nextState,
      focus: nextState === "idle" ? null : cleanFocus(msg.focus),
      since: nextState === cur.state ? cur.since : t,
      seenSpeaking,
      leftSpeakingAt,
    },
  };
}

// Shopper is invisible until it speaks, then fades after it leaves speaking.
// Helper stays on screen.
export function agentOpacity(id, agentSlot, now) {
  if (id !== "shopper") return 1;
  if (!agentSlot || !agentSlot.seenSpeaking) return 0;
  if (agentSlot.state === "speaking") return 1;
  if (agentSlot.leftSpeakingAt == null) return 0;
  const t = (Number(now) - agentSlot.leftSpeakingAt) / SHOPPER_FADE_MS;
  if (t <= 0) return 1;
  if (t >= 1) return 0;
  return 1 - t;
}

export function presenceLook(agentSlot, now, level, reduced, hearing) {
  const state = agentSlot && agentSlot.state ? agentSlot.state : "idle";
  const t = Number(now) || 0;
  const calm = Boolean(reduced);
  const mic = Boolean(hearing);
  let ripple = 0;
  if (state === "listening" || mic) {
    ripple = Number.isFinite(level) ? Math.min(1, Math.max(0, level)) : 0.45;
  }
  return {
    state,
    dim: state === "idle" ? 0.62 : 1,
    breathe: state === "idle" && !calm && !mic ? Math.sin(t / 900) : 0,
    pulse: state === "speaking" && !calm ? Math.sin(t / 160) : 0,
    ripple,
    hearing: mic,
    orbit: state === "thinking" && !calm ? t / 900 : -Math.PI / 2,
    reduced: calm,
  };
}

function clampAxis(value, span) {
  const min = SCREEN_MARGIN;
  const max = span - SCREEN_MARGIN - ORB_SIZE;
  if (!(span > 0)) return 0;
  if (max < min) return Math.max(0, (span - ORB_SIZE) / 2);
  return Math.min(max, Math.max(min, value));
}

export function cornerFor(agent, view) {
  const w = Number(view && view.w) || 0;
  const h = Number(view && view.h) || 0;
  const y = clampAxis(SCREEN_MARGIN, h);
  const x = agent === "shopper"
    ? clampAxis(w - SCREEN_MARGIN - ORB_SIZE, w)
    : clampAxis(SCREEN_MARGIN, w);
  return { x, y };
}

export function focusPixels(focus, view) {
  if (!focus || !view) return null;
  const vw = Number(view.w) || Number(view.viewW) || 0;
  const vh = Number(view.h) || Number(view.viewH) || 0;
  const srcW = Number(view.srcW) || 0;
  const srcH = Number(view.srcH) || 0;
  if (srcW > 0 && srcH > 0 && vw > 0 && vh > 0) {
    const scale = view.scale != null ? Number(view.scale) : coverTransform(srcW, srcH, vw, vh).scale;
    const offsetX = view.offsetX != null ? Number(view.offsetX) : coverTransform(srcW, srcH, vw, vh).offsetX;
    const offsetY = view.offsetY != null ? Number(view.offsetY) : coverTransform(srcW, srcH, vw, vh).offsetY;
    return {
      x: Number(focus[0]) * srcW * scale + offsetX,
      y: Number(focus[1]) * srcH * scale + offsetY,
    };
  }
  return { x: Number(focus[0]) * vw, y: Number(focus[1]) * vh };
}

function coversPoint(x, y, px, py) {
  const cx = x + ORB_SIZE / 2;
  const cy = y + ORB_SIZE / 2;
  return Math.hypot(cx - px, cy - py) <= ORB_SIZE / 2 + 1;
}

function placeBeside(px, py, view, sign) {
  const w = Number(view.w) || 0;
  const h = Number(view.h) || 0;
  const y = clampAxis(py - ORB_SIZE / 2, h);
  const xFor = (side) => clampAxis(side < 0 ? px - FOCUS_GAP - ORB_SIZE : px + FOCUS_GAP, w);
  let x = xFor(sign);
  if (!coversPoint(x, y, px, py)) return { x, y };
  const flipped = xFor(-sign);
  if (!coversPoint(flipped, y, px, py)) return { x: flipped, y };
  const below = clampAxis(py + FOCUS_GAP, h);
  if (!coversPoint(x, below, px, py)) return { x, y: below };
  const above = clampAxis(py - FOCUS_GAP - ORB_SIZE, h);
  return { x, y: above };
}

// Destination of the orb. Idle, or no focus, sits in the corner.
// Otherwise the orb sits beside the focus point, inside the screen.
export function glideTarget(agent, agentSlot, view) {
  const home = cornerFor(agent, view);
  if (!agentSlot || agentSlot.state === "idle" || !agentSlot.focus) {
    return { ...home, mode: "corner" };
  }
  const point = focusPixels(agentSlot.focus, view);
  if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) {
    return { ...home, mode: "corner" };
  }
  const sign = agent === "shopper" ? 1 : -1;
  const placed = placeBeside(point.x, point.y, view, sign);
  return { ...placed, mode: "focus" };
}

function easeOutCubic(t) {
  const x = Math.min(1, Math.max(0, t));
  return 1 - (1 - x) ** 3;
}

export function orbPosition(motion, now) {
  if (!motion) return { x: 0, y: 0 };
  const t = easeOutCubic((Number(now) - motion.t0) / GLIDE_MS);
  return {
    x: motion.x + (motion.gx - motion.x) * t,
    y: motion.y + (motion.gy - motion.y) * t,
  };
}

export function trackOrb(motion, goal, now, reduced) {
  const t = Number(now) || 0;
  if (reduced || !motion) {
    return { x: goal.x, y: goal.y, gx: goal.x, gy: goal.y, t0: t };
  }
  const same = Math.abs(motion.gx - goal.x) < 0.5 && Math.abs(motion.gy - goal.y) < 0.5;
  if (same) return motion;
  const cur = orbPosition(motion, t);
  return { x: cur.x, y: cur.y, gx: goal.x, gy: goal.y, t0: t };
}

export function layoutOrbs(presence, motion, view, now, reduced) {
  const src = presence || initialPresence(now);
  const prev = motion || {};
  const nextMotion = {};
  const poses = {};
  for (const id of ["helper", "shopper"]) {
    const goal = glideTarget(id, src[id], view);
    const tracked = trackOrb(prev[id], goal, now, reduced);
    nextMotion[id] = tracked;
    const pos = reduced ? { x: goal.x, y: goal.y } : orbPosition(tracked, now);
    poses[id] = {
      x: pos.x,
      y: pos.y,
      opacity: agentOpacity(id, src[id], now),
      goal,
    };
  }
  return { motion: nextMotion, poses };
}

function paintOrb(ctx, agent, pose, look, now) {
  if (!pose || pose.opacity <= 0.02) return;
  const spec = AGENTS[agent];
  const scale = 1 + look.breathe * 0.035 + look.pulse * 0.08;
  const cx = pose.x + ORB_SIZE / 2;
  const cy = pose.y + ORB_SIZE / 2;
  const radius = (ORB_SIZE / 2) * scale;
  ctx.save();
  ctx.globalAlpha = pose.opacity * look.dim;
  const halo = ctx.createRadialGradient(cx, cy, radius * 0.2, cx, cy, radius * 1.65);
  halo.addColorStop(0, spec.glow);
  halo.addColorStop(1, "rgba(0, 0, 0, 0)");
  ctx.fillStyle = halo;
  ctx.beginPath();
  ctx.arc(cx, cy, radius * 1.65, 0, Math.PI * 2);
  ctx.fill();
  const body = ctx.createRadialGradient(cx - radius * 0.2, cy - radius * 0.25, radius * 0.1, cx, cy, radius);
  body.addColorStop(0, spec.core);
  body.addColorStop(0.55, spec.glow);
  body.addColorStop(1, "rgba(0, 0, 0, 0)");
  ctx.fillStyle = body;
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.fill();
  if (look.state === "listening" || look.hearing) {
    const swell = radius * (1.15 + look.ripple * 0.95);
    ctx.beginPath();
    ctx.strokeStyle = spec.glow;
    ctx.globalAlpha = pose.opacity * (0.28 + look.ripple * 0.5) * look.dim;
    ctx.lineWidth = 2 + look.ripple * 6;
    ctx.arc(cx, cy, swell, 0, Math.PI * 2);
    ctx.stroke();
    if (look.state === "listening" && !look.reduced) {
      const rings = 2;
      for (let i = 0; i < rings; i += 1) {
        const phase = (now / 1400 + i / rings) % 1;
        const ringR = radius * 0.7 + phase * radius * (0.7 + look.ripple);
        ctx.beginPath();
        ctx.strokeStyle = spec.glow;
        ctx.globalAlpha = pose.opacity * (1 - phase) * 0.45 * look.dim;
        ctx.lineWidth = 1.5;
        ctx.arc(cx, cy, ringR, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
  }
  if (look.state === "thinking") {
    ctx.globalAlpha = pose.opacity * 0.9;
    ctx.strokeStyle = spec.core;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(cx, cy, radius * 0.72, look.orbit, look.orbit + 1.15);
    ctx.stroke();
  }
  ctx.globalAlpha = pose.opacity;
  ctx.fillStyle = spec.caption;
  ctx.font = "12px -apple-system, BlinkMacSystemFont, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const tagY = pose.y + ORB_SIZE + 4;
  ctx.fillText(spec.name, cx, tagY);
  ctx.restore();
}

export function paintPresence(ctx, presence, poses, view, now, reduced, hearing) {
  if (!ctx || !view) return;
  ctx.clearRect(0, 0, view.w, view.h);
  const src = presence || initialPresence(now);
  for (const id of ["helper", "shopper"]) {
    const look = presenceLook(src[id], now, src.level, reduced, id === "helper" && hearing);
    paintOrb(ctx, id, poses && poses[id], look, Number(now) || 0);
  }
}
