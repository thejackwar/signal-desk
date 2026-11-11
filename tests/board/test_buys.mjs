// The trades bought on a Strategy Board tile (tiles by trade) are the ones the tile counts:
// docs/board-trades.js allBuys, run as the page runs it, against board.json's "buys" (engine/day_board.mjs).
//   node tests/board/test_buys.mjs [market]
import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as Lab from "../../docs/lab.js";
import { allBuys, buysIn } from "../../docs/board-trades.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const market = process.argv[2] || "stocks";
const dir = join(ROOT, "docs", "data", market === "stocks" ? "" : market);
if (!existsSync(join(dir, "lab.json"))) { console.log("no lab.json, skipped"); process.exit(0); }
const load = f => existsSync(join(dir, f)) ? Lab.decode(JSON.parse(readFileSync(join(dir, f), "utf8"))) : null;
const hist = load("lab.json"), recent = load("lab_recent.json");
const B = JSON.parse(readFileSync(join(dir, "board.json"), "utf8"));
const fi = Math.max(0, hist.dates.findIndex(d => d >= "2021-01-01"));
let bad = 0;
for (const row of B.strategies) {
  const ind = row.lab; if (!ind || !row.buys || !Lab.available(hist, ind, market)) continue;
  const cfg = Lab.defaults(ind), Rh = Lab.traceRun(hist, cfg, fi), Rr = recent && Lab.available(recent, ind, market) ? Lab.traceRun(recent, cfg, 0) : null;
  const all = allBuys(hist, Rh, fi, Rr && recent, Rr), at = new Map(B.dates.map((d, i) => [d, i]));
  const n = new Map();
  for (const t of all) { const i = at.get(t.d0); if (i == null) continue; const x = n.get(i) || { n: 0, o: 0, w: 0, s: 0 }; x.n++; x.o += t.open; x.w += t.ret > 0; x.s += t.ret; n.set(i, x); }
  const Bu = row.buys;
  if (Bu.i.length !== n.size) { bad++; console.log(`  ! ${ind}: ${Bu.i.length} buy days on the board vs ${n.size}`); }
  Bu.i.forEach((i, k) => {
    const x = n.get(i);
    if (!x || x.n !== Bu.n[k] || x.o !== Bu.o[k] || x.w !== Bu.w[k] || Math.abs(Math.round(x.s / x.n * 1e5) - Bu.v[k]) > 1) { bad++; if (bad < 20) console.log(`  ! ${ind} ${B.dates[i]}: board ${Bu.n[k]}/${Bu.o[k]}/${Bu.w[k]}/${Bu.v[k]} vs`, x); }
  });
  // every trade is one position of the run: its result is its daily returns compounded, and an open one ends on the last close
  for (const t of all) {
    if (!(t.p0 > 0) || !(t.p1 > 0)) { bad++; console.log(`  ! ${ind} ${t.t} ${t.d0}: price missing`); }
    if (t.d1 && t.d1 < t.d0) { bad++; console.log(`  ! ${ind} ${t.t}: sold before it was bought`); }
  }
  // nothing in a week is missed: buysIn on the history gives the same tickers as allBuys for a history week
  const g = hist.dates.length - 10, wk = buysIn(hist, Rh, g, g + 4).map(t => t.t + t.d0).sort().join();
  const wk2 = all.filter(t => t.d0 >= hist.dates[g] && t.d0 <= hist.dates[g + 4]).map(t => t.t + t.d0).sort().join();
  if (wk !== wk2) { bad++; console.log(`  ! ${ind}: week mismatch`); }
  console.log(`${ind.padEnd(24)} ${all.length} trades, ${all.filter(t => t.open).length} open`);
}
console.log(`${bad} problems`);
process.exit(bad ? 1 : 0);
