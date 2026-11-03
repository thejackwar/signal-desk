// Payoff analysis checks on the Learn-tab examples.   node tests/options/test_analyze.mjs
import * as L from "../../docs/options-lib.js";
let bad = 0;
const ok = (c, m) => { if (!c) { bad++; console.log("FAIL", m); } };
const near = (a, b, tol = 0.5) => Math.abs(a - b) <= tol;
for (const id of L.ORDER) {
  const p = L.examplePosition(id), a = L.analyze(p);
  const prem = p.legs.reduce((s, l) => s - l.s * l.price * 100, 0);       // + credit, - debit
  const k = p.legs.map(l => l.K);
  console.log(id.padEnd(17), "strikes", k.join("/"), "prem", prem.toFixed(0), "max", a.maxGain?.toFixed(0) ?? "∞", a.maxLoss?.toFixed(0) ?? "∞",
    "BE", a.breakevens.map(x => x.toFixed(2)).join(","), "POP", (a.pop * 100).toFixed(0) + "%");
  if (id === "iron_condor") {
    ok(near(a.maxGain, prem), "condor max gain = premium");
    ok(near(-a.maxLoss, Math.min(k[1] - k[0], k[3] - k[2]) * 100 - prem), "condor max loss = wing - premium");
    ok(a.breakevens.length === 2, "condor two breakevens");
  }
  if (id === "long_straddle") { ok(a.maxGain === null, "straddle unlimited gain"); ok(near(a.maxLoss, prem), "straddle max loss = premium"); }
  if (id === "covered_call") { ok(near(a.maxGain, (k[0] - 100) * 100 + prem), "covered call max gain"); ok(a.breakevens.length === 1, "cc one breakeven"); }
  if (id === "bull_call_spread") ok(near(a.maxGain, (k[1] - k[0]) * 100 + prem), "bull spread max gain");
  if (id === "cash_secured_put") ok(near(a.breakevens[0], k[0] - prem / 100, 0.02), "csp breakeven = strike - premium");
  ok(a.pop > 0 && a.pop < 1, id + " pop in (0,1)");
}
console.log(bad ? `${bad} failures` : "OK");
process.exit(bad ? 1 : 0);
