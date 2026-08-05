/* =====================================================================
   attack_v5.js — 对 FLUX v5（P0 结构性整改）的复测
   ---------------------------------------------------------------------
   v5 相对 v4 的 P0 改动（针对“内容模板匹配”根因）：
     [P0-1] 不向前端/攻击者明文展示目标字形（移除 hint）→ 攻击者失去现成模板，
            被迫对字符集 13 个候选逐一暴力穷举（brute-force over alphabet）。
     [P0-2] 每挑战施加【全局相干弹性形变】Wg（整盘同一平滑位移场，逐挑战随机、
            攻击者未知）→ 人眼仍读得出（平滑形变），但攻击者用“未形变干净模板”
            做轴对齐相关时严重错位。
     （保留 v4：逐块 claim-12 歧义化 AMBI + 诱导笔画 + 对抗HF；保留抗边匹配/单次令牌；
       判定纯服务端，前端仅提交 映射+轨迹+令牌。）
   诚实威胁模型（移除 hint 后）：攻击者不知道目标字形，遍历 13 个候选，对每个候选
   渲染干净模板、做内容匹配、取总相关分最高者为解。两类匹配器：
     - static：z-score 亮度相关；
     - robust：盒式低通(r=2) + 小位移搜索(±6,步3)取最大相关（对抗形变/噪声的强攻击者）。
   对照：oracle-hint 攻击者（已知真字形，等价 v4 威胁）用于量化“去 hint+全局形变”的增益。
   扫描全局形变幅度 GW，统计 P 端到端恢复率与“字形识别率”。结果 → results_v5.json。
   ===================================================================== */
'use strict';
const fs = require('fs');

const N = 3, RR = 84, M = N * N, FR = N * RR;
const GWARP = 2.6, EDGEJIT = 12, EDGEFLOW = 5.5, EDGEBAND = 0.18;
const READBACK = 0.34, CORELOCK = 0.48;
const GLYPHS = "ABEGHKNRSY349".split('');
const DECOY_N = 2, DECOY_TH = 0.05;
/* claim-12 弱扰动可开关：'stacked'=保留 v4(AMBI8+诱导+对抗HF)；'pure'=仅全局相干形变 */
const MODE = process.argv[3] || 'pure';
let AMBI = 8.0, DECOY_A = 0.42, ADVEPS = 0.10;
if (MODE === 'pure') { AMBI = 0.0; DECOY_A = 0.0; ADVEPS = 0.0; }

function mulberry32(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
function palette(t, base) { const TAU = 6.2832; return [.5 + .5 * Math.cos(TAU * (t + base)), .5 + .5 * Math.cos(TAU * (t + base + .33)), .55 + .45 * Math.cos(TAU * (t + base + .66))]; }

const STROKES = {
  A: [[[.5, .1], [.16, .9]], [[.5, .1], [.84, .9]], [[.29, .6], [.71, .6]]],
  B: [[[.22, .1], [.22, .9]], [[.22, .1], [.66, .12], [.8, .3], [.64, .49], [.22, .5]], [[.22, .5], [.7, .51], [.84, .7], [.66, .89], [.22, .9]]],
  E: [[[.26, .1], [.26, .9]], [[.26, .1], [.78, .1]], [[.26, .5], [.7, .5]], [[.26, .9], [.78, .9]]],
  G: [[[.8, .3], [.62, .12], [.36, .13], [.2, .38], [.2, .62], [.36, .87], [.62, .88], [.8, .72], [.8, .55], [.55, .55]]],
  H: [[[.22, .1], [.22, .9]], [[.78, .1], [.78, .9]], [[.22, .5], [.78, .5]]],
  K: [[[.25, .1], [.25, .9]], [[.25, .52], [.78, .1]], [[.33, .5], [.8, .9]]],
  N: [[[.22, .9], [.22, .1]], [[.22, .1], [.78, .9]], [[.78, .9], [.78, .1]]],
  R: [[[.25, .1], [.25, .9]], [[.25, .1], [.7, .12], [.82, .31], [.68, .5], [.25, .5]], [[.46, .5], [.8, .9]]],
  S: [[[.8, .2], [.55, .11], [.3, .17], [.24, .36], [.46, .5], [.63, .56], [.75, .69], [.65, .87], [.37, .9], [.2, .79]]],
  Y: [[[.5, .54], [.2, .1]], [[.5, .54], [.8, .1]], [[.5, .54], [.5, .9]]],
  '3': [[[.25, .18], [.55, .1], [.78, .28], [.55, .48], [.42, .5]], [[.5, .49], [.78, .66], [.6, .88], [.3, .86], [.2, .74]]],
  '4': [[[.66, .1], [.2, .63], [.83, .63]], [[.66, .1], [.66, .9]]],
  '9': [[[.72, .43], [.5, .5], [.32, .4], [.4, .2], [.62, .14], [.74, .3], [.72, .56], [.6, .85], [.4, .92]]],
};
function distToSeg(px, py, ax, ay, bx, by) { const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy; let t = L2 > 0 ? ((px - ax) * dx + (py - ay) * dy) / L2 : 0; t = Math.max(0, Math.min(1, t)); const cx = ax + t * dx, cy = ay + t * dy; return Math.hypot(px - cx, py - cy); }
const _gcache = {};
function buildGlyphRaster(ch) {
  if (_gcache[ch]) return _gcache[ch];
  const A = new Uint8ClampedArray(FR * FR); const polys = STROKES[ch]; const th = 0.085;
  for (let y = 0; y < FR; y++) for (let x = 0; x < FR; x++) {
    const u = x / FR, w = y / FR; let dmin = 1e9;
    for (const poly of polys) for (let i = 1; i < poly.length; i++) { const d = distToSeg(u, w, poly[i - 1][0], poly[i - 1][1], poly[i][0], poly[i][1]); if (d < dmin) dmin = d; }
    A[y * FR + x] = Math.round(Math.max(0, Math.min(1, (th - dmin) / (th * 0.55))) * 255);
  }
  _gcache[ch] = A; return A;
}
function makeSampleA(boardA) {
  return function (fx, fy) {
    if (fx < 0 || fy < 0 || fx >= FR - 1 || fy >= FR - 1) return 0;
    const x0 = fx | 0, y0 = fy | 0, tx = fx - x0, ty = fy - y0, i = y0 * FR + x0;
    const a0 = boardA[i] + (boardA[i + 1] - boardA[i]) * tx; const a1 = boardA[i + FR] + (boardA[i + FR + 1] - boardA[i + FR]) * tx;
    return (a0 + (a1 - a0) * ty) / 255;
  };
}
/* 全局相干弹性形变场（板坐标 FR；逐挑战随机相位） */
function makeGlobalWarp(gp, GW) {
  return function (gx, gy) {
    const u = gx / FR, w = gy / FR;
    const wx = GW * (Math.sin(6.283 * (u * 0.9 + gp.a) + gp.b) + 0.55 * Math.cos(6.283 * (w * 1.1 + gp.c)));
    const wy = GW * (Math.cos(6.283 * (w * 0.9 + gp.d) + gp.e) + 0.55 * Math.sin(6.283 * (u * 1.1 + gp.f)));
    return [wx, wy];
  };
}
function randTileParamsV5(rng) {
  const p = [];
  for (let i = 0; i < M; i++) { const ja = rng() * 6.283; const decoys = []; for (let d = 0; d < DECOY_N; d++) decoys.push([rng(), rng(), rng(), rng()]);
    p.push({ ph: rng() * 6.283, rot: rng() * 6.283, hb: rng(), gOX: (rng() - 0.5) * 4.5, gOY: (rng() - 0.5) * 4.5, gJX: Math.cos(ja), gJY: Math.sin(ja), wph1: rng() * 6.283, wph2: rng() * 6.283, advPh: rng() * 6.283, decoys }); }
  return p;
}
function decoyAlpha(x, y, prm) { const u = x / RR, w = y / RR; let dmin = 1e9; for (const s of prm.decoys) { const d = distToSeg(u, w, s[0], s[1], s[2], s[3]); if (d < dmin) dmin = d; } return Math.max(0, Math.min(1, (DECOY_TH - dmin) / (DECOY_TH * 0.6))) * DECOY_A; }

function renderV5(prm, slot, time, sampleA, gwarp, advOn) {
  const cs = Math.cos(prm.rot), sn = Math.sin(prm.rot);
  const ox = (slot % N) * RR, oy = (slot / N | 0) * RR;
  const px = new Float32Array(RR * RR * 3); let o = 0;
  for (let y = 0; y < RR; y++) for (let x = 0; x < RR; x++) {
    const rx = x * cs - y * sn, ry = x * sn + y * cs;
    const v = Math.sin(rx * 0.09 + prm.ph) + Math.sin(ry * 0.08 - prm.ph * 1.3) + Math.sin((rx + ry) * 0.05 + time + prm.ph);
    const band = Math.pow(1 - Math.abs(Math.sin(v * 1.6 + time * 0.6)), 5);
    const col = palette(v * 0.18, prm.hb); const inten = 0.12 + band * 1.1;
    let r = col[0] * inten, g = col[1] * inten, b = col[2] * inten;
    const bd = Math.min(Math.min(x, RR - 1 - x), Math.min(y, RR - 1 - y)) / RR;
    const edgeT = Math.max(0, Math.min(1, 1 - bd / EDGEBAND)); const wB = edgeT * edgeT * (3 - 2 * edgeT);
    const coreT = Math.max(0, Math.min(1, (bd - EDGEBAND * 0.45) / 0.18));
    const seamX = Math.sin(x * 0.13 + y * 0.07 + prm.ph * 2.1 + time * 0.35);
    const seamY = Math.cos(x * 0.09 - y * 0.12 - prm.ph * 1.8 + time * 0.28);
    const dx = GWARP * Math.sin(rx * 0.055 + prm.ph * 1.7) + prm.gOX * 0.55 + wB * (EDGEJIT * prm.gJX + EDGEFLOW * seamX);
    const dy = GWARP * Math.cos(ry * 0.055 - prm.ph * 1.3) + prm.gOY * 0.55 + wB * (EDGEJIT * prm.gJY + EDGEFLOW * seamY);
    const ax = AMBI * (Math.sin(x * 0.13 + y * 0.11 + prm.wph1) + 0.6 * Math.cos(x * 0.09 - y * 0.15 + prm.wph2));
    const ay = AMBI * (Math.cos(x * 0.13 - y * 0.11 + prm.wph1) + 0.6 * Math.sin(x * 0.09 + y * 0.15 + prm.wph2));
    // 全局相干形变（板坐标）
    const [gx, gy] = gwarp(ox + x, oy + y);
    const warpedA = sampleA(ox + x - dx - ax + gx, oy + y - dy - ay + gy);
    const stableA = sampleA(ox + x - ax + gx, oy + y - ay + gy) * (READBACK + CORELOCK * coreT);
    let a = Math.max(warpedA, stableA); a = Math.max(a, decoyAlpha(x, y, prm));
    if (a > 0.01) { const core = a * a, gi = 0.45 + band * 1.5; r = r * (1 - a) + a * (col[0] * gi * 0.7) + core * 0.85; g = g * (1 - a) + a * (col[1] * gi * 0.7) + core * 0.90; b = b * (1 - a) + a * (col[2] * gi * 0.7) + core * 1.00; }
    let R = r * 255, G = g * 255, B = b * 255;
    if (advOn) { const s = Math.sin(x * 1.7 + y * 2.3 + prm.advPh) * Math.sin(x * 2.9 - y * 1.3 + prm.advPh * 1.7); const e = (s >= 0 ? 1 : -1) * ADVEPS * 255; R += e; G += e; B += e; }
    px[o] = Math.max(0, Math.min(255, R)); px[o + 1] = Math.max(0, Math.min(255, G)); px[o + 2] = Math.max(0, Math.min(255, B)); o += 3;
  }
  return px;
}
function buildChallengeV5(rng, GW, advOn) {
  const ch = GLYPHS[(rng() * GLYPHS.length) | 0];
  const boardA = buildGlyphRaster(ch); const sampleA = makeSampleA(boardA);
  let P = [...Array(M).keys()]; do { for (let i = M - 1; i > 0; i--) { const j = rng() * (i + 1) | 0;[P[i], P[j]] = [P[j], P[i]]; } } while (P.every((v, i) => v === i));
  const gp = { a: rng(), b: rng() * 6.283, c: rng(), d: rng(), e: rng() * 6.283, f: rng() };
  const gwarp = makeGlobalWarp(gp, GW);
  const prm = randTileParamsV5(rng); const time = 1.0 + rng() * 3;
  const tiles = new Array(M); for (let id = 0; id < M; id++) tiles[id] = renderV5(prm[id], P[id], time, sampleA, gwarp, advOn);
  return { ch, P, tiles };
}

/* ---------- 特征/匹配 ---------- */
function boxBlur(f, side, r) {
  if (r <= 0) return f; const n = side * side, tmp = new Float32Array(n), out = new Float32Array(n);
  for (let y = 0; y < side; y++) for (let x = 0; x < side; x++) { let s = 0, c = 0; for (let k = -r; k <= r; k++) { const xx = x + k; if (xx >= 0 && xx < side) { s += f[y * side + xx]; c++; } } tmp[y * side + x] = s / c; }
  for (let y = 0; y < side; y++) for (let x = 0; x < side; x++) { let s = 0, c = 0; for (let k = -r; k <= r; k++) { const yy = y + k; if (yy >= 0 && yy < side) { s += tmp[yy * side + x]; c++; } } out[y * side + x] = s / c; }
  return out;
}
function zscore(f) { const n = f.length; let m = 0; for (let i = 0; i < n; i++) m += f[i]; m /= n; let va = 0; const g = new Float32Array(n); for (let i = 0; i < n; i++) { g[i] = f[i] - m; va += g[i] * g[i]; } const sd = Math.sqrt(va / n) || 1; for (let i = 0; i < n; i++) g[i] /= sd; return g; }
function lumaRaw(tile) { const n = RR * RR, f = new Float32Array(n); for (let i = 0; i < n; i++) f[i] = 0.299 * tile[i * 3] + 0.587 * tile[i * 3 + 1] + 0.114 * tile[i * 3 + 2]; return f; }
function refCellRaw(boardA, slot) { const ox = (slot % N) * RR, oy = (slot / N | 0) * RR, n = RR * RR, f = new Float32Array(n); let o = 0; for (let y = 0; y < RR; y++) for (let x = 0; x < RR; x++) f[o++] = boardA[(oy + y) * FR + (ox + x)] / 255; return f; }
function dot(a, b) { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * b[i]; return s; }
function hungarian(cost) {
  const n = cost.length, INF = 1e18; const u = new Array(n + 1).fill(0), vv = new Array(n + 1).fill(0); const p = new Array(n + 1).fill(0), way = new Array(n + 1).fill(0);
  for (let i = 1; i <= n; i++) { p[0] = i; let j0 = 0; const minv = new Array(n + 1).fill(INF), used = new Array(n + 1).fill(false);
    do { used[j0] = true; const i0 = p[j0]; let delta = INF, j1 = -1; for (let j = 1; j <= n; j++) if (!used[j]) { const cur = cost[i0 - 1][j - 1] - u[i0] - vv[j]; if (cur < minv[j]) { minv[j] = cur; way[j] = j0; } if (minv[j] < delta) { delta = minv[j]; j1 = j; } } for (let j = 0; j <= n; j++) { if (used[j]) { u[p[j]] += delta; vv[j] -= delta; } else minv[j] -= delta; } j0 = j1; } while (p[j0] !== 0);
    do { const j1 = way[j0]; p[j0] = p[j1]; j0 = j1; } while (j0); }
  const assign = new Array(n); for (let j = 1; j <= n; j++) assign[p[j] - 1] = j - 1; return assign;
}
function assignCost(assign, S) { let s = 0; for (let id = 0; id < M; id++) s += S[id][assign[id]]; return s; }

/* 给定瓦片特征与模板特征，返回 {assign, score} */
function matchGivenTemplates(tileFeats, tmplFeats) {
  const S = []; for (let id = 0; id < M; id++) { S.push([]); for (let s = 0; s < M; s++) S[id][s] = dot(tileFeats[id], tmplFeats[s]) / (RR * RR); }
  const assign = hungarian(S.map(row => row.map(x => -x)));
  return { assign, score: assignCost(assign, S) };
}
function tileFeatures(tiles, blurR) { return tiles.map(t => zscore(blurR > 0 ? boxBlur(lumaRaw(t), RR, blurR) : lumaRaw(t))); }
function tmplFeatures(boardA, blurR) { const a = []; for (let s = 0; s < M; s++) a.push(zscore(blurR > 0 ? boxBlur(refCellRaw(boardA, s), RR, blurR) : refCellRaw(boardA, s))); return a; }

/* oracle-hint 攻击者：已知真字形（等价 v4 威胁） */
function attackOracle(trueCh, tiles, blurR) {
  const tf = tileFeatures(tiles, blurR); const mf = tmplFeatures(buildGlyphRaster(trueCh), blurR);
  return matchGivenTemplates(tf, mf).assign;
}
/* brute-force-alphabet 攻击者：不知字形，遍历 13 候选取总分最高 */
function attackBruteforce(tiles, blurR) {
  const tf = tileFeatures(tiles, blurR);
  let best = null, bestG = null;
  for (const g of GLYPHS) { const mf = tmplFeatures(buildGlyphRaster(g), blurR); const m = matchGivenTemplates(tf, mf); if (!best || m.score > best.score) { best = m; bestG = g; } }
  return { assign: best.assign, ch: bestG };
}

function evalConfig(seed, GW, trials) {
  const rng = mulberry32(seed);
  const R = { oracle_static: 0, bf_static: 0, bf_robust: 0, bf_glyphId: 0, bf_robust_glyphId: 0 };
  for (let t = 0; t < trials; t++) {
    const C = buildChallengeV5(rng, GW, true);
    const eq = a => a.every((v, i) => v === C.P[i]);
    if (eq(attackOracle(C.ch, C.tiles, 0))) R.oracle_static++;
    const bf = attackBruteforce(C.tiles, 0); if (eq(bf.assign)) R.bf_static++; if (bf.ch === C.ch) R.bf_glyphId++;
    const bfr = attackBruteforce(C.tiles, 2); if (eq(bfr.assign)) R.bf_robust++; if (bfr.ch === C.ch) R.bf_robust_glyphId++;
  }
  const d = trials;
  return { oracle_static: R.oracle_static / d, bf_static: R.bf_static / d, bf_robust: R.bf_robust / d, bf_glyphId: R.bf_glyphId / d, bf_robust_glyphId: R.bf_robust_glyphId / d };
}

const TR = parseInt(process.argv[2] || '300', 10);
const GWS = [0, 8, 16, 24, 32];
const out = { trials: TR, mode: MODE, ambi: AMBI, adveps: ADVEPS, alphabet: GLYPHS.length, sweep: [] };
console.log('FLUX v5（P0：去hint + 全局相干形变）复测  ' + TR + ' 次/项  字符集=' + GLYPHS.length + '  模式=' + MODE);
console.log('  GW |  oracle-hint | bf静态(P/字形) | bf鲁棒r2(P/字形)');
for (const GW of GWS) {
  const r = evalConfig(0x5E5E0000 + GW, GW, TR);
  out.sweep.push({ gw: GW, oracle_static: r.oracle_static, bf_static: r.bf_static, bf_static_glyph: r.bf_glyphId, bf_robust: r.bf_robust, bf_robust_glyph: r.bf_robust_glyphId });
  const pc = v => (v * 100).toFixed(1) + '%';
  console.log('  ' + String(GW).padStart(3) + ' |  ' + pc(r.oracle_static).padStart(6) + '     |  ' + pc(r.bf_static) + ' / ' + pc(r.bf_glyphId) + '  |  ' + pc(r.bf_robust) + ' / ' + pc(r.bf_robust_glyphId));
}
fs.writeFileSync(__dirname + '/results_v5.json', JSON.stringify(out, null, 2));
console.log('  结果 → redteam/results_v5.json');

/* ---------- 可读性蒙太奇样例：同 char/P/参数，仅 GW 不同 ---------- */
function assembleBoard(tiles, P) {
  const board = new Uint8Array(N * RR * N * RR * 3);
  for (let id = 0; id < M; id++) { const slot = P[id], r = slot / N | 0, c = slot % N; for (let y = 0; y < RR; y++) for (let x = 0; x < RR; x++) { const si = (y * RR + x) * 3, di = ((r * RR + y) * (N * RR) + (c * RR + x)) * 3; board[di] = tiles[id][si]; board[di + 1] = tiles[id][si + 1]; board[di + 2] = tiles[id][si + 2]; } }
  return Array.from(board);
}
const sampleGws = [0, 16, 32];
const sample = { side: N * RR, gws: sampleGws, panels: [] };
for (const GW of sampleGws) { const rng = mulberry32(0xD15EA5E0); const C = buildChallengeV5(rng, GW, true); sample.panels.push({ gw: GW, ch: C.ch, P: C.P, board: assembleBoard(C.tiles, C.P) }); }
fs.writeFileSync(__dirname + '/sample_v5.json', JSON.stringify(sample));
console.log('  可读性样例 → redteam/sample_v5.json  (char=' + sample.panels[0].ch + ', GW=' + JSON.stringify(sampleGws) + ')');
