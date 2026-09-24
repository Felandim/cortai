// Cortaí — detector de levantamentos (porte fiel de legacy-server/processor.py).
// Roda no navegador e no Node. Entrada: frames RGBA ou RGB 180x320 a ~10 fps.

export const DETECT_FPS = 10.0;
export const MAX_DURATION_S = 240.0;
export const MAX_DETECT_FRAMES = 2400;
export const DETECT_W = 180;
export const DETECT_H = 320;
const YELLOW_MIN_PX = 25;
const YELLOW_TOP_FRAC = 0.22;
const YELLOW_TOP_REJECT = 0.5;
const SMOOTH_S = 0.4;
const MIN_TRAVEL_FRAC = 0.18;
const LIFT_MIN_S = 0.6;
const LIFT_MAX_S = 3.5;
const BURST_MAX_S = 3.0;
const MIN_ATTEMPT_GAP_S = 6.0;
export const MIN_CLIP = 5.0;
export const MAX_CLIP = 9.0;
export const PREF_CLIP = 7.0;
export const MAX_CLIPS_PER_FILE = 8;
const EOF_IGNORE_S = 4.0;
const START_TAIL_FRAC = 0.12;
const START_TAIL_MIN_S = 3.0;
const CENTER_TOP_FRAC = 0.22;
const CENTER_BOT_FRAC = 0.7;
const REL_PEAK_FRAC = 0.58;
export const EMPTY_MSG =
  "Não achei levantamento nesse arquivo — tenta um take mais próximo da plataforma.";

// Python round(): banker's rounding (round half to even).
export function pyRound(x) {
  const f = Math.floor(x);
  const d = x - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}
function round3(x) {
  return Math.round(x * 1000) / 1000;
}
function median(arr) {
  const n = arr.length;
  if (!n) return NaN;
  const s = Float64Array.from(arr).sort();
  const m = n >> 1;
  return n % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}
function slice(a, i0, i1) {
  // numpy-like a[i0:i1] with clamping
  const n = a.length;
  const s = Math.max(0, Math.min(n, i0));
  const e = Math.max(s, Math.min(n, i1));
  return a.subarray ? a.subarray(s, e) : a.slice(s, e);
}
function argmin(a) {
  let k = 0;
  for (let i = 1; i < a.length; i++) if (a[i] < a[k]) k = i;
  return k;
}
function argmax(a) {
  let k = 0;
  for (let i = 1; i < a.length; i++) if (a[i] > a[k]) k = i;
  return k;
}

export function smooth(x, win) {
  const n = x.length;
  const out = new Float64Array(n);
  if (!n) return out;
  const w = Math.max(1, Math.trunc(win));
  if (w === 1 || n === 1) {
    out.set(x);
    return out;
  }
  const pad = w >> 1;
  const padded = new Float64Array(n + 2 * pad);
  for (let i = 0; i < padded.length; i++) {
    const j = Math.min(n - 1, Math.max(0, i - pad));
    padded[i] = x[j];
  }
  for (let k = 0; k < n; k++) {
    let s = 0;
    for (let q = 0; q < w; q++) s += padded[k + q];
    out[k] = s / w;
  }
  return out;
}

export function interpNan(x) {
  const n = x.length;
  const y = Float64Array.from(x);
  const good = [];
  for (let i = 0; i < n; i++) if (Number.isFinite(y[i])) good.push(i);
  if (!good.length) return y;
  if (good.length === 1) return y.fill(y[good[0]]);
  let g = 0;
  for (let i = 0; i < n; i++) {
    if (Number.isFinite(x[i])) continue;
    if (i < good[0]) y[i] = x[good[0]];
    else if (i > good[good.length - 1]) y[i] = x[good[good.length - 1]];
    else {
      while (good[g + 1] < i) g++;
      const a = good[g], b = good[g + 1];
      y[i] = x[a] + ((x[b] - x[a]) * (i - a)) / (b - a);
    }
  }
  return y;
}

// ---- per-frame features (accumulated while sampling; frames are not kept) ----
export class FeatureAccumulator {
  constructor(w = DETECT_W, h = DETECT_H) {
    this.w = w;
    this.h = h;
    this.cy = [];
    this.counts = [];
    this.countsAll = [];
    this.countsTop = [];
    this.cen = [];
    this.bot = [];
    this.prevLuma = null;
  }
  // data: Uint8(Clamped)Array, channels = 4 (RGBA canvas) or 3 (RGB raw)
  push(data, channels = 4) {
    const { w, h } = this;
    const topCut = pyRound(YELLOW_TOP_FRAC * h);
    let cAll = 0, cTop = 0, c = 0, ySum = 0;
    const luma = new Float32Array(w * h);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const p = (y * w + x) * channels;
        const r = data[p], g = data[p + 1], b = data[p + 2];
        luma[y * w + x] = 0.3 * r + 0.59 * g + 0.11 * b;
        if (r > g * 1.1 && g > b * 1.15 && r > 80 && r + g > 1.4 * (b + 10)) {
          cAll++;
          if (y < topCut) cTop++;
          else {
            c++;
            ySum += y;
          }
        }
      }
    }
    this.countsAll.push(cAll);
    this.countsTop.push(cTop);
    this.counts.push(c);
    this.cy.push(c >= YELLOW_MIN_PX ? ySum / c : NaN);

    if (this.prevLuma) {
      const prev = this.prevLuma;
      const x0 = w >> 2, x1 = (3 * w) >> 2;
      const y0 = pyRound(CENTER_TOP_FRAC * h);
      let y1 = pyRound(CENTER_BOT_FRAC * h);
      const yb = pyRound(0.7 * h);
      y1 = Math.max(y0 + 1, Math.min(h, y1));
      let sc = 0, sb = 0;
      for (let y = y0; y < y1; y++)
        for (let x = x0; x < x1; x++) sc += Math.abs(luma[y * w + x] - prev[y * w + x]);
      for (let y = yb; y < h; y++)
        for (let x = 0; x < w; x++) sb += Math.abs(luma[y * w + x] - prev[y * w + x]);
      const cv = sc / ((y1 - y0) * (x1 - x0));
      const bv = sb / ((h - yb) * w);
      if (this.cen.length === 0) {
        // numpy _pad: out[0] = src[0]
        this.cen.push(cv);
        this.bot.push(bv);
      }
      this.cen.push(cv);
      this.bot.push(bv);
    }
    this.prevLuma = luma;
  }
  get length() {
    return this.cy.length;
  }
  features() {
    const n = this.cy.length;
    let cen = Float64Array.from(this.cen);
    let bot = Float64Array.from(this.bot);
    if (n < 2) {
      cen = new Float64Array(Math.max(n, 1));
      bot = new Float64Array(Math.max(n, 1));
    }
    return {
      n,
      cy: Float64Array.from(this.cy),
      counts: Int32Array.from(this.counts),
      countsAll: Int32Array.from(this.countsAll),
      countsTop: Int32Array.from(this.countsTop),
      cen,
      bot,
    };
  }
}

function yellowOk(f) {
  let tot = 0, totTop = 0, good = 0;
  for (let i = 0; i < f.n; i++) {
    tot += f.countsAll[i];
    totTop += f.countsTop[i];
    if (f.counts[i] >= YELLOW_MIN_PX) good++;
  }
  const lights = tot > 0 && totTop / tot >= YELLOW_TOP_REJECT;
  return !lights && good / f.n > 0.3 && median(f.counts) >= YELLOW_MIN_PX;
}

function tailGuardS(duration) {
  return Math.max(START_TAIL_FRAC * duration, START_TAIL_MIN_S);
}

export function searchBounds(n, duration) {
  let skip0 = pyRound(1.5 * DETECT_FPS);
  let skip1 = n - pyRound(EOF_IGNORE_S * DETECT_FPS);
  const tLim = pyRound(Math.max(0, duration - EOF_IGNORE_S) * DETECT_FPS);
  skip1 = Math.min(skip1, tLim);
  if (skip1 <= skip0 + 4) {
    skip0 = pyRound(0.4 * DETECT_FPS);
    skip1 = Math.max(skip0 + 4, n - pyRound(0.4 * DETECT_FPS));
  }
  skip0 = Math.max(1, skip0);
  skip1 = Math.min(n - 1, Math.max(skip0 + 3, skip1));
  return [skip0, skip1];
}

export function keepWindow(start, end, duration, anchor) {
  if (start >= duration - tailGuardS(duration)) return false;
  if (anchor >= duration - EOF_IGNORE_S) return false;
  if (duration >= 14.0 && end >= duration - 0.25 && start >= duration - (MAX_CLIP + 0.6))
    return false;
  const d = end - start;
  if (duration >= MIN_CLIP && d < MIN_CLIP - 0.05) return false;
  if (d > MAX_CLIP + 0.05) return false;
  return true;
}

export function fitClip(start, end, duration, anchor) {
  start = Math.max(0, start);
  end = Math.min(duration, Math.max(start + 0.05, end));
  const nearEof = anchor >= duration - EOF_IGNORE_S;
  let d = end - start;
  if (d < PREF_CLIP) {
    let need = PREF_CLIP - d;
    const addL = Math.min(need * 0.7, start);
    start -= addL;
    need -= addL;
    if (!nearEof) {
      const addR = Math.min(need, Math.max(0, duration - end));
      end += addR;
      need -= addR;
    }
    if (need > 0) {
      start = Math.max(0, start - need);
      if (!nearEof) end = Math.min(duration, start + PREF_CLIP);
    }
  }
  d = end - start;
  if (d < MIN_CLIP) {
    let need = MIN_CLIP - d;
    const addL = Math.min(need, start);
    start -= addL;
    need -= addL;
    if (need > 0 && !nearEof) end = Math.min(duration, end + need);
    start = Math.max(0, start);
    if (end - start < MIN_CLIP - 0.05) return null;
  }
  d = end - start;
  if (d > MAX_CLIP) {
    const left = Math.min(4.5, PREF_CLIP * 0.65);
    start = Math.max(0, anchor - left);
    end = Math.min(duration, start + PREF_CLIP);
    if (nearEof) {
      end = Math.min(end, Math.max(anchor + 2.5, start + MIN_CLIP));
      if (end - start > MAX_CLIP) end = start + MAX_CLIP;
      if (end - start < MIN_CLIP - 0.05) return null;
    }
  }
  start = Math.max(0, start);
  end = Math.min(duration, Math.max(start + 0.05, end));
  if (!keepWindow(start, end, duration, anchor)) return null;
  return [round3(start), round3(end)];
}

function isCameraApproach(i, cenS, botS, n, duration) {
  const t = i / DETECT_FPS;
  if (t < 0.62 * duration) return false;
  const botI = botS[i], cenI = cenS[i];
  const tail0 = pyRound(0.82 * n);
  const mid0 = pyRound(0.15 * n), mid1 = pyRound(0.65 * n);
  const botTail = tail0 < n ? median(slice(botS, tail0, n)) : 0;
  const botMid = mid1 > mid0 ? median(slice(botS, mid0, mid1)) : 0;
  const growing = botTail > botMid * 1.35 + 0.8;
  const hugeBot = botI >= 5.0 && botI >= 1.15 * Math.max(cenI, 1e-6);
  return growing && hugeBot;
}

function windowsFromYellow(cyS, upVel, duration) {
  const n = cyS.length;
  if (n < 8) return [];
  const h = DETECT_H;
  const minDrop = MIN_TRAVEL_FRAC * h;
  const iMin = pyRound(LIFT_MIN_S * DETECT_FPS);
  const iMax = pyRound(LIFT_MAX_S * DETECT_FPS);
  const [skip0, skip1] = searchBounds(n, duration);
  const cands = [];
  for (let i0 = Math.max(0, skip0); i0 < Math.max(0, skip1); i0++) {
    const i1 = Math.min(skip1 < n ? skip1 - 1 : n - 1, i0 + iMax);
    if (i1 - i0 < iMin) continue;
    const wdw = slice(cyS, i0, i1 + 1);
    const kmin = argmin(wdw);
    if (kmin < iMin) continue;
    const drop = cyS[i0] - wdw[kmin];
    if (drop < minDrop) continue;
    const peakI = i0 + argmax(slice(upVel, i0, i0 + kmin + 1));
    if (peakI >= skip1) continue;
    cands.push([drop, i0, peakI]);
  }
  if (!cands.length) return [];
  const minDist = pyRound(MIN_ATTEMPT_GAP_S * DETECT_FPS);
  // Python sort is stable; JS Array.sort is stable too.
  cands.sort((a, b) => b[0] - a[0]);
  const picked = [];
  for (const c of cands) {
    if (picked.some((p) => Math.abs(c[2] - p[2]) < minDist)) continue;
    picked.push(c);
  }
  picked.sort((a, b) => a[2] - b[2]);
  const windows = [];
  for (const [, i0, peakI] of picked) {
    const lo = Math.max(0, peakI - pyRound(1.0 * DETECT_FPS));
    const hi = Math.min(n, peakI + pyRound(0.5 * DETECT_FPS) + 1);
    const peakV = hi > lo ? Math.max(...slice(upVel, lo, hi)) : upVel[peakI];
    const threshV = 0.3 * Math.max(peakV, 1e-6);
    let i = peakI;
    while (i > 0 && upVel[i] >= threshV) i--;
    const floorCy = cyS[i0];
    while (i > 0 && cyS[i] < floorCy - 0.03 * h) i--;
    const onsetT = i / DETECT_FPS;
    const endLim = Math.min(n - 1, peakI + pyRound(2.5 * DETECT_FPS), skip1);
    let j = peakI;
    let returned = false;
    while (j < endLim) {
      if (cyS[j] >= floorCy - 0.05 * h) {
        returned = true;
        j = Math.min(n - 1, j + pyRound(1.0 * DETECT_FPS));
        break;
      }
      j++;
    }
    if (!returned) j = endLim;
    const fitted = fitClip(onsetT, j / DETECT_FPS, duration, peakI / DETECT_FPS);
    if (!fitted) continue;
    windows.push({ start: fitted[0], end: fitted[1], reason: "puxo (barra amarela)" });
    if (windows.length >= MAX_CLIPS_PER_FILE) break;
  }
  return windows;
}

function windowsFromCenter(center, bot, duration) {
  const n = center.length;
  if (n < 8) return [];
  const cenS = smooth(center, Math.max(1, pyRound(0.25 * DETECT_FPS)));
  const botS = smooth(bot, Math.max(1, pyRound(0.25 * DETECT_FPS)));
  const look = Math.max(1, pyRound(1.5 * DETECT_FPS));
  const [skip0, skip1] = searchBounds(n, duration);
  const region = slice(cenS, skip0, skip1);
  if (region.length < 4) return [];
  const med = median(region);
  const mad = median(Array.from(region, (v) => Math.abs(v - med)));
  const absMin = Math.max(med + 1.5 * mad * 1.4826, 3.5);

  const idx = [], scores = [], onsetAt = [];
  for (let i = Math.max(1, skip0); i < Math.min(n - 1, skip1); i++) {
    if (!(cenS[i] >= cenS[i - 1] && cenS[i] >= cenS[i + 1])) continue;
    if (cenS[i] === cenS[i - 1] && cenS[i] === cenS[i + 1]) continue;
    const peakV = cenS[i];
    if (peakV < absMin) continue;
    const half = 0.5 * peakV;
    let a = i;
    while (a > skip0 && cenS[a] >= half) a--;
    let b = i;
    while (b < skip1 - 1 && cenS[b] >= half) b++;
    const core = (b - a) / DETECT_FPS;
    if (core < LIFT_MIN_S - 0.08 || core > BURST_MAX_S + 0.45) continue;
    const thresh = 0.32 * Math.max(peakV, 1e-6);
    let on = i;
    while (on > 0 && cenS[on] >= thresh) on--;
    const sl = slice(cenS, Math.max(0, on - look), Math.max(1, on));
    const stillOn = sl.length ? median(sl) : 0;
    if (stillOn > Math.max(3.8, 0.58 * peakV)) continue;
    if (peakV / (stillOn + 0.5) < 1.55) continue;
    const after0 = Math.min(n, b + 1);
    const after1 = Math.min(n, b + pyRound(1.2 * DETECT_FPS));
    if (after1 > after0) {
      const afterM = median(slice(cenS, after0, after1));
      if (afterM > 0.85 * peakV) continue;
    }
    if (isCameraApproach(i, cenS, botS, n, duration)) continue;
    idx.push(i);
    scores.push(peakV);
    onsetAt.push(on);
  }
  if (!idx.length) return [];
  const mx = Math.max(...scores);
  const keep = [];
  for (let k = 0; k < idx.length; k++) if (scores[k] >= REL_PEAK_FRAC * mx) keep.push(k);
  const minDist = pyRound(MIN_ATTEMPT_GAP_S * DETECT_FPS);
  const picked = [];
  for (const k of keep) if (picked.every((p) => Math.abs(idx[k] - idx[p]) >= minDist)) picked.push(k);

  const windows = [];
  for (const k of picked) {
    const peakI = idx[k];
    const on = onsetAt[k];
    const onsetT = on / DETECT_FPS;
    const sl = slice(cenS, Math.max(0, on - look), Math.max(1, on));
    const base = sl.length ? median(sl) : 0;
    const endLim = Math.min(n - 1, peakI + pyRound(2.5 * DETECT_FPS), skip1);
    let j = peakI;
    let returned = false;
    while (j < endLim) {
      if (j > peakI + pyRound(0.4 * DETECT_FPS) && cenS[j] <= Math.max(base * 1.4, 1.4)) {
        returned = true;
        j = Math.min(n - 1, j + pyRound(0.8 * DETECT_FPS));
        break;
      }
      j++;
    }
    if (!returned) j = endLim;
    const fitted = fitClip(onsetT, j / DETECT_FPS, duration, peakI / DETECT_FPS);
    if (!fitted) continue;
    windows.push({ start: fitted[0], end: fitted[1], reason: "puxo (movimento vertical)" });
    if (windows.length >= MAX_CLIPS_PER_FILE) break;
  }
  return windows;
}

// features: FeatureAccumulator.features(); duration in seconds.
export function detectFromFeatures(f, duration) {
  if (duration > MAX_DURATION_S)
    throw new Error(
      `Vídeo longo demais (${duration.toFixed(0)}s). Máximo ${MAX_DURATION_S}s por arquivo.`
    );
  if (f.n < 8) return [];
  let windows = [];
  if (yellowOk(f)) {
    const cy = interpNan(f.cy);
    if (cy.every(Number.isFinite)) {
      const [skip0, skip1] = searchBounds(cy.length, duration);
      let span = 0;
      if (skip1 > skip0) {
        const seg = slice(cy, skip0, skip1);
        span = Math.max(...seg) - Math.min(...seg);
      }
      if (span >= MIN_TRAVEL_FRAC * DETECT_H * 0.5) {
        const cyS = smooth(cy, Math.max(1, pyRound(SMOOTH_S * DETECT_FPS)));
        const upVel = new Float64Array(cyS.length);
        for (let i = 1; i < cyS.length; i++) upVel[i] = -(cyS[i] - cyS[i - 1]);
        windows = windowsFromYellow(cyS, upVel, duration);
      }
    }
  }
  if (!windows.length) windows = windowsFromCenter(f.cen, f.bot, duration);
  windows = windows.filter((w) => keepWindow(w.start, w.end, duration, w.start + 0.65 * PREF_CLIP));
  return windows.slice(0, MAX_CLIPS_PER_FILE);
}

export function lpoName(start) {
  const t = Math.max(0, pyRound(Number(start) || 0));
  const p = (n) => String(n).padStart(2, "0");
  return `lpo-${p(Math.floor(t / 3600))}${p(Math.floor((t % 3600) / 60))}${p(t % 60)}.mp4`;
}
