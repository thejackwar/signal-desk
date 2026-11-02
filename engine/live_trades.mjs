// Live (paper) trades ledger, called by engine/live_trades.py with fresh prices in the lab.json format.
// Runs every Strategy Lab strategy with its default settings through docs/lab.js (the same code as
// the Lab and its calendar) from the go-live day on, then merges the trades into the ledger:
//   closed trades are append-only (never changed or removed once written),
//   open trades are replaced on every run (with the day each was first seen).
//
//   node engine/live_trades.mjs <lab.json> <market> <ledger.json> <start YYYY-MM-DD> [demo|real]
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import * as Lab from "../docs/lab.js";

const [labPath, market, ledgerPath, start, mode = "real"] = process.argv.slice(2);
const data = Lab.decode(JSON.parse(readFileSync(labPath, "utf8")));
const D = data.dates.length, day = g => data.dates[g];
const r2 = x => Math.round(x * 100) / 100;              // % with 2 decimals
const px = x => +x.toPrecision(6);

const old = existsSync(ledgerPath) ? JSON.parse(readFileSync(ledgerPath, "utf8")) : null;
// a different go-live day (or demo vs real) starts a fresh record
const keep = old && old.start === start && !!old.demo === (mode === "demo") && old.market === market;
const closed = keep ? old.closed : [], seen = new Map((keep ? old.open : []).map(o => [o.id, o.seen]));
const ids = new Set(closed.map(t => t.id));
const strategies = keep ? { ...old.strategies } : {};
const open = [];

const fromIdx = data.dates.findIndex(d => d >= start);
let added = 0;
if (fromIdx >= 0) {
  for (const ind of Object.keys(Lab.INDICATORS)) {
    if (!Lab.available(data, ind, market)) continue;
    const I = Lab.INDICATORS[ind], sid = I.base;
    strategies[sid] = { name: I.name, lab: ind, group: I.group, long_only: !!I.buysOnly };
    const res = Lab.run(data, Lab.defaults(ind), fromIdx);
    for (const t of res.all.list) {
      const id = `${sid}|${t.t}|${t.side}|${day(t.g0)}`;
      if (ids.has(id)) continue;                      // already in the record: frozen
      ids.add(id); added++;
      closed.push({ id, s: sid, t: t.t, side: t.side, d0: day(t.g0), d1: day(t.g1), p0: px(t.p0), p1: px(t.p1),
        ret: r2(t.ret * 100), days: t.days, why: t.why, ...(t.buys ? { buys: t.buys } : {}) });
    }
    for (const o of res.openTrades) {
      const id = `${sid}|${o.t}|${o.side}|${day(o.g0)}`;
      if (ids.has(id)) continue;
      open.push({ id, s: sid, t: o.t, side: o.side, d0: day(o.g0), p0: px(o.p0), px: px(o.px), ret: r2(o.ret * 100),
        days: o.days, seen: seen.get(id) || day(D - 1), ...(o.buys ? { buys: o.buys } : {}) });
    }
  }
}
closed.sort((a, b) => a.d1.localeCompare(b.d1) || a.d0.localeCompare(b.d0) || a.id.localeCompare(b.id));
open.sort((a, b) => b.d0.localeCompare(a.d0) || a.id.localeCompare(b.id));
writeFileSync(ledgerPath, JSON.stringify({
  generated_at: new Date().toISOString().slice(0, 16) + "Z",
  market, demo: mode === "demo", start, as_of: day(D - 1),
  size: 1000, tax: market === "crypto" ? 0.33 : 0.26,
  strategies, closed, open,
}));
console.log(`-> ${ledgerPath}: ${closed.length} closed (${added} new), ${open.length} open, prices to ${day(D - 1)}`);
