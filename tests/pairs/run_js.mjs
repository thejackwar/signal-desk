// Test helper for test_parity.py: reads cases on stdin, runs docs/pairs.js, prints JSON.
import { zscore, simulate } from "../../docs/pairs.js";

const job = JSON.parse(await new Promise(ok => { let s = ""; process.stdin.on("data", d => s += d); process.stdin.on("end", () => ok(s)); }));
const out = job.map(c => {
  const z = zscore(c.spread, c.rules.z_window);
  const { pnl, trades } = simulate(z, c.ra, c.rb, c.beta, c.rules, c.dates, true);
  return { z: z.map(v => isNaN(v) ? null : v), pnl, trades };
});
process.stdout.write(JSON.stringify(out));
