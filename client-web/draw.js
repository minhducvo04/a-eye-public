// Draw-layer model for v2_draw. Coordinates are 0..1 of the sent frame.
// Canvas calls live in paintDraw; everything else is pure and safe to import from Node.

import { clampRect, coverTransform } from "./overlay.js";

export const DRAW_IN_MS = 250;
export const DRAW_FADE_MS = 350;

export const ROLE_COLOR = {
  target: "#3DDC97",
  info: "#9EB6FF",
  warn: "#FF8A3D",
  ok: "#4C9AFF",
};

const AGENTS = ["helper", "shopper"];

export function roleColor(role) {
  return ROLE_COLOR[role] || ROLE_COLOR.info;
}

export function emptyDraw() {
  return {
    seq: { helper: -1, shopper: -1 },
    ops: { helper: [], shopper: [] },
  };
}

function expired(item, now) {
  const ttl = item && item.op && item.op.ttl_ms;
  if (typeof ttl !== "number" || ttl <= 0) return false;
  return Number(now) - item.bornMs >= ttl;
}

function fadeOf(item, now) {
  const ttl = item.op.ttl_ms;
  if (typeof ttl !== "number" || ttl <= 0) return 1;
  const age = Number(now) - item.bornMs;
  if (age >= ttl) return 0;
  const windowMs = Math.min(DRAW_FADE_MS, ttl);
  const start = ttl - windowMs;
  if (age <= start) return 1;
  return (ttl - age) / windowMs;
}

function progressOf(item, now) {
  const age = Number(now) - item.bornMs;
  if (age <= 0) return 0;
  if (age >= DRAW_IN_MS) return 1;
  return age / DRAW_IN_MS;
}

// Replace one agent's ops. `clear` drops that agent only. A lower seq is ignored.
export function applyDraw(state, msg, now) {
  const prev = state || emptyDraw();
  if (!msg || msg.type !== "draw") return prev;
  const agent = msg.agent === "shopper" ? "shopper" : msg.agent === "helper" ? "helper" : null;
  if (!agent) return prev;
  if (typeof msg.seq === "number" && prev.seq[agent] > msg.seq) return prev;
  let list = (prev.ops[agent] || []).slice();
  const incoming = Array.isArray(msg.ops) ? msg.ops : [];
  for (const op of incoming) {
    if (!op || typeof op !== "object") continue;
    if (op.kind === "clear") {
      list = [];
      continue;
    }
    if (!op.kind || !op.id) continue;
    list = list.filter((item) => item.op.id !== op.id);
    list.push({ op, bornMs: Number(now) || 0, agent });
  }
  return {
    seq: {
      ...prev.seq,
      [agent]: typeof msg.seq === "number" ? msg.seq : prev.seq[agent],
    },
    ops: {
      ...prev.ops,
      [agent]: list,
    },
  };
}

export function pruneDraw(state, now) {
  const prev = state || emptyDraw();
  const ops = {};
  for (const agent of AGENTS) {
    ops[agent] = (prev.ops[agent] || []).filter((item) => !expired(item, now));
  }
  return { seq: prev.seq, ops };
}

export function visibleOps(state, now) {
  const prev = state || emptyDraw();
  const out = [];
  for (const agent of AGENTS) {
    for (const item of prev.ops[agent] || []) {
      if (expired(item, now)) continue;
      out.push({
        ...item,
        progress: progressOf(item, now),
        fade: fadeOf(item, now),
      });
    }
  }
  return out;
}

// Labels of the boxes currently drawn with the target role, longest first so "blue mug" wins over "mug".
export function targetLabels(state, now) {
  const seen = new Set();
  for (const item of visibleOps(state, now)) {
    const op = item.op;
    if (op.kind !== "box" || op.role !== "target") continue;
    const label = String(op.label || "").trim();
    if (label) seen.add(label);
  }
  return [...seen].sort((a, b) => b.length - a.length);
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Split caption text into { text, target } runs so the words naming a boxed object can take the box's colour.
export function splitByTargets(text, labels) {
  const source = String(text || "");
  const names = (labels || []).map((l) => String(l || "").trim()).filter(Boolean);
  if (!source || !names.length) return source ? [{ text: source, target: false }] : [];
  const re = new RegExp(`(${names.map(escapeRegExp).join("|")})`, "gi");
  const out = [];
  let last = 0;
  for (const m of source.matchAll(re)) {
    if (m.index > last) out.push({ text: source.slice(last, m.index), target: false });
    out.push({ text: m[0], target: true });
    last = m.index + m[0].length;
  }
  if (last < source.length) out.push({ text: source.slice(last), target: false });
  return out;
}

// transform: { scale, offsetX, offsetY, srcW, srcH, viewW, viewH }
// Build one with viewTransform so it matches object-fit: cover.
export function viewTransform(srcW, srcH, viewW, viewH) {
  const cover = coverTransform(srcW, srcH, viewW, viewH);
  return {
    ...cover,
    srcW: Number(srcW) || 0,
    srcH: Number(srcH) || 0,
    viewW: Number(viewW) || 0,
    viewH: Number(viewH) || 0,
  };
}

function mapPoint(nx, ny, t) {
  return {
    x: Number(nx) * t.srcW * t.scale + t.offsetX,
    y: Number(ny) * t.srcH * t.scale + t.offsetY,
  };
}

function mapPoints(points, t) {
  const out = [];
  for (const point of points || []) {
    if (!point || point.length < 2) continue;
    out.push(mapPoint(point[0], point[1], t));
  }
  return out;
}

// r is a fraction of the source's shorter side, then scaled with the cover transform
// so a circle stays round on screen.
export function opToPixels(op, transform) {
  if (!op || op.kind === "clear" || !transform) return null;
  const t = transform;
  if (op.kind === "box" || op.kind === "highlight") {
    const bbox = op.bbox || [0, 0, 0, 0];
    const x = Number(bbox[0]) * t.srcW * t.scale + t.offsetX;
    const y = Number(bbox[1]) * t.srcH * t.scale + t.offsetY;
    const w = Number(bbox[2]) * t.srcW * t.scale;
    const h = Number(bbox[3]) * t.srcH * t.scale;
    return {
      kind: op.kind,
      rect: clampRect(x, y, w, h, t.viewW, t.viewH),
      role: op.role,
      label: op.label || "",
    };
  }
  if (op.kind === "circle") {
    const center = op.center || [0, 0];
    return {
      kind: "circle",
      center: mapPoint(center[0], center[1], t),
      r: Number(op.r) * Math.min(t.srcW, t.srcH) * t.scale,
      role: op.role,
    };
  }
  if (op.kind === "arrow") {
    return {
      kind: "arrow",
      from: mapPoint((op.from || [0, 0])[0], (op.from || [0, 0])[1], t),
      to: mapPoint((op.to || [0, 0])[0], (op.to || [0, 0])[1], t),
      role: op.role,
    };
  }
  if (op.kind === "path") {
    return { kind: "path", points: mapPoints(op.points, t), role: op.role };
  }
  if (op.kind === "label") {
    const at = op.at || [0, 0];
    return { kind: "label", at: mapPoint(at[0], at[1], t), text: op.text || "", role: op.role };
  }
  if (op.kind === "pin") {
    const at = op.at || [0, 0];
    return { kind: "pin", at: mapPoint(at[0], at[1], t), role: op.role };
  }
  return null;
}

function roundRect(ctx, x, y, w, h, radius) {
  const r = Math.max(0, Math.min(radius, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function cutPolyline(points, fraction) {
  if (!points || points.length === 0) return [];
  if (fraction >= 1 || points.length === 1) return points.slice();
  let total = 0;
  const seg = [];
  for (let i = 1; i < points.length; i += 1) {
    const d = Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
    seg.push(d);
    total += d;
  }
  if (total <= 0) return [points[0]];
  const want = total * Math.max(0, fraction);
  const out = [points[0]];
  let walked = 0;
  for (let i = 1; i < points.length; i += 1) {
    const d = seg[i - 1];
    if (walked + d >= want) {
      const t = d === 0 ? 0 : (want - walked) / d;
      out.push({
        x: points[i - 1].x + (points[i].x - points[i - 1].x) * t,
        y: points[i - 1].y + (points[i].y - points[i - 1].y) * t,
      });
      return out;
    }
    walked += d;
    out.push(points[i]);
  }
  return out;
}

function strokeSmooth(ctx, points) {
  if (!points || points.length < 2) return;
  ctx.beginPath();
  ctx.moveTo(points[0].x, points[0].y);
  if (points.length === 2) {
    ctx.lineTo(points[1].x, points[1].y);
    ctx.stroke();
    return;
  }
  for (let i = 0; i < points.length - 1; i += 1) {
    const p0 = points[i === 0 ? 0 : i - 1];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] || p2;
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2y = p2.y - (p3.y - p1.y) / 6;
    ctx.bezierCurveTo(c1x, c1y, c2x, c2y, p2.x, p2.y);
  }
  ctx.stroke();
}

function drawArrow(ctx, from, to, progress) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.hypot(dx, dy);
  if (len < 0.5) return;
  const tipX = from.x + (dx * progress);
  const tipY = from.y + (dy * progress);
  ctx.beginPath();
  ctx.moveTo(from.x, from.y);
  ctx.lineTo(tipX, tipY);
  ctx.stroke();
  if (progress < 0.72) return;
  const ang = Math.atan2(dy, dx);
  const head = Math.max(10, Math.min(18, len * 0.12));
  ctx.beginPath();
  ctx.moveTo(tipX, tipY);
  ctx.lineTo(
    tipX - head * Math.cos(ang - Math.PI / 7),
    tipY - head * Math.sin(ang - Math.PI / 7),
  );
  ctx.lineTo(
    tipX - head * Math.cos(ang + Math.PI / 7),
    tipY - head * Math.sin(ang + Math.PI / 7),
  );
  ctx.closePath();
  ctx.fill();
}

function withAlpha(hex, alpha) {
  const n = Number.parseInt(hex.slice(1), 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// The object we are talking about: a rounded outline with a soft glow, bracket marks on the corners, and a
// chip above it holding the name. Target boxes get the dot and a tinted edge; other roles a quiet grey chip.
function paintBox(ctx, px, color, progress, viewW) {
  const rect = px.rect;
  const isTarget = px.role === "target";
  const radius = Math.min(16, rect.w / 2, rect.h / 2);
  const per = 2 * (rect.w + rect.h);
  ctx.save();
  if (isTarget) {
    ctx.fillStyle = withAlpha(color, 0.06 * progress);
    roundRect(ctx, rect.x, rect.y, rect.w, rect.h, radius);
    ctx.fill();
    ctx.shadowColor = withAlpha(color, 0.45);
    ctx.shadowBlur = 18 * progress;
  }
  ctx.lineWidth = isTarget ? 2.5 : 2;
  roundRect(ctx, rect.x, rect.y, rect.w, rect.h, radius);
  if (progress < 1) ctx.setLineDash([Math.max(0.01, per * progress), per]);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();
  if (isTarget && progress >= 1) drawBrackets(ctx, rect, radius, color);
  if (px.label && progress > 0.4) drawChip(ctx, px.label, rect, color, isTarget, viewW);
}

function drawBrackets(ctx, rect, radius, color) {
  // An L on each corner, set 4 px outside the outline and rounded with the same centre, so it hugs the box.
  const inset = 4;
  const r = radius + inset;
  const arm = Math.min(r + 10, rect.w / 3 + inset, rect.h / 3 + inset);
  if (arm <= r) return;
  const corners = [
    { x: rect.x - inset, y: rect.y - inset, sx: 1, sy: 1 },
    { x: rect.x + rect.w + inset, y: rect.y - inset, sx: -1, sy: 1 },
    { x: rect.x - inset, y: rect.y + rect.h + inset, sx: 1, sy: -1 },
    { x: rect.x + rect.w + inset, y: rect.y + rect.h + inset, sx: -1, sy: -1 },
  ];
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.lineCap = "round";
  for (const c of corners) {
    ctx.beginPath();
    ctx.moveTo(c.x, c.y + c.sy * arm);
    ctx.arcTo(c.x, c.y, c.x + c.sx * arm, c.y, r);
    ctx.lineTo(c.x + c.sx * arm, c.y);
    ctx.stroke();
  }
  ctx.restore();
}

function drawChip(ctx, text, rect, color, isTarget, viewW) {
  ctx.save();
  ctx.shadowColor = "transparent";
  ctx.font = `${isTarget ? 600 : 500} 13px -apple-system, BlinkMacSystemFont, sans-serif`;
  const padX = 10;
  const dot = isTarget ? 14 : 0;
  const height = 28;
  const width = Math.min(viewW - 16, ctx.measureText(text).width + padX * 2 + dot);
  let x = rect.x;
  let y = rect.y - height - 10;
  if (y < 8) y = rect.y + 8;
  x = Math.max(8, Math.min(x, viewW - width - 8));
  ctx.fillStyle = "rgba(8, 10, 14, 0.78)";
  roundRect(ctx, x, y, width, height, height / 2);
  ctx.fill();
  ctx.lineWidth = 1;
  ctx.strokeStyle = isTarget ? withAlpha(color, 0.5) : "rgba(255, 255, 255, 0.18)";
  ctx.stroke();
  if (isTarget) {
    ctx.shadowColor = color;
    ctx.shadowBlur = 8;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x + padX + 3, y + height / 2, 3.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowColor = "transparent";
  }
  ctx.fillStyle = isTarget ? "#ffffff" : "rgba(255, 255, 255, 0.72)";
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.fillText(text, x + padX + dot, y + height / 2 + 0.5, Math.max(0, width - padX * 2 - dot));
  ctx.restore();
}

function paintOne(ctx, px, item, viewW, viewH) {
  const color = roleColor(px.role || item.op.role);
  const alpha = item.fade;
  const progress = item.progress;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 3;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  if (px.kind === "box" && px.rect.w > 0 && px.rect.h > 0) {
    paintBox(ctx, px, color, progress, viewW);
  } else if (px.kind === "highlight" && px.rect.w > 0 && px.rect.h > 0) {
    const rect = px.rect;
    ctx.shadowColor = color;
    ctx.shadowBlur = 22 * progress;
    ctx.fillStyle = withAlpha(color, 0.28 * progress);
    roundRect(ctx, rect.x, rect.y, rect.w, rect.h, 12);
    ctx.fill();
  } else if (px.kind === "circle") {
    ctx.beginPath();
    ctx.arc(px.center.x, px.center.y, Math.max(0, px.r), 0, Math.PI * 2 * progress);
    ctx.stroke();
  } else if (px.kind === "arrow") {
    drawArrow(ctx, px.from, px.to, progress);
  } else if (px.kind === "path") {
    strokeSmooth(ctx, cutPolyline(px.points, progress));
  } else if (px.kind === "label" && px.text) {
    ctx.font = "500 15px -apple-system, BlinkMacSystemFont, sans-serif";
    const padX = 10;
    const width = Math.min(viewW - 16, ctx.measureText(px.text).width + padX * 2);
    const height = 28;
    let x = px.at.x - width / 2;
    let y = px.at.y - height / 2;
    x = Math.max(8, Math.min(x, viewW - width - 8));
    y = Math.max(8, Math.min(y, viewH - height - 8));
    ctx.globalAlpha = alpha * (0.25 + 0.75 * progress);
    ctx.fillStyle = "rgba(12, 16, 22, 0.88)";
    roundRect(ctx, x, y, width, height, height / 2);
    ctx.fill();
    ctx.strokeStyle = color;
    ctx.stroke();
    ctx.fillStyle = "#f4f7fb";
    ctx.textBaseline = "middle";
    ctx.textAlign = "center";
    ctx.fillText(px.text, x + width / 2, y + height / 2, Math.max(0, width - padX));
  } else if (px.kind === "pin") {
    const drop = (1 - progress) * 18;
    const x = px.at.x;
    const y = px.at.y + drop;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x - 6, y - 16);
    ctx.lineTo(x + 6, y - 16);
    ctx.closePath();
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x, y - 18, 7, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

export function paintDraw(ctx, state, transform, now, reduced) {
  if (!ctx || !transform) return;
  const viewW = transform.viewW;
  const viewH = transform.viewH;
  ctx.clearRect(0, 0, viewW, viewH);
  for (const item of visibleOps(state, now)) {
    const px = opToPixels(item.op, transform);
    if (!px) continue;
    const drawn = reduced ? { ...item, progress: 1 } : item;
    paintOne(ctx, px, drawn, viewW, viewH);
  }
}
