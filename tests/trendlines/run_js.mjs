// Test helper for test_parity.py: reads a JSON job on stdin, runs the JavaScript version, prints JSON.
//   {mode: "chains", cases: [{candles, lookback, swing, breakBy}]}  -> analyze() per case
//   {mode: "worker", symbol, alpaca: {"1Week": [bars], ...}, page}  -> Worker + buildFrames + analyzeAll
import { analyze, buildFrames, analyzeAll, findPivots } from "../../docs/trendlines.js";
import worker from "../../worker/trendlines-worker.js";

const job = JSON.parse(await new Promise(ok => { let s = ""; process.stdin.on("data", d => s += d); process.stdin.on("end", () => ok(s)); }));

if (job.mode === "chains") {
  const out = job.cases.map(k => {
    const r = analyze(k.candles, k.lookback, k.swing, k.breakBy);
    r.pivLow = findPivots(k.candles, k.swing, "low");
    r.pivHigh = findPivots(k.candles, k.swing, "high");
    return r;
  });
  process.stdout.write(JSON.stringify(out));
} else {
  // fake Alpaca: serves the given bars per timeframe, `page` bars per page, via next_page_token
  const calls = [];
  globalThis.fetch = async url => {
    const u = new URL(url);
    calls.push(u.searchParams.get("timeframe"));
    const all = job.alpaca[u.searchParams.get("timeframe")] || [];
    const from = +(u.searchParams.get("page_token") || 0), to = from + job.page;
    const body = { bars: { [u.searchParams.get("symbols")]: all.slice(from, to) }, next_page_token: to < all.length ? String(to) : null };
    return new Response(JSON.stringify(body), { status: 200 });
  };
  const res = await worker.fetch(new Request(`https://w.test/?symbol=${job.symbol}`), { ALPACA_KEY_ID: "k", ALPACA_SECRET_KEY: "s" }, null);
  const payload = await res.json();
  if (res.status !== 200) { process.stdout.write(JSON.stringify({ error: payload.error })); process.exit(0); }
  const frames = buildFrames(payload);
  process.stdout.write(JSON.stringify({ frames, results: analyzeAll(frames), calls: calls.length, symbol: payload.symbol }));
}
