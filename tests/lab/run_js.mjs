// Test helper for test_parity.py: reads {lab, configs, weeks, from} on stdin, runs docs/lab.js, prints JSON.
import { decode, run, defaults } from "../../docs/lab.js";

const job = JSON.parse(await new Promise(ok => { let s = ""; process.stdin.on("data", d => s += d); process.stdin.on("end", () => ok(s)); }));
const data = decode(job.lab);
const out = {};
for (const [sid, ind] of Object.entries(job.configs)) {
  const r = run(data, defaults(ind), job.from || 0);
  out[sid] = {
    all: job.weeks.map(g => r.all.eq[g]), long: job.weeks.map(g => r.long.eq[g]),
    trades: r.all.trades,
  };
}
process.stdout.write(JSON.stringify(out));
