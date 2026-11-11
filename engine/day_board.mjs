// Strategy board: every Strategy Lab strategy (default settings, long only like the Lab's default),
// one number per trading day = the whole account's change that day, open trades included,
// and the trades bought each day with how each whole trade did (closed: its result, open: so far),
// which is what the board's tiles show by default (docs/board-trades.js allBuys).
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
import { allBuys } from "../docs/board-trades.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const market = process.argv[2] || "stocks";
// UNIVERSE=sp500: the S&P 500 stocks (docs/data/sp500/), see engine/universe.py
const big = market === "stocks" && process.env.UNIVERSE === "sp500";
const dir = join(ROOT, "docs", "data", market === "stocks" ? (big ? "sp500" : "") : market);
const FROM = "2021-01-01", LIVE = big ? "2026-11-09" : "2026-11-02";
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
// the trades bought each board day, only the days with a buy: the day's index in dates (i), how many (n),
// how many still open (o), how many in profit (w), and their average result in 0.1 bp (v: closed trades'
// result, open trades' result so far)
const BI = new Map(dates.slice(1).map((d, i) => [d, i]));
function byDay(trades) {
  const N = dates.length - 1, bn = new Array(N).fill(0), bo = new Array(N).fill(0), bw = new Array(N).fill(0), sum = new Array(N).fill(0);
  for (const x of trades) {
    const i = BI.get(x.d0); if (i == null || !Number.isFinite(x.ret)) continue;
    bn[i]++; sum[i] += x.ret; if (x.open) bo[i]++; if (x.ret > 0) bw[i]++;
  }
  const bi = []; for (let i = 0; i < N; i++) if (bn[i]) bi.push(i);
  const pick = a => bi.map(i => a[i]);
  return { buys: { i: bi, n: pick(bn), o: pick(bo), w: pick(bw), v: bi.map(i => Math.round(sum[i] / bn[i] * 1e5) || (sum[i] > 0 ? 1 : -1)) } };
}
const comp = a => a.reduce((v, x) => v * (1 + x / 1e5), 1);

const rows = [];
const t0 = Date.now();
for (const ind of Object.keys(Lab.INDICATORS)) {
  if (!Lab.available(hist, ind, market)) continue;
  const I = Lab.INDICATORS[ind], cfg = Lab.defaults(ind);
  let days, buys;
  try {
    const Rh = Lab.traceRun(hist, cfg, fi);
    days = changes(Rh.long.eq, fi);
    const fresh = recent && Lab.available(recent, ind, market) ? Lab.traceRun(recent, cfg, 0) : null;
    buys = byDay(allBuys(hist, Rh, fi, fresh && recent, fresh));
    if (extra.length && fresh) {
      const R = fresh.long.eq, rd = recent.dates;
      for (const d of extra) { const g = rd.indexOf(d); days.push(g > 0 && R[g - 1] > 0 ? Math.round((R[g] / R[g - 1] - 1) * 1e5) : 0); }
    } else days.push(...extra.map(() => 0));
  } catch (e) { console.log(`  ! ${ind}: ${e.message}`); continue; }
  const up = days.filter(x => x > 0).length, dn = days.filter(x => x < 0).length;
  rows.push({ id: I.base, lab: ind, name: I.name, group: I.group || "", core: core.has(I.base),
    total: +((comp(days) - 1) * 100).toFixed(1), up, down: dn, days, ...buys });
}
// buy and hold: the same stocks (coins, commodities), equal weight
const hold = changes(Lab.holdCurve(hist, fi), fi).concat(extra.map(() => 0));
rows.sort((a, b) => b.total - a.total);
writeFileSync(join(dir, "board.json"), JSON.stringify({
  generated_at: new Date().toISOString().slice(0, 16) + "Z", market, from: dates[1], to: dates[dates.length - 1], live_from: LIVE,
  unit: "0.1bp", dates: dates.slice(1), hold: { total: +((comp(hold) - 1) * 100).toFixed(1), days: hold }, strategies: rows,
}));
console.log(`-> ${market}/board.json: ${rows.length} strategies, ${dates.length - 1} days to ${dates[dates.length - 1]} (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
