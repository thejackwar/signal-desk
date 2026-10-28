// Automatic trendlines: a line-for-line port of thejackwar/sp500-bot trendlines.py.
// Same rules on every timeframe, each computed only from its own candles, all math in
// bar-index space (candle 0, 1, 2 ...), so lines stay straight across nights and weekends.
// Used by trendlines.html; tests in engine/test_trendlines_js.py compare it with the Python.

// timeframe -> label, lookback bars searched for the absolute high/low, line width, bar seconds
export const TFS = {
  W:     { label: "Weekly", lookback: 300,  width: 3.0, step: 7 * 86400 },
  D:     { label: "Daily",  lookback: 250,  width: 2.6, step: 86400 },
  "4H":  { label: "4-hour", lookback: 400,  width: 2.2, step: 4 * 3600 },
  "1H":  { label: "1-hour", lookback: 500,  width: 1.9, step: 3600 },
  "30m": { label: "30-min", lookback: 600,  width: 1.6, step: 1800 },
  "15m": { label: "15-min", lookback: 1500, width: 1.4, step: 900 },
  "5m":  { label: "5-min",  lookback: 1500, width: 1.2, step: 300 },
};
export const ORDER = Object.keys(TFS);                     // highest -> lowest

// ------------------------------------------------------------------ the rules
// Swing highs/lows as {index, price}, plus the provisional newest one.
export function findPivots(c, n, kind) {
  const v = kind === "high" ? i => c[i].high : i => c[i].low;
  const out = [];
  for (let i = n; i < c.length - n; i++) {
    let ok = true;
    for (let k = 1; k <= n && ok; k++) {
      ok = kind === "high" ? v(i - k) <= v(i) && v(i + k) < v(i) : v(i - k) >= v(i) && v(i + k) > v(i);
    }
    if (ok) out.push({ index: i, price: v(i) });
  }
  // provisional: most extreme of the last n bars (ties -> newest), strictly beyond its n left bars
  if (c.length > n) {
    let best = Math.max(0, c.length - n);
    for (let i = best; i < c.length; i++) if (kind === "high" ? v(i) >= v(best) : v(i) <= v(best)) best = i;
    if (best >= n) {
      let ok = true;
      for (let k = 1; k <= n && ok; k++) ok = kind === "high" ? v(best - k) < v(best) : v(best - k) > v(best);
      if (ok) out.push({ index: best, price: v(best) });
    }
  }
  return out;
}

// Chain from the absolute extreme of the window: {vertices, broken, level, slope}.
export function buildChain(c, piv, isUp, lookback, breakBy = "close") {
  const last = c.length - 1, s0 = Math.max(0, c.length - lookback);
  let a = s0;
  for (let i = s0; i <= last; i++) if (isUp ? c[i].low < c[a].low : c[i].high > c[a].high) a = i;   // first extreme
  let cur = { index: a, price: isUp ? c[a].low : c[a].high };
  const vertices = [cur];
  for (;;) {
    let best = null, bestS = 0;
    for (const p of piv) {
      if (p.index <= cur.index) continue;
      const s = (p.price - cur.price) / (p.index - cur.index);
      if (best === null || (isUp ? s <= bestS : s >= bestS)) { best = p; bestS = s; }
    }
    if (!best) break;
    vertices.push(best); cur = best;
  }
  const res = { vertices, broken: null, level: null, slope: null };
  if (vertices.length < 2) return res;
  const A = vertices[vertices.length - 2], B = vertices[vertices.length - 1];
  const slope = (B.price - A.price) / (B.index - A.index);
  res.slope = slope;
  for (let i = B.index + 1; i <= last; i++) {
    const v = B.price + slope * (i - B.index);
    const px = breakBy === "close" ? c[i].close : isUp ? c[i].low : c[i].high;
    if (isUp ? px < v : px > v) { res.broken = { index: i, price: v }; return res; }
  }
  res.level = B.price + slope * (last - B.index);
  return res;
}

export function analyze(c, lookback, swing = 5, breakBy = "close") {
  if (!c.length) return { up: emptyChain(), down: emptyChain(), bias: "-" };
  const up = buildChain(c, findPivots(c, swing, "low"), true, lookback, breakBy);
  const down = buildChain(c, findPivots(c, swing, "high"), false, lookback, breakBy);
  const u = up.level !== null, d = down.level !== null;
  const bias = u && !d ? "Up" : d && !u ? "Down" : u && d ? "Squeeze" : "-";
  return { up, down, bias };
}
const emptyChain = () => ({ vertices: [], broken: null, level: null, slope: null });

// ------------------------------------------------------------------ data
// The data server sends regular-hours bars as [t, o, h, l, c], where t is the New York
// wall-clock time written as if it were UTC (seconds). W and D bars carry the date at 00:00.
const toCandles = rows => rows.map(([time, open, high, low, close]) => ({ time, open, high, low, close }));

// Session-anchored candles from 30-min bars: first one starts 9:30 (1H: 9:30..15:30, 4H: 9:30 and 13:30).
export function group(bars, minutes) {
  const out = [];
  let cur = null;
  for (const b of bars) {
    const day = Math.floor(b.time / 86400) * 86400;
    const m = Math.floor((b.time - day) / 60);
    const start = day + (570 + Math.floor((m - 570) / minutes) * minutes) * 60;
    if (!cur || cur.time !== start) {
      cur = { time: start, open: b.open, high: b.high, low: b.low, close: b.close };
      out.push(cur);
    } else {
      cur.high = Math.max(cur.high, b.high);
      cur.low = Math.min(cur.low, b.low);
      cur.close = b.close;
    }
  }
  return out;
}

// Server payload -> the 7 timeframes, oldest candle first.
export function buildFrames(payload) {
  const f = payload.frames;
  const m30 = toCandles(f["30m"]);
  return {
    W: toCandles(f.W), D: toCandles(f.D),
    "4H": group(m30, 240), "1H": group(m30, 60), "30m": m30,
    "15m": toCandles(f["15m"]), "5m": toCandles(f["5m"]),
  };
}

export function analyzeAll(frames, { swing = 5, breakBy = "close" } = {}) {
  const out = {};
  for (const t of ORDER) out[t] = analyze(frames[t], TFS[t].lookback, swing, breakBy);
  return out;
}

// ------------------------------------------------------------------ drawing helpers
// Each segment of a chain as {from, to, last}: points {index, price} in the timeframe's own
// bar numbering. Older segments run vertex to vertex; the newest one runs to the break, or to
// `extendTo` (a bar index in the empty space on the right) when it is still intact.
export function segments(chain, extendTo) {
  const v = chain.vertices, out = [];
  for (let j = 0; j < v.length - 1; j++) {
    const A = v[j], B = v[j + 1], slope = (B.price - A.price) / (B.index - A.index);
    const last = j === v.length - 2;
    const end = !last ? B.index : chain.broken ? chain.broken.index : extendTo;
    out.push({ from: A, to: { index: end, price: A.price + slope * (end - A.index) }, last });
  }
  return out;
}

export const fmtLevel = ch => ch.level !== null
  ? ch.level.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  : ch.broken ? "broken" : "–";
