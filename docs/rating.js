// Technical rating: a summary of what common indicators say on one timeframe, in the style of
// TradingView's technical ratings. Two groups vote Buy (+1), Sell (-1) or Neutral (0):
//   Moving averages: SMA and EMA of 10, 20, 30, 50, 100 and 200 candles.
//                    Buy when the average is below the last close, Sell when above.
//   Oscillators:     RSI 14, Stochastic 14/3/3, CCI 20, ADX 14, Momentum 10, MACD 12/26/9,
//                    Williams %R 14 (rules below, next to each one).
// Each group scores (buys - sells) / votes, from -1 to +1; the summary is the average of the two.
// Labels: >= 0.5 Strong buy, >= 0.1 Buy, > -0.1 Neutral, > -0.5 Sell, else Strong sell.
// An indicator needs enough candles; without them it is left out (and listed as "not enough data").
// Information only: it describes the indicators, it doesn't predict the price.

export const LABELS = ["Strong sell", "Sell", "Neutral", "Buy", "Strong buy"];
export function labelOf(score) {
  if (score === null || Number.isNaN(score)) return null;
  return score >= 0.5 ? "Strong buy" : score >= 0.1 ? "Buy" : score > -0.1 ? "Neutral" : score > -0.5 ? "Sell" : "Strong sell";
}

// ------------------------------------------------------------------ indicator maths (arrays, oldest first)
export function sma(v, n) {
  const out = Array(v.length).fill(null);
  let s = 0;
  for (let i = 0; i < v.length; i++) {
    s += v[i];
    if (i >= n) s -= v[i - n];
    if (i >= n - 1) out[i] = s / n;
  }
  return out;
}
// EMA seeded with the SMA of the first n values (the usual charting convention)
export function ema(v, n) {
  const out = Array(v.length).fill(null), k = 2 / (n + 1);
  if (v.length < n) return out;
  let e = v.slice(0, n).reduce((a, b) => a + b, 0) / n;
  out[n - 1] = e;
  for (let i = n; i < v.length; i++) { e = v[i] * k + e * (1 - k); out[i] = e; }
  return out;
}
// Wilder's smoothing (RMA), seeded with the simple average of the first n values
function rma(v, n, start = 0) {
  const out = Array(v.length).fill(null);
  if (v.length - start < n) return out;
  let r = 0;
  for (let i = start; i < start + n; i++) r += v[i];
  r /= n; out[start + n - 1] = r;
  for (let i = start + n; i < v.length; i++) { r = (r * (n - 1) + v[i]) / n; out[i] = r; }
  return out;
}
export function rsi(c, n = 14) {
  const up = [0], dn = [0];
  for (let i = 1; i < c.length; i++) { const d = c[i] - c[i - 1]; up.push(Math.max(d, 0)); dn.push(Math.max(-d, 0)); }
  const au = rma(up, n, 1), ad = rma(dn, n, 1);
  return au.map((u, i) => u === null ? null : ad[i] === 0 ? 100 : 100 - 100 / (1 + u / ad[i]));
}
export function stoch(h, l, c, n = 14, sk = 3, sd = 3) {
  const raw = c.map((x, i) => {
    if (i < n - 1) return null;
    let hi = -Infinity, lo = Infinity;
    for (let j = i - n + 1; j <= i; j++) { hi = Math.max(hi, h[j]); lo = Math.min(lo, l[j]); }
    return hi === lo ? 50 : (x - lo) / (hi - lo) * 100;
  });
  const smooth = (v, m) => v.map((_, i) => {
    if (i < m - 1) return null;
    const w = v.slice(i - m + 1, i + 1);
    return w.some(x => x === null) ? null : w.reduce((a, b) => a + b, 0) / m;
  });
  const k = smooth(raw, sk), d = smooth(k, sd);
  return { k, d };
}
export function cci(h, l, c, n = 20) {
  const tp = c.map((x, i) => (h[i] + l[i] + x) / 3), m = sma(tp, n);
  return tp.map((x, i) => {
    if (m[i] === null) return null;
    let md = 0;
    for (let j = i - n + 1; j <= i; j++) md += Math.abs(tp[j] - m[i]);
    md /= n;
    return md === 0 ? 0 : (x - m[i]) / (0.015 * md);
  });
}
export function adx(h, l, c, n = 14) {
  const tr = [null], pdm = [null], ndm = [null];
  for (let i = 1; i < c.length; i++) {
    const upm = h[i] - h[i - 1], dnm = l[i - 1] - l[i];
    pdm.push(upm > dnm && upm > 0 ? upm : 0);
    ndm.push(dnm > upm && dnm > 0 ? dnm : 0);
    tr.push(Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1])));
  }
  const atr = rma(tr, n, 1), sp = rma(pdm, n, 1), sn = rma(ndm, n, 1);
  const pdi = atr.map((a, i) => a === null ? null : a === 0 ? 0 : 100 * sp[i] / a);
  const ndi = atr.map((a, i) => a === null ? null : a === 0 ? 0 : 100 * sn[i] / a);
  const dx = pdi.map((p, i) => p === null ? null : (p + ndi[i]) === 0 ? 0 : 100 * Math.abs(p - ndi[i]) / (p + ndi[i]));
  const first = dx.findIndex(x => x !== null);
  const ax = first < 0 ? dx.map(() => null) : rma(dx.map(x => x ?? 0), n, first);
  return { adx: ax, pdi, ndi };
}
export function macd(c, f = 12, s = 26, sig = 9) {
  const ef = ema(c, f), es = ema(c, s);
  const line = c.map((_, i) => ef[i] === null || es[i] === null ? null : ef[i] - es[i]);
  const first = line.findIndex(x => x !== null);
  const signal = Array(c.length).fill(null);
  if (first >= 0) ema(line.slice(first), sig).forEach((x, j) => signal[first + j] = x);
  return { line, signal };
}
export function williams(h, l, c, n = 14) {
  return c.map((x, i) => {
    if (i < n - 1) return null;
    let hi = -Infinity, lo = Infinity;
    for (let j = i - n + 1; j <= i; j++) { hi = Math.max(hi, h[j]); lo = Math.min(lo, l[j]); }
    return hi === lo ? -50 : (hi - x) / (hi - lo) * -100;
  });
}
const momentum = (c, n = 10) => c.map((x, i) => i >= n ? x - c[i - n] : null);

// ------------------------------------------------------------------ the rating
const last = v => v[v.length - 1], prev = v => v[v.length - 2];
const ok = (...xs) => xs.every(x => x !== null && x !== undefined && Number.isFinite(x));

export function rate(candles) {
  const c = candles.map(x => x.close), h = candles.map(x => x.high), l = candles.map(x => x.low);
  const px = last(c);
  const ma = [], osc = [];

  for (const n of [10, 20, 30, 50, 100, 200]) {
    for (const [kind, f] of [["SMA", sma], ["EMA", ema]]) {
      const v = c.length >= n ? last(f(c, n)) : null;
      ma.push({ name: `${kind} ${n}`, value: v, vote: !ok(v, px) ? null : v < px ? 1 : v > px ? -1 : 0 });
    }
  }

  const r = rsi(c);
  osc.push({ name: "RSI 14", value: last(r), vote: !ok(last(r), prev(r)) ? null
    : last(r) < 30 && last(r) > prev(r) ? 1 : last(r) > 70 && last(r) < prev(r) ? -1 : 0 });
  const st = stoch(h, l, c), K = last(st.k), D = last(st.d);
  osc.push({ name: "Stochastic 14, 3, 3", value: K, vote: !ok(K, D) ? null
    : K < 20 && D < 20 && K > D ? 1 : K > 80 && D > 80 && K < D ? -1 : 0 });
  const cc = cci(h, l, c);
  osc.push({ name: "CCI 20", value: last(cc), vote: !ok(last(cc), prev(cc)) ? null
    : last(cc) < -100 && last(cc) > prev(cc) ? 1 : last(cc) > 100 && last(cc) < prev(cc) ? -1 : 0 });
  const ax = adx(h, l, c), A = last(ax.adx), P = last(ax.pdi), N = last(ax.ndi);
  osc.push({ name: "ADX 14", value: A, vote: !ok(A, P, N) ? null : A > 20 && P > N ? 1 : A > 20 && P < N ? -1 : 0 });
  const mo = momentum(c);
  osc.push({ name: "Momentum 10", value: last(mo), vote: !ok(last(mo), prev(mo)) ? null : last(mo) > prev(mo) ? 1 : last(mo) < prev(mo) ? -1 : 0 });
  const m = macd(c), ML = last(m.line), MS = last(m.signal);
  osc.push({ name: "MACD 12, 26", value: ML, vote: !ok(ML, MS) ? null : ML > MS ? 1 : ML < MS ? -1 : 0 });
  const w = williams(h, l, c);
  osc.push({ name: "Williams %R 14", value: last(w), vote: !ok(last(w), prev(w)) ? null
    : last(w) < -80 && last(w) > prev(w) ? 1 : last(w) > -20 && last(w) < prev(w) ? -1 : 0 });

  const group = items => {
    const used = items.filter(x => x.vote !== null);
    const buy = used.filter(x => x.vote > 0).length, sell = used.filter(x => x.vote < 0).length;
    const score = used.length ? (buy - sell) / used.length : null;
    return { items, buy, sell, neutral: used.length - buy - sell, used: used.length, score, label: labelOf(score) };
  };
  const g1 = group(ma), g2 = group(osc);
  const parts = [g1.score, g2.score].filter(x => x !== null);
  const score = parts.length ? parts.reduce((a, b) => a + b, 0) / parts.length : null;
  return { score, label: labelOf(score), ma: g1, osc: g2, candles: candles.length, close: px };
}

// Weekly candles (Monday start) from daily ones, for when only daily data is available.
export function weeklyFrom(daily) {
  const out = [];
  for (const d of daily) {
    const day = new Date(d.time * 1000).getUTCDay(), monday = d.time - ((day + 6) % 7) * 86400;
    const cur = out[out.length - 1];
    if (!cur || cur.time !== monday) out.push({ time: monday, open: d.open, high: d.high, low: d.low, close: d.close });
    else { cur.high = Math.max(cur.high, d.high); cur.low = Math.min(cur.low, d.low); cur.close = d.close; }
  }
  return out;
}
