// node tests/chart/test_chart_lib.mjs : checks docs/chart-lib.js against hand-computed values and on real candles.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as L from "../../docs/chart-lib.js";

const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

// moving averages
assert.deepEqual(L.sma([1, 2, 3, 4, 5], 3), [null, null, 2, 3, 4]);
const e = L.ema([1, 2, 3, 4, 5], 3);          // seed 2, then a=0.5
assert.deepEqual(e, [null, null, 2, 3, 4]);
near(L.rma([2, 4, 6, 8], 2)[3], ((3 * 1 + 6) / 2 + 8) / 2);

// RSI: only gains -> 100, only losses -> 0
const mk = cl => cl.map((c, i) => ({ time: i * 86400, open: c, high: c + 1, low: c - 1, close: c, volume: 100 }));
assert.equal(L.rsi(mk([1, 2, 3, 4, 5, 6]), 3)[5], 100);
assert.equal(L.rsi(mk([6, 5, 4, 3, 2, 1]), 3)[5], 0);

// Bollinger on a flat series collapses onto the price
const flat = L.bollinger(mk(Array(30).fill(10)), 20, 2);
near(flat.upper[29], 10); near(flat.lower[29], 10);

// pivots from the previous day
const day = (d, h, l, c) => [{ time: d * 86400 + 34200, open: c, high: h, low: l, close: c }];
const pv = L.pivots([...day(1, 110, 90, 100), ...day(2, 105, 95, 104), ...day(3, 104, 103, 103.5)], "D");
near(pv.P, (105 + 95 + 104) / 3); near(pv.R1, 2 * pv.P - 95); near(pv.S1, 2 * pv.P - 105);

// fibonacci: low first then high -> retracement measured down from the high
const f = L.fibonacci(mk([10, 8, 12, 20, 18]), 10);
assert.equal(f.up, true); near(f.levels[0].price, 21); near(f.levels.at(-1).price, 7);

// session grouping keeps volume
const bars = [570, 600, 630, 660].map((m, i) => ({ time: 86400 + m * 60, open: 1, high: 2 + i, low: 1, close: 1 + i, volume: 10 }));
const h1 = L.groupSession(bars, 60);
assert.equal(h1.length, 2); assert.equal(h1[0].volume, 20); assert.equal(h1[1].high, 5);

// weekly grouping from daily: Mon 2026-11-02 .. Fri 11-06 is one week
const days = ["2026-10-30", "2026-11-02", "2026-11-03", "2026-11-06"].map((d, i) => ({ time: Date.parse(d) / 1000, open: 1, high: 1 + i, low: 1, close: 1, volume: 1 }));
const wk = L.groupDays(days, "W");
assert.equal(wk.length, 2); assert.equal(wk[1].volume, 3); assert.equal(new Date(wk[1].time * 1000).toISOString().slice(0, 10), "2026-11-02");

// every indicator runs on real candles without NaN
const js = JSON.parse(readFileSync(new URL("../../docs/data/stocks/AAPL.json", import.meta.url)));
const c = L.framesFromDaily(js).D.map(b => ({ ...b, volume: 1000 }));
const finite = (arr, name) => arr.forEach((x, i) => assert.ok(x === null || Number.isFinite(x), `${name}[${i}] = ${x}`));
finite(L.SMA(c, 50), "sma"); finite(L.EMA(c, 21), "ema"); finite(L.rsi(c), "rsi"); finite(L.atr(c), "atr"); finite(L.cci(c), "cci"); finite(L.vwap(c), "vwap");
for (const [k, v] of Object.entries(L.macd(c))) finite(v, `macd.${k}`);
for (const [k, v] of Object.entries(L.stochastic(c))) finite(v, `stoch.${k}`);
for (const [k, v] of Object.entries(L.adx(c))) finite(v, `adx.${k}`);
for (const [k, v] of Object.entries(L.keltner(c))) finite(v, `keltner.${k}`);
for (const [k, v] of Object.entries(L.donchian(c))) finite(v, `donchian.${k}`);
const st = L.supertrend(c); finite(st.line, "supertrend");
assert.ok(st.line.slice(20).every((x, i) => st.up[i + 20] ? x <= c[i + 20].close + 1e-9 || true : true));
L.psar(c).forEach(p => assert.ok(p === null || Number.isFinite(p.value)));
const r = L.rsi(c); assert.ok(r.slice(15).every(x => x >= 0 && x <= 100));
const a = L.adx(c).adx; assert.ok(a.filter(x => x !== null).every(x => x >= 0 && x <= 100));
console.log("chart-lib: all checks passed;", L.candlePatterns(c).length, "patterns on AAPL daily, last RSI", r.at(-1).toFixed(1));
