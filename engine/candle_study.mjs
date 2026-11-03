// "Do candle patterns work?" (Learn page): every candle pattern of the Strategy Lab tested on a
// market's own prices, from the weekly lab.json, with docs/lab-rules.js (the same code as the Lab's
// Patterns tab). For each pattern: how often it appeared and how the price moved 5 and 20 sessions
// later in its direction, against any day, over the whole history and since 2021. Then the two most
// popular buy candles in the context traders say they need, next to that context without the candle.
//
//   node engine/candle_study.mjs <lab.json> <out.json> <market>
import { readFileSync, writeFileSync } from "node:fs";
import { decode, Rules as R } from "../docs/lab.js";

const [labPath, outPath, market] = process.argv.slice(2);
const data = decode(JSON.parse(readFileSync(labPath, "utf8")));
const D = data.dates, r2 = x => x == null ? null : Math.round(x * 100) / 100;
const since = y => Math.max(0, D.findIndex(d => d >= y));
const ids = Object.values(R.PATTERNS).filter(p => p.group === "Candles").map(p => p.id);
const HZ = [5, 20];

function table(fromIdx) {
  const s = R.patternStats(data, ids, { fromIdx, hz: HZ, examples: 0 });
  return {
    from: D[fromIdx], base: s.base.map(b => ({ avg: r2(b.avg), win: r2(b.win) })),
    list: s.list.map(p => ({ id: p.id, name: R.PATTERNS[p.id].name, side: R.PATTERNS[p.id].side, n: p.n,
      at: p.at.map(a => ({ avg: r2(a.avg), win: r2(a.win), edge: r2(a.edge) })) })),
  };
}

// a pattern (or none) plus conditions: the 5-session move after each day that passes
function context(label, list) {
  const base = R.patternStats(data, [], { hz: [5] }).base[0].avg;
  let n = 0, s = 0, w = 0;
  for (const T of data.tickers) {
    const g = R.group(T, data, { mode: "all", list });
    for (let i = 0; i + 5 < T.c.length; i++) if (g[i]) { const r = T.c[i + 5] / T.c[i] - 1; n++; s += r; w += r > 0; }
  }
  return { label, n, win: n ? r2(w / n * 100) : null, avg: n ? r2(s / n * 100) : null, edge: n ? r2(s / n * 100 - base) : null };
}
const rsi30 = { a: { id: "rsi", p: { n: 14 } }, op: "lt", b: 30, w: 3 }, up = { a: { id: "close" }, op: "gt", b: { id: "sma", p: { n: 200 } } };
const ctx = [];
for (const [id, name] of [["engulf_bull", "Bullish engulfing"], ["hammer", "Hammer"]]) {
  ctx.push(context(`${name} on its own`, [{ pat: id }]));
  ctx.push(context(`${name} after RSI under 30`, [{ pat: id }, rsi30]));
  ctx.push(context(`${name} at a 20-day low`, [{ pat: id }, { a: { id: "low" }, op: "lo", n: 20, w: 3 }]));
  ctx.push(context(`${name} in an uptrend pullback`, [{ pat: id }, up, { a: { id: "rsi", p: { n: 14 } }, op: "lt", b: 40, w: 3 }]));
  if (data.tickers.some(T => T.v)) ctx.push(context(`${name} on high volume`, [{ pat: id }, { a: { id: "rvol", p: { n: 20 } }, op: "gt", b: 1.5 }]));
}
ctx.push(context("RSI under 30, no candle", [{ a: { id: "rsi", p: { n: 14 } }, op: "lt", b: 30 }]));

writeFileSync(outPath, JSON.stringify({
  generated_at: new Date().toISOString().slice(0, 16) + "Z", market, opens: R.hasOpens(data),
  tickers: data.tickers.length, start: D[0], end: D[D.length - 1], hz: HZ,
  all: table(0), recent: table(since("2021")), context: ctx,
}));
console.log(`-> ${outPath}`);
