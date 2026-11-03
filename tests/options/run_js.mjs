// Test helper for test_parity.py: reads cases on stdin, runs docs/options-lib.js, prints JSON.
import * as L from "../../docs/options-lib.js";

const job = JSON.parse(await new Promise(ok => { let s = ""; process.stdin.on("data", d => s += d); process.stdin.on("end", () => ok(s)); }));
const out = {
  bs: job.bs.map(([S, K, T, iv, t]) => [L.bs(S, K, T, iv, t), L.greeks(S, K, T, iv, t)]),
  skew: job.skew.map(([a, S, K, T, sl]) => L.skewIv(a, S, K, T, sl)),
  pick: job.pick.map(([id, S, atm, days, slope]) => {
    const st = L.STRATEGIES[id];
    return L.pickStrikes(st.legs, S, l => (l.exp === "far" ? days + 28 : days) / 365, (K, T) => L.skewIv(atm, S, K, T, slope));
  }),
  expiry: job.expiry.map(d => [L.nextExpiry(d), L.farExpiry(L.nextExpiry(d))]),
  strategies: L.STRATEGIES,
};
process.stdout.write(JSON.stringify(out));
