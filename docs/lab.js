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

// Trailing stop, in % from the best price since the entry (highest high for a buy, lowest low for a short).
RISK.trail = { label: "Trailing stop", min: 0, max: 30, step: 0.5, about: "follows the best price since the entry" };

// Extra rules a trader can add. All off by default, so the defaults still match the app's backtest.
//   fMa    only buy when the stock is above its N-day average (and only short when below); 0 = off
//   fMkt   only buy when the benchmark (SPY, or Bitcoin / gold) is above its 200-day average, short when below
//   fInd   only take an alert when a second indicator (at its usual settings) agrees
//   maxPos at most N positions at once, each 1/N of the account (0 = split across every open position)
//   cost   % paid on every buy and every sell (commission + spread + slippage)
export const FILTER_IND = {
  "": "Off",
  cross: "50-day above 200-day average",
  macd: "MACD above its signal line",
  supertrend: "Supertrend in an uptrend",
  rsi: "RSI(14) above 50",
  donchian: "Last 20-day breakout was upward",
};
export const EXTRA = {
  fMa: { min: 0, max: 300, step: 10 },
  maxPos: { min: 0, max: 50, step: 1 },
  cost: { min: 0, max: 1, step: 0.05 },
};

export function defaults(ind) {
  const I = INDICATORS[ind], p = {};
  I.params.forEach(x => p[x.k] = x.def);
  return { ind, p, exit: I.exit, hold: I.hold ?? HOLD_DEFAULT, tp: I.tp ?? 0, sl: 0, trail: 0, fMa: 0, fMkt: 0, fInd: "", maxPos: 0, cost: 0 };
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
  const snap = (R, v) => Number.isFinite(+v) ? Math.min(R.max, Math.max(R.min, Math.round(+v / R.step) * R.step)) : 0;
  for (const k of ["tp", "sl", "trail"]) out[k] = snap(RISK[k], out[k]);
  for (const k of ["fMa", "maxPos"]) out[k] = snap(EXTRA[k], out[k]);
  if (out.fMa && out.fMa < 20) out.fMa = 20;
  out.cost = +snap(EXTRA.cost, out.cost).toFixed(2);
  out.fMkt = out.fMkt ? 1 : 0;
  out.fInd = Object.hasOwn(FILTER_IND, out.fInd) ? out.fInd : "";
  // fixed key order, so sameCfg can compare JSON
  const { ind, p, exit, hold, tp, sl, trail, fMa, fMkt, fInd, maxPos, cost } = out;
  return { ind, p, exit, hold, tp, sl, trail, fMa, fMkt, fInd, maxPos, cost };
}
// how many of the extra rules are on (for the UI)
export const extrasOn = cfg => ["trail", "fMa", "fMkt", "fInd", "maxPos", "cost"].filter(k => cfg[k]).length;
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

// The "second indicator agrees" filter: +1 bullish, -1 bearish, 0 not known yet, at the usual settings.
export function states(T, ind) {
  const { c, h, l } = T, n = c.length, st = new Int8Array(n);
  const set = (i, up, dn) => { st[i] = up ? 1 : dn ? -1 : 0; };
  if (ind === "cross") {
    const f = sma(c, 50), s = sma(c, 200);
    for (let i = 0; i < n; i++) set(i, f[i] > s[i], f[i] < s[i]);
  } else if (ind === "macd") {
    const ef = ema(c, 12), es = ema(c, 26), line = ef.map((v, i) => v - es[i]), s = ema(line, 9);
    for (let i = 35; i < n; i++) set(i, line[i] > s[i], line[i] < s[i]);
  } else if (ind === "rsi") {
    const r = rsi(c, 14);
    for (let i = 0; i < n; i++) set(i, r[i] > 50, r[i] < 50);
  } else if (ind === "supertrend" || ind === "donchian") {
    // the last alert of that indicator sets the state until the next one
    const sig = signals(T, defaults(ind));
    let s = 0;
    for (let i = 0; i < n; i++) { if (sig[i]) s = sig[i]; st[i] = s; }
  }
  return st;
}

// Which alerts may open a position: allowBuy / allowShort per bar (null = no filter on).
function gates(T, cfg, data) {
  if (!cfg.fMa && !cfg.fMkt && !cfg.fInd) return null;
  const n = T.c.length, buy = new Uint8Array(n).fill(1), short = new Uint8Array(n).fill(1);
  if (cfg.fMa) {
    const m = sma(T.c, cfg.fMa);
    for (let i = 0; i < n; i++) { buy[i] &= T.c[i] > m[i]; short[i] &= T.c[i] < m[i]; }
  }
  if (cfg.fMkt && data.spy) {
    data._bm200 = data._bm200 || sma(data.spy, 200);
    const b = data.spy, m = data._bm200;
    for (let i = 0; i < n; i++) { const g = T.idx[i]; buy[i] &= b[g] > m[g]; short[i] &= b[g] < m[g]; }
  }
  if (cfg.fInd) {
    const s = states(T, cfg.fInd);
    for (let i = 0; i < n; i++) { buy[i] &= s[i] > 0; short[i] &= s[i] < 0; }
  }
  return { buy, short };
}

// A smaller copy of the data: only some tickers, and/or ending on day `to` (index into dates).
export function subset(data, { tickers = null, to = null } = {}) {
  const end = to == null ? data.dates.length - 1 : Math.min(to, data.dates.length - 1);
  const out = { dates: data.dates.slice(0, end + 1), spy: data.spy ? data.spy.subarray(0, end + 1) : null, tickers: [] };
  for (const T of data.tickers) {
    if (tickers && !tickers.has(T.t)) continue;
    let k = T.idx.length; while (k > 0 && T.idx[k - 1] > end) k--;
    if (k < 2) continue;
    out.tickers.push(k === T.idx.length ? T : { t: T.t, idx: T.idx.subarray(0, k), c: T.c.subarray(0, k), h: T.h.subarray(0, k), l: T.l.subarray(0, k) });
  }
  return out;
}

// ------------------------------------------------------------------ run it
// Take profit / stop loss check on one daily bar for a position of `side` (+1 buy, -1 short)
// entered at `entry`. Returns { px, why } when the bar reaches a level, else null.
// Daily bars have no open: a level touched inside the bar fills at the level; a bar that
// opened beyond it (the whole range past the level, a gap) fills at the middle of its range.
// When one bar reaches both, the stop is counted, since the order inside the day is unknown.
// `peak` is the best price since the entry before this bar (for the trailing stop): when the stop
// loss and the trailing stop are both on, the one closer to the price counts.
export function riskHit(side, entry, h, l, cfg, peak = entry) {
  const mid = (h + l) / 2;
  let lv = null, why = null;
  if (cfg.sl) { lv = entry * (1 - side * cfg.sl / 100); why = "sl"; }
  if (cfg.trail) {
    const tv = peak * (1 - side * cfg.trail / 100);
    if (lv == null || (side > 0 ? tv > lv : tv < lv)) { lv = tv; why = "trail"; }
  }
  if (lv != null) {
    if (side > 0 ? l <= lv : h >= lv) return { px: (side > 0 ? h < lv : l > lv) ? mid : lv, why };
  }
  if (cfg.tp) {
    const lv = entry * (1 + side * cfg.tp / 100);
    if (side > 0 ? h >= lv : l <= lv) return { px: (side > 0 ? l > lv : h < lv) ? mid : lv, why: "tp" };
  }
  return null;
}

// Every ticker's alerts drive one EUR ACCOUNT account. By default the money is split equally across
// every open position and rebalanced daily, idle money earns 0%, no costs, taxes or borrow fees.
// With maxPos set, the account has that many slots of 1/maxPos each: an alert that comes while
// every slot is taken is skipped, and empty slots wait in cash. `cost` is taken on every buy and sell.
export function run(data, cfg, fromIdx = 0) {
  cfg = clean(cfg);
  const D = data.dates.length, trend = cfg.exit === "opposite", H = cfg.hold, risk = cfg.tp > 0 || cfg.sl > 0 || cfg.trail > 0;
  // buy-only strategies (Buy the dip) ignore alerts while a position is open, so the time limit counts
  // from the buy (own_exit_positions in engine/backtest.py)
  const once = !!INDICATORS[cfg.ind].buysOnly, fee = cfg.cost / 100, slots = cfg.maxPos;
  const sum = { ls: new Float64Array(D), long: new Float64Array(D) }, cnt = { ls: new Int32Array(D), long: new Int32Array(D) };
  const trades = [], openL = [], book = { ls: [], long: [] };   // book: every position, kept only with a position limit
  for (const T of data.tickers) {
    const sig = signals(T, cfg), gate = gates(T, cfg, data), n = sig.length, c = T.c, h = T.h, l = T.l, idx = T.idx, taken = new Uint8Array(n);
    // positions held at each close (earn the next session's return)
    let side = 0, exitLS = -1, exitL = -1;
    let heldLS = 0, heldL = 0, entLS = 0, entL = 0, sinceL = 0;   // held at the previous close, and its entry price (and day)
    let pkLS = 0, pkL = 0;                                         // best price since the entry (trailing stop)
    let posLS = null, posL = null, freshLS = false, freshL = false, lastLS = -1, lastL = -1;
    for (let i = 0; i < n; i++) {
      // 1) the session from the previous close to this one: take profit / stop loss inside the bar
      let pxLS = c[i], pxL = c[i], stoppedLS = false, stoppedL = false;
      if (risk && i > 0) {
        const a = heldLS ? riskHit(heldLS, entLS, h[i], l[i], cfg, pkLS) : null, b = heldL ? riskHit(1, entL, h[i], l[i], cfg, pkL) : null;
        if (a) { pxLS = a.px; exitLS = i; stoppedLS = true; }   // closed during the day: flat at this close unless a new alert opens one
        if (b) { pxL = b.px; exitL = -1; stoppedL = true; }
      }
      // next session must be the very next calendar day for this ticker, like pct_change on the shared calendar
      if (i > 0 && idx[i] === idx[i - 1] + 1) {
        const g = idx[i];
        if (heldLS) {
          let r = heldLS * (pxLS / c[i - 1] - 1);
          if (freshLS) { r -= fee; freshLS = false; }
          sum.ls[g] += r; cnt.ls[g]++; lastLS = g;
          if (posLS) { posLS.g.push(g); posLS.r.push(r); }
        }
        if (heldL) {
          let r = pxL / c[i - 1] - 1;
          if (freshL) { r -= fee; freshL = false; }
          sum.long[g] += r; cnt.long[g]++; lastL = g;
          if (posL) { posL.g.push(g); posL.r.push(r); }
        }
      }
      if (heldLS && !stoppedLS) pkLS = heldLS > 0 ? Math.max(pkLS, h[i]) : Math.min(pkLS, l[i]);
      if (heldL && !stoppedL) pkL = Math.max(pkL, h[i]);
      // 2) this close's alert; a filter (trend, market, second indicator) can stop it opening a position,
      //    but it still closes a position on the other side
      let s = sig[i], ok = true;
      if (gate && s) ok = !!(s > 0 ? gate.buy[i] : gate.short[i]);
      if (once && s === 1 && !ok) s = 0;
      if (once && s === 1 && heldL && !stoppedL && i < exitL) s = 0;   // already holding it
      if (once && s === 1) taken[i] = 1;
      if (s) {
        if (ok) { side = s; exitLS = trend ? n : i + H; }
        else if (side === -s && i < exitLS) exitLS = i;
      }
      if (s === -1) exitL = -1; else if (s === 1 && ok) exitL = trend ? n : i + H;
      const hLS = i < exitLS ? side : 0, hL = i < exitL ? 1 : 0;
      const newLS = !!hLS && (hLS !== heldLS || stoppedLS || !!taken[i]), newL = !!hL && (!heldL || stoppedL || !!taken[i]);
      // a position that ends at this close pays the selling cost on its last day
      const g = idx[i];
      if (heldLS && (stoppedLS || !hLS || newLS)) {
        if (fee && lastLS === g) { sum.ls[g] -= fee; if (posLS) posLS.r[posLS.r.length - 1] -= fee; }
        if (posLS) posLS.gE = g;
        posLS = null; freshLS = false;
      }
      if (heldL && (stoppedL || !hL || newL)) {
        if (fee && lastL === g) { sum.long[g] -= fee; if (posL) posL.r[posL.r.length - 1] -= fee; }
        if (posL) posL.gE = g;
        posL = null; freshL = false;
      }
      // a new position (or one reopened after a stop) is entered at this close
      if (newLS) {
        entLS = c[i]; pkLS = c[i]; freshLS = fee > 0;
        if (slots) { posLS = { t: T.t, g0: g, gE: Infinity, g: [], r: [] }; book.ls.push(posLS); }
      }
      if (newL) {
        entL = c[i]; sinceL = idx[i]; pkL = c[i]; freshL = fee > 0;
        if (slots) { posL = { t: T.t, g0: g, gE: Infinity, g: [], r: [] }; book.long.push(posL); }
      }
      heldLS = hLS; heldL = hL;
    }
    if (heldL && n && idx[n - 1] === data.dates.length - 1) openL.push({ t: T.t, since: data.dates[sinceL], ret: (c[n - 1] / entL - 1) * 100 });
    // every alert as a trade, for the win rate (same rule as trade_summary in backtest.py)
    for (let i = 0; i < n; i++) {
      const s = sig[i]; if (!s || idx[i] < fromIdx || (once && !taken[i])) continue;
      if (gate && !(s > 0 ? gate.buy[i] : gate.short[i])) continue;
      let j = n - 1, opp = false;
      for (let q = i + 1; q < n; q++) if (sig[q] === -s) { j = q; opp = true; break; }
      if (!trend) j = Math.min(j, i + H);
      let closed = j < n - 1 || (!trend && i + H <= n - 1) || opp;
      j = Math.min(j, n - 1);
      let px = c[j], why = "rule";
      if (risk) for (let q = i + 1, pk = c[i]; q <= j; q++) {
        const r = riskHit(s, c[i], h[q], l[q], cfg, pk);
        if (r) { j = q; px = r.px; why = r.why; closed = true; break; }
        pk = s > 0 ? Math.max(pk, h[q]) : Math.min(pk, l[q]);
      }
      if (j > i && closed) trades.push({ t: T.t, side: s, g0: idx[i], g1: idx[j], ret: s * (px / c[i] - 1) - 2 * fee, days: j - i, why });
    }
  }
  // with a position limit: fill the slots in date order (same-day alerts in a fixed shuffled order)
  const mix = s => { let x = 2166136261; for (let k = 0; k < s.length; k++) x = Math.imul(x ^ s.charCodeAt(k), 16777619); return x >>> 0; };
  const fill = key => {
    const P = book[key].map(p => ({ p, k: mix(p.t + p.g0) })).sort((a, b) => a.p.g0 - b.p.g0 || a.k - b.k).map(x => x.p);
    const s = new Float64Array(D), k = new Int32Array(D), acc = new Set();
    let open = [];
    for (const p of P) {
      open = open.filter(q => q.gE > p.g0);
      if (open.length >= slots) continue;
      open.push(p); acc.add(p.t + "|" + p.g0);
      for (let m = 0; m < p.g.length; m++) { s[p.g[m]] += p.r[m]; k[p.g[m]]++; }
    }
    return { s, k, acc };
  };
  const books = slots ? { ls: fill("ls"), long: fill("long") } : null;
  const curve = key => {
    const eq = new Float64Array(D); let v = ACCOUNT;
    const S = books ? books[key].s : sum[key], K = books ? books[key].k : cnt[key];
    for (let g = 0; g < D; g++) {
      if (g > fromIdx && K[g]) v *= 1 + S[g] / (books ? slots : K[g]);
      eq[g] = g < fromIdx ? NaN : v;
    }
    return eq;
  };
  const invested = key => {
    const K = books ? books[key].k : cnt[key];
    let k = 0, s = 0; for (let g = fromIdx + 1; g < D; g++) if (K[g]) { k++; s += K[g]; }
    return { share: k / Math.max(1, D - fromIdx - 1), avg: k ? s / k : 0 };
  };
  const pick = key => {
    let list = key === "ls" ? trades : trades.filter(t => t.side > 0);
    if (books) list = list.filter(t => books[key].acc.has(t.t + "|" + t.g0));
    return list.sort((a, b) => a.g1 - b.g1 || a.g0 - b.g0);
  };
  const L = { ls: pick("ls"), long: pick("long") };
  // held[g]: how many positions earned session g's return (to size each trade in euro, see the calendar)
  const held = key => books ? books[key].k : cnt[key];
  return {
    cfg,
    all: { eq: curve("ls"), inv: invested("ls"), trades: stat(L.ls), list: L.ls, held: held("ls") },
    long: { eq: curve("long"), inv: invested("long"), trades: stat(L.long), list: L.long, held: held("long") },
    open: openL.sort((a, b) => a.ret - b.ret),   // long positions still held on the last day, worst first
  };
}

// trade statistics (list in the order the trades closed)
export function stat(list) {
  const n = list.length;
  let w = 0, sw = 0, sl = 0, nl = 0, tot = 0, best = -Infinity, worst = Infinity, days = 0, streak = 0, run = 0;
  const why = { rule: 0, tp: 0, sl: 0, trail: 0 };
  for (const t of list) {
    tot += t.ret; days += t.days; why[t.why]++;
    if (t.ret > 0) { w++; sw += t.ret; run = 0; } else { nl++; sl += t.ret; run++; if (run > streak) streak = run; }
    if (t.ret > best) best = t.ret; if (t.ret < worst) worst = t.ret;
  }
  return {
    n, wins: w, winRate: n ? w / n * 100 : null, avg: n ? tot / n * 100 : null,
    tp: why.tp, sl: why.sl, trail: why.trail,
    worst: n ? worst * 100 : null, best: n ? best * 100 : null,
    avgWin: w ? sw / w * 100 : null, avgLoss: nl ? sl / nl * 100 : null,
    pf: sl < 0 ? sw / -sl : w ? Infinity : null, days: n ? days / n : null, streak,
  };
}

export function spyCurve(data, fromIdx = 0) {
  if (!data.spy) return null;
  const D = data.dates.length, eq = new Float64Array(D), b = data.spy[fromIdx];
  for (let g = 0; g < D; g++) eq[g] = g < fromIdx ? NaN : ACCOUNT * data.spy[g] / b;
  return eq;
}

// summary numbers for a curve from fromIdx to the end
// Also: Sharpe and Sortino ratios (daily returns, no risk-free rate, x sqrt(252)), the longest time
// under a previous high (in sessions) and the return / worst drop ratio (Calmar).
export function summary(eq, dates, fromIdx, to = eq.length - 1) {
  const start = eq[fromIdx], end = eq[to];
  let peak = start, dd = 0, under = 0, longest = 0, s = 0, q = 0, dn = 0, k = 0;
  for (let g = fromIdx; g <= to; g++) {
    if (eq[g] >= peak) { peak = eq[g]; under = 0; } else if (++under > longest) longest = under;
    const x = eq[g] / peak - 1; if (x < dd) dd = x;
    if (g > fromIdx && eq[g - 1] > 0) { const r = eq[g] / eq[g - 1] - 1; s += r; q += r * r; if (r < 0) dn += r * r; k++; }
  }
  const years = (Date.parse(dates[to]) - Date.parse(dates[fromIdx])) / (365.25 * 864e5);
  const mean = k ? s / k : 0, sd = k > 1 ? Math.sqrt(Math.max(0, q / k - mean * mean)) : 0, dsd = k ? Math.sqrt(dn / k) : 0;
  const cagr = years > 0 && end > 0 && start > 0 ? ((end / start) ** (1 / years) - 1) * 100 : null;
  return {
    end, ret: (end / start - 1) * 100, cagr, dd: dd * 100,
    sharpe: sd > 0 ? mean / sd * Math.sqrt(252) : null, sortino: dsd > 0 ? mean / dsd * Math.sqrt(252) : null,
    calmar: cagr != null && dd < 0 ? cagr / (-dd * 100) : null, under: longest,
  };
}

// The account after Italian costs: `rate` tax on each calendar year's net gain (losses carried
// forward for 4 years, as in an "amministrato" account) and a 0.2% a year stamp duty. The tax still
// due is paid on the last day. Simplified: gains are taxed in the year they're made.
export function afterTax(eq, dates, fromIdx, rate = 0.26, stamp = 0.002, carry = 4) {
  const out = new Float64Array(eq.length).fill(NaN), losses = [];
  let v = eq[fromIdx], yStart = v, paid = 0;
  const pay = (gain, year) => {
    if (gain < 0) { losses.push({ year, amount: -gain }); return 0; }
    for (const L of losses) if (year - L.year <= carry && L.amount > 0 && gain > 0) { const u = Math.min(gain, L.amount); L.amount -= u; gain -= u; }
    return gain * rate;
  };
  out[fromIdx] = v;
  for (let g = fromIdx + 1; g < eq.length; g++) {
    if (dates[g].slice(0, 4) !== dates[g - 1].slice(0, 4)) { const t = pay(v - yStart, +dates[g - 1].slice(0, 4)); v -= t; paid += t; yStart = v; }
    v *= eq[g] / eq[g - 1];
    v -= v * stamp / 252;
    out[g] = v;
  }
  const t = pay(v - yStart, +dates[eq.length - 1].slice(0, 4)); out[eq.length - 1] = v - t; paid += t;
  return { eq: out, tax: paid };
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
