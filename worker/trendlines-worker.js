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

async function fetchSeries(sym, tf, days, end, env) {
  const fmt = d => d.toISOString().replace(/\.\d{3}Z$/, "Z");
  const params = new URLSearchParams({ symbols: sym, timeframe: tf, start: fmt(new Date(end - days * DAY)),
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

async function krakenSeries(coin, minutes) {
  const js = await getJson(`${KRAKEN}?pair=${KRAKEN_NAME[coin] || coin}USD&interval=${minutes}`, `Kraken ${minutes}m`);
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
  for (const [source, get] of [
    [`Binance ${coin}/USDT`, k => binanceSeries(coin, CRYPTO_SERIES[k][0], CRYPTO_SERIES[k][1])],
    [`Kraken ${coin}/USD`, k => krakenSeries(coin, CRYPTO_SERIES[k][2])],
  ]) {
    try {
      const got = await Promise.all(names.map(get));
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
    let sym = (url.searchParams.get("symbol") || "").trim().toUpperCase().replace(/\./g, "-");
    if (["SPX", "^SPX", "^GSPC"].includes(sym)) sym = "SPY";       // index: no Alpaca data
    const coin = TICKERS.has(sym) ? null : coinOf(sym);
    if (coin) return crypto(coin, ctx);
    if (!TICKERS.has(sym)) return json({ error: `${sym || "No ticker"}: only S&P 100 stocks, SPY and the app's 19 coins are available.` }, 400);
    if (!env.ALPACA_KEY_ID || !env.ALPACA_SECRET_KEY) return json({ error: "The Alpaca keys are not set on the Worker." }, 500);

    // same request within a minute: answer from the cache
    const cache = typeof caches !== "undefined" ? caches.default : null;
    const key = new Request(`https://trendlines.cache/${sym}`);
    if (cache) { const hit = await cache.match(key); if (hit) return hit; }

    const end = Date.now() - 16 * 60e3;                    // free plan: SIP data 15+ minutes old
    const alpacaSym = sym.replace(/-/g, ".");              // BRK-B -> BRK.B
    try {
      const names = Object.keys(SERIES);
      const got = await Promise.all(names.map(k => fetchSeries(alpacaSym, SERIES[k][0], SERIES[k][1], end, env)));
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
    const res = json({ symbol: sym, market: "crypto", source, end, frames }, 200, { "Cache-Control": "max-age=60" });
    if (cache) ctx?.waitUntil?.(cache.put(key, res.clone()));
    return res;
  } catch (e) {
    return json({ error: `No data for ${sym}. ${e.message || e}` }, 502);
  }
}
