// The Strategy Lab's VIX filter (fVix, attachVix in docs/lab.js; data from engine/vix_season.py).
// Off: settings and results unchanged. On: no buy on a blocked day, no short on an allowed one.
// Uses a made-up gate, so it runs before docs/data/vix.json exists.   node tests/lab/test_vix.mjs
import fs from "fs";
import { decode, run, clean, subset, extrasOn, attachVix } from "../../docs/lab.js";

const raw = JSON.parse(fs.readFileSync(new URL("../../docs/data/lab.json", import.meta.url)));
const ok = (c, m) => { if (!c) { console.log("FAIL " + m); process.exit(1); } console.log("ok  " + m); };

// made-up gate: Aug-Oct blocked for the calendar state; every 4th month blocked for the VIX state
const changes = [];
let prev = "";
for (const d of raw.dates) {
  const m = +d.slice(5, 7), y = +d.slice(0, 4), st = `${m >= 8 && m <= 10 ? 0 : 1}${(y * 12 + m) % 4 ? 1 : 0}`;
  if (st !== prev) changes.push([d, +st[0], +st[1]]), prev = st;
}
const gate = { th: 25, changes };
const base = decode(raw), withV = attachVix(decode(raw), gate);

const cfg = clean({ ind: "macd" });
ok(!("fVix" in cfg), "off: not in saved or shared settings");
ok(JSON.stringify(clean({ ind: "macd", fVix: 0 })) === JSON.stringify(cfg), "fVix 0 is the same as no setting");
const r0 = run(base, cfg), r0v = run(withV, cfg);
ok(r0.long.eq.at(-1) === r0v.long.eq.at(-1) && r0.all.trades.n === r0v.all.trades.n, "off: same result with the VIX data attached");
ok(run(base, clean({ ind: "macd", fVix: 2 })).all.trades.n === r0.all.trades.n, "on but no VIX data: does nothing");

for (const f of [1, 2]) {
  const c = clean({ ind: "macd", fVix: f });
  ok(c.fVix === f, `fVix ${f} kept`);
  const r = run(withV, c), g = withV.vixOk[f];
  ok(r.long.trades.n < r0.long.trades.n, `fVix ${f}: fewer buys (${r.long.trades.n} vs ${r0.long.trades.n})`);
  ok(r.long.list.every(t => g[t.g0] === 1), `fVix ${f}: no buy on a blocked day`);
  ok(r.all.list.filter(t => t.side === -1).every(t => g[t.g0] === 0), `fVix ${f}: no short on an allowed day`);
  const p = run(withV, { ...c, entry: "open" });   // the trade-by-trade engine
  ok(p.long.list.every(t => g[t.g0 - 1] === 1 || g[t.g0] === 1), `fVix ${f}: next-open entries filtered too`);
}
const sub = subset(withV, { to: 500 });
ok(sub.vixOk?.[1].length === 501 && sub.vixTh === 25, "subset keeps the VIX states");
ok(extrasOn(clean({ ind: "macd", fVix: 2 })) === 1, "counts as an extra rule");
const wk = attachVix({ dates: ["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"] },
  { th: 25, changes: [["2026-10-01", 1, 1], ["2026-10-30", 0, 1], ["2026-11-02", 1, 0]] });
ok(wk.vixOk[1].join() === "0,0,0,1" && wk.vixOk[2].join() === "1,1,1,0", "weekend dates (crypto) keep Friday's state");
const early = attachVix({ dates: ["1980-01-02", "2026-11-02"] }, gate);
ok(early.vixOk[1][0] === 1 && early.vixOk[2][0] === 1, "dates before the VIX data: allowed");
console.log("PASS");
