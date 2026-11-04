// Cloudflare Worker: candle server for docs/trendlines.html.
// Holds the Alpaca keys (secrets ALPACA_KEY_ID, ALPACA_SECRET_KEY) so they never reach the browser.
// GET /?symbol=AAPL  ->  { symbol, end, frames: { W, D, "30m", "15m", "5m" } }
// Each bar is [t, open, high, low, close]; t = New York wall-clock time written as if UTC (seconds).
// W/D bars carry their New York date at 00:00. Intraday bars: regular hours only (9:30-16:00).
// Data exactly as trendlines.py loads it (feed=sip, adjustment=all, end = now - 16 min).
//
// Crypto: GET /?symbol=BTC-USD (also BTC, BTCUSD, BTC/USD)  ->  { symbol, market: "crypto", source, end,
// frames: { W, D, "4H", "1H", "30m", "15m", "5m" } }. Crypto trades around the clock, so every timeframe
// comes straight from the exchange and t is plain UTC (seconds); daily candles start at 00:00 UTC.
// Source: Binance's public market data (<COIN>/USDT, no keys needed); if Binance doesn't list the coin
// or can't be reached, Kraken (<COIN>/USD, last 720 candles per timeframe).
//
// Options (for docs/options.html): GET /?options=AAPL  ->  { symbol, spot, end, expirations: [...],
// chain: [[expiry, "C"|"P", strike, bid, ask, iv, delta, gamma, theta, vega], ...] }
// The next four monthly expiries (third Fridays), strikes within 35% of the price, from Alpaca's
// option snapshots (feed=indicative, the free one). Cached for a minute.
//
// Volume profile (for docs/volumeprofile.html): GET /?vp=AAPL&range=1|5|20|60|250&hours=rth|all -> intraday bars
// with volume for the last `range` sessions (see volumeBars below).

const ALPACA = "https://data.alpaca.markets/v2/stocks/bars";
const DAY = 86400e3;
// S&P 100 (GOOG left out, GOOGL kept) + SPY. SPX and BRK.B are mapped below.
const TICKERS = new Set(("SPY AAPL ABBV ABT ACN ADBE AIG AMD AMGN AMT AMZN AVGO AXP BA BAC BK BKNG BLK BMY BRK-B C CAT " +
  "CHTR CL CMCSA COF COP COST CRM CSCO CVS CVX DE DHR DIS DUK EMR F FDX GD GE GILD GM GOOGL GS HD HON IBM INTC " +
  "INTU ISRG JNJ JPM KO LIN LLY LMT LOW MA MCD MDLZ MDT MET META MMM MO MRK MS MSFT NEE NFLX NKE NOW NVDA ORCL " +
  "PEP PFE PG PLTR PM PYPL QCOM RTX SBUX SCHW SO SPG T TGT TMO TMUS TSLA TXN UBER UNH UNP UPS USB V VZ WFC WMT XOM").split(" "));

// The coins of engine/crypto.txt (Yahoo-style names, as in the rest of the app).
const COINS = new Set(("BTC ETH XRP BNB SOL DOGE TRX ADA LINK XLM BCH AVAX LTC HBAR DOT XMR ETC ATOM ALGO").split(" "));

// series: Alpaca timeframe, days of history, daily-type (date only) or intraday (regular hours)
const SERIES = {
  W:     ["1Week", 365 * 8, "date"],
  D:     ["1Day", 460, "date"],
  "30m": ["30Min", 330, "session"],
  "15m": ["15Min", 95, "session"],
  "5m":  ["5Min", 35, "session"],
};

const NY = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hourCycle: "h23",
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
// New York wall-clock of a UTC instant, as seconds "as if UTC"
function nyWall(iso) {
  const p = {};
  for (const { type, value } of NY.formatToParts(new Date(iso))) p[type] = +value;
  return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) / 1000;
}

async function fetchSeries(sym, tf, start, end, env) {
  const fmt = d => d.toISOString().replace(/\.\d{3}Z$/, "Z");
  const params = new URLSearchParams({ symbols: sym, timeframe: tf, start: fmt(new Date(start)),
    end: fmt(new Date(end)), limit: "10000", adjustment: "all", feed: "sip" });
  const headers = { "APCA-API-KEY-ID": env.ALPACA_KEY_ID, "APCA-API-SECRET-KEY": env.ALPACA_SECRET_KEY };
  const bars = [];
  for (;;) {
    let r;
    for (let tries = 0; tries < 4; tries++) {
      r = await fetch(`${ALPACA}?${params}`, { headers });
      if (r.status !== 429) break;                       // too many requests: wait and retry
      await new Promise(ok => setTimeout(ok, 2000));
    }
    if (!r.ok) throw new Error(`Alpaca ${tf}: HTTP ${r.status} ${(await r.text()).slice(0, 200)}`);
    const js = await r.json();
    bars.push(...((js.bars || {})[sym] || []));
    if (!js.next_page_token) break;
    params.set("page_token", js.next_page_token);
  }
  return bars;
}

// Speed: years of candles take Alpaca 10-25 seconds, but only the last few days ever change.
// So each series is fetched in two parts: the older history, kept in the Worker's cache for the rest
// of the (UTC) day, and the last 8-14 days (from a Monday, so the current weekly bar is whole),
// fetched fresh every time. The history is re-fetched daily, so split and dividend adjustments
// (adjustment=all) stay consistent. The result is the same bars as one long request.
async function fetchSeriesFast(sym, tf, days, end, env, cache) {
  const today = Math.floor(end / DAY) * DAY;
  const start = today - days * DAY;
  const monday = today - ((new Date(today).getUTCDay() + 6) % 7) * DAY;   // this week's Monday 00:00 UTC
  const recentStart = monday - 7 * DAY;                                  // last week's Monday
  const recent = await Promise.all([
    cachedHistory(sym, tf, start, recentStart + 2 * DAY, today, env, cache),   // overlaps the recent part
    fetchSeries(sym, tf, recentStart, end, env),
  ]);
  const [hist, fresh] = recent;
  if (!fresh.length) return hist;
  const first = Date.parse(fresh[0].t);
  return [...hist.filter(b => Date.parse(b.t) < first), ...fresh];
}

async function cachedHistory(sym, tf, start, end, today, env, cache) {
  const key = new Request(`https://trendlines.cache/h/${sym}/${tf}/${new Date(today).toISOString().slice(0, 10)}`);
  if (cache) {
    const hit = await cache.match(key).catch(() => null);
    if (hit) return hit.json();
  }
  const bars = await fetchSeries(sym, tf, start, end, env);
  if (cache) await cache.put(key, new Response(JSON.stringify(bars), {
    headers: { "Content-Type": "application/json", "Cache-Control": "max-age=86400" } })).catch(() => {});
  return bars;
}

function shape(bars, kind) {
  bars = bars.map(b => [nyWall(b.t), b.o, b.h, b.l, b.c]).sort((a, b) => a[0] - b[0]);
  const out = [];
  if (kind === "session") {
    let prev = null;
    for (const b of bars) {
      if (b[0] === prev) continue;                         // duplicate timestamp: keep the first
      prev = b[0];
      const m = Math.floor((b[0] % 86400) / 60);
      if (m >= 570 && m < 960) out.push(b);                // 9:30-16:00 New York
    }
  } else {
    let prevT = null;
    for (const b of bars) {
      if (b[0] === prevT) continue;                        // exact duplicate: keep the first
      prevT = b[0];
      b[0] = Math.floor(b[0] / 86400) * 86400;             // New York date
      if (out.length && out[out.length - 1][0] === b[0]) out[out.length - 1] = b;   // same date: keep the last
      else out.push(b);
    }
  }
  return out;
}

// ------------------------------------------------------------------ crypto
// timeframe -> [Binance interval, candles wanted, Kraken interval in minutes]
const CRYPTO_SERIES = {
  W:     ["1w", 520, 10080],
  D:     ["1d", 1000, 1440],
  "4H":  ["4h", 1000, 240],
  "1H":  ["1h", 1000, 60],
  "30m": ["30m", 1000, 30],
  "15m": ["15m", 2000, 15],
  "5m":  ["5m", 2000, 5],
};
const BINANCE = "https://data-api.binance.vision/api/v3/klines";   // Binance's public market-data host
const KRAKEN = "https://api.kraken.com/0/public/OHLC";
const KRAKEN_NAME = { BTC: "XBT", DOGE: "XDG" };

// "btc", "BTC-USD", "BTCUSD", "BTC/USDT" ... -> "BTC" if it is one of the coins, else null
function coinOf(sym) {
  const m = sym.toUpperCase().replace(/[\s/_]/g, "-").match(/^([A-Z0-9]+?)(?:-?USDT?)?$/);
  return m && COINS.has(m[1]) ? m[1] : null;
}

async function getJson(url, label) {
  let r;
  for (let tries = 0; tries < 3; tries++) {
    r = await fetch(url, { headers: { "User-Agent": "signal-desk-trendlines" } });
    if (r.status !== 429 && r.status < 500) break;       // busy: wait and retry
    await new Promise(ok => setTimeout(ok, 1500 * (tries + 1)));
  }
  if (!r.ok) throw new Error(`${label}: HTTP ${r.status} ${(await r.text()).slice(0, 160)}`);
  return r.json();
}

// newest `want` candles, 1000 per request, paging back in time
async function binanceSeries(coin, interval, want) {
  const rows = [];
  let endTime = null;
  while (rows.length < want) {
    const q = new URLSearchParams({ symbol: `${coin}USDT`, interval, limit: String(Math.min(1000, want - rows.length)) });
    if (endTime !== null) q.set("endTime", String(endTime));
    const js = await getJson(`${BINANCE}?${q}`, `Binance ${interval}`);
    if (!Array.isArray(js) || !js.length) break;
    rows.unshift(...js);
    if (js.length < +q.get("limit")) break;              // reached the coin's first candle
    endTime = js[0][0] - 1;
  }
  return rows.map(k => [Math.floor(k[0] / 1000), +k[1], +k[2], +k[3], +k[4]]);
}

// Kraken allows about one public request per second: callers go one at a time, and a
// "Too many requests" answer is retried after a pause.
async function krakenSeries(coin, minutes) {
  let js;
  for (let tries = 0; ; tries++) {
    js = await getJson(`${KRAKEN}?pair=${KRAKEN_NAME[coin] || coin}USD&interval=${minutes}`, `Kraken ${minutes}m`);
    const busy = (js.error || []).some(e => /too many requests|rate limit/i.test(e));
    if (!busy || tries >= 4) break;
    await new Promise(ok => setTimeout(ok, 2500 * (tries + 1)));
  }
  if (js.error && js.error.length) throw new Error(`Kraken: ${js.error.join(", ")}`);
  const key = Object.keys(js.result || {}).find(k => k !== "last");
  if (!key) throw new Error(`Kraken: no ${coin}/USD data`);
  return js.result[key].map(k => [+k[0], +k[1], +k[2], +k[3], +k[4]]);
}

// sorted, one candle per time
function clean(rows) {
  rows.sort((a, b) => a[0] - b[0]);
  return rows.filter((r, i) => i === 0 || r[0] !== rows[i - 1][0]);
}

async function cryptoFrames(coin) {
  const names = Object.keys(CRYPTO_SERIES);
  const errors = [];
  for (const [source, get, oneByOne] of [
    [`Binance ${coin}/USDT`, k => binanceSeries(coin, CRYPTO_SERIES[k][0], CRYPTO_SERIES[k][1]), false],
    [`Kraken ${coin}/USD`, k => krakenSeries(coin, CRYPTO_SERIES[k][2]), true],
  ]) {
    try {
      let got;
      if (oneByOne) {
        got = [];
        for (const k of names) {
          if (got.length) await new Promise(ok => setTimeout(ok, 1100));
          got.push(await get(k));
        }
      } else got = await Promise.all(names.map(get));
      if (!got[1].length) throw new Error("no daily candles");
      const frames = {};
      names.forEach((k, i) => frames[k] = clean(got[i]));
      return { source, frames };
    } catch (e) {
      errors.push(`${source}: ${e.message || e}`);
    }
  }
  throw new Error(errors.join(" · "));
}

const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, OPTIONS" };
const json = (body, status = 200, extra = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...cors, ...extra } });

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });
    const url = new URL(request.url);
    if (url.searchParams.has("options")) return options(url.searchParams.get("options"), env, ctx);
    if (url.searchParams.has("vp")) return volumeBars(url.searchParams, env, ctx);
    let sym = (url.searchParams.get("symbol") || "").trim().toUpperCase().replace(/\./g, "-");
    if (["SPX", "^SPX", "^GSPC"].includes(sym)) sym = "SPY";       // index: no Alpaca data
    const coin = TICKERS.has(sym) ? null : coinOf(sym);
    if (coin) return crypto(coin, ctx);
    if (!TICKERS.has(sym)) return json({ error: `${sym || "No ticker"}: only S&P 100 stocks, SPY and the app's 19 coins are available.` }, 400);
    if (!env.ALPACA_KEY_ID || !env.ALPACA_SECRET_KEY) return json({ error: "The Alpaca keys are not set on the Worker." }, 500);

    // same request within a minute: answer from the cache (older candles are cached separately for the day)
    const cache = typeof caches !== "undefined" ? caches.default : null;
    const key = new Request(`https://trendlines.cache/${sym}`);
    if (cache) { const hit = await cache.match(key); if (hit) return hit; }

    const end = Date.now() - 16 * 60e3;                    // free plan: SIP data 15+ minutes old
    const alpacaSym = sym.replace(/-/g, ".");              // BRK-B -> BRK.B
    try {
      const names = Object.keys(SERIES);
      const got = await Promise.all(names.map(k => fetchSeriesFast(alpacaSym, SERIES[k][0], SERIES[k][1], end, env, cache)));
      const frames = {};
      names.forEach((k, i) => {
        frames[k] = shape(got[i], SERIES[k][2]);
      });
      if (!frames.D.length) return json({ error: `No data for ${sym}.` }, 404);
      const res = json({ symbol: sym, end: new Date(end).toISOString(), frames }, 200, { "Cache-Control": "max-age=60" });
      if (cache) ctx?.waitUntil?.(cache.put(key, res.clone()));
      return res;
    } catch (e) {
      return json({ error: String(e.message || e) }, 502);
    }
  },
};

async function crypto(coin, ctx) {
  const sym = `${coin}-USD`;
  const cache = typeof caches !== "undefined" ? caches.default : null;
  const key = new Request(`https://trendlines.cache/${sym}`);
  if (cache) { const hit = await cache.match(key); if (hit) return hit; }
  try {
    const { source, frames } = await cryptoFrames(coin);
    const last = Math.max(...Object.values(frames).map(f => f.length ? f[f.length - 1][0] : 0));
    const end = new Date((last + 5 * 60) * 1000).toISOString();   // end of the newest 5-minute candle
    const res = json({ symbol: sym, market: "crypto", source, end, frames }, 200, { "Cache-Control": "max-age=300" });
    if (cache) ctx?.waitUntil?.(cache.put(key, res.clone()));
    return res;
  } catch (e) {
    return json({ error: `No data for ${sym}. ${e.message || e}` }, 502);
  }
}

// ------------------------------------------------------------------ option chains
const OPT_SNAP = "https://data.alpaca.markets/v1beta1/options/snapshots/";
const thirdFriday = (y, m) => { const d = new Date(Date.UTC(y, m, 15)); d.setUTCDate(15 + ((5 - d.getUTCDay() + 7) % 7)); return d; };
function monthlyExpiries(n) {
  const today = new Date(); today.setUTCHours(0, 0, 0, 0);
  const out = [];
  for (let y = today.getUTCFullYear(), m = today.getUTCMonth(); out.length < n; m++) {
    if (m === 12) { m = 0; y++; }
    const f = thirdFriday(y, m);
    if (f >= today) out.push(f);
  }
  return out;
}
const iso = d => d.toISOString().slice(0, 10);

async function options(raw, env, ctx) {
  let sym = (raw || "").trim().toUpperCase().replace(/\./g, "-");
  if (["SPX", "^SPX", "^GSPC"].includes(sym)) sym = "SPY";
  if (!TICKERS.has(sym)) return json({ error: `${sym || "No ticker"}: options are available for S&P 100 stocks and SPY.` }, 400);
  if (!env.ALPACA_KEY_ID || !env.ALPACA_SECRET_KEY) return json({ error: "The Alpaca keys are not set on the Worker." }, 500);
  const cache = typeof caches !== "undefined" ? caches.default : null;
  const key = new Request(`https://trendlines.cache/options/${sym}`);
  if (cache) { const hit = await cache.match(key); if (hit) return hit; }
  const headers = { "APCA-API-KEY-ID": env.ALPACA_KEY_ID, "APCA-API-SECRET-KEY": env.ALPACA_SECRET_KEY };
  const aSym = sym.replace(/-/g, ".");
  try {
    // price: the latest trade on IEX (free), or the last daily close
    const sr = await fetch(`https://data.alpaca.markets/v2/stocks/${encodeURIComponent(aSym)}/snapshot?feed=iex`, { headers });
    if (!sr.ok) throw new Error(`Alpaca price: HTTP ${sr.status}`);
    const sj = await sr.json();
    const spot = sj.latestTrade?.p || sj.dailyBar?.c || sj.prevDailyBar?.c;
    if (!spot) throw new Error("no price");
    const exps = monthlyExpiries(4);
    const rows = [];
    await Promise.all(exps.map(async f => {
      // a holiday Friday (Good Friday) moves the expiry to Thursday: look at both days
      const params = new URLSearchParams({ feed: "indicative", limit: "1000",
        expiration_date_gte: iso(new Date(f.getTime() - DAY)), expiration_date_lte: iso(f),
        strike_price_gte: (spot * 0.65).toFixed(2), strike_price_lte: (spot * 1.35).toFixed(2) });
      for (let page = 0; page < 6; page++) {
        const r = await fetch(`${OPT_SNAP}${encodeURIComponent(aSym)}?${params}`, { headers });
        if (!r.ok) throw new Error(`Alpaca options: HTTP ${r.status} ${(await r.text()).slice(0, 160)}`);
        const js = await r.json();
        for (const [occ, s] of Object.entries(js.snapshots || {})) {
          const t = occ.slice(-15);
          const exp = `20${t.slice(0, 2)}-${t.slice(2, 4)}-${t.slice(4, 6)}`;
          const q = s.latestQuote || {}, g = s.greeks || {};
          const num = v => (v == null || !isFinite(v)) ? null : +(+v).toFixed(5);
          rows.push([exp, t[6], +t.slice(7) / 1000, num(q.bp), num(q.ap), num(s.impliedVolatility),
            num(g.delta), num(g.gamma), num(g.theta), num(g.vega)]);
        }
        if (!js.next_page_token) break;
        params.set("page_token", js.next_page_token);
      }
    }));
    if (!rows.length) return json({ error: `No option quotes for ${sym} right now.` }, 404);
    rows.sort((a, b) => a[0].localeCompare(b[0]) || a[1].localeCompare(b[1]) || a[2] - b[2]);
    const expirations = [...new Set(rows.map(r => r[0]))];
    const res = json({ symbol: sym, spot, end: new Date().toISOString(), expirations, chain: rows }, 200, { "Cache-Control": "max-age=60" });
    if (cache) ctx?.waitUntil?.(cache.put(key, res.clone()));
    return res;
  } catch (e) {
    return json({ error: String(e.message || e) }, 502);
  }
}

// ------------------------------------------------------------------ volume profile (docs/volumeprofile.html)
// GET /?vp=AAPL&range=5&hours=rth  ->  { symbol, market, range, tf, hours, unit, end, sessions, bars }
// bars: [t, open, high, low, close, volume]. Stocks: full-market (SIP) bars, 15+ minutes old (free plan),
// t = New York wall-clock as if UTC; hours=rth keeps 9:30-16:00, hours=all keeps 4:00-20:00. The last `range`
// trading sessions. Crypto: Binance coin/USDT bars with their USDT volume (Kraken coin/USD as a fallback,
// volume in coins), t = UTC, the last `range` days. sessions = the dates covered (New York / UTC).
// range -> [Alpaca timeframe, minutes, Binance interval, minutes, Kraken minutes]
const VP_RANGES = {
  1:   ["1Min", 1, "1m", 1, 1],
  5:   ["1Min", 1, "5m", 5, 5],
  20:  ["5Min", 5, "15m", 15, 15],
  60:  ["15Min", 15, "1h", 60, 60],
  250: ["30Min", 30, "4h", 240, 240],
};
const short = x => +(+x).toPrecision(7);

async function volumeBars(q, env, ctx) {
  let sym = (q.get("vp") || "").trim().toUpperCase().replace(/\./g, "-");
  if (["SPX", "^SPX", "^GSPC"].includes(sym)) sym = "SPY";
  const range = VP_RANGES[q.get("range")] ? +q.get("range") : 5;
  const hours = q.get("hours") === "all" ? "all" : "rth";
  const coin = TICKERS.has(sym) ? null : coinOf(sym);
  if (!coin && !TICKERS.has(sym)) return json({ error: `${sym || "No ticker"}: only S&P 100 stocks, SPY and the app's 19 coins are available.` }, 400);
  if (!coin && (!env.ALPACA_KEY_ID || !env.ALPACA_SECRET_KEY)) return json({ error: "The Alpaca keys are not set on the Worker." }, 500);

  const name = coin ? `${coin}-USD` : sym;
  const cache = typeof caches !== "undefined" ? caches.default : null;
  const key = new Request(`https://trendlines.cache/vp/${name}/${range}/${coin ? "24h" : hours}`);
  if (cache) { const hit = await cache.match(key).catch(() => null); if (hit) return hit; }
  const [atf, amin, binterval, bmin, kmin] = VP_RANGES[range];
  try {
    let out;
    if (coin) {
      const want = Math.ceil(range * 1440 / bmin);
      let bars, source, unit = "USD";
      try {
        bars = await binanceVolume(coin, binterval, want);
        source = `Binance ${coin}/USDT`;
      } catch (e) {
        bars = await krakenVolume(coin, kmin);
        source = `Kraken ${coin}/USD`; unit = coin;
      }
      const from = Date.now() / 1000 - range * 86400;
      bars = clean(bars).filter(b => b[0] >= from - bmin * 60);
      if (!bars.length) return json({ error: `No data for ${name}.` }, 404);
      const sessions = [...new Set(bars.map(b => new Date(b[0] * 1000).toISOString().slice(0, 10)))];
      out = { symbol: name, market: "crypto", source, range, tf: bmin, hours: "24h", unit,
        end: new Date((bars[bars.length - 1][0] + bmin * 60) * 1000).toISOString(), sessions, bars };
    } else {
      const end = Date.now() - 16 * 60e3;                      // free plan: SIP data 15+ minutes old
      const days = Math.ceil(range * 7 / 5) + 6;               // calendar days that hold `range` sessions
      const raw = await fetchSeries(sym.replace(/-/g, "."), atf, end - days * DAY, end, env);
      const lo = hours === "rth" ? 570 : 240, hi = hours === "rth" ? 960 : 1200;
      let bars = [], prev = null;
      for (const b of raw) {
        const t = nyWall(b.t);
        if (t === prev) continue;
        prev = t;
        const m = Math.floor((t % 86400) / 60);
        if (m >= lo && m < hi) bars.push([t, short(b.o), short(b.h), short(b.l), short(b.c), b.v]);
      }
      bars.sort((a, b) => a[0] - b[0]);
      const dateOf = t => new Date(t * 1000).toISOString().slice(0, 10);
      const sessions = [...new Set(bars.map(b => dateOf(b[0])))].slice(-range);
      bars = bars.filter(b => dateOf(b[0]) >= sessions[0]);
      if (!bars.length) return json({ error: `No data for ${sym}.` }, 404);
      out = { symbol: sym, market: "stock", source: "Alpaca (all US exchanges, SIP)", range, tf: amin, hours, unit: "shares",
        end: new Date(end).toISOString(), sessions, bars };
    }
    const res = json(out, 200, { "Cache-Control": `max-age=${range >= 60 ? 300 : 60}` });
    if (cache) ctx?.waitUntil?.(cache.put(key, res.clone()));
    return res;
  } catch (e) {
    return json({ error: String(e.message || e) }, 502);
  }
}

// newest `want` candles with their USDT (quote) volume
async function binanceVolume(coin, interval, want) {
  const rows = [];
  let endTime = null;
  while (rows.length < want) {
    const q = new URLSearchParams({ symbol: `${coin}USDT`, interval, limit: String(Math.min(1000, want - rows.length)) });
    if (endTime !== null) q.set("endTime", String(endTime));
    const js = await getJson(`${BINANCE}?${q}`, `Binance ${interval}`);
    if (!Array.isArray(js) || !js.length) break;
    rows.unshift(...js);
    if (js.length < +q.get("limit")) break;
    endTime = js[0][0] - 1;
  }
  if (!rows.length) throw new Error("no candles");
  return rows.map(k => [Math.floor(k[0] / 1000), +k[1], +k[2], +k[3], +k[4], Math.round(+k[7])]);
}

// Kraken: last 720 candles, volume in coins
async function krakenVolume(coin, minutes) {
  let js;
  for (let tries = 0; ; tries++) {
    js = await getJson(`${KRAKEN}?pair=${KRAKEN_NAME[coin] || coin}USD&interval=${minutes}`, `Kraken ${minutes}m`);
    const busy = (js.error || []).some(e => /too many requests|rate limit/i.test(e));
    if (!busy || tries >= 4) break;
    await new Promise(ok => setTimeout(ok, 2500 * (tries + 1)));
  }
  if (js.error && js.error.length) throw new Error(`Kraken: ${js.error.join(", ")}`);
  const k = Object.keys(js.result || {}).find(x => x !== "last");
  if (!k) throw new Error(`Kraken: no ${coin}/USD data`);
  return js.result[k].map(r => [+r[0], +r[1], +r[2], +r[3], +r[4], +r[6]]);
}
