// node tests/chart/test_chart_patterns.mjs : checks docs/chart-patterns.js on drawn shapes and on real candles.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chartPatterns, majorLevels, zigzag } from "../../docs/chart-patterns.js";
import { framesFromDaily } from "../../docs/chart-lib.js";

// candles that walk straight between the given (index, price) waypoints, with a little range each
function path(points, wiggle = 0.6) {
  const out = [];
  for (let k = 0; k < points.length - 1; k++) {
    const [i0, p0] = points[k], [i1, p1] = points[k + 1];
    for (let i = i0; i < i1; i++) {
      const p = p0 + (p1 - p0) * (i - i0) / (i1 - i0), q = p0 + (p1 - p0) * (i + 1 - i0) / (i1 - i0);
      out.push({ time: i * 86400, open: p, close: q, high: Math.max(p, q) + wiggle, low: Math.min(p, q) - wiggle, volume: 1 });
    }
  }
  return out;
}
const kinds = c => chartPatterns(c).map(p => `${p.kind}:${p.status}`);

// double top forming: 100 -> 130 -> 112 -> 130.3, now drifting at 124 (neckline 112 not broken)
const dt = path([[0, 90], [30, 100], [50, 130], [65, 112], [80, 130.3], [86, 124]]);
assert.deepEqual(kinds(dt), ["dtop:forming"]);
const p = chartPatterns(dt)[0];
assert.ok(Math.abs(p.trigger - 111.4) < 1, `neckline ${p.trigger}`);
assert.ok(p.target < p.trigger && p.bull === false);

// the same top, broken: closes under the neckline -> breakdown, still shown while fresh
const dtb = path([[0, 90], [30, 100], [50, 130], [65, 112], [80, 130.3], [92, 108]]);
assert.deepEqual(kinds(dtb), ["dtop:breakdown"]);
// ... and gone once the break is old news
const dto = path([[0, 90], [30, 100], [50, 130], [65, 112], [80, 130.3], [92, 108], [140, 70]]);
assert.ok(!kinds(dto).includes("dtop:breakdown"));
// invalidated: price closes well above the tops -> no double top
const dti = path([[0, 90], [30, 100], [50, 130], [65, 112], [80, 130.3], [86, 124], [96, 140]]);
assert.ok(!kinds(dti).some(k => k.startsWith("dtop")));

// inverse head & shoulders forming
const ihs = path([[0, 140], [20, 120], [35, 100], [48, 115], [62, 88], [76, 116], [90, 101], [97, 110]]);
assert.ok(kinds(ihs).includes("ihs:forming"), kinds(ihs).join());

// ascending triangle: flat top at 120, rising lows
const at = path([[0, 80], [20, 100], [35, 120], [50, 102], [65, 120.2], [78, 110], [84, 117]]);
assert.ok(kinds(at).includes("atri:forming"), kinds(at).join());

// a plain trend shows nothing
const tr = path([[0, 50], [200, 150]]);
assert.deepEqual(kinds(tr), []);

// zigzag alternates
const z = zigzag(dt, 4, 3);
for (let i = 1; i < z.length; i++) assert.notEqual(z[i].hi, z[i - 1].hi);

// real candles: Bitcoin daily from the site's data. At most 2 patterns, never noisy.
const btc = framesFromDaily(JSON.parse(readFileSync(new URL("../../docs/data/crypto/stocks/BTC-USD.json", import.meta.url))));
for (const tf of ["W", "D"]) {
  const ps = chartPatterns(btc[tf]);
  assert.ok(ps.length <= 2);
  for (const q of ps) assert.ok(Number.isFinite(q.trigger) && Number.isFinite(q.target));
  const m = majorLevels(btc, tf);
  const close = btc[tf][btc[tf].length - 1].close;
  assert.ok(m.support.length <= 2 && m.resistance.length <= 2);
  m.support.forEach(l => assert.ok(l.price < close));
  m.resistance.forEach(l => assert.ok(l.price > close));
  console.log(tf, "patterns:", ps.map(q => `${q.name} (${q.status})`).join(", ") || "none",
    "| S:", m.support.map(l => `${l.price.toFixed(0)} ${l.tfs.join("+")}`).join(", "), "| R:", m.resistance.map(l => `${l.price.toFixed(0)} ${l.tfs.join("+")}`).join(", "));
}
// walk back through history: patterns found on many days, but never more than 2 at once
let found = 0;
for (let end = 300; end <= btc.D.length; end += 5) {
  const ps = chartPatterns(btc.D.slice(0, end));
  assert.ok(ps.length <= 2);
  found += ps.length > 0;
}
console.log("days with a pattern (every 5th day checked):", found, "of", Math.floor((btc.D.length - 300) / 5) + 1);
console.log("chart patterns OK");

// bull flag: pole 100 -> 130 in 8 candles, then a gentle drift down to 124 (forming)
const bf = path([[0, 95], [40, 100], [48, 130], [51, 127], [53, 128.5], [56, 125.5], [58, 127], [61, 124.5]], 0.5);
assert.ok(kinds(bf).includes("bflag:forming"), kinds(bf).join());
const f0 = chartPatterns(bf).find(p => p.kind === "bflag");
assert.ok(f0.trigger > 124 && f0.trigger < 131 && f0.target > 140, `${f0.trigger} ${f0.target}`);
// ... breaking out of the top of the flag
const bfo = path([[0, 95], [40, 100], [48, 130], [51, 127], [53, 128.5], [56, 125.5], [58, 127], [61, 124.5], [64, 131]], 0.5);
assert.ok(kinds(bfo).includes("bflag:breakout"), kinds(bfo).join());
// ... and failing: falls back most of the pole
const bff = path([[0, 95], [40, 100], [48, 130], [51, 127], [53, 128.5], [56, 125.5], [58, 127], [61, 124.5], [67, 108]], 0.5);
assert.ok(!kinds(bff).some(k => k.startsWith("bflag")), kinds(bff).join());
// bear flag (mirror image)
const sf = path([[0, 205], [40, 200], [48, 170], [51, 173], [53, 171.5], [56, 174.5], [58, 173], [61, 175.5]], 0.5);
assert.ok(kinds(sf).includes("sflag:forming"), kinds(sf).join());
const s0 = chartPatterns(sf).find(p => p.kind === "sflag");
assert.ok(s0.trigger < 176 && s0.trigger > 169 && s0.target < 160 && s0.bull === false, `${s0.trigger} ${s0.target}`);
console.log("flags OK");

// ------------------------------------------------------------------ levels around a trade (tradeLevels)
{
  const { tradeLevels } = await import("../../docs/chart-patterns.js");
  // a range: resistance tested at 130 three times, support at 100 three times, then a long from 110
  const pts = [[0, 80], [20, 100], [35, 130], [50, 101], [65, 129.5], [80, 100.5], [95, 130.4], [110, 100], [125, 110], [126, 110]];
  const D = path(pts, 0.8), entryT = D[125].time;
  const r = tradeLevels({ D }, "D", entryT, { entry: 110, stop: 98, target: 134 });
  const res = r.major.find(l => l.side === "resistance"), sup = r.major.find(l => l.side === "support");
  assert.ok(res && Math.abs(res.price - 130) < 1.5, `resistance ${JSON.stringify(r.major)}`);
  assert.ok(sup && Math.abs(sup.price - 100) < 1.5, `support ${JSON.stringify(r.major)}`);
  // no hindsight: a level that only forms after the entry is never used
  const later = path([...pts.slice(0, -1), [140, 160], [150, 120], [160, 160.2], [170, 140]], 0.8);
  const r2 = tradeLevels({ D: later }, "D", entryT, { entry: 110, stop: 98, target: 165 });
  assert.ok(![...r2.major, ...r2.local].some(l => Math.abs(l.price - 160) < 3), JSON.stringify(r2.major));
  // real BTC: every level/line in the path, priced sensibly
  const t0 = Date.parse("2026-04-20T00:00:00Z") / 1000, i0 = btc.D.findIndex(b => b.time >= t0), e = btc.D[i0].open;
  const rb = tradeLevels(btc, "D", btc.D[i0].time, { entry: e, stop: e * 0.92, target: e * 1.16 });
  assert.ok(rb.major.length && rb.lines.length, JSON.stringify(rb));
  for (const l of [...rb.major, ...rb.local, ...rb.lines]) assert.ok(l.price > e * 0.85 && l.price < e * 1.25 && ["support", "resistance"].includes(l.side), JSON.stringify(l));
  console.log("trade levels OK", rb.major.length, "major", rb.local.length, "local", rb.lines.length, "lines");
}
