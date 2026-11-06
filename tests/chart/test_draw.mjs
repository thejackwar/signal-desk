// Drawing tools maths (docs/draw.js). Run: node tests/chart/test_draw.mjs
import assert from "node:assert/strict";
import { makeTimeMap, fibPrice, positionStats, upgrade, TOOL } from "../../docs/draw.js";

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
console.log("draw.js: all good");
