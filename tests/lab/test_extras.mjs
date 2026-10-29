// Checks the Strategy Lab's extra rules (docs/lab.js): trading costs, trailing stop, trend / market /
// second-indicator filters, the position limit, subsets, after-tax curve and the extra statistics.
// Run:  node tests/lab/test_extras.mjs        (uses docs/data/lab.json)
import fs from "fs";
import { decode, run, clean, defaults, subset, summary, afterTax, riskHit, states, sameCfg } from "../../docs/lab.js";

let bad = 0;
const check = (ok, msg) => { if (!ok) bad++; console.log(`${ok ? "ok " : "BAD"} ${msg}`); };
const near = (a, b, tol = 1e-9) => Math.abs(a - b) < tol;
const data = decode(JSON.parse(fs.readFileSync(new URL("../../docs/data/lab.json", import.meta.url))));
const end = r => r.long.eq[r.long.eq.length - 1];

// defaults: the new keys are off, and old saved settings (without them) clean to the same thing
check(sameCfg({ ind: "rsi" }, defaults("rsi")), "old saved settings clean to the defaults");
check(clean({ ind: "ma", fMa: 7, maxPos: 900, cost: 0.123, fInd: "nope" }).fMa === 20 && clean({ ind: "ma", maxPos: 900 }).maxPos === 50
  && clean({ ind: "ma", cost: 0.123 }).cost === 0.1 && clean({ ind: "ma", fInd: "nope" }).fInd === "", "clean keeps the extras in range");

// trailing stop on one bar
const tc = clean({ ind: "ma", trail: 10 });
check(riskHit(1, 100, 112, 105, tc, 115) === null, "trail: above the trailing level -> nothing");
const tr = riskHit(1, 100, 106, 102, tc, 115);
check(tr && tr.why === "trail" && near(tr.px, 103.5), "trail: bar through 90% of the peak fills at the level");
const both = riskHit(1, 100, 106, 90, clean({ ind: "ma", trail: 10, sl: 5 }), 115);
check(both && both.why === "trail", "trail above the stop loss: the trailing stop counts");

// costs
const base = run(data, { ind: "macd" }), costed = run(data, { ind: "macd", cost: 0.2 });
check(end(costed) < end(base), `costs lower the result (${Math.round(end(base))} -> ${Math.round(end(costed))})`);
check(costed.long.trades.n === base.long.trades.n && near(costed.long.trades.avg, base.long.trades.avg - 0.4, 1e-6), "0.2% each way = 0.4% off every trade");

// trailing stop in the account
const tsr = run(data, { ind: "ma", trail: 8 });
check(tsr.long.trades.trail > 0, `trailing stop closes trades (${tsr.long.trades.trail} of ${tsr.long.trades.n})`);

// trend filter: every buy is above its 100-day average
{
  const r = run(data, { ind: "rsi", fMa: 100 });
  const by = Object.fromEntries(data.tickers.map(T => [T.t, T]));
  let wrong = 0;
  for (const t of r.long.list) {
    const T = by[t.t], i = T.idx.indexOf(t.g0); let s = 0; for (let j = i - 99; j <= i; j++) s += T.c[j];
    if (!(T.c[i] > s / 100)) wrong++;
  }
  check(r.long.list.length > 0 && wrong === 0, `trend filter: ${r.long.list.length} buys, all above the 100-day average`);
  check(r.long.trades.n < run(data, { ind: "rsi" }).long.trades.n, "trend filter takes fewer trades");
}
// market filter and second indicator
check(run(data, { ind: "donchian", fMkt: 1 }).long.trades.n < run(data, { ind: "donchian" }).long.trades.n, "market filter takes fewer trades");
check(states(data.tickers[0], "macd").some(v => v === 1) && states(data.tickers[0], "supertrend").some(v => v === -1), "second-indicator states are set");
check(run(data, { ind: "bollinger", fInd: "supertrend" }).all.trades.n > 0, "second-indicator filter still trades");

// position limit
for (const k of [1, 5]) {
  const r = run(data, { ind: "rsi", maxPos: k });
  const busy = new Map();
  for (const t of r.long.list) for (let g = t.g0; g < t.g1; g++) busy.set(g, (busy.get(g) || 0) + 1);
  const most = Math.max(...busy.values());
  check(most <= k && r.long.inv.avg <= k + 1e-9, `max ${k} position${k > 1 ? "s" : ""}: at most ${most} open at once`);
}

// subsets: one stock, and ending early
{
  const one = subset(data, { tickers: new Set(["AAPL"]) });
  check(one.tickers.length === 1 && run(one, { ind: "ma" }).long.list.every(t => t.t === "AAPL"), "one-stock subset");
  const to = data.dates.indexOf(data.dates.find(d => d.startsWith("2020")));
  const early = subset(data, { to });
  check(early.dates.length === to + 1 && early.tickers.every(T => T.idx[T.idx.length - 1] <= to), "subset ends on the chosen day");
  const full = run(data, { ind: "ma" }), cut = run(early, { ind: "ma" });
  check(near(cut.all.eq[to], full.all.eq[to], 1e-6), "a shorter period gives the same curve up to its end");
}

// summary and tax
{
  const s = summary(base.long.eq, data.dates, 0);
  check(s.sharpe != null && s.sortino != null && s.under > 0, `summary: Sharpe ${s.sharpe.toFixed(2)}, Sortino ${s.sortino.toFixed(2)}, ${s.under} sessions under water at most`);
  const tx = afterTax(base.long.eq, data.dates, 0);
  check(tx.eq[tx.eq.length - 1] < end(base) && tx.tax > 0, `after tax ${Math.round(tx.eq[tx.eq.length - 1])} < ${Math.round(end(base))}`);
}

console.log(bad ? `FAIL (${bad})` : "PASS");
process.exit(bad ? 1 : 0);
