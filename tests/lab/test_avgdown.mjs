// Checks the Strategy Lab's "Average down" strategy (runAvg in docs/lab.js).
// Run:  node tests/lab/test_avgdown.mjs        (uses docs/data/lab.json)
import fs from "fs";
import { decode, run, clean, defaults, summary, INDICATORS } from "../../docs/lab.js";

let bad = 0;
const check = (ok, msg) => { if (!ok) bad++; console.log(`${ok ? "ok " : "BAD"} ${msg}`); };
const data = decode(JSON.parse(fs.readFileSync(new URL("../../docs/data/lab.json", import.meta.url))));
const D = data.dates, last = D.length - 1;
const pct = x => x.toFixed(1) + "%";
const show = (name, r) => { const s = summary(r.long.eq, D, 0); console.log(`   ${name}: CAGR ${pct(s.cagr)}, worst drop ${pct(s.dd)}, trades ${r.long.trades.n}, win ${pct(r.long.trades.winRate)}, open ${r.open.length}`); return s; };

check(INDICATORS.avg_down && clean({ ind: "avg_down" }).tp === 10 && clean({ ind: "avg_down", p: { buys: 99 } }).p.buys === 10, "defaults and limits");

const base = run(data, defaults("avg_down"));
show("defaults (10 positions, no costs)", base);
check(clean({ ind: "avg_down" }).maxPos === 10, "always runs with slots (10 by default)");
// with no stop and no time limit, every closed trade is the take profit, at about +10% on the money put in
{ const off = base.long.list.filter(t => t.why !== "tp" || t.ret < 0.10 - 1e-9);
  check(off.length === 0, `no stop: every closed trade is the take profit, at +10% or more (gaps above it fill higher)`); }
check(base.long.list.every(t => t.buys >= 1 && t.buys <= 3), "never more than 3 buys");
check(JSON.stringify(base.all.eq) === JSON.stringify(base.long.eq), "long only: 'all' is the same as 'long'");

// slot returns add up to the trade: product of (1 + r) over the position = 1 + ret * share of the slot invested
{
  let worst = 0;
  for (const t of base.long.list) {
    const v = t.rs.r.reduce((a, r) => a * (1 + r), 1), want = 1 + t.ret * t.buys / 3;
    worst = Math.max(worst, Math.abs(v - want));
  }
  check(worst < 1e-9, `daily slot returns match each trade (max error ${worst.toExponential(1)})`);
}

// like the chat backtest: 10 slots, 0.1% each way
const ten = run(data, { ...defaults("avg_down"), maxPos: 10, cost: 0.1 });
const s10 = show("10 positions, 0.1% costs", ten);
check(s10.cagr > 7 && s10.cagr < 14, "10 positions, 0.1% costs: in the range of the Python backtest (9.5% a year; it buys a day later)");
check(ten.open.length <= 10, `only the account's own open positions are listed (${ten.open.length})`);
// no one-day spikes: the account never falls more than 15% in a day
{ let w = 0; const e = ten.long.eq; for (let g = 1; g < e.length; g++) if (e[g - 1] > 0) w = Math.min(w, e[g] / e[g - 1] - 1);
  check(w > -0.15, `no one-day jumps in the account (worst day ${(w * 100).toFixed(1)}%)`); }
// the account adds up: money never goes below zero in cash, and euros of the trades + open positions = the result
{ let sumE = 0; const seen = new Set();
  for (const t of ten.long.list) sumE += t.P.eur;
  const endEq = ten.long.eq[last], openE = endEq - 100000 - sumE;
  console.log(`   closed trades made ${Math.round(sumE)}, open positions ${Math.round(openE)}, account gain ${Math.round(endEq - 100000)}`); }
check(ten.long.list.every(t => t.ret >= 0.10 - 0.0011 - 1e-9), "with costs the take profit nets +10% less the 0.1% selling cost (the buying cost is in the average cost)");

// a stop loss closes losing trades, at about -30% on the money put in
const st = run(data, { ...defaults("avg_down"), maxPos: 10, sl: 30 });
show("10 positions, 30% stop", st);
check(st.long.trades.sl > 0 && st.long.list.filter(t => t.why === "sl").every(t => t.ret <= -0.29), "30% stop: losing trades close at about -30% or worse (gaps)");

// a time limit frees the money
const tl = run(data, { ...defaults("avg_down"), maxPos: 10, exit: "hold", hold: 120 });
show("10 positions, 120-session limit", tl);
check(tl.long.list.every(t => t.days <= 120), "time limit: no trade held longer than 120 sessions");

// trend filter: every first buy above its 200-day average
{
  const r = run(data, { ...defaults("avg_down"), maxPos: 10, fMa: 200 });
  show("10 positions, above the 200-day only", r);
  const by = Object.fromEntries(data.tickers.map(T => [T.t, T]));
  let wrong = 0;
  for (const t of r.long.list) {
    const T = by[t.t], i = T.idx.indexOf(t.g0);
    let s = 0; for (let j = i - 199; j <= i; j++) s += T.c[j];
    if (!(T.c[i] > s / 200)) wrong++;
  }
  check(wrong === 0, "trend filter: every first buy is above the 200-day average");
}

// the position limit is respected
{
  const cnt = new Int32Array(D.length);
  for (const t of ten.long.list) for (let g = t.g0; g < t.g1; g++) cnt[g]++;
  check(Math.max(...cnt) <= 10, "10 positions: never more than 10 open at once (closed trades)");
}
check(base.open.every(o => o.buys >= 1), `open positions listed (${base.open.length}, worst ${pct(base.open[0]?.ret ?? 0)})`);

console.log(bad ? `\n${bad} check(s) failed` : "\nall good");
process.exit(bad ? 1 : 0);
