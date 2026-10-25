// Local QR byte-mode encoder, versions 1–6, ECC L, fixed mask 0.
// Copyright (c) 2026 A-eye contributors. MIT License:
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software to deal in the Software without restriction, including use,
// copy, modification, distribution, sublicensing and sale, provided this notice
// is included. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
export function qrMatrix(text) {
  const bytes = new TextEncoder().encode(text);
  const dataCounts = [19, 34, 55, 80, 108, 136], eccCounts = [7, 10, 15, 20, 26, 18];
  const v = dataCounts.findIndex(n => bytes.length + 2 <= n) + 1;
  if (!v) throw new RangeError('Link too long for QR. Copy the link instead.');
  const bits = [];
  const put = (n, count) => { for (let i = count - 1; i >= 0; i--) bits.push((n >>> i) & 1); };
  put(4, 4); put(bytes.length, 8);
  for (const b of bytes) put(b, 8);
  put(0, Math.min(4, dataCounts[v - 1] * 8 - bits.length));
  while (bits.length % 8) bits.push(0);
  const data = [];
  for (let i = 0; i < bits.length; i += 8) data.push(bits.slice(i, i + 8).reduce((a, b) => a * 2 + b, 0));
  for (let i = 0; data.length < dataCounts[v - 1]; i++) data.push(i % 2 ? 0x11 : 0xec);
  const mul = (a, b) => {
    let r = 0;
    for (; b; b >>>= 1) { if (b & 1) r ^= a; a <<= 1; if (a & 256) a ^= 0x11d; }
    return r;
  };
  const count = eccCounts[v - 1];
  let gen = [1], root = 1;
  for (let i = 0; i < count; i++) {
    const next = Array(gen.length + 1).fill(0);
    gen.forEach((x, j) => { next[j] ^= x; next[j + 1] ^= mul(x, root); });
    gen = next; root = mul(root, 2);
  }
  // Version 6-L has two equal blocks of 68 data + 18 correction codewords, interleaved by column.
  const blocks = v === 6 ? [data.slice(0, 68), data.slice(68)] : [data];
  const corrections = blocks.map(block => {
    const ecc = Array(count).fill(0);
    for (const b of block) {
      const factor = b ^ ecc.shift(); ecc.push(0);
      for (let i = 0; i < count; i++) ecc[i] ^= mul(gen[i + 1], factor);
    }
    return ecc;
  });
  const interleave = blocks => blocks[0].flatMap((_, i) => blocks.map(block => block[i]));
  const stream = interleave(blocks).concat(interleave(corrections))
    .flatMap(b => Array.from({ length: 8 }, (_, i) => (b >>> (7 - i)) & 1));
  const size = 17 + 4 * v;
  const m = Array.from({ length: size }, () => Array(size).fill(null));
  const set = (x, y, b) => { if (x >= 0 && y >= 0 && x < size && y < size) m[y][x] = Boolean(b); };
  for (const [cx, cy] of [[3, 3], [size - 4, 3], [3, size - 4]]) {
    for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {
      const d = Math.max(Math.abs(dx), Math.abs(dy)); set(cx + dx, cy + dy, d !== 2 && d !== 4);
    }
  }
  for (let i = 8; i < size - 8; i++) { set(i, 6, i % 2 === 0); set(6, i, i % 2 === 0); }
  if (v > 1) for (let y = -2; y <= 2; y++) for (let x = -2; x <= 2; x++) set(size - 7 + x, size - 7 + y, Math.max(Math.abs(x), Math.abs(y)) !== 1);
  const format = 0x77c4; // BCH-protected L / mask 0.
  for (let i = 0; i < 15; i++) {
    const bit = (format >>> i) & 1;
    if (i < 6) set(8, i, bit); else if (i < 8) set(8, i + 1, bit); else if (i === 8) set(7, 8, bit); else set(14 - i, 8, bit);
    if (i < 8) set(size - 1 - i, 8, bit); else set(8, size - 15 + i, bit);
  }
  set(8, size - 8, true);
  let index = 0, up = true;
  for (let right = size - 1; right > 0; right -= 2) {
    if (right === 6) right--;
    for (let row = 0; row < size; row++) {
      const y = up ? size - 1 - row : row;
      for (let x = right; x >= right - 1; x--) if (m[y][x] === null) set(x, y, (stream[index++] || 0) ^ ((x + y) % 2 === 0));
    }
    up = !up;
  }
  return m;
}

export function drawQr(canvas, text) {
  const m = qrMatrix(text), scale = 6, size = m.length + 8;
  canvas.width = canvas.height = size * scale;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#000';
  m.forEach((row, y) => row.forEach((on, x) => { if (on) ctx.fillRect((x + 4) * scale, (y + 4) * scale, scale, scale); }));
}
