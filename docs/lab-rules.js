// Strategy Lab, "Build your own": an indicator library, a rule (condition) engine and candle /
// price-action pattern detectors, all on the Lab's daily data (docs/lab.js decode()).
//
// A ticker T is { t, idx, c, h, l, o, v } (o and v can be null); `data` is the decoded market
// ({ dates, tickers, spy, bench }). Every series is a Float64Array with one value per ticker day,
// NaN where it isn't known yet. Nothing looks ahead: a value on day i only uses days up to i
// (weekly values only use weeks that have closed).
//
// A series "spec" is { id, p: { ...params }, tf: "D" | "W", ago: bars back }.
// A condition is { a: spec, op, b: number | spec, mul: factor on b, n: bars for the op, w: "within the last w bars" }
//   or { pat: pattern id, w }.
// A rule group is { mode: "all" | "any", list: [conditions] }.
// Informational only.

// ------------------------------------------------------------------ maths
const nan = n => new Float64Array(n).fill(NaN);
const ok = x => x === x;   // not NaN
// rolling mean of the last n values; NaN until n valid values in a row (NaN inputs restart it)
export function sma(x, n) {
  const out = nan(x.length); let s = 0, k = 0;
  for (let i = 0; i < x.length; i++) {
    if (!ok(x[i])) { s = 0; k = 0; continue; }
    s += x[i]; k++;
    if (k > n) { s -= x[i - n]; k = n; }
    if (k === n) out[i] = s / n;
  }
  return out;
}
// ema, adjust=False like pandas, started at the first valid value
export function ema(x, n, alpha = 2 / (n + 1), from = 0) {
  const out = nan(x.length); let e = NaN, k = 0;
  for (let i = 0; i < x.length; i++) {
    if (!ok(x[i])) continue;
    e = ok(e) ? alpha * x[i] + (1 - alpha) * e : x[i]; k++;
    if (k > from) out[i] = e;
  }
  return out;
}
const wilder = (x, n, from) => ema(x, n, 1 / n, from);
function wma(x, n) {
  const out = nan(x.length), W = n * (n + 1) / 2;
  for (let i = n - 1; i < x.length; i++) {
    let s = 0, good = true;
    for (let j = 0; j < n; j++) { const v = x[i - n + 1 + j]; if (!ok(v)) { good = false; break; } s += v * (j + 1); }
    if (good) out[i] = s / W;
  }
  return out;
}
function stdev(x, n, mean) {   // population std (ddof=0), like the Lab's Bollinger
  const out = nan(x.length);
  for (let i = n - 1; i < x.length; i++) {
    if (!ok(mean[i])) continue;
    let q = 0; for (let j = i - n + 1; j <= i; j++) q += (x[j] - mean[i]) ** 2;
    out[i] = Math.sqrt(q / n);
  }
  return out;
}
// highest / lowest of the last n values; `skip` = 1 leaves today out (a breakout level)
function roll(x, n, max, skip = 0) {
  const out = nan(x.length), q = []; let head = 0;
  for (let i = 0; i < x.length; i++) {
    const j = i - skip;   // newest value in the window
    if (j >= 0) {
      while (q.length > head && (max ? x[q[q.length - 1]] <= x[j] : x[q[q.length - 1]] >= x[j])) q.pop();
      q.push(j);
      while (q[head] <= j - n) head++;
      if (j >= n - 1) out[i] = x[q[head]];
    }
    if (q.length > 4096 && head > 2048) { q.splice(0, head); head = 0; }
  }
  return out;
}
const hh = (T, n, skip = 0) => roll(T.h, n, true, skip), ll = (T, n, skip = 0) => roll(T.l, n, false, skip);
function trueRange(T) {
  const { c, h, l } = T, out = new Float64Array(c.length);
  for (let i = 0; i < c.length; i++) out[i] = i === 0 ? h[i] - l[i] : Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1]));
  return out;
}
// ATR with Wilder smoothing (same as the Lab's Supertrend)
export const atr = (T, n) => wilder(trueRange(T), n, n - 1);
const openOf = T => T.o || T._o || (T._o = Float64Array.from(T.c, (v, i) => i ? T.c[i - 1] : v));   // no opens: yesterday's close
const vol = T => T.v || nan(T.c.length);
const map = (n, f) => { const out = nan(n); for (let i = 0; i < n; i++) out[i] = f(i); return out; };
const shift = (x, k) => { const out = nan(x.length); for (let i = k; i < x.length; i++) out[i] = x[i - k]; return out; };
function rsiOf(c, n) {   // the Lab's RSI (ewm alpha 1/n of gains and losses)
  const out = nan(c.length), a = 1 / n; let g = 0, l = 0;
  for (let i = 1; i < c.length; i++) {
    const d = c[i] - c[i - 1], gi = Math.max(d, 0), li = Math.max(-d, 0);
    if (i === 1) { g = gi; l = li; } else { g = (1 - a) * g + a * gi; l = (1 - a) * l + a * li; }
    if (i >= n) out[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
  }
  return out;
}
function supertrendOf(T, n, mult) {   // the line itself (lower band in an uptrend, upper in a downtrend)
  const { c, h, l } = T, N = c.length, A = atr(T, n), out = nan(N);
  let up = NaN, lo = NaN, t = 0;
  for (let i = 0; i < N; i++) {
    if (!ok(A[i])) continue;
    const mid = (h[i] + l[i]) / 2, bu = mid + mult * A[i], bl = mid - mult * A[i];
    if (!ok(up)) { up = bu; lo = bl; t = c[i] > bu ? 1 : c[i] < bl ? -1 : 0; continue; }
    const nu = bu < up || c[i - 1] > up ? bu : up, nl = bl > lo || c[i - 1] < lo ? bl : lo;
    up = nu; lo = nl;
    if (t >= 0 && c[i] < lo) t = -1; else if (t <= 0 && c[i] > up) t = 1;
    if (t) out[i] = t > 0 ? lo : up;
  }
  return out;
}
function psarOf(T, step, maxAf) {
  const { h, l } = T, n = h.length, out = nan(n);
  if (n < 3) return out;
  let up = h[1] >= h[0], sar = up ? l[0] : h[0], ep = up ? h[1] : l[1], af = step;
  for (let i = 1; i < n; i++) {
    sar = sar + af * (ep - sar);
    if (up) {
      sar = Math.min(sar, l[i - 1], i > 1 ? l[i - 2] : l[i - 1]);
      if (l[i] < sar) { up = false; sar = ep; ep = l[i]; af = step; }
      else if (h[i] > ep) { ep = h[i]; af = Math.min(maxAf, af + step); }
    } else {
      sar = Math.max(sar, h[i - 1], i > 1 ? h[i - 2] : h[i - 1]);
      if (h[i] > sar) { up = true; sar = ep; ep = h[i]; af = step; }
      else if (l[i] < ep) { ep = l[i]; af = Math.min(maxAf, af + step); }
    }
    out[i] = sar;
  }
  return out;
}
function adxOf(T, n) {
  const { h, l } = T, N = h.length, pdm = new Float64Array(N), mdm = new Float64Array(N);
  for (let i = 1; i < N; i++) {
    const u = h[i] - h[i - 1], d = l[i - 1] - l[i];
    pdm[i] = u > d && u > 0 ? u : 0; mdm[i] = d > u && d > 0 ? d : 0;
  }
  const tr = wilder(trueRange(T), n, n), p = wilder(pdm, n, n), m = wilder(mdm, n, n);
  const pdi = map(N, i => tr[i] > 0 ? 100 * p[i] / tr[i] : NaN), mdi = map(N, i => tr[i] > 0 ? 100 * m[i] / tr[i] : NaN);
  const dx = map(N, i => pdi[i] + mdi[i] > 0 ? 100 * Math.abs(pdi[i] - mdi[i]) / (pdi[i] + mdi[i]) : (ok(pdi[i]) ? 0 : NaN));
  return { adx: wilder(dx, n, n - 1), pdi, mdi };
}
function sinceExtreme(x, n, max) {   // bars since the highest (or lowest) of the last n+1 values
  const out = nan(x.length);
  for (let i = n; i < x.length; i++) {
    let b = i; for (let j = i - n; j <= i; j++) if (max ? x[j] >= x[b] : x[j] <= x[b]) b = j;
    out[i] = i - b;
  }
  return out;
}
// Rolling volume profile from daily bars (same loops as value_area() in engine/strategies.py): each
// day's volume spread evenly over its high-low range, on `rows` equal price rows between the window's
// lowest low and highest high. poc / vah / val on day e describe the n bars ending at e (e included).
export function valueArea(T, n, va = 0.7, rows = 50) {
  return memo(T, `#VA|${n}|${va}|${rows}`, () => {
    const { h, l, c } = T, v = vol(T), N = c.length;
    const poc = nan(N), vah = nan(N), val = nan(N), acc = new Float64Array(rows);
    for (let e = n - 1; e < N; e++) {
      let lo = Infinity, hi = -Infinity;
      for (let j = e - n + 1; j <= e; j++) { if (l[j] < lo) lo = l[j]; if (h[j] > hi) hi = h[j]; }
      if (!(hi > lo)) continue;
      const step = (hi - lo) / rows;
      acc.fill(0);
      let total = 0;
      for (let j = e - n + 1; j <= e; j++) {
        const vj = v[j];
        if (!(vj > 0)) continue;
        total += vj;
        if (!(h[j] > l[j])) { acc[Math.min(rows - 1, Math.max(0, Math.floor((c[j] - lo) / step)))] += vj; continue; }
        const per = vj / (h[j] - l[j]);
        const k0 = Math.max(0, Math.floor((l[j] - lo) / step) - 1), k1 = Math.min(rows - 1, Math.floor((h[j] - lo) / step) + 1);
        for (let k = k0; k <= k1; k++) {
          const top = Math.min(h[j], lo + (k + 1) * step), bot = Math.max(l[j], lo + k * step);
          if (top > bot) acc[k] += (top - bot) * per;
        }
      }
      if (!(total > 0)) continue;
      let p = 0;
      for (let k = 1; k < rows; k++) if (acc[k] > acc[p]) p = k;
      let a = p, b = p, inside = acc[p];
      while (inside < va * total && (a > 0 || b < rows - 1)) {
        const up = b < rows - 1 ? acc[b + 1] : -1, dn = a > 0 ? acc[a - 1] : -1;
        if (up >= dn) inside += acc[++b]; else inside += acc[--a];
      }
      poc[e] = lo + (p + 0.5) * step; vah[e] = lo + (b + 1) * step; val[e] = lo + a * step;
    }
    return { poc, vah, val };
  });
}
const benchOn = (T, data) => { const b = data?.bench; return b ? map(T.c.length, i => b[T.idx[i]]) : nan(T.c.length); };
const dateOf = (T, data, i) => data.dates[T.idx[i]];

// -------------------------------------------------------------- catalogue
// scale tells the screen what a value can be compared with: "price" (same units as the price),
// "pct" (a %), "osc" (0 to 100), "num" (any number), "vol" (shares), "cal" (calendar numbers).
const P = (k, label, def, min, max, step = 1, unit = "") => ({ k, label, def, min, max, step, unit });
const N = (def, min = 2, max = 400, label = "Length") => P("n", label, def, min, max, 1, "bars");
export const IND_GROUPS = ["Price", "Trend", "Momentum", "Volume", "Volatility", "Support & resistance", "Market & relative", "Calendar"];
export const IND = {
  // price
  close: { name: "Close", group: "Price", scale: "price", params: [], f: T => T.c },
  open: { name: "Open", group: "Price", scale: "price", params: [], open: true, f: T => openOf(T) },
  high: { name: "High", group: "Price", scale: "price", params: [], f: T => T.h },
  low: { name: "Low", group: "Price", scale: "price", params: [], f: T => T.l },
  chg: { name: "Change over N bars", short: "Change", group: "Price", scale: "pct", params: [N(5, 1, 252)], f: (T, p) => map(T.c.length, i => i >= p.n ? (T.c[i] / T.c[i - p.n] - 1) * 100 : NaN), hint: { op: "lt", b: -5 } },
  gap: { name: "Gap at the open", short: "Gap", group: "Price", scale: "pct", params: [], open: true, f: T => { const o = openOf(T); return map(T.c.length, i => i ? (o[i] / T.c[i - 1] - 1) * 100 : NaN); }, hint: { op: "gt", b: 2 } },
  body: { name: "Candle body (+ up, − down)", short: "Body", group: "Price", scale: "pct", params: [], open: true, f: T => { const o = openOf(T); return map(T.c.length, i => (T.c[i] / o[i] - 1) * 100); }, hint: { op: "gt", b: 2 } },
  range: { name: "Day's range", short: "Range", group: "Price", scale: "pct", params: [], f: T => map(T.c.length, i => (T.h[i] / T.l[i] - 1) * 100), hint: { op: "gt", b: 4 } },
  up_days: { name: "Up closes in a row", short: "Up days", group: "Price", scale: "num", params: [], f: T => { let k = 0; return map(T.c.length, i => (k = i && T.c[i] > T.c[i - 1] ? k + 1 : 0)); }, hint: { op: "gt", b: 3 } },
  down_days: { name: "Down closes in a row", short: "Down days", group: "Price", scale: "num", params: [], f: T => { let k = 0; return map(T.c.length, i => (k = i && T.c[i] < T.c[i - 1] ? k + 1 : 0)); }, hint: { op: "gt", b: 3 } },
  // averages & trend
  sma: { name: "Simple moving average", short: "SMA", group: "Trend", scale: "price", params: [N(50)], f: (T, p) => sma(T.c, p.n), hint: { op: "lt", rev: true } },
  ema: { name: "Exponential moving average", short: "EMA", group: "Trend", scale: "price", params: [N(21)], f: (T, p) => ema(T.c, p.n, undefined, p.n - 1), hint: { op: "lt", rev: true } },
  wma: { name: "Weighted moving average", short: "WMA", group: "Trend", scale: "price", params: [N(20, 2, 200)], f: (T, p) => wma(T.c, p.n), hint: { op: "lt", rev: true } },
  hma: { name: "Hull moving average", short: "HMA", group: "Trend", scale: "price", params: [N(20, 4, 200)], f: (T, p) => { const a = wma(T.c, Math.max(1, Math.round(p.n / 2))), b = wma(T.c, p.n); return wma(map(T.c.length, i => 2 * a[i] - b[i]), Math.max(1, Math.round(Math.sqrt(p.n)))); }, hint: { op: "rise", n: 1 } },
  dist_ma: { name: "% from the moving average", short: "From SMA", group: "Trend", scale: "pct", params: [N(50)], f: (T, p) => { const m = sma(T.c, p.n); return map(T.c.length, i => (T.c[i] / m[i] - 1) * 100); }, hint: { op: "lt", b: -5 } },
  slope: { name: "Slope of the moving average", short: "SMA slope", group: "Trend", scale: "pct", params: [N(50), P("k", "Over", 10, 1, 60, 1, "bars")], f: (T, p) => { const m = sma(T.c, p.n); return map(T.c.length, i => i >= p.k ? (m[i] / m[i - p.k] - 1) * 100 : NaN); }, hint: { op: "gt", b: 0 } },
  supertrend: { name: "Supertrend line", short: "Supertrend", group: "Trend", scale: "price", params: [N(10, 3, 50, "ATR length"), P("mult", "Distance", 3, 1, 6, 0.25, "ATRs")], f: (T, p) => supertrendOf(T, p.n, p.mult), hint: { op: "lt", rev: true } },
  psar: { name: "Parabolic SAR", short: "SAR", group: "Trend", scale: "price", params: [P("step", "Step", 0.02, 0.01, 0.1, 0.01), P("max", "Maximum", 0.2, 0.1, 0.5, 0.05)], f: (T, p) => psarOf(T, p.step, p.max), hint: { op: "lt", rev: true } },
  adx: { name: "ADX (trend strength)", short: "ADX", group: "Trend", scale: "osc", params: [N(14, 3, 50)], f: (T, p) => adxOf(T, p.n).adx, hint: { op: "gt", b: 25 } },
  pdi: { name: "+DI", short: "+DI", group: "Trend", scale: "osc", params: [N(14, 3, 50)], f: (T, p) => adxOf(T, p.n).pdi, hint: { op: "xa", b: { id: "mdi" } } },
  mdi: { name: "−DI", short: "−DI", group: "Trend", scale: "osc", params: [N(14, 3, 50)], f: (T, p) => adxOf(T, p.n).mdi, hint: { op: "xa", b: { id: "pdi" } } },
  aroon_up: { name: "Aroon up", short: "Aroon up", group: "Trend", scale: "osc", params: [N(25, 5, 100)], f: (T, p) => { const s = sinceExtreme(T.h, p.n, true); return map(T.c.length, i => 100 * (p.n - s[i]) / p.n); }, hint: { op: "gt", b: 70 } },
  aroon_dn: { name: "Aroon down", short: "Aroon down", group: "Trend", scale: "osc", params: [N(25, 5, 100)], f: (T, p) => { const s = sinceExtreme(T.l, p.n, false); return map(T.c.length, i => 100 * (p.n - s[i]) / p.n); }, hint: { op: "gt", b: 70 } },
  tenkan: { name: "Ichimoku conversion line", short: "Tenkan", group: "Trend", scale: "price", params: [N(9, 2, 60)], f: (T, p) => { const a = hh(T, p.n), b = ll(T, p.n); return map(T.c.length, i => (a[i] + b[i]) / 2); }, hint: { op: "xa", b: { id: "kijun" } } },
  kijun: { name: "Ichimoku base line", short: "Kijun", group: "Trend", scale: "price", params: [N(26, 2, 120)], f: (T, p) => { const a = hh(T, p.n), b = ll(T, p.n); return map(T.c.length, i => (a[i] + b[i]) / 2); }, hint: { op: "lt", rev: true } },
  span_a: { name: "Ichimoku cloud, span A", short: "Span A", group: "Trend", scale: "price", params: [P("t", "Conversion", 9, 2, 60), P("k", "Base", 26, 2, 120)], f: (T, p) => { const a = hh(T, p.t), b = ll(T, p.t), x = hh(T, p.k), y = ll(T, p.k); return shift(map(T.c.length, i => ((a[i] + b[i]) / 2 + (x[i] + y[i]) / 2) / 2), p.k); }, hint: { op: "lt", rev: true } },
  span_b: { name: "Ichimoku cloud, span B", short: "Span B", group: "Trend", scale: "price", params: [P("s", "Span B length", 52, 10, 200), P("k", "Shift", 26, 2, 120)], f: (T, p) => { const x = hh(T, p.s), y = ll(T, p.s); return shift(map(T.c.length, i => (x[i] + y[i]) / 2), p.k); }, hint: { op: "lt", rev: true } },
  // bands & channels
  atr: { name: "ATR (average true range)", short: "ATR", group: "Volatility", scale: "price", params: [N(14, 2, 100)], f: (T, p) => atr(T, p.n), hint: { op: "rise", n: 3 } },
  atr_pct: { name: "ATR as % of the price", short: "ATR %", group: "Volatility", scale: "pct", params: [N(14, 2, 100)], f: (T, p) => { const a = atr(T, p.n); return map(T.c.length, i => a[i] / T.c[i] * 100); }, hint: { op: "lt", b: 3 } },
  hv: { name: "Historical volatility (yearly %)", short: "Volatility", group: "Volatility", scale: "pct", params: [N(20, 5, 252)], f: (T, p) => { const r = map(T.c.length, i => i ? Math.log(T.c[i] / T.c[i - 1]) : NaN), m = sma(r, p.n), s = stdev(r, p.n, m); return map(T.c.length, i => s[i] * Math.sqrt(252) * 100); }, hint: { op: "lt", b: 30 } },
  bb_up: { name: "Bollinger upper band", short: "BB upper", group: "Volatility", scale: "price", params: [N(20, 5, 100), P("k", "Width", 2, 1, 3.5, 0.25, "std devs")], f: (T, p) => { const m = sma(T.c, p.n), s = stdev(T.c, p.n, m); return map(T.c.length, i => m[i] + p.k * s[i]); }, hint: { op: "gt", rev: true } },
  bb_mid: { name: "Bollinger middle band", short: "BB middle", group: "Volatility", scale: "price", params: [N(20, 5, 100)], f: (T, p) => sma(T.c, p.n), hint: { op: "lt", rev: true } },
  bb_lo: { name: "Bollinger lower band", short: "BB lower", group: "Volatility", scale: "price", params: [N(20, 5, 100), P("k", "Width", 2, 1, 3.5, 0.25, "std devs")], f: (T, p) => { const m = sma(T.c, p.n), s = stdev(T.c, p.n, m); return map(T.c.length, i => m[i] - p.k * s[i]); }, hint: { op: "lt", rev: true } },
  bb_pct: { name: "Bollinger %B", short: "%B", group: "Volatility", scale: "num", params: [N(20, 5, 100), P("k", "Width", 2, 1, 3.5, 0.25, "std devs")], f: (T, p) => { const m = sma(T.c, p.n), s = stdev(T.c, p.n, m); return map(T.c.length, i => s[i] > 0 ? (T.c[i] - (m[i] - p.k * s[i])) / (2 * p.k * s[i]) : NaN); }, hint: { op: "lt", b: 0 } },
  bb_width: { name: "Bollinger band width", short: "BB width", group: "Volatility", scale: "pct", params: [N(20, 5, 100), P("k", "Width", 2, 1, 3.5, 0.25, "std devs")], f: (T, p) => { const m = sma(T.c, p.n), s = stdev(T.c, p.n, m); return map(T.c.length, i => 2 * p.k * s[i] / m[i] * 100); }, hint: { op: "lo", n: 120 } },
  kc_up: { name: "Keltner upper channel", short: "KC upper", group: "Volatility", scale: "price", params: [N(20, 5, 100), P("mult", "Width", 2, 0.5, 4, 0.25, "ATRs")], f: (T, p) => { const m = ema(T.c, p.n), a = atr(T, p.n); return map(T.c.length, i => m[i] + p.mult * a[i]); }, hint: { op: "gt", rev: true } },
  kc_lo: { name: "Keltner lower channel", short: "KC lower", group: "Volatility", scale: "price", params: [N(20, 5, 100), P("mult", "Width", 2, 0.5, 4, 0.25, "ATRs")], f: (T, p) => { const m = ema(T.c, p.n), a = atr(T, p.n); return map(T.c.length, i => m[i] - p.mult * a[i]); }, hint: { op: "lt", rev: true } },
  hi_n: { name: "Highest high of the last N bars", short: "N-bar high", group: "Support & resistance", scale: "price", params: [N(20, 2, 252)], f: (T, p) => hh(T, p.n, 1) },
  lo_n: { name: "Lowest low of the last N bars", short: "N-bar low", group: "Support & resistance", scale: "price", params: [N(20, 2, 252)], f: (T, p) => ll(T, p.n, 1) },
  from_hi: { name: "% below the N-bar high close", short: "From high", group: "Support & resistance", scale: "pct", params: [N(252, 5, 504)], f: (T, p) => { const m = roll(T.c, p.n, true); return map(T.c.length, i => (T.c[i] / m[i] - 1) * 100); }, hint: { op: "lt", b: -10 } },
  from_lo: { name: "% above the N-bar low close", short: "From low", group: "Support & resistance", scale: "pct", params: [N(252, 5, 504)], f: (T, p) => { const m = roll(T.c, p.n, false); return map(T.c.length, i => (T.c[i] / m[i] - 1) * 100); }, hint: { op: "gt", b: 20 } },
  dc_up: { name: "Donchian upper (N-bar high)", short: "DC upper", group: "Support & resistance", scale: "price", params: [N(20, 2, 252)], f: (T, p) => hh(T, p.n, 1), hint: { op: "gt", rev: true } },
  dc_lo: { name: "Donchian lower (N-bar low)", short: "DC lower", group: "Support & resistance", scale: "price", params: [N(20, 2, 252)], f: (T, p) => ll(T, p.n, 1), hint: { op: "lt", rev: true } },
  dc_mid: { name: "Donchian middle", short: "DC middle", group: "Support & resistance", scale: "price", params: [N(20, 2, 252)], f: (T, p) => { const a = hh(T, p.n, 1), b = ll(T, p.n, 1); return map(T.c.length, i => (a[i] + b[i]) / 2); }, hint: { op: "lt", rev: true } },
  // momentum
  rsi: { name: "RSI", short: "RSI", group: "Momentum", scale: "osc", params: [N(14, 2, 50)], f: (T, p) => rsiOf(T.c, p.n), hint: { op: "xa", b: 30 } },
  stoch_k: { name: "Stochastic %K", short: "Stoch %K", group: "Momentum", scale: "osc", params: [N(14, 3, 60), P("s", "Smoothing", 3, 1, 10, 1, "bars")], f: (T, p) => { const a = hh(T, p.n), b = ll(T, p.n); return sma(map(T.c.length, i => a[i] > b[i] ? 100 * (T.c[i] - b[i]) / (a[i] - b[i]) : 50), p.s); }, hint: { op: "xa", b: 20 } },
  stoch_d: { name: "Stochastic %D", short: "Stoch %D", group: "Momentum", scale: "osc", params: [N(14, 3, 60), P("s", "Smoothing", 3, 1, 10, 1, "bars"), P("d", "%D length", 3, 1, 10, 1, "bars")], f: (T, p) => sma(IND.stoch_k.f(T, p), p.d), hint: { op: "lt", b: 20 } },
  stoch_rsi: { name: "Stochastic RSI", short: "Stoch RSI", group: "Momentum", scale: "osc", params: [N(14, 3, 50)], f: (T, p) => { const r = rsiOf(T.c, p.n), a = roll(r, p.n, true), b = roll(r, p.n, false); return map(T.c.length, i => a[i] > b[i] ? 100 * (r[i] - b[i]) / (a[i] - b[i]) : NaN); }, hint: { op: "xa", b: 20 } },
  macd: { name: "MACD line", short: "MACD", group: "Momentum", scale: "num", params: [P("fast", "Fast EMA", 12, 2, 50), P("slow", "Slow EMA", 26, 5, 100)], f: (T, p) => { const a = ema(T.c, p.fast), b = ema(T.c, p.slow); return map(T.c.length, i => i >= p.slow ? a[i] - b[i] : NaN); }, hint: { op: "xa", b: { id: "macd_sig" } } },
  macd_sig: { name: "MACD signal line", short: "MACD signal", group: "Momentum", scale: "num", params: [P("fast", "Fast EMA", 12, 2, 50), P("slow", "Slow EMA", 26, 5, 100), P("sig", "Signal", 9, 2, 30)], f: (T, p) => ema(IND.macd.f(T, p), p.sig), hint: { op: "lt", b: 0 } },
  macd_hist: { name: "MACD histogram", short: "MACD hist.", group: "Momentum", scale: "num", params: [P("fast", "Fast EMA", 12, 2, 50), P("slow", "Slow EMA", 26, 5, 100), P("sig", "Signal", 9, 2, 30)], f: (T, p) => { const m = IND.macd.f(T, p), s = ema(m, p.sig); return map(T.c.length, i => m[i] - s[i]); }, hint: { op: "xa", b: 0 } },
  cci: { name: "CCI", short: "CCI", group: "Momentum", scale: "num", params: [N(20, 5, 100)], f: (T, p) => { const tp = map(T.c.length, i => (T.h[i] + T.l[i] + T.c[i]) / 3), m = sma(tp, p.n); return map(T.c.length, i => { if (!ok(m[i])) return NaN; let d = 0; for (let j = i - p.n + 1; j <= i; j++) d += Math.abs(tp[j] - m[i]); d /= p.n; return d > 0 ? (tp[i] - m[i]) / (0.015 * d) : 0; }); }, hint: { op: "xa", b: -100 } },
  willr: { name: "Williams %R", short: "%R", group: "Momentum", scale: "num", params: [N(14, 3, 60)], f: (T, p) => { const a = hh(T, p.n), b = ll(T, p.n); return map(T.c.length, i => a[i] > b[i] ? -100 * (a[i] - T.c[i]) / (a[i] - b[i]) : NaN); }, hint: { op: "xa", b: -80 } },
  roc: { name: "Rate of change", short: "ROC", group: "Momentum", scale: "pct", params: [N(12, 1, 252)], f: (T, p) => IND.chg.f(T, p), hint: { op: "xa", b: 0 } },
  // volatility
  // volume
  volume: { name: "Volume", short: "Volume", group: "Volume", scale: "vol", params: [], needs: "v", f: T => vol(T), hint: { op: "gt", b: { id: "vol_ma" }, mul: 1.5 } },
  vol_ma: { name: "Average volume (last N bars)", short: "Avg volume", group: "Volume", scale: "vol", params: [N(20, 2, 252)], needs: "v", f: (T, p) => shift(sma(vol(T), p.n), 1) },
  rvol: { name: "Relative volume (× average)", short: "Rel. volume", group: "Volume", scale: "num", params: [N(20, 2, 252)], needs: "v", f: (T, p) => { const v = vol(T), a = shift(sma(v, p.n), 1); return map(T.c.length, i => v[i] / a[i]); }, hint: { op: "gt", b: 2 } },
  obv: { name: "On-balance volume", short: "OBV", group: "Volume", scale: "num", params: [], needs: "v", f: T => { const v = vol(T); let s = 0; return map(T.c.length, i => (s += i && ok(v[i]) ? Math.sign(T.c[i] - T.c[i - 1]) * v[i] : 0)); }, hint: { op: "gt", b: { id: "obv_ma" } } },
  obv_ma: { name: "OBV moving average", short: "OBV avg", group: "Volume", scale: "num", params: [N(20, 2, 200)], needs: "v", f: (T, p) => sma(IND.obv.f(T), p.n) },
  ad: { name: "Accumulation / distribution", short: "A/D", group: "Volume", scale: "num", params: [], needs: "v", f: T => { const v = vol(T); let s = 0; return map(T.c.length, i => { const r = T.h[i] - T.l[i]; if (r > 0 && ok(v[i])) s += ((T.c[i] - T.l[i]) - (T.h[i] - T.c[i])) / r * v[i]; return s; }); }, hint: { op: "rise", n: 5 } },
  cmf: { name: "Chaikin money flow", short: "CMF", group: "Volume", scale: "num", params: [N(20, 3, 100)], needs: "v", f: (T, p) => { const v = vol(T), n = T.c.length, mf = new Float64Array(n), vv = new Float64Array(n); for (let i = 0; i < n; i++) { const r = T.h[i] - T.l[i]; if (ok(v[i])) { vv[i] = v[i]; if (r > 0) mf[i] = ((T.c[i] - T.l[i]) - (T.h[i] - T.c[i])) / r * v[i]; } } const a = sma(mf, p.n), b = sma(vv, p.n); return map(n, i => b[i] > 0 ? a[i] / b[i] : NaN); }, hint: { op: "gt", b: 0.1 } },
  mfi: { name: "Money flow index", short: "MFI", group: "Volume", scale: "osc", params: [N(14, 3, 50)], needs: "v", f: (T, p) => { const v = vol(T), n = T.c.length, tp = map(n, i => (T.h[i] + T.l[i] + T.c[i]) / 3), pos = new Float64Array(n), neg = new Float64Array(n); for (let i = 1; i < n; i++) { const f = ok(v[i]) ? tp[i] * v[i] : 0; if (tp[i] > tp[i - 1]) pos[i] = f; else if (tp[i] < tp[i - 1]) neg[i] = f; } const a = sma(pos, p.n), b = sma(neg, p.n); return map(n, i => i < p.n ? NaN : b[i] === 0 ? 100 : 100 - 100 / (1 + a[i] / b[i])); }, hint: { op: "xa", b: 20 } },
  vp_poc: { name: "Point of control (volume profile, last N bars)", short: "POC", group: "Volume", scale: "price", params: [N(20, 5, 252), P("va", "Value area", 70, 50, 90, 5, "%")], needs: "v", f: (T, p) => valueArea(T, p.n, p.va / 100).poc, hint: { op: "lt", rev: true } },
  vp_vah: { name: "Value area high (volume profile, last N bars)", short: "VAH", group: "Volume", scale: "price", params: [N(20, 5, 252), P("va", "Value area", 70, 50, 90, 5, "%")], needs: "v", f: (T, p) => valueArea(T, p.n, p.va / 100).vah, hint: { op: "lt", rev: true } },
  vp_val: { name: "Value area low (volume profile, last N bars)", short: "VAL", group: "Volume", scale: "price", params: [N(20, 5, 252), P("va", "Value area", 70, 50, 90, 5, "%")], needs: "v", f: (T, p) => valueArea(T, p.n, p.va / 100).val, hint: { op: "lt", rev: true } },
  vwap: { name: "Rolling VWAP", short: "VWAP", group: "Volume", scale: "price", params: [N(20, 2, 252)], needs: "v", f: (T, p) => { const v = vol(T), tp = map(T.c.length, i => (T.h[i] + T.l[i] + T.c[i]) / 3 * v[i]); const a = sma(tp, p.n), b = sma(v, p.n); return map(T.c.length, i => a[i] / b[i]); }, hint: { op: "lt", rev: true } },
  // market & relative
  mkt: { name: "Market (benchmark price)", short: "Market", group: "Market & relative", scale: "mkt", params: [], needs: "bench", f: (T, p, data) => benchOn(T, data), hint: { op: "gt", b: { id: "mkt_ma" } } },
  mkt_ma: { name: "Market's moving average", short: "Market SMA", group: "Market & relative", scale: "mkt", params: [N(200)], needs: "bench", f: (T, p, data) => { if (!data?.bench) return nan(T.c.length); const k = "mkt_ma" + p.n, m = data[k] || (data[k] = sma(data.bench, p.n)); return map(T.c.length, i => m[T.idx[i]]); } },
  mkt_chg: { name: "Market's change over N bars", short: "Market change", group: "Market & relative", scale: "pct", params: [N(20, 1, 252)], needs: "bench", f: (T, p, data) => { const b = benchOn(T, data); return map(T.c.length, i => i >= p.n ? (b[i] / b[i - p.n] - 1) * 100 : NaN); }, hint: { op: "gt", b: 0 } },
  rs: { name: "Relative strength line (price ÷ market)", short: "RS line", group: "Market & relative", scale: "rs", params: [], needs: "bench", f: (T, p, data) => { const b = benchOn(T, data); return map(T.c.length, i => T.c[i] / b[i] * 100); }, hint: { op: "hi", n: 63 } },
  rs_chg: { name: "Beat the market by, over N bars", short: "vs market", group: "Market & relative", scale: "pct", params: [N(63, 5, 252)], needs: "bench", f: (T, p, data) => { const b = benchOn(T, data); return map(T.c.length, i => i >= p.n ? ((T.c[i] / T.c[i - p.n]) / (b[i] / b[i - p.n]) - 1) * 100 : NaN); }, hint: { op: "gt", b: 0 } },
  // calendar
  dow: { name: "Day of the week (1 Mon … 7 Sun)", short: "Weekday", group: "Calendar", scale: "cal", params: [], f: (T, p, data) => map(T.c.length, i => (new Date(dateOf(T, data, i) + "T00:00:00Z").getUTCDay() + 6) % 7 + 1), hint: { op: "eq", b: 1 } },
  month: { name: "Month (1 Jan … 12 Dec)", short: "Month", group: "Calendar", scale: "cal", params: [], f: (T, p, data) => map(T.c.length, i => +dateOf(T, data, i).slice(5, 7)), hint: { op: "eq", b: 11 } },
  dom: { name: "Day of the month", short: "Day of month", group: "Calendar", scale: "cal", params: [], f: (T, p, data) => map(T.c.length, i => +dateOf(T, data, i).slice(8, 10)), hint: { op: "lt", b: 6 } },
};
for (const [k, I] of Object.entries(IND)) I.id = k;

export const OPS = {
  gt: { name: "is above", short: ">" }, lt: { name: "is below", short: "<" },
  xa: { name: "crosses above", short: "crosses above" }, xb: { name: "crosses below", short: "crosses below" },
  eq: { name: "equals", short: "=" },
  rise: { name: "has risen for", short: "rising", bars: true }, fall: { name: "has fallen for", short: "falling", bars: true },
  hi: { name: "is the highest of the last", short: "highest of", bars: true }, lo: { name: "is the lowest of the last", short: "lowest of", bars: true },
};
export const unary = op => !!OPS[op]?.bars;

// ---------------------------------------------------------------- patterns
// side: +1 bullish, -1 bearish, 0 neutral (tested as a buy). open: needs the day's opening price.
const C = T => { const o = openOf(T), { c, h, l } = T, n = c.length; return { o, c, h, l, n,
  body: i => Math.abs(c[i] - o[i]), rng: i => h[i] - l[i], up: i => h[i] - Math.max(o[i], c[i]), dn: i => Math.min(o[i], c[i]) - l[i],
  green: i => c[i] > o[i], red: i => c[i] < o[i], fell: i => i >= 6 && c[i - 1] < c[i - 6], rose: i => i >= 6 && c[i - 1] > c[i - 6] }; };
const det = (T, from, f) => { const out = new Uint8Array(T.c.length); for (let i = from; i < T.c.length; i++) if (f(i)) out[i] = 1; return out; };
// a "big" body: larger than the average body of the last 10 days
const bigBody = (K, i) => { let s = 0; for (let j = Math.max(0, i - 10); j < i; j++) s += K.body(j); return K.body(i) > s / Math.max(1, Math.min(10, i)); };
const hammerShape = (K, i) => K.rng(i) > 0 && K.dn(i) >= 2 * K.body(i) && K.up(i) <= Math.max(K.body(i) * 0.6, K.rng(i) * 0.1) && K.body(i) >= K.rng(i) * 0.05;
const starShape = (K, i) => K.rng(i) > 0 && K.up(i) >= 2 * K.body(i) && K.dn(i) <= Math.max(K.body(i) * 0.6, K.rng(i) * 0.1) && K.body(i) >= K.rng(i) * 0.05;
const doji = (K, i) => K.rng(i) > 0 && K.body(i) <= K.rng(i) * 0.1;
const PAT = (name, side, group, about, f, extra = {}) => ({ name, side, group, about, f, open: group === "Candles", ...extra });
export const PAT_GROUPS = ["Candles", "Price action", "Chart patterns"];
export const PATTERNS = {
  hammer: PAT("Hammer", 1, "Candles", "Small body at the top, long lower wick (2× the body), after a fall.", T => { const K = C(T); return det(T, 6, i => K.fell(i) && hammerShape(K, i)); }),
  inv_hammer: PAT("Inverted hammer", 1, "Candles", "Small body at the bottom, long upper wick, after a fall.", T => { const K = C(T); return det(T, 6, i => K.fell(i) && starShape(K, i)); }),
  engulf_bull: PAT("Bullish engulfing", 1, "Candles", "An up day whose body covers the whole body of the down day before.", T => { const K = C(T); return det(T, 1, i => K.red(i - 1) && K.green(i) && K.o[i] <= K.c[i - 1] && K.c[i] >= K.o[i - 1] && K.body(i) > K.body(i - 1)); }),
  harami_bull: PAT("Bullish harami", 1, "Candles", "A small up day inside the body of a big down day, after a fall.", T => { const K = C(T); return det(T, 6, i => K.fell(i) && K.red(i - 1) && bigBody(K, i - 1) && K.green(i) && K.o[i] > K.c[i - 1] && K.c[i] < K.o[i - 1]); }),
  piercing: PAT("Piercing line", 1, "Candles", "After a big down day it opens lower, then closes above the middle of that day's body.", T => { const K = C(T); return det(T, 1, i => K.red(i - 1) && bigBody(K, i - 1) && K.o[i] < K.c[i - 1] && K.c[i] > (K.o[i - 1] + K.c[i - 1]) / 2 && K.c[i] < K.o[i - 1]); }),
  morning_star: PAT("Morning star", 1, "Candles", "A big down day, a small-bodied day, then an up day closing above the middle of the first.", T => { const K = C(T); return det(T, 2, i => K.red(i - 2) && bigBody(K, i - 2) && K.body(i - 1) <= K.body(i - 2) * 0.35 && K.green(i) && K.c[i] > (K.o[i - 2] + K.c[i - 2]) / 2); }),
  soldiers: PAT("Three white soldiers", 1, "Candles", "Three up days in a row, each closing higher and near its high.", T => { const K = C(T); return det(T, 3, i => [0, 1, 2].every(k => K.green(i - k) && K.up(i - k) <= K.body(i - k) * 0.35) && K.c[i] > K.c[i - 1] && K.c[i - 1] > K.c[i - 2] && !(K.green(i - 3) && K.c[i - 3] < K.c[i - 2] && K.up(i - 3) <= K.body(i - 3) * 0.35 && K.c[i - 2] > K.c[i - 3])); }),
  dragonfly: PAT("Dragonfly doji", 1, "Candles", "Opens and closes at the top of the day after a long fall inside it, after a fall.", T => { const K = C(T); return det(T, 6, i => K.fell(i) && doji(K, i) && K.dn(i) >= K.rng(i) * 0.7); }),
  tweezer_bot: PAT("Tweezer bottom", 1, "Candles", "Two days with the same low (a down day, then an up day), after a fall.", T => { const K = C(T); return det(T, 6, i => K.fell(i) && K.red(i - 1) && K.green(i) && Math.abs(K.l[i] - K.l[i - 1]) <= K.c[i] * 0.002); }),
  marubozu_bull: PAT("Bullish marubozu", 1, "Candles", "A big up day with almost no wicks: buyers in control all day.", T => { const K = C(T); return det(T, 1, i => K.green(i) && K.rng(i) > 0 && K.body(i) >= K.rng(i) * 0.9 && bigBody(K, i)); }),
  hanging_man: PAT("Hanging man", -1, "Candles", "A hammer shape after a rise: sellers pushed the price down during the day.", T => { const K = C(T); return det(T, 6, i => K.rose(i) && hammerShape(K, i)); }),
  shooting_star: PAT("Shooting star", -1, "Candles", "Small body at the bottom, long upper wick, after a rise.", T => { const K = C(T); return det(T, 6, i => K.rose(i) && starShape(K, i)); }),
  engulf_bear: PAT("Bearish engulfing", -1, "Candles", "A down day whose body covers the whole body of the up day before.", T => { const K = C(T); return det(T, 1, i => K.green(i - 1) && K.red(i) && K.o[i] >= K.c[i - 1] && K.c[i] <= K.o[i - 1] && K.body(i) > K.body(i - 1)); }),
  harami_bear: PAT("Bearish harami", -1, "Candles", "A small down day inside the body of a big up day, after a rise.", T => { const K = C(T); return det(T, 6, i => K.rose(i) && K.green(i - 1) && bigBody(K, i - 1) && K.red(i) && K.o[i] < K.c[i - 1] && K.c[i] > K.o[i - 1]); }),
  dark_cloud: PAT("Dark cloud cover", -1, "Candles", "After a big up day it opens higher, then closes below the middle of that day's body.", T => { const K = C(T); return det(T, 1, i => K.green(i - 1) && bigBody(K, i - 1) && K.o[i] > K.c[i - 1] && K.c[i] < (K.o[i - 1] + K.c[i - 1]) / 2 && K.c[i] > K.o[i - 1]); }),
  evening_star: PAT("Evening star", -1, "Candles", "A big up day, a small-bodied day, then a down day closing below the middle of the first.", T => { const K = C(T); return det(T, 2, i => K.green(i - 2) && bigBody(K, i - 2) && K.body(i - 1) <= K.body(i - 2) * 0.35 && K.red(i) && K.c[i] < (K.o[i - 2] + K.c[i - 2]) / 2); }),
  crows: PAT("Three black crows", -1, "Candles", "Three down days in a row, each closing lower and near its low.", T => { const K = C(T); return det(T, 3, i => [0, 1, 2].every(k => K.red(i - k) && K.dn(i - k) <= K.body(i - k) * 0.35) && K.c[i] < K.c[i - 1] && K.c[i - 1] < K.c[i - 2] && !(K.red(i - 3) && K.c[i - 3] > K.c[i - 2] && K.dn(i - 3) <= K.body(i - 3) * 0.35 && K.c[i - 2] < K.c[i - 3])); }),
  gravestone: PAT("Gravestone doji", -1, "Candles", "Opens and closes at the bottom of the day after a long rise inside it, after a rise.", T => { const K = C(T); return det(T, 6, i => K.rose(i) && doji(K, i) && K.up(i) >= K.rng(i) * 0.7); }),
  tweezer_top: PAT("Tweezer top", -1, "Candles", "Two days with the same high (an up day, then a down day), after a rise.", T => { const K = C(T); return det(T, 6, i => K.rose(i) && K.green(i - 1) && K.red(i) && Math.abs(K.h[i] - K.h[i - 1]) <= K.c[i] * 0.002); }),
  marubozu_bear: PAT("Bearish marubozu", -1, "Candles", "A big down day with almost no wicks: sellers in control all day.", T => { const K = C(T); return det(T, 1, i => K.red(i) && K.rng(i) > 0 && K.body(i) >= K.rng(i) * 0.9 && bigBody(K, i)); }),
  doji: PAT("Doji", 0, "Candles", "Opens and closes at almost the same price: indecision. Tested as a buy.", T => { const K = C(T); return det(T, 0, i => doji(K, i)); }),
  // price action (high, low and close only)
  pin_bull: PAT("Bullish pin bar", 1, "Price action", "A long lower tail (two thirds of the day's range) with the close in the top third.", T => det(T, 0, i => { const r = T.h[i] - T.l[i]; return r > 0 && T.c[i] >= T.h[i] - r / 3 && Math.min(T.c[i], openOf(T)[i]) - T.l[i] >= r * 2 / 3; })),
  pin_bear: PAT("Bearish pin bar", -1, "Price action", "A long upper tail (two thirds of the day's range) with the close in the bottom third.", T => det(T, 0, i => { const r = T.h[i] - T.l[i]; return r > 0 && T.c[i] <= T.l[i] + r / 3 && T.h[i] - Math.max(T.c[i], openOf(T)[i]) >= r * 2 / 3; })),
  inside: PAT("Inside bar", 0, "Price action", "The whole day sits inside the day before: a pause. Tested as a buy.", T => det(T, 1, i => T.h[i] < T.h[i - 1] && T.l[i] > T.l[i - 1])),
  inside_up: PAT("Inside bar breakout up", 1, "Price action", "Closes above the high of the bar before an inside bar.", T => det(T, 2, i => T.h[i - 1] < T.h[i - 2] && T.l[i - 1] > T.l[i - 2] && T.c[i] > T.h[i - 2])),
  inside_dn: PAT("Inside bar breakdown", -1, "Price action", "Closes below the low of the bar before an inside bar.", T => det(T, 2, i => T.h[i - 1] < T.h[i - 2] && T.l[i - 1] > T.l[i - 2] && T.c[i] < T.l[i - 2])),
  outside_up: PAT("Bullish outside reversal", 1, "Price action", "A higher high and a lower low than yesterday, closing above yesterday's high.", T => det(T, 1, i => T.h[i] > T.h[i - 1] && T.l[i] < T.l[i - 1] && T.c[i] > T.h[i - 1])),
  outside_dn: PAT("Bearish outside reversal", -1, "Price action", "A higher high and a lower low than yesterday, closing below yesterday's low.", T => det(T, 1, i => T.h[i] > T.h[i - 1] && T.l[i] < T.l[i - 1] && T.c[i] < T.l[i - 1])),
  key_up: PAT("Key reversal up", 1, "Price action", "A new 10-day low, then a close above yesterday's close.", T => { const lo = ll(T, 10, 1); return det(T, 11, i => T.l[i] < lo[i] && T.c[i] > T.c[i - 1]); }),
  key_dn: PAT("Key reversal down", -1, "Price action", "A new 10-day high, then a close below yesterday's close.", T => { const hi = hh(T, 10, 1); return det(T, 11, i => T.h[i] > hi[i] && T.c[i] < T.c[i - 1]); }),
  nr7: PAT("NR7 (narrowest range of 7)", 0, "Price action", "The smallest daily range of the last 7 days: often comes before a big move. Tested as a buy.", T => { const r = map(T.c.length, i => T.h[i] - T.l[i]), m = roll(r, 7, false); return det(T, 7, i => r[i] <= m[i]); }),
  gap_up: PAT("Gap up", 1, "Price action", "The whole day is above yesterday's high.", T => det(T, 1, i => T.l[i] > T.h[i - 1])),
  gap_dn: PAT("Gap down", -1, "Price action", "The whole day is below yesterday's low.", T => det(T, 1, i => T.h[i] < T.l[i - 1])),
  fvg_bull: PAT("Bullish fair value gap", 1, "Price action", "Three bars where today's low is above the high two days ago: a gap the price jumped over.", T => det(T, 2, i => T.l[i] > T.h[i - 2])),
  fvg_bear: PAT("Bearish fair value gap", -1, "Price action", "Three bars where today's high is below the low two days ago.", T => det(T, 2, i => T.h[i] < T.l[i - 2])),
  hh_hl: PAT("Higher high and higher low", 1, "Price action", "Both today's high and low are above yesterday's.", T => det(T, 1, i => T.h[i] > T.h[i - 1] && T.l[i] > T.l[i - 1])),
  ll_lh: PAT("Lower low and lower high", -1, "Price action", "Both today's high and low are below yesterday's.", T => det(T, 1, i => T.h[i] < T.h[i - 1] && T.l[i] < T.l[i - 1])),
  brk20: PAT("20-day breakout", 1, "Price action", "Closes above the highest high of the last 20 days.", T => { const a = hh(T, 20, 1); return det(T, 21, i => T.c[i] > a[i] && !(T.c[i - 1] > a[i - 1])); }),
  brkdn20: PAT("20-day breakdown", -1, "Price action", "Closes below the lowest low of the last 20 days.", T => { const a = ll(T, 20, 1); return det(T, 21, i => T.c[i] < a[i] && !(T.c[i - 1] < a[i - 1])); }),
  hi52: PAT("New 52-week high", 1, "Price action", "Closes above the highest high of the last year.", T => { const a = hh(T, 252, 1); return det(T, 253, i => T.c[i] > a[i] && !(T.c[i - 1] > a[i - 1])); }),
  lo52: PAT("New 52-week low", -1, "Price action", "Closes below the lowest low of the last year.", T => { const a = ll(T, 252, 1); return det(T, 253, i => T.c[i] < a[i] && !(T.c[i - 1] < a[i - 1])); }),
  // the weekly backtest's chart patterns (lab.json "pre"): fixed rules
  dbl_bottom: PAT("Double bottom", 1, "Chart patterns", "A break above the neckline of a double bottom.", (T) => pre(T, "double_top_bottom", 1), { pre: "double_top_bottom" }),
  dbl_top: PAT("Double top", -1, "Chart patterns", "A break below the neckline of a double top.", (T) => pre(T, "double_top_bottom", -1), { pre: "double_top_bottom" }),
  inv_hs: PAT("Inverse head & shoulders", 1, "Chart patterns", "A break above the neckline of an inverse head & shoulders.", (T) => pre(T, "head_shoulders", 1), { pre: "head_shoulders" }),
  hs: PAT("Head & shoulders", -1, "Chart patterns", "A break below the neckline of a head & shoulders top.", (T) => pre(T, "head_shoulders", -1), { pre: "head_shoulders" }),
  tri_up: PAT("Triangle breakout", 1, "Chart patterns", "A break up out of a tightening triangle.", (T) => pre(T, "triangle", 1), { pre: "triangle" }),
  tri_dn: PAT("Triangle breakdown", -1, "Chart patterns", "A break down out of a tightening triangle.", (T) => pre(T, "triangle", -1), { pre: "triangle" }),
};
for (const [k, X] of Object.entries(PATTERNS)) X.id = k;
function pre(T, sid, side) {
  const out = new Uint8Array(T.c.length), raw = T.pre?.[sid];
  if (raw) for (const x of raw) { const i = Math.abs(x) - 1; if (i < out.length && Math.sign(x) === side) out[i] = 1; }
  return out;
}
export const patternOk = (data, id) => { const X = PATTERNS[id]; return !!X && (!X.pre || data.tickers.some(T => T.pre?.[X.pre])); };
export const hasOpens = data => data.tickers.some(T => T.o);

// ------------------------------------------------------------- evaluation
// Values are cached per ticker (a few dozen series at most), so a rule used by many conditions,
// or the optimizer's many runs, computes each series once.
const CACHE = new WeakMap();
function memo(T, key, f) {
  let m = CACHE.get(T); if (!m) CACHE.set(T, m = new Map());
  if (m.has(key)) { const v = m.get(key); m.delete(key); m.set(key, v); return v; }
  const v = f(); m.set(key, v);
  if (m.size > 60) m.delete(m.keys().next().value);
  return v;
}
// a spec's params filled in and kept in range
export function specP(s) {
  const I = IND[s.id], p = {};
  for (const x of I?.params || []) { const v = +s.p?.[x.k]; p[x.k] = Number.isFinite(v) ? Math.min(x.max, Math.max(x.min, v)) : x.def; }
  return p;
}
// weekly bars of a ticker (a week closes on its last trading day); day i may only use weeks that have
// closed by day i's close
function weekly(T, data) {
  return memo(T, "#W", () => {
    const wk = d => Math.floor((Date.parse(d + "T00:00:00Z") / 864e5 + 3) / 7);
    const n = T.c.length, key = new Int32Array(n);
    for (let i = 0; i < n; i++) key[i] = wk(data.dates[T.idx[i]]);
    const W = { c: [], h: [], l: [], o: [], v: [], idx: [], t: T.t, pre: null }, done = new Int32Array(n);
    const o = openOf(T), v = T.v;
    for (let i = 0; i < n; i++) {
      if (!i || key[i] !== key[i - 1]) { W.o.push(o[i]); W.h.push(T.h[i]); W.l.push(T.l[i]); W.v.push(v ? (ok(v[i]) ? v[i] : 0) : NaN); W.c.push(T.c[i]); W.idx.push(T.idx[i]); }
      else { const k = W.c.length - 1; W.h[k] = Math.max(W.h[k], T.h[i]); W.l[k] = Math.min(W.l[k], T.l[i]); if (v && ok(v[i])) W.v[k] += v[i]; W.c[k] = T.c[i]; W.idx[k] = T.idx[i]; }
      // the week has closed at day i when the next day is in another week (the last day: when it's a Friday or later)
      const last = i === n - 1 ? [5, 6, 0].includes(new Date(data.dates[T.idx[i]] + "T00:00:00Z").getUTCDay()) : key[i + 1] !== key[i];
      done[i] = last ? W.c.length - 1 : W.c.length - 2;
    }
    const out = { c: Float64Array.from(W.c), h: Float64Array.from(W.h), l: Float64Array.from(W.l), o: Float64Array.from(W.o),
      v: v ? Float64Array.from(W.v, x => x > 0 ? x : NaN) : null, idx: Int32Array.from(W.idx), t: T.t, pre: null };
    return { W: out, done };
  });
}
// one series for one ticker
export function series(T, data, s) {
  const I = IND[s.id]; if (!I) return nan(T.c.length);
  const p = specP(s), tf = s.tf === "W" ? "W" : "D", ago = Math.max(0, Math.min(250, Math.round(+s.ago || 0)));
  const key = s.id + "|" + tf + "|" + JSON.stringify(p) + "|" + ago;
  return memo(T, key, () => {
    let x;
    if (tf === "W") {
      const { W, done } = weekly(T, data), wv = I.f(W, p, data);
      x = map(T.c.length, i => done[i] >= 0 ? wv[done[i]] : NaN);
      if (ago) { const out = nan(T.c.length); for (let i = 0; i < out.length; i++) { const k = done[i] - ago; out[i] = k >= 0 ? wv[k] : NaN; } return out; }
    } else x = I.f(T, p, data);
    return ago ? shift(x, ago) : x;
  });
}
// true/false per day for one condition
export function condition(T, data, q) {
  const n = T.c.length;
  let out;
  if (q.pat) {
    const X = PATTERNS[q.pat]; out = X ? memo(T, "#P" + q.pat, () => X.f(T)) : new Uint8Array(n);
  } else {
    const a = series(T, data, q.a || { id: "close" }), op = OPS[q.op] ? q.op : "gt", k = Math.max(1, Math.min(400, Math.round(+q.n || 1)));
    out = new Uint8Array(n);
    if (unary(op)) {
      if (op === "rise" || op === "fall") {
        let run = 0;
        for (let i = 1; i < n; i++) { run = (op === "rise" ? a[i] > a[i - 1] : a[i] < a[i - 1]) ? run + 1 : 0; out[i] = run >= k ? 1 : 0; }
      } else {
        const m = roll(a, k, op === "hi");
        for (let i = k - 1; i < n; i++) out[i] = ok(a[i]) && a[i] === m[i] ? 1 : 0;
      }
    } else {
      const mul = Number.isFinite(+q.mul) && +q.mul ? +q.mul : 1;
      const b = typeof q.b === "object" && q.b ? series(T, data, q.b) : null, c0 = +q.b || 0;
      const B = i => (b ? b[i] : c0) * (b ? mul : 1);
      for (let i = 0; i < n; i++) {
        const x = a[i], y = B(i);
        if (!ok(x) || !ok(y)) continue;
        if (op === "gt") out[i] = x > y ? 1 : 0;
        else if (op === "lt") out[i] = x < y ? 1 : 0;
        else if (op === "eq") out[i] = Math.abs(x - y) < 1e-9 ? 1 : 0;
        else if (i > 0) {
          const px = a[i - 1], py = B(i - 1);
          if (op === "xa") out[i] = x > y && px <= py ? 1 : 0;
          else out[i] = x < y && px >= py ? 1 : 0;
        }
      }
    }
  }
  const w = Math.max(1, Math.min(60, Math.round(+q.w || 1)));
  if (w > 1) { const o2 = new Uint8Array(n); let last = -1e9; for (let i = 0; i < n; i++) { if (out[i]) last = i; o2[i] = i - last < w ? 1 : 0; } out = o2; }
  return out;
}
// a rule group: all (or any) of its conditions; an empty group is never true
export function group(T, data, g) {
  const list = (g?.list || []).filter(Boolean), n = T.c.length, out = new Uint8Array(n);
  if (!list.length) return out;
  const any = g.mode === "any", parts = list.map(q => condition(T, data, q));
  for (let i = 0; i < n; i++) {
    let v = any ? 0 : 1;
    for (const p of parts) { if (any) { if (p[i]) { v = 1; break; } } else if (!p[i]) { v = 0; break; } }
    out[i] = v;
  }
  return out;
}
// what a rule set needs from the data ("o" opens, "v" volume, "bench" a benchmark, pre ids)
export function needs(rules) {
  const out = new Set(), spec = s => { const I = IND[s?.id]; if (!I) return; if (I.open) out.add("o"); if (I.needs) out.add(I.needs); };
  for (const g of [rules?.long, rules?.short, rules?.xl, rules?.xs]) for (const q of g?.list || []) {
    if (q.pat) { const X = PATTERNS[q.pat]; if (X?.open) out.add("o"); if (X?.pre) out.add("pre:" + X.pre); }
    else { spec(q.a); if (typeof q.b === "object") spec(q.b); }
  }
  return out;
}

// --------------------------------------------------------- your own rules
// Entries: +1 buy, -1 short (a day where both are true is skipped). With `edge`, only the first day
// the rules become true counts (not every day they stay true). Exits: xl closes a buy, xs a short.
export function customSignals(T, data, rules) {
  const n = T.c.length, sig = new Int8Array(n), L = group(T, data, rules?.long), S = group(T, data, rules?.short), edge = rules?.edge !== 0;
  for (let i = 0; i < n; i++) {
    const b = L[i] && (!edge || !i || !L[i - 1]), s = S[i] && (!edge || !i || !S[i - 1]);
    sig[i] = b && !s ? 1 : s && !b ? -1 : 0;
  }
  return { sig, xl: group(T, data, rules?.xl), xs: group(T, data, rules?.xs) };
}

// --------------------------------------------------------- pattern tester
// How the price moved after each pattern, in the pattern's direction (neutral ones as a buy), from
// the close of the day it appeared, `hz` sessions later. `gate(T)` (optional) returns which days
// count (a trend or volume filter). Also the same for every day ("any day"), the baseline a pattern
// has to beat to show an edge.
export function patternStats(data, ids, { fromIdx = 0, hz = [1, 5, 10, 20], gate = null, examples = 30 } = {}) {
  const res = ids.map(id => ({ id, side: PATTERNS[id].side || 1, n: 0, r: hz.map(() => []), last: [], years: {} }));
  const base = hz.map(() => ({ s: 0, k: 0, w: 0 }));
  for (const T of data.tickers) {
    const n = T.c.length, g = gate ? gate(T) : null, c = T.c;
    for (let i = 0; i < n; i++) {
      if (T.idx[i] < fromIdx || (g && !g[i])) continue;
      hz.forEach((h, k) => { if (i + h < n) { const r = c[i + h] / c[i] - 1; base[k].s += r; base[k].k++; if (r > 0) base[k].w++; } });
    }
    for (const R of res) {
      const hit = memo(T, "#P" + R.id, () => PATTERNS[R.id].f(T));
      for (let i = 0; i < n; i++) {
        if (!hit[i] || T.idx[i] < fromIdx || (g && !g[i])) continue;
        R.n++;
        hz.forEach((h, k) => { if (i + h < n) R.r[k].push(R.side * (c[i + h] / c[i] - 1)); });
        R.last.push({ t: T.t, g: T.idx[i], i, r: i + hz[1] < n ? R.side * (c[i + hz[1]] / c[i] - 1) : null });
        const y = data.dates[T.idx[i]].slice(0, 4), m = hz.indexOf(5) >= 0 ? hz.indexOf(5) : 0;
        if (i + hz[m] < n) { const Y = R.years[y] || (R.years[y] = { s: 0, k: 0 }); Y.s += R.side * (c[i + hz[m]] / c[i] - 1); Y.k++; }
      }
    }
  }
  const med = a => { if (!a.length) return null; const s = Float64Array.from(a).sort(), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
  return {
    hz,
    base: base.map(b => ({ avg: b.k ? b.s / b.k * 100 : null, win: b.k ? b.w / b.k * 100 : null, k: b.k })),
    list: res.map(R => ({
      id: R.id, side: R.side, n: R.n,
      at: R.r.map((a, k) => ({ k: a.length, avg: a.length ? a.reduce((s, x) => s + x, 0) / a.length * 100 : null,
        med: a.length ? med(a) * 100 : null, win: a.length ? a.filter(x => x > 0).length / a.length * 100 : null,
        // with every day's average move in the pattern's direction taken away (a buy has to beat the drift)
        edge: a.length && base[k].k ? (a.reduce((s, x) => s + x, 0) / a.length - R.side * base[k].s / base[k].k) * 100 : null })),
      last: R.last.sort((a, b) => b.g - a.g).slice(0, examples),
      years: Object.entries(R.years).map(([y, Y]) => ({ year: +y, avg: Y.s / Y.k * 100, k: Y.k })).sort((a, b) => a.year - b.year),
    })),
  };
}

// -------------------------------------------------------------- read out
// a condition in words: "RSI(14) crosses above 30", "Close is above SMA(200) on the weekly"
const fmt = v => Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M" : String(+(+v).toFixed(4));
export function specTxt(s) {
  const I = IND[s?.id]; if (!I) return "?";
  const p = specP(s), vals = I.params.map(x => fmt(p[x.k]));
  return (I.short || I.name) + (vals.length ? `(${vals.join(", ")})` : "") + (s.tf === "W" ? " weekly" : "") + (s.ago ? ` ${s.ago} bar${s.ago > 1 ? "s" : ""} ago` : "");
}
export function condTxt(q) {
  let t;
  if (q.pat) t = (PATTERNS[q.pat]?.name || "?") + " appears";
  else {
    const op = OPS[q.op] ? q.op : "gt";
    if (unary(op)) t = `${specTxt(q.a)} ${OPS[op].name} ${Math.max(1, Math.round(+q.n || 1))} bar${(+q.n || 1) > 1 ? "s" : ""}`;
    else t = `${specTxt(q.a)} ${OPS[op].name} ${typeof q.b === "object" && q.b ? ((+q.mul && +q.mul !== 1) ? fmt(+q.mul) + "× " : "") + specTxt(q.b) : fmt(+q.b || 0)}`;
  }
  return t + ((+q.w || 1) > 1 ? ` (in the last ${Math.round(+q.w)} bars)` : "");
}
