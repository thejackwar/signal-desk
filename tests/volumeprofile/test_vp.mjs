// Volume profile: the maths (docs/vp-lib.js) and the Worker's ?vp= endpoint with fake Alpaca / Binance answers.
// Run:  node tests/volumeprofile/test_vp.mjs
import { buildProfile, findNodes, sessionProfiles, candles, where } from "../../docs/vp-lib.js";
import worker from "../../worker/trendlines-worker.js";

let fails = 0;
const ok = (c, m) => { if (!c) { fails++; console.log("FAIL", m); } else console.log("ok  ", m); };
const near = (a, b, e = 1e-9) => Math.abs(a - b) <= e;

// --- maths
{
  // 10 rows from 100 to 110; one bar 100-110 with 1000 shares spreads 100 per row
  const p = buildProfile([[0, 100, 110, 100, 105, 1000]], { rows: 10 });
  ok(p.vol.every(v => near(v, 100)), "volume spread evenly over the bar's range");
  ok(near(p.total, 1000), "total volume kept");
  // add 500 at 104-105: POC is that row
  const q = buildProfile([[0, 100, 110, 100, 105, 1000], [60, 104.2, 104.8, 104.2, 104.5, 500]], { rows: 10 });
  ok(q.pocRow === 4 && near(q.poc, 104.5), "POC = busiest row, at its middle");
  ok(near(q.vol.reduce((a, b) => a + b, 0), 1500), "row volumes add up to the total");
  ok(q.vaShare >= 0.7 && q.val <= q.poc && q.vah >= q.poc, "value area holds 70%+ and contains the POC");
  // value area grows toward the busier side
  const r = buildProfile([[0, 0, 1, 0, 1, 10], [0, 1, 2, 1, 2, 100], [0, 2, 3, 2, 3, 30], [0, 3, 4, 3, 4, 60]], { rows: 4, va: 0.8 });
  ok(r.pocRow === 1 && r.vaRows[0] === 1 && r.vaRows[1] === 3, "value area expands to the busier neighbour first");
  ok(near(r.val, 1) && near(r.vah, 4), "VAL/VAH at the value area's outer edges");
  // a zero-range bar lands in its close's row
  const z = buildProfile([[0, 5, 5, 5, 5, 7], [0, 0, 10, 0, 1, 10]], { rows: 10 });
  ok(near(z.vol[5], 8), "flat bar goes into one row");
  ok(buildProfile([], {}) === null && buildProfile([[0, 1, 2, 1, 2, 0]], {}) === null, "no bars / no volume: no profile");

  // two humps with a gap: two HVNs, one LVN between
  const bars = [];
  for (let k = 0; k < 50; k++) bars.push([k, 100, 101, 100, 101, 1000]);
  for (let k = 0; k < 30; k++) bars.push([k, 109, 110, 109, 110, 1000]);
  bars.push([99, 100, 110, 100, 110, 200]);
  const p2 = buildProfile(bars, { rows: 20 }), n2 = findNodes(p2);
  ok(p2.poc < 101.5, "POC on the bigger hump");
  ok(n2.hvn.length === 1 && n2.hvn[0].price > 108.5, "second hump is a high volume node");
  ok(n2.lvn.length === 1 && n2.lvn[0].price > 101 && n2.lvn[0].price < 109, "the gap between them is a low volume node");

  // sessions and naked POCs
  const day = (d, h, m) => Date.UTC(2026, 10, d, h, m) / 1000;
  const sb = [];
  for (let m = 0; m < 30; m++) sb.push([day(2, 10, m), 50, 50.2, 49.8, 50, 100]);    // day 1 around 50
  for (let m = 0; m < 30; m++) sb.push([day(3, 10, m), 55, 55.2, 54.8, 55, 100]);    // day 2 around 55
  for (let m = 0; m < 30; m++) sb.push([day(4, 10, m), 54, 56, 53, 55, 100]);        // day 3 trades 53-56
  const ss = sessionProfiles(sb, { rows: 30 });
  ok(ss.length === 3 && ss.map(s => s.date).join() === "2026-11-02,2026-11-03,2026-11-04", "split into sessions by date");
  ok(ss[0].naked === true, "day 1 POC (50) never revisited: naked");
  ok(ss[1].naked === false, "day 2 POC (55) traded through later: not naked");
  ok(ss[2].naked === false, "the last session is never naked");
  ok(near(ss[0].p.step, ss[2].p.step), "sessions share one row height");

  const cd = candles(sb.slice(0, 30), 15);
  ok(cd.length === 2 && cd[0].volume === 1500 && cd[0].open === 50, "1-minute bars grouped into 15-minute candles");
  ok(candles(sb, "D").length === 3, "daily candles: one per session");
  ok(where(120, p2) === "above" && where(90, p2) === "below" && where(p2.poc, p2) === "inside", "price vs value area");
}

// --- Worker
const NOW = Date.UTC(2026, 10, 4, 18, 0);                  // Wed 4 Nov 2026, 13:00 New York
Date.now = () => NOW;
let seen = [];
globalThis.caches = undefined;
globalThis.fetch = async url => {
  const u = new URL(url);
  seen.push(u);
  if (u.host === "data.alpaca.markets") {
    const tf = u.searchParams.get("timeframe"), step = { "1Min": 60, "5Min": 300, "15Min": 900, "30Min": 1800 }[tf];
    const a = Date.parse(u.searchParams.get("start")) / 1000, b = Date.parse(u.searchParams.get("end")) / 1000;
    const bars = [];
    for (let t = Math.ceil(a / step) * step; t <= b && bars.length < 10000; t += step) {
      const wd = new Date(t * 1000).getUTCDay();
      if (wd === 0 || wd === 6) continue;
      const h = new Date(t * 1000).getUTCHours();          // EST (UTC-5) in November: 9:00-01:00 UTC = 4:00-20:00 NY
      if (h < 9 && h >= 1) continue;
      const p = 100 + (t % 3600) / 3600;
      bars.push({ t: new Date(t * 1000).toISOString(), o: p, h: p + 0.1, l: p - 0.1, c: p, v: 1000 });
    }
    return new Response(JSON.stringify({ bars: { [u.searchParams.get("symbols")]: bars }, next_page_token: null }));
  }
  if (u.host.includes("binance")) {
    const lim = +u.searchParams.get("limit"), step = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400 }[u.searchParams.get("interval")];
    const end = u.searchParams.get("endTime") ? Math.floor(+u.searchParams.get("endTime") / 1000) : NOW / 1000;
    const rows = [];
    for (let t = Math.floor(end / step) * step; rows.length < lim; t -= step) rows.unshift([t * 1000, "30000", "30100", "29900", "30050", "2", 0, "60000"]);
    return new Response(JSON.stringify(rows));
  }
  return new Response("not found", { status: 404 });
};
const env = { ALPACA_KEY_ID: "k", ALPACA_SECRET_KEY: "s" };
const call = async q => { const r = await worker.fetch(new Request(`https://w.dev/?${q}`), env, {}); return [r.status, await r.json()]; };

{
  let [st, js] = await call("vp=aapl&range=5");
  ok(st === 200 && js.symbol === "AAPL" && js.market === "stock" && js.tf === 1, "stock: 1-minute bars for 5 sessions");
  const u = seen.find(x => x.host === "data.alpaca.markets");
  ok(u.searchParams.get("feed") === "sip" && u.searchParams.get("timeframe") === "1Min", "asks Alpaca for full-market SIP data");
  ok(Date.parse(u.searchParams.get("end")) <= NOW - 15 * 60e3, "ends 15+ minutes ago (free plan)");
  ok(js.sessions.length === 5 && js.sessions[4] === "2026-11-04", "exactly the last 5 sessions, today included");
  const mins = js.bars.map(b => Math.floor((b[0] % 86400) / 60));
  ok(Math.min(...mins) === 570 && Math.max(...mins) < 960, "regular hours only (9:30-16:00 New York)");
  ok(js.bars.every(b => b.length === 6 && b[5] === 1000), "bars carry their volume");
  [st, js] = await call("vp=AAPL&range=5&hours=all");
  const mins2 = js.bars.map(b => Math.floor((b[0] % 86400) / 60));
  ok(Math.min(...mins2) === 240 && js.hours === "all", "extended hours from 4:00");
  seen = [];
  [st, js] = await call("vp=MSFT&range=250");
  ok(st === 200 && js.tf === 30 && seen[0].searchParams.get("timeframe") === "30Min" && js.sessions.length === 250, "1 year: 30-minute bars, 250 sessions");
  [st, js] = await call("vp=BRK.B&range=20");
  ok(st === 200 && js.symbol === "BRK-B" && seen.some(x => x.searchParams.get("symbols") === "BRK.B"), "BRK.B mapped for Alpaca");
  [st, js] = await call("vp=SPX&range=1");
  ok(st === 200 && js.symbol === "SPY" && js.sessions.length === 1, "SPX -> SPY, last session");
  [st, js] = await call("vp=ZZZZ");
  ok(st === 400 && /only S&P 100/.test(js.error), "unknown ticker refused");
  [st, js] = await worker.fetch(new Request("https://w.dev/?vp=AAPL"), {}, {}).then(async r => [r.status, await r.json()]);
  ok(st === 500 && /keys/.test(js.error), "missing keys explained");

  seen = [];
  [st, js] = await call("vp=btc&range=20");
  ok(st === 200 && js.symbol === "BTC-USD" && js.market === "crypto" && js.unit === "USD" && js.tf === 15, "crypto: Binance 15-minute bars with dollar volume");
  ok(js.bars.every(b => b[5] === 60000) && js.bars[0][0] >= NOW / 1000 - 20 * 86400 - 900, "quote volume kept, only the last 20 days");
  ok(seen.filter(x => x.host.includes("binance")).length === 2, "pages back 1000 candles at a time");
}

console.log(fails ? `\n${fails} FAILED` : "\nall passed");
process.exit(fails ? 1 : 0);
