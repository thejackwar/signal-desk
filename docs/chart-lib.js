// Chart page maths (docs/chart.html). Pure functions, no DOM: every indicator takes candles
// {time, open, high, low, close, volume} (oldest first) and returns an array of the same length,
// null where there isn't enough history yet. Tests: node tests/chart/test_chart_lib.mjs

// ------------------------------------------------------------------ data
// Server rows [t, o, h, l, c, v?] -> candles. v is missing on old Worker versions: volume = null.
export const toCandles = rows => rows.map(r => ({ time: r[0], open: r[1], high: r[2], low: r[3], close: r[4], volume: r.length > 5 ? r[5] : null }));

function merge(cur, b) {
  cur.high = Math.max(cur.high, b.high); cur.low = Math.min(cur.low, b.low); cur.close = b.close;
  if (cur.volume !== null && b.volume !== null) cur.volume += b.volume; else cur.volume = null;
}

// Stocks: session-anchored candles from 30-min bars (1H: 9:30..15:30, 4H: 9:30 and 13:30), as trendlines.js.
export function groupSession(bars, minutes) {
  const out = [];
  let cur = null;
  for (const b of bars) {
    const day = Math.floor(b.time / 86400) * 86400;
    const m = Math.floor((b.time - day) / 60);
    const start = day + (570 + Math.floor((m - 570) / minutes) * minutes) * 60;
    if (!cur || cur.time !== start) out.push(cur = { ...b, time: start });
    else merge(cur, b);
  }
  return out;
}

// Daily candles -> weekly (week starting Monday) or monthly; time = first day of the period.
export function groupDays(days, unit) {
  const key = t => {
    const d = new Date(t * 1000);
    if (unit === "M") return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1) / 1000;
    return t - ((d.getUTCDay() + 6) % 7) * 86400;
  };
  const out = [];
  let cur = null;
  for (const b of days) {
    const k = key(b.time);
    if (!cur || cur.time !== k) out.push(cur = { ...b, time: k });
    else merge(cur, b);
  }
  return out;
}

// Worker payload -> {W, D, 4H, 1H, 30m, 15m, 5m}. Crypto payloads carry every timeframe.
export function buildFrames(payload) {
  const f = payload.frames, c = k => toCandles(f[k] || []);
  if (f["4H"] && f["1H"]) return { W: c("W"), D: c("D"), "4H": c("4H"), "1H": c("1H"), "30m": c("30m"), "15m": c("15m"), "5m": c("5m") };
  const m30 = c("30m");
  return { W: c("W"), D: c("D"), "4H": groupSession(m30, 240), "1H": groupSession(m30, 60), "30m": m30, "15m": c("15m"), "5m": c("5m") };
}

// Static daily file {dates, o, h, l, c} (docs/data/.../stocks/X.json) -> daily + weekly frames.
export function framesFromDaily(js) {
  const D = js.dates.map((d, i) => ({ time: Date.parse(d + "T00:00:00Z") / 1000, open: js.o[i], high: js.h[i], low: js.l[i], close: js.c[i],
    volume: js.v ? js.v[i] : null }));
  return { W: groupDays(D, "W"), D };
}

export const hasVolume = c => c.length > 0 && c.every(b => b.volume !== null && b.volume !== undefined) && c.some(b => b.volume > 0);

// ------------------------------------------------------------------ building blocks
const nulls = n => new Array(n).fill(null);

export function sma(v, n) {
  const out = nulls(v.length);
  let sum = 0, k = 0;
  for (let i = 0; i < v.length; i++) {
    if (v[i] === null) { sum = 0; k = 0; continue; }
    sum += v[i]; k++;
    if (k > n) { sum -= v[i - n]; k = n; }
    if (k === n) out[i] = sum / n;
  }
  return out;
}

// EMA seeded with the SMA of the first n values (as most charting apps).
export function ema(v, n) {
  const out = nulls(v.length), a = 2 / (n + 1);
  let prev = null, sum = 0, k = 0;
  for (let i = 0; i < v.length; i++) {
    if (v[i] === null) continue;
    if (prev === null) { sum += v[i]; if (++k === n) out[i] = prev = sum / n; continue; }
    out[i] = prev = v[i] * a + prev * (1 - a);
  }
  return out;
}

// Wilder's smoothing (RMA), used by RSI, ATR and ADX.
export function rma(v, n) {
  const out = nulls(v.length);
  let prev = null, sum = 0, k = 0;
  for (let i = 0; i < v.length; i++) {
    if (v[i] === null) continue;
    if (prev === null) { sum += v[i]; if (++k === n) out[i] = prev = sum / n; continue; }
    out[i] = prev = (prev * (n - 1) + v[i]) / n;
  }
  return out;
}

export function stdev(v, n) {
  const m = sma(v, n), out = nulls(v.length);
  for (let i = n - 1; i < v.length; i++) {
    if (m[i] === null) continue;
    let s = 0;
    for (let j = i - n + 1; j <= i; j++) s += (v[j] - m[i]) ** 2;
    out[i] = Math.sqrt(s / n);                             // population, as TradingView's Bollinger Bands
  }
  return out;
}

export const closes = c => c.map(b => b.close);
export function trueRange(c) {
  return c.map((b, i) => i === 0 ? b.high - b.low
    : Math.max(b.high - b.low, Math.abs(b.high - c[i - 1].close), Math.abs(b.low - c[i - 1].close)));
}
const hh = (c, i, n) => { let m = -Infinity; for (let j = i - n + 1; j <= i; j++) m = Math.max(m, c[j].high); return m; };
const ll = (c, i, n) => { let m = Infinity; for (let j = i - n + 1; j <= i; j++) m = Math.min(m, c[j].low); return m; };

// ------------------------------------------------------------------ overlays
export const SMA = (c, n) => sma(closes(c), n);
export const EMA = (c, n) => ema(closes(c), n);

export function bollinger(c, n = 20, k = 2) {
  const m = sma(closes(c), n), sd = stdev(closes(c), n);
  return { mid: m, upper: m.map((x, i) => x === null ? null : x + k * sd[i]), lower: m.map((x, i) => x === null ? null : x - k * sd[i]) };
}

export function keltner(c, n = 20, mult = 2, atrN = 10) {
  const m = ema(closes(c), n), a = atr(c, atrN);
  return { mid: m, upper: m.map((x, i) => x === null || a[i] === null ? null : x + mult * a[i]),
    lower: m.map((x, i) => x === null || a[i] === null ? null : x - mult * a[i]) };
}

// Highest high / lowest low of the last n candles (current one included), and their middle.
export function donchian(c, n = 20) {
  const upper = nulls(c.length), lower = nulls(c.length), mid = nulls(c.length);
  for (let i = n - 1; i < c.length; i++) { upper[i] = hh(c, i, n); lower[i] = ll(c, i, n); mid[i] = (upper[i] + lower[i]) / 2; }
  return { upper, lower, mid };
}

// VWAP that restarts every day (stocks: each session; crypto: midnight UTC). Needs volume.
export function vwap(c) {
  const out = nulls(c.length);
  let day = null, pv = 0, vol = 0;
  for (let i = 0; i < c.length; i++) {
    const b = c[i], d = Math.floor(b.time / 86400);
    if (d !== day) { day = d; pv = 0; vol = 0; }
    const v = b.volume || 0;
    pv += ((b.high + b.low + b.close) / 3) * v; vol += v;
    out[i] = vol > 0 ? pv / vol : null;
  }
  return out;
}

// Supertrend (ATR bands that flip with the trend): {line, up} with up[i] true while the trend is up.
export function supertrend(c, n = 10, mult = 3) {
  const a = rma(trueRange(c), n), line = nulls(c.length), up = nulls(c.length);
  let fu = null, fl = null, dir = 1;
  for (let i = 0; i < c.length; i++) {
    if (a[i] === null) continue;
    const hl2 = (c[i].high + c[i].low) / 2, bu = hl2 + mult * a[i], bl = hl2 - mult * a[i];
    const pc = i > 0 ? c[i - 1].close : c[i].close;
    const nu = fu === null || bu < fu || pc > fu ? bu : fu;
    const nl = fl === null || bl > fl || pc < fl ? bl : fl;
    if (fu !== null) {
      if (dir === 1 && c[i].close < nl) dir = -1;
      else if (dir === -1 && c[i].close > nu) dir = 1;
    }
    fu = nu; fl = nl;
    line[i] = dir === 1 ? fl : fu; up[i] = dir === 1;
  }
  return { line, up };
}

// Parabolic SAR (Wilder): dots below price in an uptrend, above in a downtrend.
export function psar(c, step = 0.02, max = 0.2) {
  const out = nulls(c.length);
  if (c.length < 2) return out;
  let upTrend = c[1].close >= c[0].close;
  let sar = upTrend ? c[0].low : c[0].high, ep = upTrend ? c[0].high : c[0].low, af = step;
  for (let i = 1; i < c.length; i++) {
    sar = sar + af * (ep - sar);
    if (upTrend) {
      sar = Math.min(sar, c[i - 1].low, i > 1 ? c[i - 2].low : c[i - 1].low);
      if (c[i].low < sar) { upTrend = false; sar = ep; ep = c[i].low; af = step; }
      else if (c[i].high > ep) { ep = c[i].high; af = Math.min(max, af + step); }
    } else {
      sar = Math.max(sar, c[i - 1].high, i > 1 ? c[i - 2].high : c[i - 1].high);
      if (c[i].high > sar) { upTrend = true; sar = ep; ep = c[i].high; af = step; }
      else if (c[i].low < ep) { ep = c[i].low; af = Math.min(max, af + step); }
    }
    out[i] = { value: sar, up: upTrend };
  }
  return out;
}

// Ichimoku lines on each candle's own time (the cloud is drawn shifted by the page).
export function ichimoku(c, conv = 9, base = 26, spanB = 52) {
  const mid = (i, n) => i >= n - 1 ? (hh(c, i, n) + ll(c, i, n)) / 2 : null;
  const tenkan = c.map((_, i) => mid(i, conv)), kijun = c.map((_, i) => mid(i, base));
  return { tenkan, kijun,
    spanA: tenkan.map((t, i) => t === null || kijun[i] === null ? null : (t + kijun[i]) / 2),
    spanB: c.map((_, i) => mid(i, spanB)), shift: base };
}

// ------------------------------------------------------------------ oscillators (own panes)
export function rsi(c, n = 14) {
  const cl = closes(c), g = nulls(c.length), l = nulls(c.length);
  for (let i = 1; i < cl.length; i++) { const d = cl[i] - cl[i - 1]; g[i] = Math.max(d, 0); l[i] = Math.max(-d, 0); }
  const ag = rma(g, n), al = rma(l, n);
  return ag.map((x, i) => x === null ? null : al[i] === 0 ? 100 : 100 - 100 / (1 + x / al[i]));
}

export function macd(c, fast = 12, slow = 26, signal = 9) {
  const f = ema(closes(c), fast), s = ema(closes(c), slow);
  const line = f.map((x, i) => x === null || s[i] === null ? null : x - s[i]);
  const sig = ema(line, signal);
  return { line, signal: sig, hist: line.map((x, i) => x === null || sig[i] === null ? null : x - sig[i]) };
}

// Slow stochastic: %K over n candles smoothed by `smooth`, %D its d-period average.
export function stochastic(c, n = 14, smooth = 3, d = 3) {
  const raw = c.map((b, i) => {
    if (i < n - 1) return null;
    const h = hh(c, i, n), l = ll(c, i, n);
    return h === l ? 50 : (100 * (b.close - l)) / (h - l);
  });
  const k = sma(raw, smooth);
  return { k, d: sma(k, d) };
}

export const atr = (c, n = 14) => rma(trueRange(c), n);

// ADX with +DI / −DI (Wilder).
export function adx(c, n = 14) {
  const pdm = nulls(c.length), mdm = nulls(c.length), tr = trueRange(c).map((x, i) => i === 0 ? null : x);
  for (let i = 1; i < c.length; i++) {
    const upMove = c[i].high - c[i - 1].high, dnMove = c[i - 1].low - c[i].low;
    pdm[i] = upMove > dnMove && upMove > 0 ? upMove : 0;
    mdm[i] = dnMove > upMove && dnMove > 0 ? dnMove : 0;
  }
  const str = rma(tr, n), sp = rma(pdm, n), sm = rma(mdm, n);
  const plus = str.map((t, i) => t ? (100 * sp[i]) / t : null), minus = str.map((t, i) => t ? (100 * sm[i]) / t : null);
  const dx = plus.map((p, i) => p === null || minus[i] === null ? null : p + minus[i] === 0 ? 0 : (100 * Math.abs(p - minus[i])) / (p + minus[i]));
  return { adx: rma(dx, n), plus, minus };
}

export function obv(c) {
  let run = 0;
  return c.map((b, i) => {
    if (i > 0) run += b.close > c[i - 1].close ? b.volume || 0 : b.close < c[i - 1].close ? -(b.volume || 0) : 0;
    return run;
  });
}

export function cci(c, n = 20) {
  const tp = c.map(b => (b.high + b.low + b.close) / 3), m = sma(tp, n);
  return tp.map((x, i) => {
    if (m[i] === null) return null;
    let md = 0;
    for (let j = i - n + 1; j <= i; j++) md += Math.abs(tp[j] - m[i]);
    md /= n;
    return md === 0 ? 0 : (x - m[i]) / (0.015 * md);
  });
}

// ------------------------------------------------------------------ levels
// Fibonacci retracement of the biggest swing in the last `lookback` candles:
// from the lowest low to the highest high (or the other way round if the high came first).
export const FIB_RATIOS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
export function fibonacci(c, lookback = 120) {
  if (c.length < 2) return null;
  const s0 = Math.max(0, c.length - lookback);
  let hi = s0, lo = s0;
  for (let i = s0; i < c.length; i++) { if (c[i].high > c[hi].high) hi = i; if (c[i].low < c[lo].low) lo = i; }
  const up = lo < hi;                                       // move went up: retrace down from the high
  const top = c[hi].high, bot = c[lo].low;
  return { up, from: up ? lo : hi, to: up ? hi : lo, high: top, low: bot,
    levels: FIB_RATIOS.map(r => ({ ratio: r, price: up ? top - (top - bot) * r : bot + (top - bot) * r })) };
}

// Classic floor pivots from the previous completed period (day for intraday, week for daily,
// month for weekly candles). Returns {P, R1, R2, R3, S1, S2, S3, period} or null.
export function pivots(c, period) {
  const key = t => {
    const d = new Date(t * 1000);
    if (period === "M") return d.getUTCFullYear() * 12 + d.getUTCMonth();
    if (period === "W") return Math.floor((t / 86400 + 3) / 7);   // weeks starting Monday
    return Math.floor(t / 86400);
  };
  if (!c.length) return null;
  const last = key(c[c.length - 1].time);
  let h = -Infinity, l = Infinity, cl = null, prevKey = null;
  for (let i = c.length - 1; i >= 0; i--) {
    const k = key(c[i].time);
    if (k === last) continue;
    if (prevKey === null) { prevKey = k; cl = c[i].close; }
    if (k !== prevKey) break;
    h = Math.max(h, c[i].high); l = Math.min(l, c[i].low);
  }
  if (prevKey === null) return null;
  const P = (h + l + cl) / 3;
  return { P, R1: 2 * P - l, S1: 2 * P - h, R2: P + (h - l), S2: P - (h - l), R3: h + 2 * (P - l), S3: l - 2 * (h - P), high: h, low: l, period };
}

// ------------------------------------------------------------------ candle patterns
// Returns [{index, kind, bull}] for the classic one- and two-candle patterns.
export function candlePatterns(c) {
  const out = [], a = atr(c, 14);
  for (let i = 2; i < c.length; i++) {
    const b = c[i], p = c[i - 1], range = b.high - b.low;
    if (!range || a[i] === null) continue;
    const body = Math.abs(b.close - b.open), upW = b.high - Math.max(b.open, b.close), loW = Math.min(b.open, b.close) - b.low;
    const pBody = Math.abs(p.close - p.open);
    const downBefore = c[i - 2].close > p.close, upBefore = c[i - 2].close < p.close;
    if (p.close < p.open && b.close > b.open && b.close >= p.open && b.open <= p.close && body > pBody && pBody > 0.2 * a[i])
      out.push({ index: i, kind: "Bullish engulfing", bull: true });
    else if (p.close > p.open && b.close < b.open && b.open >= p.close && b.close <= p.open && body > pBody && pBody > 0.2 * a[i])
      out.push({ index: i, kind: "Bearish engulfing", bull: false });
    else if (range > 0.8 * a[i] && body <= range * 0.35 && loW >= 2 * body && upW <= range * 0.15 && downBefore)
      out.push({ index: i, kind: "Hammer", bull: true });
    else if (range > 0.8 * a[i] && body <= range * 0.35 && upW >= 2 * body && loW <= range * 0.15 && upBefore)
      out.push({ index: i, kind: "Shooting star", bull: false });
    else if (body <= range * 0.08 && range > 0.6 * a[i])
      out.push({ index: i, kind: "Doji", bull: null });
  }
  return out;
}

// Highest high / lowest low of the previous day / week (TradingView's "PDH / PDL").
export function previousRange(c, period) {
  const p = pivots(c, period);
  return p ? { high: p.high, low: p.low } : null;
}
