// Checks the Strategy Lab's robustness tools (docs/lab.js: holdCurve, walkForward, bootstrap).
// Run:  node tests/lab/test_robust.mjs
import { holdCurve, walkForward, bootstrap, ACCOUNT } from "../../docs/lab.js";

let bad = 0;
const check = (ok, msg) => { if (!ok) bad++; console.log(`${ok ? "ok " : "BAD"} ${msg}`); };
const near = (a, b, e = 1e-6) => Math.abs(a - b) < e;

// trading days over 8 calendar years
const dates = [];
for (let y = 2015; y <= 2022; y++) for (let d = 0; d < 252; d++) dates.push(`${y}-${String(1 + Math.floor(d / 21)).padStart(2, "0")}-${String(1 + d % 21).padStart(2, "0")}`);
const D = dates.length;
const curve = f => { const e = new Float64Array(D); let v = ACCOUNT; for (let g = 0; g < D; g++) { if (g) v *= 1 + f(g); e[g] = v; } return e; };

// --- holdCurve: two stocks, one +1%/day, one flat -> +0.5%/day
const T = (r) => { const c = new Float64Array(D), idx = new Int32Array(D); let p = 10; for (let g = 0; g < D; g++) { if (g) p *= 1 + r; c[g] = p; idx[g] = g; } return { t: "x", c, idx }; };
const h = holdCurve({ dates, tickers: [T(0.01), T(0)] });
check(near(h[1] / h[0], 1.005), "holdCurve averages the stocks' daily returns");

// --- walkForward: curve A wins in odd years, B in even years -> with a 1-year window it always picks last year's winner
const yr = g => +dates[g].slice(0, 4);
const A = curve(g => yr(g) % 2 ? 0.002 : -0.001), B = curve(g => yr(g) % 2 ? -0.001 : 0.002);
const wf = walkForward([A, B], dates, 0, { train: 1, by: "cagr" });
check(wf.years.length === 7 && wf.years[0].year === 2016, "one test year per year after the training window");
check(wf.years.every(y => y.pick === (y.year % 2 ? 1 : 0)), "picks the best of the year before (no look-ahead)");
check(wf.years.every(y => y.ret < 0), "so it always loses when the leader flips");
const steady = curve(() => 0.001), wf2 = walkForward([steady, A], dates, 0, { train: 3 });
check(wf2.years[0].year === 2018 && near(wf2.eq[D - 1] / wf2.eq[wf2.from], steady[D - 1] / steady[wf2.from]), "a curve that is always best is chained exactly");

// --- bootstrap: a curve always 0.05%/day ahead wins every history; identical curves never do
const base = curve(g => Math.sin(g) * 0.01), ahead = curve(g => Math.sin(g) * 0.01 + 0.0005);
const b1 = bootstrap(ahead, base, 0, { sims: 200 }), b2 = bootstrap(base, base, 0, { sims: 200 });
check(b1.p === 100 && b1.lo > 0, "always ahead -> 100% of histories");
check(b2.p === 0 && near(b2.mid, 0), "same curve -> never ahead, no extra return");
check(b1.dd < 0, "reports a worst drop");
check(JSON.stringify(bootstrap(ahead, base, 0, { sims: 50 })) === JSON.stringify(bootstrap(ahead, base, 0, { sims: 50 })), "same seed -> same result");

console.log(bad ? `${bad} failed` : "all ok");
process.exit(bad ? 1 : 0);
