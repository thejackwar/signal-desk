// Drawing tools maths (docs/draw.js). Run: node tests/chart/test_draw.mjs
import assert from "node:assert/strict";
import { makeTimeMap, fibPrice, positionStats, upgrade, rangeProfile, measureStats, TOOL } from "../../docs/draw.js";

const day = 86400, times = [0, 1, 2, 5, 6].map(d => 1.7e9 + d * day);   // a weekend gap after the third candle
const { tToL, lToT } = makeTimeMap(times, day);
assert.equal(tToL(times[0]), 0);
assert.equal(tToL(times[3]), 3);
assert.equal(tToL(times[2] + 1.5 * day), 2.5, "inside a gap: halfway between the candles");
assert.equal(tToL(times[4] + 3 * day), 7, "past the last candle: one step per bar");
assert.equal(tToL(times[0] - 2 * day), -2);
for (const l of [-1, 0, 0.4, 2.5, 3, 4, 6.25]) assert.ok(Math.abs(tToL(lToT(l)) - l) < 1e-9, `round trip ${l}`);

assert.equal(fibPrice(100, 200, 0), 200);
assert.equal(fibPrice(100, 200, 1), 100);
assert.equal(fibPrice(100, 200, 0.5), 150);

const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} ≈ ${b}`);
const L = positionStats("long", 100, 110, 95);
near(L.rr, 2); near(L.tpPct, 10); near(L.slPct, -5);
const Sh = positionStats("short", 100, 90, 105);
near(Sh.rr, 2); near(Sh.tpPct, 10); near(Sh.slPct, -5);

const up = upgrade([{ type: "h", p: 10 }, { type: "l", a: { t: 1, p: 2 }, b: { t: 3, p: 4 } }, { type: "bogus" }, null]);
assert.deepEqual(up.map(d => d.type), ["hline", "trend"]);
assert.equal(up[1].pts[1].p, 4);
assert.equal(TOOL.channel.n, 3);
// fixed range volume profile: only the picked candles count; up candles are the buying share
const bars = [
  { time: 1, open: 10, high: 11, low: 9, close: 10.5, volume: 100 },
  { time: 2, open: 10.5, high: 12, low: 10, close: 10, volume: 300 },
  { time: 3, open: 10, high: 10.2, low: 9.8, close: 10.1, volume: 600 },
  { time: 4, open: 50, high: 60, low: 40, close: 55, volume: 1e6 },   // outside the range
];
const vp = rangeProfile(bars, 2.4, -0.3, 20);   // any order, fractional ends -> candles 0..2
assert.equal(vp.from, 0); assert.equal(vp.to, 2);
near(vp.total, 1000);
near(vp.buy.reduce((a, b) => a + b, 0), 700);   // candles 0 and 2 closed up
assert.ok(vp.poc > 9.8 && vp.poc < 10.2, "POC where the big candle traded");
assert.ok(vp.val <= vp.poc && vp.vah >= vp.poc);
assert.equal(rangeProfile(bars.map(b => ({ ...b, volume: null })), 0, 2), null, "no volume -> no profile");
// measure tool: % change, bars and time between two points
{
  const tm = makeTimeMap([0, 86400, 172800, 259200], 86400);
  const m = measureStats({ t: 0, p: 100 }, { t: 259200, p: 112.5 }, tm.tToL);
  near(m.pct, 12.5); near(m.diff, 12.5); assert.equal(m.bars, 3); assert.equal(m.span, "3d");
  const m2 = measureStats({ t: 0, p: 200 }, { t: 5400, p: 190 }, tm.tToL);
  near(m2.pct, -5); assert.equal(m2.span, "1h 30m");
}
console.log("draw.js: all good");
