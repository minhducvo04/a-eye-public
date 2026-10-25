/**
 * Fast, dependency-free translation tracking in camera-image coordinates.
 * const gray = toGray(imageData.data, imageData.width, imageData.height, 1);
 * const tracker = createTracker();
 * tracker.seed(anchor.object_id, anchor.bbox, gray);
 * // Each camera frame: render applyAnchors(originalOps, tracker.update(gray)).
 * // Fresh slow-loop / v2_track boxes: tracker.reseed(id, bbox, matchingGray).
 * // Removed objects: tracker.drop(id).
 *
 * Bboxes are normalized [x, y, width, height]. scale is an output/input ratio
 * in (0, 1]; grayscale uses box averaging and ignores alpha. Keep frame size
 * fixed; after a camera resolution change, reseed (old tracks report lost).
 * Seeds must be positive, in-frame regions. Templates have at most patchMax
 * samples per side. A shared averaged half-frame provides the coarse search;
 * full-resolution NCC refines the best eight candidates within 2 pixels. Strong matches (>= 0.85 and
 * >= minScore) blend 10% of the new patch. Flat patches report lost.
 *
 * Reuse ORIGINAL ops each render: anchor metadata stays in server coordinates.
 * Arrow tails stay fixed; local-only moveFrom:true opts into moving both ends
 * (this is not a wire-contract field). Circle radius uses the mean axis scale.
 * No rotation, scale estimation, perspective correction or long-range recovery;
 * server reseeding supplies those corrections. No image data is persisted.
 */
export function toGray(rgba, width, height, scale = 1) {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 ||
      !(scale > 0 && scale <= 1) || rgba.length < width * height * 4) {
    throw new RangeError("Invalid RGBA dimensions or scale");
  }
  const w = Math.max(1, Math.floor(width * scale));
  const h = Math.max(1, Math.floor(height * scale));
  const data = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) {
    const y0 = Math.floor(y * height / h), y1 = Math.floor((y + 1) * height / h);
    for (let x = 0; x < w; x++) {
      const x0 = Math.floor(x * width / w), x1 = Math.floor((x + 1) * width / w);
      let sum = 0;
      for (let j = y0; j < y1; j++) for (let i = x0; i < x1; i++) {
        const k = (j * width + i) * 4;
        sum += 0.299 * rgba[k] + 0.587 * rgba[k + 1] + 0.114 * rgba[k + 2];
      }
      data[y * w + x] = Math.round(sum / ((x1 - x0) * (y1 - y0)));
    }
  }
  return { data, w, h };
}

function half(gray) {
  const w = Math.ceil(gray.w / 2), h = Math.ceil(gray.h / 2);
  const data = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const a = y * 2 * gray.w + x * 2;
    const dx = x * 2 + 1 < gray.w ? 1 : 0;
    const dy = y * 2 + 1 < gray.h ? gray.w : 0;
    data[y * w + x] = (gray.data[a] + gray.data[a + dx] +
      gray.data[a + dy] + gray.data[a + dy + dx]) / 4;
  }
  return { data, w, h };
}

function template(gray, x, y, width, height, max) {
  const ratio = Math.min(1, max / Math.max(width, height));
  const w = Math.max(1, Math.round(width * ratio));
  const h = Math.max(1, Math.round(height * ratio));
  const offsets = new Int32Array(w * h), values = new Float64Array(w * h);
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
    const k = j * w + i;
    offsets[k] = Math.floor((j + 0.5) * height / h) * gray.w + Math.floor((i + 0.5) * width / w);
    values[k] = gray.data[y * gray.w + x + offsets[k]];
  }
  return { offsets, values, width, height };
}

function stats(t) {
  let sum = 0, squares = 0;
  for (const value of t.values) { sum += value; squares += value * value; }
  return { mean: sum / t.values.length, energy: squares - sum * sum / t.values.length };
}

function search(gray, t, x0, y0, x1, y1, preferredX, preferredY, count = 1) {
  const { mean, energy } = stats(t);
  let best = { x: preferredX, y: preferredY, score: -1, distance: Infinity };
  if (energy < 1e-6) return best;
  const candidates = [];
  const n = t.values.length;
  const centered = new Float64Array(n);
  for (let i = 0; i < n; i++) centered[i] = t.values[i] - mean;
  for (let y = Math.max(0, y0); y <= Math.min(gray.h - t.height, y1); y++) {
    for (let x = Math.max(0, x0); x <= Math.min(gray.w - t.width, x1); x++) {
      const base = y * gray.w + x;
      let sum = 0, squares = 0, cross = 0;
      for (let i = 0; i < n; i++) {
        const value = gray.data[base + t.offsets[i]];
        sum += value; squares += value * value; cross += value * centered[i];
      }
      const variance = squares - sum * sum / n;
      const score = variance > 1e-6 ? Math.max(-1, Math.min(1, cross / Math.sqrt(energy * variance))) : -1;
      const distance = (x - preferredX) ** 2 + (y - preferredY) ** 2;
      if (score > best.score || (score === best.score && distance < best.distance)) best = { x, y, score, distance };
      if (count > 1 && (candidates.length < count || score > candidates[candidates.length - 1].score)) {
        candidates.push({ x, y, score, distance });
        candidates.sort((a, b) => b.score - a.score || a.distance - b.distance);
        if (candidates.length > count) candidates.pop();
      }
    }
  }
  return count > 1 ? { ...best, candidates } : best;
}

function blend(t, gray, x, y) {
  const base = y * gray.w + x;
  for (let i = 0; i < t.values.length; i++) t.values[i] = 0.9 * t.values[i] + 0.1 * gray.data[base + t.offsets[i]];
}

export function createTracker({ searchRadius = 0.08, patchMax = 48, minScore = 0.6 } = {}) {
  if (!Number.isFinite(searchRadius) || searchRadius < 0 || searchRadius > 1 ||
      !Number.isInteger(patchMax) || patchMax < 2 || !Number.isFinite(minScore) || minScore < -1 || minScore > 1) {
    throw new RangeError("Invalid tracker options");
  }
  const objects = new Map();
  function seed(objectId, bbox, gray) {
    const [bx, by, bw, bh] = bbox;
    if (!bbox.every(Number.isFinite) || bx < 0 || by < 0 || bw <= 0 || bh <= 0 ||
        bx + bw > 1 + 1e-9 || by + bh > 1 + 1e-9) throw new RangeError("Invalid seed bbox");
    const width = Math.max(1, Math.round(bw * gray.w)), height = Math.max(1, Math.round(bh * gray.h));
    const x = Math.min(gray.w - width, Math.round(bx * gray.w));
    const y = Math.min(gray.h - height, Math.round(by * gray.h));
    const small = half(gray);
    objects.set(objectId, {
      bbox: bbox.slice(), x, y, w: gray.w, h: gray.h,
      fine: template(gray, x, y, width, height, patchMax),
      coarse: template(small, Math.floor(x / 2), Math.floor(y / 2),
        Math.max(1, Math.floor(width / 2)), Math.max(1, Math.floor(height / 2)), Math.max(2, Math.floor(patchMax / 2))),
    });
  }
  return {
    seed, reseed: seed,
    drop(objectId) { objects.delete(objectId); },
    update(gray) {
      const result = new Map();
      if (!objects.size) return result;
      const small = half(gray);
      for (const [id, t] of objects) {
        let score = -1, lost = true;
        if (gray.w === t.w && gray.h === t.h) {
          const rx = Math.floor(searchRadius * gray.w), ry = Math.floor(searchRadius * gray.h);
          const x0 = t.x - rx, x1 = t.x + rx, y0 = t.y - ry, y1 = t.y + ry;
          const coarse = search(small, t.coarse, Math.floor(x0 / 2), Math.floor(y0 / 2),
            Math.ceil(x1 / 2), Math.ceil(y1 / 2), Math.floor(t.x / 2), Math.floor(t.y / 2), 8);
          let fine = { score: -1 };
          for (const candidate of coarse.candidates || [coarse]) {
            const match = search(gray, t.fine, Math.max(x0, candidate.x * 2 - 2), Math.max(y0, candidate.y * 2 - 2),
              Math.min(x1, candidate.x * 2 + 2), Math.min(y1, candidate.y * 2 + 2), t.x, t.y);
            if (match.score > fine.score) fine = match;
            if (fine.score > 0.999) break;
          }
          score = fine.score;
          lost = score < minScore || score === -1;
          if (!lost) {
            t.bbox = [t.bbox[0] + (fine.x - t.x) / gray.w, t.bbox[1] + (fine.y - t.y) / gray.h, t.bbox[2], t.bbox[3]];
            t.x = fine.x; t.y = fine.y;
            if (score >= Math.max(0.85, minScore)) {
              blend(t.fine, gray, t.x, t.y);
              blend(t.coarse, small, Math.floor(t.x / 2), Math.floor(t.y / 2));
            }
          }
        }
        result.set(id, { bbox: t.bbox.slice(), score, lost });
      }
      return result;
    },
  };
}

export function applyAnchors(ops, tracks) {
  return ops.map(op => {
    const track = op.anchor && tracks.get(op.anchor.object_id);
    if (!track || track.lost) return op;
    const [ax, ay, aw, ah] = op.anchor.bbox;
    if (!(aw > 0 && ah > 0)) return op;
    const [x, y, w, h] = track.bbox, sx = w / aw, sy = h / ah;
    const point = ([px, py]) => [x + (px - ax) * sx, y + (py - ay) * sy];
    const moved = { ...op };
    if (op.bbox) moved.bbox = [...point(op.bbox), op.bbox[2] * sx, op.bbox[3] * sy];
    for (const key of ["center", "from", "to", "at"]) {
      if (op[key] && !(key === "from" && op.kind === "arrow" && op.moveFrom !== true)) moved[key] = point(op[key]);
    }
    if (op.r !== undefined) moved.r = op.r * (sx + sy) / 2;
    if (op.points) moved.points = op.points.map(point);
    return moved;
  });
}
