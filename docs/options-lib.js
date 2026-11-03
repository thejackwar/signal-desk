// Options model for docs/options.html: the same rules as engine/options_model.py
// (tests/options/test_parity.py checks they agree). Black-Scholes prices and Greeks, the
// volatility skew, strike picking by delta, monthly expiries, and the analysis of a position
// (profit and loss curve, breakevens, max gain/loss, chance of profit, net Greeks).

export const R = 0.04;
export const COMMISSION = 0.65;
export const SPREAD_PCT = 0.025, SPREAD_MIN = 0.01, SPREAD_MAX = 0.15;
// calibrated on SPY's real option chain (2 Nov 2026), as in engine/options_model.py
export const ATM_VIX = 0.82, SKEW_INDEX = 0.30, SKEW_STOCK = 0.15, CALL_LIN = 0.46, CALL_QUAD = 0.21;
export const MIN_DAYS = 21;

// legs: t C/P, s +1 buy / -1 sell, d target |delta| or "atm", exp "far" = the month after
export const STRATEGIES = {
  covered_call:     { stock: 1, kind: "full", legs: [{ t: "C", s: -1, d: 0.30 }] },
  cash_secured_put: { stock: 0, kind: "full", legs: [{ t: "P", s: -1, d: 0.30 }] },
  wheel:            { stock: 0, kind: "full", legs: [{ t: "P", s: -1, d: 0.30 }], wheel: true },
  protective_put:   { stock: 1, kind: "full", legs: [{ t: "P", s: 1, d: 0.25 }] },
  collar:           { stock: 1, kind: "full", legs: [{ t: "P", s: 1, d: 0.25 }, { t: "C", s: -1, d: 0.25 }] },
  bull_call_spread: { stock: 0, kind: "risk", legs: [{ t: "C", s: 1, d: "atm" }, { t: "C", s: -1, d: 0.25 }] },
  bear_put_spread:  { stock: 0, kind: "risk", legs: [{ t: "P", s: 1, d: "atm" }, { t: "P", s: -1, d: 0.25 }] },
  iron_condor:      { stock: 0, kind: "risk", legs: [{ t: "P", s: 1, d: 0.08 }, { t: "P", s: -1, d: 0.20 }, { t: "C", s: -1, d: 0.20 }, { t: "C", s: 1, d: 0.08 }] },
  long_straddle:    { stock: 0, kind: "risk", legs: [{ t: "C", s: 1, d: "atm" }, { t: "P", s: 1, d: "atm" }] },
  long_strangle:    { stock: 0, kind: "risk", legs: [{ t: "C", s: 1, d: 0.25 }, { t: "P", s: 1, d: 0.25 }] },
  calendar:         { stock: 0, kind: "risk", legs: [{ t: "C", s: -1, d: "atm" }, { t: "C", s: 1, d: "atm", exp: "far" }] },
};
export const ORDER = Object.keys(STRATEGIES);

// What each strategy is, in plain words. risk: "defined" (the most you can lose is known when you
// open it), "stock" (you can lose about what the shares can lose), "premium" (all you paid).
export const META = {
  covered_call: {
    name: "Covered call", group: "Income", outlook: "Neutral to slightly bullish", risk: "stock", vol: "Short volatility",
    short: "Own 100 shares and sell a call above the price: you're paid every month, but you give up the gains above the strike.",
    how: ["Own 100 shares of the stock (one contract covers 100 shares).", "Sell one call about 30 delta (roughly 5-8% above the price for a typical stock), expiring in 3-6 weeks.", "At expiry: if the stock is below the strike you keep the shares and the premium and sell a new call. Above it, the shares are sold at the strike (you can buy them back and start again)."],
    good: "Sideways or slowly rising markets: the premium adds a few percent a year on top of the shares.",
    bad: "A sharp fall (the premium cushions only a little of it) and a sharp rally (your gain stops at the strike).",
  },
  cash_secured_put: {
    name: "Cash-secured put", group: "Income", outlook: "Neutral to bullish", risk: "stock", vol: "Short volatility",
    short: "Sell a put below the price and keep the cash to buy the shares: paid to wait for a lower entry price.",
    how: ["Keep enough cash to buy 100 shares at the strike.", "Sell one put about 30 delta (a few % below the price), expiring in 3-6 weeks.", "At expiry: above the strike you keep the premium and repeat. Below it you buy 100 shares at the strike, minus the premium you already got."],
    good: "Stocks you'd be happy to own at a lower price, in calm or rising markets.",
    bad: "A crash: you must buy at the strike while the stock is much lower. The loss is close to owning the shares.",
  },
  wheel: {
    name: "The wheel", group: "Income", outlook: "Neutral to bullish", risk: "stock", vol: "Short volatility",
    short: "Sell puts until you get the shares, then sell calls on them until they're sold, and go round again.",
    how: ["Start with cash: sell a 30-delta cash-secured put each month.", "If it's exercised you now own 100 shares: sell a 30-delta covered call each month.", "When the shares are called away you're back to cash: sell puts again."],
    good: "Range-bound stocks: premium comes in every month on both sides.",
    bad: "A stock that keeps falling: you hold it all the way down, collecting small call premiums.",
  },
  protective_put: {
    name: "Protective put", group: "Protection", outlook: "Bullish, but worried", risk: "defined", vol: "Long volatility",
    short: "Own the shares and buy a put: insurance that sets a floor under the price, for a monthly cost.",
    how: ["Own 100 shares.", "Buy one put about 25 delta (a few % below the price), 3-6 weeks out.", "Your loss can't go below the strike (plus the put's cost) until expiry; renew it each month if you still want cover."],
    good: "Holding through an event you're nervous about, or locking in a big gain without selling.",
    bad: "Quiet markets: you pay the insurance every month and it expires worthless most of the time.",
  },
  collar: {
    name: "Collar", group: "Protection", outlook: "Neutral, protect gains", risk: "defined", vol: "Roughly neutral",
    short: "Own the shares, buy a put below and sell a call above: the call pays for the insurance, the gains are capped.",
    how: ["Own 100 shares.", "Buy a 25-delta put (the floor) and sell a 25-delta call (the cap), same expiry.", "The call's premium pays for most or all of the put, so protection costs little or nothing."],
    good: "Protecting a position cheaply when you're fine giving up the upside for a while.",
    bad: "A strong rally: the shares are sold at the cap.",
  },
  bull_call_spread: {
    name: "Bull call spread", group: "Direction", outlook: "Bullish", risk: "defined", vol: "Mostly neutral",
    short: "Buy a call at the price and sell a higher one: a cheaper bet on a rise, with the gain and the loss both capped.",
    how: ["Buy a call at the money (strike nearest the price).", "Sell a call about 25 delta higher up, same expiry.", "Max gain: the gap between the strikes minus what you paid. Max loss: what you paid."],
    good: "You expect a moderate rise by expiry.",
    bad: "The stock goes nowhere or falls: you lose what you paid.",
  },
  bear_put_spread: {
    name: "Bear put spread", group: "Direction", outlook: "Bearish", risk: "defined", vol: "Mostly neutral",
    short: "Buy a put at the price and sell a lower one: a cheaper bet on a fall, with the gain and the loss both capped.",
    how: ["Buy a put at the money.", "Sell a put about 25 delta lower, same expiry.", "Max gain: the gap between the strikes minus what you paid. Max loss: what you paid."],
    good: "You expect a moderate fall by expiry, or want a hedge that costs little.",
    bad: "The stock rises or stays put: you lose what you paid.",
  },
  iron_condor: {
    name: "Iron condor", group: "Range", outlook: "Neutral (stays in a range)", risk: "defined", vol: "Short volatility",
    short: "Sell a put below and a call above, buy cheaper ones further out: paid if the price stays between the short strikes.",
    how: ["Sell a 20-delta put and a 20-delta call.", "Buy an 8-delta put and an 8-delta call further out (the wings cap the loss).", "Max gain: the premium. Max loss: the width of a wing minus the premium."],
    good: "Calm markets, especially after a spike in volatility.",
    bad: "A big move either way. Wins are frequent but small; losses are rarer but bigger.",
  },
  long_straddle: {
    name: "Long straddle", group: "Volatility", outlook: "A big move, either way", risk: "premium", vol: "Long volatility",
    short: "Buy a call and a put at the same strike: you profit if the price moves a lot in either direction.",
    how: ["Buy one call and one put at the money, same expiry.", "You profit if the stock moves more than the total premium, up or down.", "Max loss: the premium, if the stock ends exactly at the strike."],
    good: "Before an event that could move the stock hard (when the options aren't already priced for it).",
    bad: "Quiet markets: both options lose value every day (time decay).",
  },
  long_strangle: {
    name: "Long strangle", group: "Volatility", outlook: "A very big move, either way", risk: "premium", vol: "Long volatility",
    short: "Buy a call above and a put below the price: cheaper than a straddle, but it needs a bigger move.",
    how: ["Buy a 25-delta call and a 25-delta put, same expiry.", "You profit if the stock moves past a strike by more than the premium.", "Max loss: the premium."],
    good: "Expecting a shock (crash or squeeze) without knowing which way.",
    bad: "Most months: the move isn't big enough and both options expire worthless.",
  },
  calendar: {
    name: "Calendar spread", group: "Volatility", outlook: "Neutral (stays near the strike)", risk: "defined", vol: "Long volatility",
    short: "Sell a near-month call and buy the next month's at the same strike: profits from the short one decaying faster.",
    how: ["Sell an at-the-money call expiring this month.", "Buy the same strike expiring the month after.", "Best result if the stock is near the strike at the first expiry; then the longer call is sold. Max loss: about what you paid."],
    good: "Quiet markets, especially when volatility is low and could rise.",
    bad: "A big move either way, or volatility falling.",
  },
};
export const GROUPS = ["Income", "Protection", "Direction", "Range", "Volatility"];

// ------------------------------------------------------------------ Black-Scholes
// erfc with ~1e-12 relative accuracy (W. J. Cody's rational approximations are overkill here;
// this continued-fraction/series pair matches Python's math.erfc to well under 1e-9)
function erfc(x) {
  const z = Math.abs(x);
  let r;
  if (z < 2.5) {                                    // series for erf
    let sum = z, term = z, n = 0;
    while (Math.abs(term) > 1e-17 * Math.abs(sum)) { n++; term *= -z * z / n; sum += term / (2 * n + 1); }
    r = 1 - 2 / Math.sqrt(Math.PI) * sum;
  } else {                                          // continued fraction for erfc
    let f = 0;
    for (let n = 60; n >= 1; n--) f = n / 2 / (z + f);
    r = Math.exp(-z * z) / Math.sqrt(Math.PI) / (z + f);
  }
  return x >= 0 ? r : 2 - r;
}
export const ncdf = x => 0.5 * erfc(-x / Math.SQRT2);
export const npdf = x => Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI);

export function bs(S, K, T, iv, t, r = R) {
  if (T <= 1e-9 || iv <= 0) return t === "C" ? Math.max(S - K, 0) : Math.max(K - S, 0);
  const sq = iv * Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * iv * iv) * T) / sq, d2 = d1 - sq;
  return t === "C" ? S * ncdf(d1) - K * Math.exp(-r * T) * ncdf(d2) : K * Math.exp(-r * T) * ncdf(-d2) - S * ncdf(-d1);
}

export function greeks(S, K, T, iv, t, r = R) {
  if (T <= 1e-9 || iv <= 0) {
    const itm = t === "C" ? S > K : S < K;
    return { delta: itm ? (t === "C" ? 1 : -1) : 0, gamma: 0, theta: 0, vega: 0 };
  }
  const sq = iv * Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * iv * iv) * T) / sq, d2 = d1 - sq;
  const pdf = npdf(d1);
  const gamma = pdf / (S * sq), vega = S * pdf * Math.sqrt(T) / 100;
  const decay = -S * pdf * iv / (2 * Math.sqrt(T));
  return t === "C"
    ? { delta: ncdf(d1), gamma, vega, theta: (decay - r * K * Math.exp(-r * T) * ncdf(d2)) / 365 }
    : { delta: ncdf(d1) - 1, gamma, vega, theta: (decay + r * K * Math.exp(-r * T) * ncdf(-d2)) / 365 };
}

// volatility that makes Black-Scholes match a price (null if the price is outside what any vol gives)
export function impliedVol(price, S, K, T, t, r = R) {
  if (!(price > 0) || T <= 0) return null;
  const intrinsic = t === "C" ? Math.max(S - K * Math.exp(-r * T), 0) : Math.max(K * Math.exp(-r * T) - S, 0);
  if (price <= intrinsic + 1e-6) return null;
  let lo = 0.005, hi = 5;
  if (bs(S, K, T, hi, t, r) < price) return null;
  for (let k = 0; k < 80; k++) {
    const mid = (lo + hi) / 2;
    if (bs(S, K, T, mid, t, r) > price) hi = mid; else lo = mid;
  }
  return (lo + hi) / 2;
}

export function skewIv(atm, S, K, T, slope) {
  T = Math.max(T, 1 / 365);
  let x = -Math.log(K / S) / (atm * Math.sqrt(T));
  x = Math.max(-2.5, Math.min(2.5, x));
  const k = x > 0 ? 1 + slope * x : Math.max(0.6, 1 + slope / SKEW_INDEX * (CALL_LIN * x + CALL_QUAD * x * x));
  return Math.max(0.05, atm * k);
}

export const halfSpread = p => Math.min(SPREAD_MAX, Math.max(SPREAD_MIN, SPREAD_PCT * p));

// ------------------------------------------------------------------ strikes and expiries
export function strikeStep(S) { return S < 25 ? 0.5 : S < 100 ? 1 : S < 250 ? 2.5 : S < 500 ? 5 : 10; }
const r4 = x => Math.round(x * 1e4) / 1e4;
export function strikeGrid(S, step = strikeStep(S)) {
  const lo = Math.floor(S * 0.5 / step), hi = Math.ceil(S * 1.5 / step), out = [];
  for (let i = Math.max(lo, 1); i <= hi; i++) out.push(r4(i * step));
  return out;
}

export function pickStrikesBy(legs, S, grid, deltaOf) {
  grid = [...new Set(grid)].sort((a, b) => a - b);
  const atmK = grid.reduce((b, K) => (Math.abs(K - S) < Math.abs(b - S) || (Math.abs(K - S) === Math.abs(b - S) && K < b)) ? K : b, grid[0]);
  const out = legs.map(leg => {
    if (leg.d === "atm") return atmK;
    let best = null, bd = 9;
    for (const K of grid) {
      if ((leg.t === "C" && K < S) || (leg.t === "P" && K > S)) continue;
      const dl = deltaOf(leg, K);
      if (dl == null) continue;
      if (Math.abs(dl - leg.d) < bd - 1e-12) { best = K; bd = Math.abs(dl - leg.d); }
    }
    return best ?? atmK;
  });
  for (const t of ["C", "P"]) {
    const idx = legs.map((l, i) => i).filter(i => legs[i].t === t && (legs[i].exp || "near") === "near");
    idx.sort((a, b) => -(legs[a].d === "atm" ? 0.5 : legs[a].d) + (legs[b].d === "atm" ? 0.5 : legs[b].d));
    for (let n = 1; n < idx.length; n++) {
      const a = idx[n - 1], b = idx[n];
      if ((t === "C" && out[b] <= out[a]) || (t === "P" && out[b] >= out[a])) {
        const nxt = t === "C" ? grid.filter(K => K > out[a]) : grid.filter(K => K < out[a]).reverse();
        if (nxt.length) out[b] = nxt[0];
      }
    }
  }
  return out;
}

export function pickStrikes(legs, S, Tof, ivOf, step = strikeStep(S)) {
  return pickStrikesBy(legs, S, strikeGrid(S, step), (leg, K) => {
    const T = Tof(leg);
    return Math.abs(greeks(S, K, T, ivOf(K, T), leg.t).delta);
  });
}

const D = s => new Date(s + "T00:00:00Z");
export const isoDay = d => d.toISOString().slice(0, 10);
export function thirdFriday(y, m) {            // m: 1-12
  const d = new Date(Date.UTC(y, m - 1, 15));
  d.setUTCDate(15 + (5 - d.getUTCDay() + 7) % 7);
  return d;
}
export function nextExpiry(day, minDays = MIN_DAYS) {   // day: "YYYY-MM-DD"
  const d = D(day);
  let y = d.getUTCFullYear(), m = d.getUTCMonth() + 1;
  for (let k = 0; k < 4; k++) {
    const e = thirdFriday(y, m);
    if ((e - d) / 864e5 >= minDays) return isoDay(e);
    if (m === 12) { y++; m = 1; } else m++;
  }
  throw new Error("no expiry");
}
export function farExpiry(near) {
  const d = D(near);
  let y = d.getUTCFullYear(), m = d.getUTCMonth() + 2;
  if (m === 13) { y++; m = 1; }
  return isoDay(thirdFriday(y, m));
}
export const daysBetween = (a, b) => Math.round((D(b) - D(a)) / 864e5);

// ------------------------------------------------------------------ a position
// pos = { S (price when opened), shares, legs: [{ t, s, K, qty, price (per share, paid or received),
//         T (years left), iv }] }. The "near" expiry is the shortest T among the legs.
export const nearT = pos => Math.min(...pos.legs.map(l => l.T));

export function pnlAt(pos, P, elapsed) {          // elapsed: years from now
  let v = pos.shares * (P - pos.S);
  for (const l of pos.legs) {
    const left = Math.max(l.T - elapsed, 0);
    v += l.s * l.qty * 100 * (bs(P, l.K, left, l.iv, l.t) - l.price);
  }
  return v;
}

export function netGreeks(pos, P = pos.S) {
  const g = { delta: pos.shares, gamma: 0, theta: 0, vega: 0 };
  for (const l of pos.legs) {
    const x = greeks(P, l.K, l.T, l.iv, l.t);
    for (const k of ["delta", "gamma", "theta", "vega"]) g[k] += l.s * l.qty * 100 * x[k];
  }
  return g;
}

// Profit/loss at the first expiry: max gain, max loss (null = no limit), breakevens, chance of profit
export function analyze(pos, { vol } = {}) {
  const T = nearT(pos);
  const ks = pos.legs.map(l => l.K);
  const lo = Math.max(0.01, Math.min(pos.S, ...ks) * 0.2), hi = Math.max(pos.S, ...ks) * 3;
  const xs = [];
  for (let i = 0; i <= 1200; i++) xs.push(lo + (hi - lo) * i / 1200);
  xs.push(...ks, pos.S); xs.sort((a, b) => a - b);
  const ys = xs.map(x => pnlAt(pos, x, T));
  let maxG = Math.max(...ys), maxL = Math.min(...ys);
  // beyond the grid: does the line keep rising or falling?
  const n = xs.length, slopeHi = (ys[n - 1] - ys[n - 2]) / (xs[n - 1] - xs[n - 2]);
  const unlimitedGain = slopeHi > 1e-6, unlimitedLoss = slopeHi < -1e-6;
  const at0 = pnlAt(pos, 0.01, T);
  maxG = Math.max(maxG, at0); maxL = Math.min(maxL, at0);
  const be = [];
  for (let i = 1; i < n; i++) {
    const a = ys[i - 1], b = ys[i];
    if ((a < 0 && b >= 0) || (a >= 0 && b < 0)) {
      if (a === b) continue;
      const x = xs[i - 1] + (xs[i] - xs[i - 1]) * (-a) / (b - a);
      if (!be.length || Math.abs(x - be[be.length - 1]) > pos.S * 1e-4) be.push(x);
    }
  }
  // chance of profit: a lognormal price at the first expiry, with the position's volatility
  const sig = vol || pos.legs.reduce((s, l) => s + l.iv, 0) / pos.legs.length;
  const m = Math.log(pos.S) + (R - 0.5 * sig * sig) * T, sd = sig * Math.sqrt(Math.max(T, 1 / 365));
  let pop = 0, ev = 0;
  for (let i = 1; i < n; i++) {
    const p = ncdf((Math.log(xs[i]) - m) / sd) - ncdf((Math.log(xs[i - 1]) - m) / sd);
    const y = (ys[i] + ys[i - 1]) / 2;
    if (y > 0) pop += p;
    ev += p * y;
  }
  if (ys[n - 1] > 0) pop += 1 - ncdf((Math.log(xs[n - 1]) - m) / sd);
  if (ys[0] > 0) pop += ncdf((Math.log(xs[0]) - m) / sd);
  return { maxGain: unlimitedGain ? null : maxG, maxLoss: unlimitedLoss ? null : maxL,
           breakevens: be, pop, ev, T, xs, ys, unlimitedGain, unlimitedLoss };
}

// The example position used on the Learn tab: a $100 stock, volatility `atm`, `days` to expiry,
// strikes picked by the strategy's own rule on the model.
export function examplePosition(id, { S = 100, atm = 0.25, days = 35, slope = SKEW_STOCK } = {}) {
  const st = STRATEGIES[id];
  const Tn = days / 365, Tf = (days + 28) / 365;
  const Tof = l => l.exp === "far" ? Tf : Tn;
  const ivOf = (K, T) => skewIv(atm, S, K, T, slope);
  const strikes = pickStrikes(st.legs, S, Tof, ivOf);
  const legs = st.legs.map((l, i) => {
    const T = Tof(l), K = strikes[i], iv = ivOf(K, T);
    return { t: l.t, s: l.s, K, qty: 1, T, iv, price: bs(S, K, T, iv, l.t), exp: l.exp || "near" };
  });
  return { S, shares: 100 * st.stock, legs, atm };
}

// Money tied up: the shares, the cash for an assignment, or the most a spread can lose
export function capital(id, pos, a = analyze(pos)) {
  const st = STRATEGIES[id];
  const debit = pos.legs.reduce((s, l) => s + l.s * l.qty * 100 * l.price, 0);
  if (pos.shares) return pos.shares * pos.S + Math.max(debit, 0);
  if (id === "cash_secured_put" || id === "wheel") return pos.legs.reduce((s, l) => s + (l.t === "P" && l.s < 0 ? l.K * 100 * l.qty : 0), 0) || Math.max(debit, 0);
  if (st && st.legs.some(l => l.exp === "far")) return Math.max(debit, 0);
  return a.maxLoss != null ? -a.maxLoss : null;
}
