// Generates the app icons in frontend/public/ (pure Node, no dependencies):
//   icon-192.png, icon-512.png            "any" icons: rounded square, transparent corners
//   icon-maskable-512.png                 full-bleed, mark kept inside the 80% safe zone
//   apple-touch-icon.png (180)            full-bleed and opaque (iOS rounds the corners itself)
// The PNGs are committed; run `npm run icons` only to change the design or the accent colour.
import { deflateSync } from "node:zlib";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const OUT = fileURLToPath(new URL("../public/", import.meta.url));
const ACCENT = [0x6c, 0x8c, 0xff]; // the app's default accent
const WHITE = [255, 255, 255];
const SS = 4; // 4x4 samples per pixel

// ---- shapes: coverage of a point, in unit coordinates (0..1) ----
function inRoundRect(px, py, x, y, w, h, r) {
  const qx = Math.abs(px - (x + w / 2)) - (w / 2 - r);
  const qy = Math.abs(py - (y + h / 2)) - (h / 2 - r);
  return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r <= 0;
}

// A 2x2 grid of tiles (a start page), centred; `span` is the grid's share of the icon's width.
function tiles(span) {
  const gap = span * 0.1;
  const t = (span - gap) / 2;
  const x0 = (1 - span) / 2;
  const r = t * 0.28;
  return [
    { x: x0, y: x0, color: WHITE, alpha: 1 },
    { x: x0 + t + gap, y: x0, color: WHITE, alpha: 1 },
    { x: x0, y: x0 + t + gap, color: WHITE, alpha: 1 },
    { x: x0 + t + gap, y: x0 + t + gap, color: WHITE, alpha: 0.55 },
  ].map((o) => ({ ...o, w: t, h: t, r }));
}

function render(size, { rounded, span }) {
  const shapes = tiles(span);
  const px = Buffer.alloc(size * size * 4);
  for (let j = 0; j < size; j++) {
    for (let i = 0; i < size; i++) {
      let r = 0, g = 0, b = 0, a = 0; // premultiplied sums
      for (let sj = 0; sj < SS; sj++) {
        for (let si = 0; si < SS; si++) {
          const x = (i + (si + 0.5) / SS) / size;
          const y = (j + (sj + 0.5) / SS) / size;
          if (rounded && !inRoundRect(x, y, 0, 0, 1, 1, 0.22)) continue;
          let c = ACCENT;
          for (const s of shapes) {
            if (!inRoundRect(x, y, s.x, s.y, s.w, s.h, s.r)) continue;
            c = c.map((v, k) => Math.round(s.color[k] * s.alpha + v * (1 - s.alpha)));
            break;
          }
          r += c[0]; g += c[1]; b += c[2]; a += 1;
        }
      }
      const o = (j * size + i) * 4;
      if (a) {
        px[o] = Math.round(r / a);
        px[o + 1] = Math.round(g / a);
        px[o + 2] = Math.round(b / a);
      }
      px[o + 3] = Math.round((255 * a) / (SS * SS));
    }
  }
  return px;
}

// ---- PNG encoding ----
const CRC = new Uint32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
const crc32 = (buf) => {
  let c = 0xffffffff;
  for (const byte of buf) c = CRC[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
};
function chunk(type, data) {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(data.length, 0);
  head.write(type, 4, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
  return Buffer.concat([head, data, crc]);
}
function png(size, rgba) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr.set([8, 6, 0, 0, 0], 8); // 8-bit RGBA
  const raw = Buffer.alloc(size * (size * 4 + 1));
  for (let y = 0; y < size; y++) rgba.copy(raw, y * (size * 4 + 1) + 1, y * size * 4, (y + 1) * size * 4); // filter byte 0
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

mkdirSync(OUT, { recursive: true });
const icons = [
  ["icon-192.png", 192, { rounded: true, span: 0.54 }],
  ["icon-512.png", 512, { rounded: true, span: 0.54 }],
  ["icon-maskable-512.png", 512, { rounded: false, span: 0.46 }], // inside the 80% safe circle
  ["apple-touch-icon.png", 180, { rounded: false, span: 0.54 }],
];
for (const [name, size, opts] of icons) {
  const file = png(size, render(size, opts));
  writeFileSync(OUT + name, file);
  console.log(`wrote ${name} (${size}x${size}, ${file.length} bytes)`);
}
