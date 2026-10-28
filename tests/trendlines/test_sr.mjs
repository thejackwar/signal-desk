// Checks for the horizontal support / resistance levels in docs/trendlines.js.
// Run:  node tests/trendlines/test_sr.mjs
import { srLevels, findPivots } from "../../docs/trendlines.js";

let fails = 0;
const ok = (cond, msg) => { if (!cond) { fails++; console.log("FAIL", msg); } };

// 1. A hand-made range: price bounces between ~100 and ~110 three times, then sits at 105.
const path = [];
for (let r = 0; r < 3; r++) {
  for (let i = 0; i <= 10; i++) path.push(100 + i);        // up to 110
  for (let i = 9; i >= 1; i--) path.push(100 + i);         // back down toward 100
}
for (let i = 0; i <= 5; i++) path.push(100 + i);           // ends at 105
const c = path.map((p, i) => ({ time: i, open: p, high: p + 0.4, low: p - 0.4, close: p }));
const r = srLevels(c, 250, 3);
ok(r.support.length >= 1 && Math.abs(r.support[0].price - 100.6) < 1.5, `range support ${JSON.stringify(r.support[0])}`);
ok(r.resistance.length >= 1 && Math.abs(r.resistance[0].price - 110.4) < 1.5, `range resistance ${JSON.stringify(r.resistance[0])}`);
ok(r.resistance[0]?.touches === 3, `resistance touches ${r.resistance[0]?.touches}`);

// 2. Properties on random candles.
let seed = 42;
const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
let levels = 0;
for (let k = 0; k < 2000; k++) {
  const n = 1 + Math.floor(rnd() * 900), swing = [1, 2, 3, 5, 8][k % 5], lookback = [20, 250, 600, 5000][k % 4];
  let p = 100;
  const cs = [];
  for (let i = 0; i < n; i++) {
    const o = p; p += (rnd() - 0.5) * 3; p = Math.round(p * 4) / 4;
    cs.push({ time: i, open: o, high: Math.max(o, p) + rnd(), low: Math.min(o, p) - rnd(), close: p });
  }
  const s = srLevels(cs, lookback, swing);
  const close = cs[n - 1].close, s0 = Math.max(0, n - lookback);
  const piv = [...findPivots(cs, swing, "high"), ...findPivots(cs, swing, "low")].filter(q => q.index >= s0);
  for (const [list, side] of [[s.support, "support"], [s.resistance, "resistance"]]) {
    for (let j = 0; j < list.length; j++) {
      const l = list[j];
      levels++;
      ok(side === "support" ? l.price <= close : l.price > close, `case ${k}: ${side} ${l.price} vs close ${close}`);
      ok(l.touches >= 2, `case ${k}: ${l.touches} touches`);
      ok(l.hi - l.lo <= s.atr + 1e-9 && l.lo <= l.price && l.price <= l.hi, `case ${k}: zone ${l.lo}-${l.hi} atr ${s.atr}`);
      ok(l.first >= s0 && l.last < n && l.first < l.last, `case ${k}: touches at ${l.first}..${l.last}`);
      ok(piv.filter(q => q.price >= l.lo && q.price <= l.hi).length >= l.touches, `case ${k}: swings in zone`);
      if (j) ok(side === "support" ? list[j - 1].price > l.price : list[j - 1].price < l.price, `case ${k}: ${side} order`);
    }
  }
}
console.log(`sr: 2000 random candle sets, ${levels} levels`);
console.log(fails ? `${fails} failures` : "ALL OK");
process.exit(fails ? 1 : 0);
