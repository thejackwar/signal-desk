// Strategy board: every Strategy Lab strategy (default settings, long only like the Lab's default),
// one number per trading day = the whole account's change that day, open trades included.
// History comes from lab.json (weekly), the days after it from lab_recent.json (fresh prices after each
// daily scan), both run through docs/lab.js, the same code as the Lab and its calendar.
// Days from the live start (2 Nov 2026, the paper-trading go-live) are marked live on the page.
//
//   node engine/day_board.mjs <market>              # stocks | crypto | commodities
// -> docs/data/board.json, docs/data/crypto/board.json, docs/data/commodities/board.json
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as Lab from "../docs/lab.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const market = process.argv[2] || "stocks";
const dir = join(ROOT, "docs", "data", market === "stocks" ? "" : market);
const FROM = "2021-01-01", LIVE = "2026-11-02";
const load = f => existsSync(join(dir, f)) ? Lab.decode(JSON.parse(readFileSync(join(dir, f), "utf8"))) : null;
const hist = load("lab.json"), recent = load("lab_recent.json");
if (!hist) { console.log(`no lab.json for ${market}`); process.exit(0); }
const lineup = (() => { try { return JSON.parse(readFileSync(join(ROOT, "docs", "data", "lineup.json"), "utf8")); } catch { return {}; } })();
const core = new Set(lineup.keep?.[market] || []);

// the days: lab.json's from FROM on, then the newer days only lab_recent.json has
const fi = Math.max(0, hist.dates.findIndex(d => d >= FROM));
const last = hist.dates[hist.dates.length - 1];
const extra = recent ? recent.dates.filter(d => d > last) : [];
const dates = hist.dates.slice(fi).concat(extra);

// daily change of an equity curve, in units of 0.1 basis point (0 = flat: no position)
const changes = (eq, from) => {
  const out = [];
  for (let g = from + 1; g < eq.length; g++) out.push(eq[g - 1] > 0 && Number.isFinite(eq[g]) ? Math.round((eq[g] / eq[g - 1] - 1) * 1e5) : 0);
  return out;
};
const comp = a => a.reduce((v, x) => v * (1 + x / 1e5), 1);

const rows = [];
const t0 = Date.now();
for (const ind of Object.keys(Lab.INDICATORS)) {
  if (!Lab.available(hist, ind, market)) continue;
  const I = Lab.INDICATORS[ind], cfg = Lab.defaults(ind);
  let days;
  try {
    days = changes(Lab.run(hist, cfg, fi).long.eq, fi);
    if (extra.length && Lab.available(recent, ind, market)) {
      const R = Lab.run(recent, cfg, 0).long.eq, rd = recent.dates;
      for (const d of extra) { const g = rd.indexOf(d); days.push(g > 0 && R[g - 1] > 0 ? Math.round((R[g] / R[g - 1] - 1) * 1e5) : 0); }
    } else days.push(...extra.map(() => 0));
  } catch (e) { console.log(`  ! ${ind}: ${e.message}`); continue; }
  const up = days.filter(x => x > 0).length, dn = days.filter(x => x < 0).length;
  rows.push({ id: I.base, lab: ind, name: I.name, group: I.group || "", core: core.has(I.base),
    total: +((comp(days) - 1) * 100).toFixed(1), up, down: dn, days });
}
// buy and hold: the same stocks (coins, commodities), equal weight
const hold = changes(Lab.holdCurve(hist, fi), fi).concat(extra.map(() => 0));
rows.sort((a, b) => b.total - a.total);
writeFileSync(join(dir, "board.json"), JSON.stringify({
  generated_at: new Date().toISOString().slice(0, 16) + "Z", market, from: dates[1], to: dates[dates.length - 1], live_from: LIVE,
  unit: "0.1bp", dates: dates.slice(1), hold: { total: +((comp(hold) - 1) * 100).toFixed(1), days: hold }, strategies: rows,
}));
console.log(`-> ${market}/board.json: ${rows.length} strategies, ${dates.length - 1} days to ${dates[dates.length - 1]} (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
