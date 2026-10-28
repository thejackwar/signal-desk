// Crypto path of the Trendlines Worker + page, with fake Binance / Kraken answers (no network).
// Run:  node tests/trendlines/test_crypto.mjs
import worker from "../../worker/trendlines-worker.js";
import { buildFrames, analyzeAll, ORDER } from "../../docs/trendlines.js";

let fails = 0;
const ok = (c, m) => { if (!c) { fails++; console.log("FAIL", m); } };
const STEP = { "1w": 604800, "1d": 86400, "4h": 14400, "1h": 3600, "30m": 1800, "15m": 900, "5m": 300 };
const NOW = Date.UTC(2026, 9, 28, 14, 7) / 1000;
const FIRST = Date.UTC(2017, 7, 14) / 1000;                 // a coin's first Binance candle (a Monday)
let calls = [], binanceDown = false, binanceCoins = null;

function candle(t, step) {
  const p = 30000 + 8000 * Math.sin(t / 3e6) + 500 * Math.sin(t / (step * 7));
  return [p, p * 1.004, p * 0.996, p * 1.001];
}
globalThis.fetch = async url => {
  const u = new URL(url);
  calls.push(u.host);
  if (u.host.includes("binance")) {
    if (binanceDown) return new Response("restricted location", { status: 451 });
    const sym = u.searchParams.get("symbol");
    if (binanceCoins && !binanceCoins.includes(sym)) return new Response('{"code":-1121,"msg":"Invalid symbol."}', { status: 400 });
    const step = STEP[u.searchParams.get("interval")], lim = +u.searchParams.get("limit");
    let end = u.searchParams.get("endTime") ? Math.floor(+u.searchParams.get("endTime") / 1000) : NOW;
    let t = Math.floor(end / step) * step;
    if (step === 604800) t = Math.floor((end - 345600) / step) * step + 345600;   // weeks start Monday
    const rows = [];
    for (; rows.length < lim && t >= FIRST; t -= step) { const [o, h, l, c] = candle(t, step); rows.unshift([t * 1000, String(o), String(h), String(l), String(c), "1", 0]); }
    return new Response(JSON.stringify(rows));
  }
  // Kraken
  const step = +u.searchParams.get("interval") * 60, rows = [];
  for (let k = 719, t0 = Math.floor(NOW / step) * step; k >= 0; k--) { const t = t0 - k * step, [o, h, l, c] = candle(t, step); rows.push([t, String(o), String(h), String(l), String(c), "0", "1", 3]); }
  return new Response(JSON.stringify({ error: [], result: { [`X${u.searchParams.get("pair")}`]: rows, last: NOW } }));
};
const ask = async s => { const r = await worker.fetch(new Request(`https://w.test/?symbol=${encodeURIComponent(s)}`), {}, null); return [r.status, await r.json()]; };

function checkFrames(js, tag, minLen) {
  for (const t of ORDER) {
    const f = js.frames[t];
    ok(f && f.length >= minLen[t], `${tag} ${t}: ${f?.length} candles`);
    ok(f.every((b, i) => i === 0 || b[0] > f[i - 1][0]), `${tag} ${t}: times not increasing`);
    ok(f.every(b => b.length === 5 && b.every(Number.isFinite) && b[2] >= b[3]), `${tag} ${t}: bad candle`);
  }
  ok(js.frames.D.every(b => b[0] % 86400 === 0), `${tag}: daily candles not at 00:00 UTC`);
  const fr = buildFrames(js), res = analyzeAll(fr);
  ok(ORDER.every(t => fr[t].length === js.frames[t].length), `${tag}: buildFrames changed the candles`);
  ok(ORDER.every(t => res[t].up.vertices.length >= 1 && res[t].sr), `${tag}: analysis missing`);
}

// 1. Binance, with paging (15m and 5m need two pages; W stops at the coin's first candle)
calls = [];
let [st, js] = await ask("btc");
ok(st === 200 && js.symbol === "BTC-USD" && js.market === "crypto" && js.source.startsWith("Binance"), `binance: ${st} ${JSON.stringify(js).slice(0, 200)}`);
checkFrames(js, "binance", { W: 450, D: 1000, "4H": 1000, "1H": 1000, "30m": 1000, "15m": 2000, "5m": 2000 });
ok(js.frames.W.length < 520 && js.frames.W[0][0] === FIRST, `binance W starts ${js.frames.W[0][0]}`);
ok(calls.length === 9, `binance requests: ${calls.length}`);

// 2. names the page and people type
for (const s of ["BTC-USD", "BTCUSD", "btc/usdt", "ETH", "doge-usd"]) { [st, js] = await ask(s); ok(st === 200, `${s}: ${st} ${js.error}`); }
[st, js] = await ask("SHIB");
ok(st === 400, `SHIB should be refused (${st})`);
[st, js] = await ask("LINK");
ok(st === 200 && js.symbol === "LINK-USD", `LINK -> ${js.symbol}`);

// 3. Binance doesn't list the coin -> Kraken
binanceCoins = ["BTCUSDT"];
[st, js] = await ask("XMR");
ok(st === 200 && js.source === "Kraken XMR/USD", `kraken fallback: ${st} ${js.source} ${js.error}`);
checkFrames(js, "kraken", Object.fromEntries(ORDER.map(t => [t, 720])));

// 4. Binance unreachable -> Kraken (DOGE is XDG there)
binanceCoins = null; binanceDown = true; calls = [];
[st, js] = await ask("DOGE");
ok(st === 200 && js.source === "Kraken DOGE/USD", `binance down: ${st} ${js.source} ${js.error}`);

console.log(fails ? `${fails} failures` : "ALL OK");
process.exit(fails ? 1 : 0);
