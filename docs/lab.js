// Strategy Lab: reruns the indicator strategies in the browser with your own settings,
// on the whole market's daily prices (data/lab.json, written by engine/backtest.py).
//
// The rules mirror engine/strategies.py and the account mirrors weekly_account() in
// engine/backtest.py, so default settings reproduce the app's own backtest
// (tests/lab/test_parity.py checks this). Informational only.

export const ACCOUNT = 100_000;

// ---------------------------------------------------------------- settings
export const INDICATORS = {
  ma: {
    name: "Price vs moving average", base: "ma200", exit: "opposite",
    about: "Bullish when the price closes above its moving average, bearish when it closes below.",
    params: [{ k: "n", label: "Average length", min: 5, max: 300, step: 5, def: 200, unit: "days" }],
  },
  cross: {
    name: "Average crossover", base: "golden_cross", exit: "opposite",
    about: "Bullish when the fast average crosses above the slow one (golden cross), bearish when it crosses below.",
    params: [{ k: "fast", label: "Fast average", min: 5, max: 150, step: 5, def: 50, unit: "days" },
             { k: "slow", label: "Slow average", min: 20, max: 300, step: 5, def: 200, unit: "days" }],
  },
  bollinger: {
    name: "Bollinger bands", base: "bollinger", exit: "hold",
    about: "Bullish when the price closes back inside the lower band after dropping below it, bearish at the upper band.",
    params: [{ k: "n", label: "Band length", min: 5, max: 100, step: 1, def: 20, unit: "days" },
             { k: "k", label: "Band width", min: 1, max: 3.5, step: 0.25, def: 2, unit: "std devs" }],
  },
  rsi: {
    name: "RSI", base: "rsi", exit: "hold",
    about: "Bullish when RSI climbs back above the oversold level, bearish when it falls back below the overbought level.",
    params: [{ k: "n", label: "RSI length", min: 2, max: 30, step: 1, def: 14, unit: "days" },
             { k: "low", label: "Oversold below", min: 5, max: 45, step: 1, def: 30, unit: "" },
             { k: "high", label: "Overbought above", min: 55, max: 95, step: 1, def: 70, unit: "" }],
  },
  macd: {
    name: "MACD", base: "macd", exit: "hold",
    about: "Bullish when the MACD line crosses above its signal line, bearish when it crosses below.",
    params: [{ k: "fast", label: "Fast EMA", min: 3, max: 30, step: 1, def: 12, unit: "days" },
             { k: "slow", label: "Slow EMA", min: 10, max: 60, step: 1, def: 26, unit: "days" },
             { k: "sig", label: "Signal line", min: 3, max: 20, step: 1, def: 9, unit: "days" }],
  },
  donchian: {
    name: "Donchian breakout", base: "donchian", exit: "opposite",
    about: "Bullish on a close above the highest high of the last N days, bearish on a close below the lowest low.",
    params: [{ k: "n", label: "Channel length", min: 5, max: 200, step: 1, def: 20, unit: "days" }],
  },
  supertrend: {
    name: "Supertrend", base: "supertrend", exit: "opposite",
    about: "A line a set number of ATRs from the price that only moves with the trend. Bullish when the price closes above it in a downtrend, bearish when it closes below it in an uptrend.",
    params: [{ k: "n", label: "ATR length", min: 3, max: 50, step: 1, def: 10, unit: "days" },
             { k: "mult", label: "Distance", min: 1, max: 6, step: 0.25, def: 3, unit: "ATRs" }],
  },
  dip: {
    name: "Buy the dip", base: "buy_the_dip", exit: "hold", hold: 60, tp: 10, buysOnly: true,
    about: "Buys when a stock closes a set % below its highest close of the last N days, then sells at the take profit or after the time limit. It never sells on a loss by itself, so with no time limit losing positions are held until they recover.",
    exitLabels: { opposite: "No time limit", hold: "After a time limit" },
    params: [{ k: "drop", label: "Buy after a drop of", min: 1, max: 30, step: 0.5, def: 5, unit: "%" },
             { k: "n", label: "From the highest close of the last", min: 5, max: 120, step: 1, def: 20, unit: "days" }],
  },
};
export const HOLD_DEFAULT = 20;
// Take profit / stop loss, in % from the entry price. 0 = off (the default, so the app's own
// backtest is unchanged). They work on top of the exit rule: whichever comes first closes the trade.
export const RISK = {
  tp: { label: "Take profit", min: 0, max: 50, step: 0.5, about: "above the entry for a buy, below it for a short" },
  sl: { label: "Stop loss", min: 0, max: 30, step: 0.5, about: "below the entry for a buy, above it for a short" },
};

export function defaults(ind) {
  const I = INDICATORS[ind], p = {};
  I.params.forEach(x => p[x.k] = x.def);
  return { ind, p, exit: I.exit, hold: I.hold ?? HOLD_DEFAULT, tp: I.tp ?? 0, sl: 0 };
}
// keep a setting set valid (fast < slow, oversold < overbought, numbers in range)
export function clean(cfg) {
  const I = INDICATORS[cfg.ind] ? cfg.ind : "ma", D = defaults(I), out = { ...D, ...cfg, ind: I, p: { ...D.p } };
  for (const x of INDICATORS[I].params) {
    const v = +cfg.p?.[x.k];
    out.p[x.k] = Number.isFinite(v) ? Math.min(x.max, Math.max(x.min, v)) : x.def;
  }
  if (I === "cross" && out.p.fast >= out.p.slow) out.p.slow = Math.min(300, out.p.fast + 5), out.p.fast = Math.min(out.p.fast, out.p.slow - 5);
  if (I === "macd" && out.p.fast >= out.p.slow) out.p.slow = Math.min(60, out.p.fast + 1), out.p.fast = Math.min(out.p.fast, out.p.slow - 1);
  out.exit = out.exit === "hold" ? "hold" : "opposite";
  out.hold = Math.min(120, Math.max(2, Math.round(+out.hold || HOLD_DEFAULT)));
  for (const k of ["tp", "sl"]) {
    const R = RISK[k], v = +out[k];
    out[k] = Number.isFinite(v) ? Math.min(R.max, Math.max(R.min, Math.round(v / R.step) * R.step)) : 0;
  }
  return out;
}
export const sameCfg = (a, b) => JSON.stringify(clean(a)) === JSON.stringify(clean(b));

// ------------------------------------------------------------------- data
export function decode(raw) {
  const D = raw.dates.length;
  const tickers = raw.tickers.map(T => {
    const n = T.c.length, c = new Float64Array(n), h = new Float64Array(n), l = new Float64Array(n), idx = new Int32Array(n);
    const miss = new Set(T.miss);
    let L = 0;
    for (let i = 0, g = T.s; i < n; i++, g++) {
      while (miss.has(g)) g++;
      idx[i] = g;
      L += T.c[i];
      c[i] = Math.exp(L / 1e4); h[i] = Math.exp((L + T.h[i]) / 1e4); l[i] = Math.exp((L + T.l[i]) / 1e4);
    }
    return { t: T.t, idx, c, h, l };
  });
  let spy = null;
  if (raw.spy?.length === D) {
    spy = new Float64Array(D); let L = 0;
    raw.spy.forEach((d, i) => { L += d; spy[i] = Math.exp(L / 1e4); });
  }
  return { dates: raw.dates, tickers, spy };
}

// -------------------------------------------------------------- indicators
// Same maths as pandas in engine/strategies.py (rolling with min_periods, ewm adjust=False).
function sma(x, n) {
  const out = new Float64Array(x.length).fill(NaN);
  let s = 0;
  for (let i = 0; i < x.length; i++) {
    s += x[i]; if (i >= n) s -= x[i - n];
    if (i >= n - 1) out[i] = s / n;
  }
  return out;
}
function stdev(x, n, mean) {   // population std (ddof=0)
  const out = new Float64Array(x.length).fill(NaN);
  for (let i = n - 1; i < x.length; i++) {
    let q = 0; const m = mean[i];
    for (let j = i - n + 1; j <= i; j++) q += (x[j] - m) ** 2;
    out[i] = Math.sqrt(q / n);
  }
  return out;
}
function ema(x, n) {
  const a = 2 / (n + 1), out = new Float64Array(x.length);
  out[0] = x[0];
  for (let i = 1; i < x.length; i++) out[i] = a * x[i] + (1 - a) * out[i - 1];
  return out;
}
function rsi(c, n) {
  const out = new Float64Array(c.length).fill(NaN), a = 1 / n;
  let g = 0, l = 0;
  for (let i = 1; i < c.length; i++) {
    const d = c[i] - c[i - 1], gi = Math.max(d, 0), li = Math.max(-d, 0);
    if (i === 1) { g = gi; l = li; } else { g = (1 - a) * g + a * gi; l = (1 - a) * l + a * li; }
    if (i >= n) out[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
  }
  return out;
}
const above = (a, b, i) => a[i] > b[i] && a[i - 1] <= b[i - 1];   // NaN compares false, like pandas
const below = (a, b, i) => a[i] < b[i] && a[i - 1] >= b[i - 1];

// +1 bullish alert, -1 bearish alert, 0 nothing (a bar with both counts as bearish, like _signals)
export function signals(T, cfg) {
  const { c, h, l } = T, n = c.length, p = cfg.p, sig = new Int8Array(n);
  const put = (i, buy, sell) => { sig[i] = sell ? -1 : buy ? 1 : 0; };
  if (cfg.ind === "ma") {
    const m = sma(c, p.n);
    for (let i = 1; i < n; i++) put(i, above(c, m, i), below(c, m, i));
  } else if (cfg.ind === "cross") {
    const f = sma(c, p.fast), s = sma(c, p.slow);
    for (let i = 1; i < n; i++) put(i, above(f, s, i), below(f, s, i));
  } else if (cfg.ind === "bollinger") {
    const mid = sma(c, p.n), sd = stdev(c, p.n, mid);
    const up = mid.map((m, i) => m + p.k * sd[i]), lo = mid.map((m, i) => m - p.k * sd[i]);
    for (let i = 1; i < n; i++) put(i, c[i] > lo[i] && c[i - 1] < lo[i - 1], c[i] < up[i] && c[i - 1] > up[i - 1]);
  } else if (cfg.ind === "rsi") {
    const r = rsi(c, p.n), lo = new Float64Array(n).fill(p.low), hi = new Float64Array(n).fill(p.high);
    for (let i = 1; i < n; i++) put(i, above(r, lo, i), below(r, hi, i));
  } else if (cfg.ind === "macd") {
    const ef = ema(c, p.fast), es = ema(c, p.slow), line = ef.map((v, i) => v - es[i]), s = ema(line, p.sig), from = p.slow + p.sig;
    for (let i = Math.max(1, from); i < n; i++) put(i, above(line, s, i), below(line, s, i));
  } else if (cfg.ind === "donchian") {
    const hi = new Float64Array(n).fill(NaN), lo = new Float64Array(n).fill(NaN);
    for (let i = p.n; i < n; i++) {
      let mx = -Infinity, mn = Infinity;
      for (let j = i - p.n; j < i; j++) { if (h[j] > mx) mx = h[j]; if (l[j] < mn) mn = l[j]; }
      hi[i] = mx; lo[i] = mn;
    }
    for (let i = 1; i < n; i++) put(i, above(c, hi, i), below(c, lo, i));
  } else if (cfg.ind === "dip") {
    // same rule as buy_the_dip() in engine/strategies.py: first close at least drop% below the n-day high close
    const top = new Float64Array(n).fill(NaN), d = p.drop / 100, eps = 1e-12;
    for (let i = p.n - 1; i < n; i++) { let mx = -Infinity; for (let j = i - p.n + 1; j <= i; j++) if (c[j] > mx) mx = c[j]; top[i] = mx; }
    for (let i = 0; i < n; i++) {
      const now = c[i] / top[i] - 1 <= -d + eps, before = i > 0 && c[i - 1] / top[i - 1] - 1 <= -d + eps;
      if (now && !before) sig[i] = 1;
    }
  } else if (cfg.ind === "supertrend") {
    // same loop as supertrend() in engine/strategies.py
    const a = 1 / p.n, atr = new Float64Array(n).fill(NaN);
    let r = 0;
    for (let i = 0; i < n; i++) {
      const tr = i === 0 ? h[i] - l[i] : Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1]));
      r = i === 0 ? tr : (1 - a) * r + a * tr;
      if (i >= p.n - 1) atr[i] = r;
    }
    let up = NaN, lo = NaN, t = 0;
    for (let i = 0; i < n; i++) {
      if (!Number.isFinite(atr[i])) continue;
      const mid = (h[i] + l[i]) / 2, bu = mid + p.mult * atr[i], bl = mid - p.mult * atr[i];
      if (!Number.isFinite(up)) { up = bu; lo = bl; t = c[i] > bu ? 1 : c[i] < bl ? -1 : 0; continue; }
      const nu = bu < up || c[i - 1] > up ? bu : up, nl = bl > lo || c[i - 1] < lo ? bl : lo;
      up = nu; lo = nl;
      const prev = t;
      if (prev >= 0 && c[i] < lo) t = -1; else if (prev <= 0 && c[i] > up) t = 1;
      if (prev !== 0 && t !== prev) sig[i] = t;
    }
  }
  return sig;
}

// ------------------------------------------------------------------ run it
// Take profit / stop loss check on one daily bar for a position of `side` (+1 buy, -1 short)
// entered at `entry`. Returns { px, why } when the bar reaches a level, else null.
// Daily bars have no open: a level touched inside the bar fills at the level; a bar that
// opened beyond it (the whole range past the level, a gap) fills at the middle of its range.
// When one bar reaches both, the stop is counted, since the order inside the day is unknown.
export function riskHit(side, entry, h, l, cfg) {
  const mid = (h + l) / 2;
  if (cfg.sl) {
    const lv = entry * (1 - side * cfg.sl / 100);
    if (side > 0 ? l <= lv : h >= lv) return { px: (side > 0 ? h < lv : l > lv) ? mid : lv, why: "sl" };
  }
  if (cfg.tp) {
    const lv = entry * (1 + side * cfg.tp / 100);
    if (side > 0 ? h >= lv : l <= lv) return { px: (side > 0 ? l > lv : h < lv) ? mid : lv, why: "tp" };
  }
  return null;
}

// Every ticker's alerts drive one EUR ACCOUNT account: the money is split equally across every
// open position and rebalanced daily, idle money earns 0%. No costs, taxes or borrow fees.
export function run(data, cfg, fromIdx = 0) {
  cfg = clean(cfg);
  const D = data.dates.length, trend = cfg.exit === "opposite", H = cfg.hold, risk = cfg.tp > 0 || cfg.sl > 0;
  // buy-only strategies (Buy the dip) ignore alerts while a position is open, so the time limit counts
  // from the buy (own_exit_positions in engine/backtest.py)
  const once = !!INDICATORS[cfg.ind].buysOnly;
  const sum = { ls: new Float64Array(D), long: new Float64Array(D) }, cnt = { ls: new Int32Array(D), long: new Int32Array(D) };
  const trades = [], openL = [];
  for (const T of data.tickers) {
    const sig = signals(T, cfg), n = sig.length, c = T.c, h = T.h, l = T.l, idx = T.idx, taken = new Uint8Array(n);
    // positions held at each close (earn the next session's return)
    let side = 0, exitLS = -1, exitL = -1;
    let heldLS = 0, heldL = 0, entLS = 0, entL = 0, sinceL = 0;   // held at the previous close, and its entry price (and day)
    for (let i = 0; i < n; i++) {
      // 1) the session from the previous close to this one: take profit / stop loss inside the bar
      let pxLS = c[i], pxL = c[i], stoppedLS = false, stoppedL = false;
      if (risk && i > 0) {
        const a = heldLS ? riskHit(heldLS, entLS, h[i], l[i], cfg) : null, b = heldL ? riskHit(1, entL, h[i], l[i], cfg) : null;
        if (a) { pxLS = a.px; exitLS = i; stoppedLS = true; }   // closed during the day: flat at this close unless a new alert opens one
        if (b) { pxL = b.px; exitL = -1; stoppedL = true; }
      }
      // next session must be the very next calendar day for this ticker, like pct_change on the shared calendar
      if (i > 0 && idx[i] === idx[i - 1] + 1) {
        const g = idx[i];
        if (heldLS) { sum.ls[g] += heldLS * (pxLS / c[i - 1] - 1); cnt.ls[g]++; }
        if (heldL) { sum.long[g] += pxL / c[i - 1] - 1; cnt.long[g]++; }
      }
      // 2) this close's alert
      let s = sig[i];
      if (once && s === 1 && heldL && !stoppedL && i < exitL) s = 0;   // already holding it
      if (once && s === 1) taken[i] = 1;
      if (s) { side = s; exitLS = trend ? n : i + H; }
      if (s === -1) exitL = -1; else if (s === 1) exitL = trend ? n : i + H;
      const hLS = i < exitLS ? side : 0, hL = i < exitL ? 1 : 0;
      // a new position (or one reopened after a stop) is entered at this close
      if (hLS && (hLS !== heldLS || stoppedLS || taken[i])) entLS = c[i];
      if (hL && (!heldL || stoppedL || taken[i])) { entL = c[i]; sinceL = idx[i]; }
      heldLS = hLS; heldL = hL;
    }
    if (heldL && n && idx[n - 1] === data.dates.length - 1) openL.push({ t: T.t, since: data.dates[sinceL], ret: (c[n - 1] / entL - 1) * 100 });
    // every alert as a trade, for the win rate (same rule as trade_summary in backtest.py)
    for (let i = 0; i < n; i++) {
      const s = sig[i]; if (!s || idx[i] < fromIdx || (once && !taken[i])) continue;
      let j = n - 1, opp = false;
      for (let q = i + 1; q < n; q++) if (sig[q] === -s) { j = q; opp = true; break; }
      if (!trend) j = Math.min(j, i + H);
      let closed = j < n - 1 || (!trend && i + H <= n - 1) || opp;
      j = Math.min(j, n - 1);
      let px = c[j], why = "rule";
      if (risk) for (let q = i + 1; q <= j; q++) {
        const r = riskHit(s, c[i], h[q], l[q], cfg);
        if (r) { j = q; px = r.px; why = r.why; closed = true; break; }
      }
      if (j > i && closed) trades.push({ side: s, ret: s * (px / c[i] - 1), days: j - i, why });
    }
  }
  const curve = key => {
    const eq = new Float64Array(D); let v = ACCOUNT;
    for (let g = 0; g < D; g++) { if (g > fromIdx && cnt[key][g]) v *= 1 + sum[key][g] / cnt[key][g]; eq[g] = g < fromIdx ? NaN : v; }
    return eq;
  };
  const invested = key => { let k = 0, s = 0; for (let g = fromIdx + 1; g < D; g++) if (cnt[key][g]) { k++; s += cnt[key][g]; } return { share: k / Math.max(1, D - fromIdx - 1), avg: k ? s / k : 0 }; };
  const stat = list => {
    const n = list.length, w = list.filter(t => t.ret > 0).length;
    const by = k => list.filter(t => t.why === k).length;
    return { n, wins: w, winRate: n ? w / n * 100 : null, avg: n ? list.reduce((a, t) => a + t.ret, 0) / n * 100 : null, tp: by("tp"), sl: by("sl"),
             worst: n ? list.reduce((m, t) => Math.min(m, t.ret), Infinity) * 100 : null };
  };
  return {
    cfg,
    all: { eq: curve("ls"), inv: invested("ls"), trades: stat(trades) },
    long: { eq: curve("long"), inv: invested("long"), trades: stat(trades.filter(t => t.side > 0)) },
    open: openL.sort((a, b) => a.ret - b.ret),   // long positions still held on the last day, worst first
  };
}

export function spyCurve(data, fromIdx = 0) {
  if (!data.spy) return null;
  const D = data.dates.length, eq = new Float64Array(D), b = data.spy[fromIdx];
  for (let g = 0; g < D; g++) eq[g] = g < fromIdx ? NaN : ACCOUNT * data.spy[g] / b;
  return eq;
}

// summary numbers for a curve from fromIdx to the end
export function summary(eq, dates, fromIdx) {
  const end = eq[eq.length - 1];
  let peak = eq[fromIdx], dd = 0;
  for (let g = fromIdx; g < eq.length; g++) { if (eq[g] > peak) peak = eq[g]; const x = eq[g] / peak - 1; if (x < dd) dd = x; }
  const years = (Date.parse(dates[dates.length - 1]) - Date.parse(dates[fromIdx])) / (365.25 * 864e5);
  return { end, ret: (end / ACCOUNT - 1) * 100, cagr: years > 0 && end > 0 ? ((end / ACCOUNT) ** (1 / years) - 1) * 100 : null, dd: dd * 100 };
}

// return of each calendar year (first year and the current one are partial)
export function byYear(eq, dates, fromIdx) {
  const out = []; let start = eq[fromIdx], y = dates[fromIdx].slice(0, 4);
  for (let g = fromIdx + 1; g < dates.length; g++) {
    const yy = dates[g].slice(0, 4);
    if (yy !== y) { out.push({ year: +y, ret: (eq[g - 1] / start - 1) * 100 }); start = eq[g - 1]; y = yy; }
  }
  out.push({ year: +y, ret: (eq[eq.length - 1] / start - 1) * 100 });
  return out;
}
