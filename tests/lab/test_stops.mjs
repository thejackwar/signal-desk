// Checks the Strategy Lab's take profit / stop loss (docs/lab.js).
// Run:  node tests/lab/test_stops.mjs
import { riskHit, run, defaults, clean } from "../../docs/lab.js";

let bad = 0;
const near = (a, b) => Math.abs(a - b) < 1e-9;
const check = (ok, msg) => { if (!ok) bad++; console.log(`${ok ? "ok " : "BAD"} ${msg}`); };

// --- one bar
const cfg = clean({ ind: "ma", tp: 10, sl: 5 });
check(riskHit(1, 100, 104, 96, cfg) === null, "buy: bar inside both levels -> nothing");
check(near(riskHit(1, 100, 101, 94, cfg).px, 95) && riskHit(1, 100, 101, 94, cfg).why === "sl", "buy: stop touched -> fills at 95");
check(near(riskHit(1, 100, 92, 90, cfg).px, 91), "buy: gap below the stop -> fills mid-range (91)");
check(near(riskHit(1, 100, 112, 101, cfg).px, 110) && riskHit(1, 100, 112, 101, cfg).why === "tp", "buy: target touched -> fills at 110");
check(near(riskHit(1, 100, 116, 114, cfg).px, 115), "buy: gap above the target -> fills mid-range (115)");
check(riskHit(1, 100, 111, 94, cfg).why === "sl", "buy: both in one bar -> stop counts");
check(near(riskHit(-1, 100, 106, 99, cfg).px, 105) && riskHit(-1, 100, 106, 99, cfg).why === "sl", "short: stop above -> fills at 105");
check(near(riskHit(-1, 100, 99, 89, cfg).px, 90) && riskHit(-1, 100, 99, 89, cfg).why === "tp", "short: target below -> fills at 90");
check(riskHit(1, 100, 200, 1, clean({ ind: "ma" })) === null, "both off -> never exits");

// --- whole runs on a random walk without gaps (every bar's range includes the previous close)
function walk(n, seed, tickers = 12) {
  let s = seed; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  const dates = Array.from({ length: n }, (_, i) => new Date(Date.UTC(2010, 0, 1 + i)).toISOString().slice(0, 10));
  const T = [];
  for (let k = 0; k < tickers; k++) {
    const c = new Float64Array(n), h = new Float64Array(n), l = new Float64Array(n), idx = new Int32Array(n);
    let p = 50 + 50 * rnd();
    for (let i = 0; i < n; i++) {
      const prev = p; p *= Math.exp((rnd() - 0.5) * 0.05 + 0.0003);
      c[i] = p; h[i] = Math.max(prev, p) * (1 + 0.01 * rnd()); l[i] = Math.min(prev, p) * (1 - 0.01 * rnd()); idx[i] = i;
    }
    T.push({ t: "T" + k, c, h, l, idx });
  }
  return { dates, tickers: T, spy: null };
}
const data = walk(1500, 7);
for (const ind of ["ma", "rsi", "donchian"]) {
  const base = run(data, defaults(ind)), off = run(data, { ...defaults(ind), tp: 0, sl: 0 });
  check(base.all.eq.every((v, g) => v === off.all.eq[g]), `${ind}: tp/sl at 0 changes nothing`);
  const r = run(data, { ...defaults(ind), sl: 4, tp: 8 });
  for (const key of ["all", "long"]) {
    const t = r[key].trades;
    check(t.tp + t.sl > 0 && t.tp + t.sl <= t.n, `${ind} ${key}: ${t.sl} stops + ${t.tp} targets of ${t.n} trades`);
    check(r[key].eq.every(v => Number.isFinite(v) && v > 0), `${ind} ${key}: account stays finite and positive`);
  }
}
// no trade loses more than the stop when nothing gaps (here every bar's range includes the previous close)
for (const ind of ["ma", "rsi", "donchian", "supertrend"]) {
  const d = run(data, defaults(ind)).all.trades, r = run(data, { ...defaults(ind), sl: 3 }).all.trades;
  check(r.sl > 0 && r.worst >= -3 - 1e-9 && d.worst < -3, `${ind}: worst trade ${d.worst.toFixed(1)}% without a stop, ${r.worst.toFixed(1)}% with a 3% stop`);
}
// a stop tighter than any day's move closes every position on its first day
{
  const r = run(data, { ...defaults("ma"), sl: 0.5 }), d = run(data, defaults("ma"));
  check(r.all.inv.share < d.all.inv.share, "tight stop: fewer days invested than without");
}

console.log(bad ? `FAIL (${bad})` : "PASS");
process.exit(bad ? 1 : 0);
