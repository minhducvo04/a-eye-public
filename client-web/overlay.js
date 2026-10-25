// Pure overlay drawing. No DOM access at import time (Node tests import this file).
// Coordinates in messages are normalized 0..1 of the frame that was sent.

const ROLE_COLOR = {
  target: "#3DDC97",
  other: "#8B909A",
  wrong: "#FF4B4B",
  ok: "#4C9AFF",
};

export const OVERLAY_STALE_MS = 3000;

export function roleColor(role) {
  return ROLE_COLOR[role] || ROLE_COLOR.other;
}

// Pixel rect clamped so it stays inside width x height. Negative sizes flip first.
export function clampRect(x, y, w, h, width, height) {
  let rx = x;
  let ry = y;
  let rw = w;
  let rh = h;
  if (rw < 0) {
    rx += rw;
    rw = -rw;
  }
  if (rh < 0) {
    ry += rh;
    rh = -rh;
  }
  if (rx < 0) {
    rw += rx;
    rx = 0;
  }
  if (ry < 0) {
    rh += ry;
    ry = 0;
  }
  if (rx > width) rx = width;
  if (ry > height) ry = height;
  if (rw < 0) rw = 0;
  if (rh < 0) rh = 0;
  if (rx + rw > width) rw = Math.max(0, width - rx);
  if (ry + rh > height) rh = Math.max(0, height - ry);
  return { x: rx, y: ry, w: rw, h: rh };
}

// bbox is [x, y, w, h]. Values outside the frame are clamped so the rect stays inside.
export function toPixels(bbox, width, height) {
  return clampRect(
    Number(bbox[0]) * width,
    Number(bbox[1]) * height,
    Number(bbox[2]) * width,
    Number(bbox[3]) * height,
    width,
    height,
  );
}

// object-fit: cover. scale maps source pixels onto the view; offsets are the crop
// (negative when that axis is larger than the view and centered).
export function coverTransform(srcW, srcH, viewW, viewH) {
  const sw = Number(srcW);
  const sh = Number(srcH);
  const vw = Number(viewW);
  const vh = Number(viewH);
  if (!(sw > 0) || !(sh > 0) || !(vw > 0) || !(vh > 0)) {
    return { scale: 1, offsetX: 0, offsetY: 0 };
  }
  const scale = Math.max(vw / sw, vh / sh);
  return {
    scale,
    offsetX: (vw - sw * scale) / 2,
    offsetY: (vh - sh * scale) / 2,
  };
}

export function mapCoverPoint(nx, ny, srcW, srcH, viewW, viewH) {
  const t = coverTransform(srcW, srcH, viewW, viewH);
  return {
    x: Number(nx) * srcW * t.scale + t.offsetX,
    y: Number(ny) * srcH * t.scale + t.offsetY,
  };
}

// Normalized box of the sent frame -> view pixels under object-fit: cover, then clamped.
export function mapCoverBox(bbox, srcW, srcH, viewW, viewH) {
  const t = coverTransform(srcW, srcH, viewW, viewH);
  return clampRect(
    Number(bbox[0]) * srcW * t.scale + t.offsetX,
    Number(bbox[1]) * srcH * t.scale + t.offsetY,
    Number(bbox[2]) * srcW * t.scale,
    Number(bbox[3]) * srcH * t.scale,
    viewW,
    viewH,
  );
}

export function sharpCanvasSize(cssW, cssH, dpr) {
  const ratio = typeof dpr === "number" && dpr > 0 ? dpr : 1;
  return {
    width: Math.max(1, Math.round(Number(cssW) * ratio)),
    height: Math.max(1, Math.round(Number(cssH) * ratio)),
    ratio,
  };
}

export function reduceOverlay(state, message, nowMs) {
  const prev = state || { seq: -1, overlay: null, atMs: 0 };
  if (!message || message.type !== "overlay") return prev;
  if (typeof message.seq === "number" && message.seq < prev.seq) return prev;
  return {
    seq: typeof message.seq === "number" ? message.seq : prev.seq,
    overlay: message,
    atMs: nowMs,
  };
}

export function visibleOverlay(state, nowMs) {
  if (!state || !state.overlay) return null;
  if (typeof state.atMs !== "number") return null;
  if (Number(nowMs) - state.atMs >= OVERLAY_STALE_MS) return null;
  return state.overlay;
}

function boxCaption(box) {
  const name = box.label || box.header || box.id || "";
  const pct = Math.round(Number(box.conf) * 100);
  return name ? `${name} ${pct}%` : `${pct}%`;
}

function drawArrow(ctx, from, to, viewMin) {
  const x1 = from.x;
  const y1 = from.y;
  const x2 = to.x;
  const y2 = to.y;
  const angle = Math.atan2(y2 - y1, x2 - x1);
  const head = Math.max(10, viewMin * 0.04);
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x2, y2);
  ctx.lineTo(
    x2 - head * Math.cos(angle - Math.PI / 6),
    y2 - head * Math.sin(angle - Math.PI / 6),
  );
  ctx.lineTo(
    x2 - head * Math.cos(angle + Math.PI / 6),
    y2 - head * Math.sin(angle + Math.PI / 6),
  );
  ctx.closePath();
  ctx.fill();
}

function drawBoxes(ctx, overlay, width, height, mapBox, mapPoint) {
  ctx.clearRect(0, 0, width, height);
  if (!overlay) return;
  const boxes = overlay.boxes || [];
  ctx.lineWidth = Math.max(2, Math.round(Math.min(width, height) / 160));
  ctx.font = `${Math.max(14, Math.round(height * 0.045))}px sans-serif`;
  ctx.textBaseline = "bottom";
  for (const box of boxes) {
    const rect = mapBox(box.bbox);
    const color = roleColor(box.role);
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
    ctx.fillText(boxCaption(box), rect.x, Math.max(14, rect.y - 2));
    if (overlay.level === "pin" && box.pins) {
      for (const pin of box.pins) {
        const point = mapPoint(pin.x, pin.y);
        ctx.beginPath();
        ctx.fillStyle = roleColor(pin.role);
        ctx.arc(point.x, point.y, 4, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }
  if (overlay.arrow && overlay.arrow.from && overlay.arrow.to) {
    ctx.strokeStyle = roleColor("target");
    ctx.fillStyle = roleColor("target");
    const from = mapPoint(overlay.arrow.from[0], overlay.arrow.from[1]);
    const to = mapPoint(overlay.arrow.to[0], overlay.arrow.to[1]);
    drawArrow(ctx, from, to, Math.min(width, height));
  }
}

export function drawOverlay(ctx, overlay, width, height) {
  drawBoxes(
    ctx,
    overlay,
    width,
    height,
    (bbox) => toPixels(bbox, width, height),
    (nx, ny) => ({ x: nx * width, y: ny * height }),
  );
}

// srcW/srcH are the sent frame (same aspect as the camera frame). view is the element box.
export function drawOverlayCover(ctx, overlay, srcW, srcH, viewW, viewH) {
  drawBoxes(
    ctx,
    overlay,
    viewW,
    viewH,
    (bbox) => mapCoverBox(bbox, srcW, srcH, viewW, viewH),
    (nx, ny) => mapCoverPoint(nx, ny, srcW, srcH, viewW, viewH),
  );
}
