// The trades behind a Strategy Board tile add up to the tile (docs/board-trades.js).
//   node tests/board/test_trades.mjs [market]
import { readFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as Lab from "../../docs/lab.js";
import { tileTrades } from "../../docs/board-trades.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const market = process.argv[2] || "stocks";
const dir = join(ROOT, "docs", "data", market === "stocks" ? "" : market);
if (!existsSync(join(dir, "lab.json"))) { console.log("no lab.json, skipped"); process.exit(0); }
const data = Lab.decode(JSON.parse(readFileSync(join(dir, "lab.json"), "utf8")));
const B = JSON.parse(readFileSync(join(dir, "board.json"), "utf8"));
const fi = Math.max(0, data.dates.findIndex(d => d >= "2021-01-01"));
let bad = 0, checked = 0, t0 = Date.now();
for (const row of B.strategies) {
  const ind = row.lab; if (!ind || !Lab.INDICATORS[ind] || !Lab.available(data, ind, market)) continue;
  const cfg = Lab.defaults(ind), R = Lab.traceRun(data, cfg, fi), R0 = Lab.run(data, cfg, fi);
  const eq = R.long.eq, last = data.dates.length - 1;
  // the trace changes nothing in the run itself
  for (let g = fi; g <= last; g++) if (eq[g] !== R0.long.eq[g]) { bad++; console.log(`  ! ${ind}: traceRun's curve differs on ${data.dates[g]}`); break; }
  // every session
  const gs = []; for (let g = fi + 1; g <= last; g++) gs.push(g);
  let worst = 0;
  for (const g of gs) {
    const X = tileTrades(Lab, data, cfg, fi, g, g, R);
    const acct = eq[g] / eq[g - 1] - 1;
    const err = Math.abs(X.sum - acct);
    if (Math.abs(X.acct - acct) > 1e-12) { bad++; console.log(`  ! ${ind} ${data.dates[g]}: acct ${X.acct} vs ${acct}`); }
    if (err > 1e-9) { bad++; if (bad < 20) console.log(`  ! ${ind} ${data.dates[g]}: trades ${(X.sum*100).toFixed(4)}% vs tile ${(acct*100).toFixed(4)}%`); }
    // and the tile on the board, where both cover the same day (0.1 bp rounding)
    const bi = B.dates.indexOf(data.dates[g]);
    if (bi >= 0 && Math.abs(row.days[bi] - Math.round(acct * 1e5)) > 1) { bad++; console.log(`  ! ${ind} ${data.dates[g]}: board ${row.days[bi]} vs ${Math.round(acct*1e5)}`); }
    worst = Math.max(worst, err); checked++;
  }
  // a week tile: the account's compounded change
  const X = tileTrades(Lab, data, cfg, fi, last - 4, last, R);
  const wk = eq[last] / eq[last - 5] - 1;
  if (Math.abs(X.acct - wk) > 1e-9) { bad++; console.log(`  ! ${ind} week: ${X.acct} vs ${wk}`); }
  console.log(`${ind.padEnd(24)} worst gap ${(worst * 100).toExponential(1)}%  (last day: ${X.trades.length} trades in the week)`);
}
// the other account paths: a position limit, and runPro (an ATR stop) with risk sizing
for (const [ind, extra] of [["macd", { maxPos: 5 }], ["avg_down", { maxPos: 4 }], ["rsi", { slMode: "atr", slAtr: 2, size: "risk", risk: 1 }], ["triangle", { maxPos: 3, sl: 8 }]]) {
  if (!Lab.INDICATORS[ind] || !Lab.available(data, ind, market)) continue;
  const cfg = Lab.clean({ ...Lab.defaults(ind), ...extra }), R = Lab.traceRun(data, cfg, fi), eq = R.long.eq;
  let worst = 0;
  for (let g = fi + 1; g < data.dates.length; g++) {
    const X = tileTrades(Lab, data, cfg, fi, g, g, R), a = eq[g] / eq[g - 1] - 1;
    worst = Math.max(worst, Math.abs(X.sum - a)); checked++;
  }
  if (worst > 1e-9) bad++;
  console.log(`${(ind + " " + JSON.stringify(extra)).padEnd(60)} worst gap ${(worst * 100).toExponential(1)}%`);
}
console.log(`${checked} tiles checked in ${((Date.now() - t0) / 1e3).toFixed(0)}s, ${bad} problems`);
process.exit(bad ? 1 : 0);
