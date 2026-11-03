// Checks the Strategy Lab's own rules, patterns and advanced exits (docs/lab-rules.js, runPro in docs/lab.js).
// Run:  node tests/lab/test_pro.mjs
import { run, clean, isPro, subset, todaySignals, defaults, START_RULES } from "../../docs/lab.js";
import * as R from "../../docs/lab-rules.js";

let bad = 0;
const check = (ok, msg) => { if (!ok) bad++; console.log(`${ok ? "ok " : "BAD"} ${msg}`); };
const near = (a, b, e = 1e-9) => Math.abs(a - b) < e;

// random walks with opens, highs, lows and volume
function market(n, seed, tickers = 8) {
  let s = seed; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  const dates = []; for (let d = new Date(Date.UTC(2012, 0, 2)); dates.length < n; d.setUTCDate(d.getUTCDate() + 1)) if (d.getUTCDay() % 6) dates.push(d.toISOString().slice(0, 10));
  const T = [];
  for (let k = 0; k < tickers; k++) {
    const c = new Float64Array(n), h = new Float64Array(n), l = new Float64Array(n), o = new Float64Array(n), v = new Float64Array(n), idx = new Int32Array(n);
    let p = 50 + 50 * rnd();
    for (let i = 0; i < n; i++) {
      o[i] = p * Math.exp((rnd() - 0.5) * 0.01); p = o[i] * Math.exp((rnd() - 0.5) * 0.04 + 0.0003);
      c[i] = p; h[i] = Math.max(o[i], c[i]) * (1 + rnd() * 0.01); l[i] = Math.min(o[i], c[i]) * (1 - rnd() * 0.01); v[i] = 1e6 * (0.5 + rnd()); idx[i] = i;
    }
    T.push({ t: "T" + k, idx, c, h, l, o, v, pre: null });
  }
  const bench = Float64Array.from(T[0].c);
  return { dates, tickers: T, spy: bench, bench, benchName: "bench", sectors: Object.fromEntries(T.map((x, k) => [x.t, k % 2 ? "A" : "B"])) };
}
const data = market(900, 7);

// --- defaults are unchanged: no advanced option on means the old engine
check(!isPro(clean({ ind: "ma" })) && !isPro(defaults("rsi")) && isPro(clean({ ind: "custom" })), "isPro only for your rules or advanced options");
check(JSON.stringify(clean({ ind: "custom" }).rules) === JSON.stringify(clean({ ind: "custom", rules: START_RULES }).rules), "custom starts from the example rules");
check(clean({ ind: "ma", slMode: "atr" }).slMode === "pct", "an ATR stop at 0 falls back to off");
check(clean({ ind: "own_unknown" }).ind === "ma", "unknown strategy -> ma");

// --- indicators: SMA and RSI match the plain maths; weekly values never look ahead
const T = data.tickers[0];
const sm = R.series(T, data, { id: "sma", p: { n: 10 } });
check(near(sm[50], T.c.slice(41, 51).reduce((a, b) => a + b) / 10), "SMA(10) is the mean of the last 10 closes");
const cut = subset(data, { to: 400 }), full = R.series(data.tickers[0], data, { id: "ema", p: { n: 5 }, tf: "W" }), part = R.series(cut.tickers[0], cut, { id: "ema", p: { n: 5 }, tf: "W" });
let same = true; for (let i = 0; i < 395; i++) if (!(near(full[i], part[i]) || (isNaN(full[i]) && isNaN(part[i])))) same = false;
check(same, "weekly EMA on day i doesn't change when later days are added");
const daily = R.series(T, data, { id: "close" }), wk = R.series(T, data, { id: "close", tf: "W" });
check([...wk].every((v, i) => isNaN(v) || [...daily.slice(0, i + 1)].includes(v)), "weekly close is always a close already seen");
for (const id of Object.keys(R.IND)) { const x = R.series(T, data, { id }); if (!Number.isFinite(x[x.length - 1])) { check(false, `indicator ${id} has a value on the last day`); } }
check(true, `all ${Object.keys(R.IND).length} indicators computed`);

// --- conditions
const q = { a: { id: "close" }, op: "xa", b: { id: "sma", p: { n: 10 } } }, x = R.condition(T, data, q);
let okx = true; for (let i = 11; i < x.length; i++) if (!!x[i] !== (T.c[i] > sm[i] && T.c[i - 1] <= sm[i - 1])) okx = false;
check(okx, "crosses above = above today, not above yesterday");
const w = R.condition(T, data, { ...q, w: 5 }); let okw = true;
for (let i = 5; i < w.length; i++) if (!!w[i] !== [0, 1, 2, 3, 4].some(k => x[i - k])) okw = false;
check(okw, "'in the last 5 bars' widens a condition");
check(R.condTxt(q) === "Close crosses above SMA(10)", "condition read out in words");

// --- patterns: a hand-made bullish engulfing
const E = { t: "E", idx: Int32Array.from([0, 1, 2, 3, 4, 5, 6, 7]), o: Float64Array.from([10, 10, 10, 10, 10, 10, 10, 9.5]), c: Float64Array.from([10, 10, 10, 10, 10, 10, 9.6, 10.3]),
  h: Float64Array.from([10.1, 10.1, 10.1, 10.1, 10.1, 10.1, 10.05, 10.35]), l: Float64Array.from([9.9, 9.9, 9.9, 9.9, 9.9, 9.9, 9.55, 9.45]), v: null, pre: null };
check(R.PATTERNS.engulf_bull.f(E)[7] === 1 && R.PATTERNS.engulf_bull.f(E)[6] === 0, "bullish engulfing found on the right day");
const ps = R.patternStats(data, ["engulf_bull", "doji"], { hz: [1, 5] });
check(ps.list.length === 2 && ps.base[0].k > 0 && ps.list[0].n > 0, "pattern stats with a baseline");

// --- advanced exits on whole runs
const base = { ind: "custom", rules: { long: { mode: "all", list: [{ a: { id: "close" }, op: "xa", b: { id: "sma", p: { n: 20 } } }] }, xl: { mode: "any", list: [{ a: { id: "close" }, op: "xb", b: { id: "sma", p: { n: 20 } } }] } } };
const r0 = run(data, base);
check(r0.long.trades.n > 20, `your rules trade (${r0.long.trades.n} trades)`);
const ra = run(data, { ...base, slMode: "atr", slAtr: 1.5, tpMode: "rr", rr: 2 });
const L = ra.long.list.filter(t => t.why === "sl");
check(L.length > 0 && L.every(t => t.rm < -0.8 && t.rm > -3), "an ATR stop-out is about −1R");
check(ra.long.list.filter(t => t.why === "tp").every(t => t.rm > 1.5), "a 1:2 target is about +2R");
const rp = run(data, { ...base, slMode: "atr", slAtr: 1.5, tpMode: "rr", rr: 3, part: 50, p1: 1, p1Be: 1 });
check(rp.long.trades.part > 0 && rp.long.list.filter(t => t.part && t.why === "be").every(t => t.ret > -0.001), "after half at 1R, a break-even exit isn't a loss");
const rr = run(data, { ...base, slMode: "atr", slAtr: 2, size: "risk", risk: 1, maxPos: 5 });
check(rr.long.list.every(t => t.pos.w <= 0.2 + 1e-12), "risk sizing never puts more than 1/maxPos in one position");
let peak = 0; { const P = rr.long.list.map(t => t.pos); for (let g = 0; g < data.dates.length; g++) { const e = P.filter(p => p.g0 < g && p.gE >= g).reduce((a, p) => a + p.w, 0); peak = Math.max(peak, e); } }
check(peak <= 1 + 1e-9, `never more than 100% invested with risk sizing (peak ${(peak * 100).toFixed(0)}%)`);
const rs = run(data, { ...base, secMax: 1, maxPos: 8 });
let okS = true; { const P = rs.long.list.map(t => t.pos); for (let g = 0; g < data.dates.length; g++) { const by = {}; for (const p of P) if (p.g0 < g && p.gE > g) by[p.sec] = (by[p.sec] || 0) + 1; if (Object.values(by).some(n => n > 1)) okS = false; } }
check(okS, "at most one open position per sector");
const rc = run(data, { ...base, cool: 30 });
check(rc.long.trades.n <= r0.long.trades.n, "a pause after a loss trades less");
const ro = run(data, { ...base, entry: "open" });
check(ro.long.list.every(t => { const T2 = data.tickers.find(x => x.t === t.t); return near(t.p0, T2.o[t.g0 + 1]); }), "next-open entries fill at the next day's open");
const rt = run(data, { ...base, tLose: 5 });
check(rt.long.list.some(t => t.why === "time") && rt.long.list.filter(t => t.why === "time").every(t => t.ret < 0.01), "losers closed after 5 sessions");
const s1 = todaySignals(data, base);
check(Array.isArray(s1) && s1.every(x => x.side === 1), "today's signals listed");

console.log(bad ? `${bad} FAILED` : "PASS");
process.exit(bad ? 1 : 0);
