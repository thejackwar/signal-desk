// Cloudflare Worker: candle server for docs/trendlines.html.
// Holds the Alpaca keys (secrets ALPACA_KEY_ID, ALPACA_SECRET_KEY) so they never reach the browser.
// GET /?symbol=AAPL  ->  { symbol, end, frames: { W, D, "30m", "15m", "5m" } }
// Each bar is [t, open, high, low, close]; t = New York wall-clock time written as if UTC (seconds).
// W/D bars carry their New York date at 00:00. Intraday bars: regular hours only (9:30-16:00), or with
// &hours=all the extended session too (pre-market 4:00-9:30 and after-hours 16:00-20:00 New York).
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
//
// Live quotes (for the Terminal): GET /?quotes=stocks | crypto  ->  { source, at, q: { TICKER: [price, prevClose, changePct, time] } }
// Stocks: Alpaca snapshots on the free IEX feed (real time, IEX trades only). Coins: Binance 24h tickers, Kraken for XMR.
// Cached for 15 seconds.
//
// Volume screener (for the Terminal's VOLS panel):
//   GET /?volscan=sp100|sp500  ->  { end, partial, rows: { TICKER: [price, prevClose, chgPct, vol, avg10, avg30, dollarAvg10, dollarAvg30, relVol, date] } }
//   Daily SIP bars of every stock in the list; averages over finished sessions only; relVol = today's volume
//   against the 10-day average, scaled by how much of the session has passed (so 1.0 = a normal pace). Cached 5 minutes.
//   GET /?grid=AAPL,MSFT,...&tf=1D|5D|1M|3M|6M|YTD|1Y|5Y  ->  { tf, end, bars: { TICKER: [[t, o, h, l, c, v], ...] } }
//   Candles for up to 30 stocks in one request (1D: 5-minute, 5D: 15-minute, 1M: 30-minute, regular hours;
//   3M-1Y: daily; 5Y: weekly). t as in the rest of this Worker (New York wall clock as if UTC).
//
// Bar replay (for docs/replay.html): GET /?replay=AAPL&tf=1Day|1Hour|15Min|5Min&start=YYYY-MM-DD   (also replay=BTC: Binance BTC/USDT, market: "crypto")
//   ->  { symbol, tf, start, end, source, bars: [[t, open, high, low, close, volume], ...] }
//   t is plain UTC (seconds), unlike the rest of this Worker. Intraday bars include pre-market and after-hours
//   (4:00-20:00 New York) so the page can shade them. Window: some history before `start`, then a fixed stretch
//   after it (see REPLAY). Alpaca SIP data from 2016. Past windows are cached for a day.
//
// Price alerts (for the Terminal): the page stores an alert, this Worker checks prices every few minutes (Cron Trigger)
// and sends a phone notification through ntfy.sh to the viewer's own channel when the price crosses the level.
//   POST /?px=add   {topic, sym, level, dir: "above"|"below", note}   ->  {ok, alert}
//   GET  /?px=list&topic=...                                          ->  {alerts: [...]}
//   POST /?px=del   {topic, id}                                       ->  {ok}
// Needs a KV namespace bound as PX and a Cron Trigger (every 5 minutes); see worker/README.md.

const ALPACA = "https://data.alpaca.markets/v2/stocks/bars";
const DAY = 86400e3;
// S&P 100 (GOOG left out, GOOGL kept) + SPY. SPX and BRK.B are mapped below.
const TICKERS = new Set(("SPY AAPL ABBV ABT ACN ADBE AIG AMD AMGN AMT AMZN AVGO AXP BA BAC BK BKNG BLK BMY BRK-B C CAT " +
  "CHTR CL CMCSA COF COP COST CRM CSCO CVS CVX DE DHR DIS DUK EMR F FDX GD GE GILD GM GOOGL GS HD HON IBM INTC " +
  "INTU ISRG JNJ JPM KO LIN LLY LMT LOW MA MCD MDLZ MDT MET META MMM MO MRK MS MSFT NEE NFLX NKE NOW NVDA ORCL " +
  "PEP PFE PG PLTR PM PYPL QCOM RTX SBUX SCHW SO SPG T TGT TMO TMUS TSLA TXN UBER UNH UNP UPS USB V VZ WFC WMT XOM").split(" "));

// The rest of the S&P 500 (the site's S&P 500 option), rewritten by engine/sp500_list.py each month.
// After it changes, paste this Worker again (worker/README.md).
// SP500-START
const SP500 = new Set("A AAPL ABBV ABNB ABT ACGL ACN ADBE ADI ADM ADP ADSK AEE AEP AES AFL AIG AIZ AJG AKAM ALB ALGN ALL ALLE AMAT AMCR AMD AME AMGN AMP AMT AMZN ANET AON AOS APA APD APH APO APP APTV ARE ARES ATO AVGO AVY AWK AXON AXP AZO BA BAC BALL BAX BBY BDX BE BEN BF-B BG BIIB BK BKNG BKR BLK BMY BNY BR BRK-B BRO BSX BX BXP C CAH CARR CASY CAT CB CBOE CBRE CCI CCL CDNS CDW CEG CF CFG CHD CHRW CHTR CI CIEN CINF CL CLX CMCSA CME CMG CMI CMS CNC CNP COF COHR COIN COO COP COR COST CPAY CPRT CPT CRH CRL CRM CRWD CSCO CSGP CSX CTAS CTSH CVNA CVS CVX D DAL DASH DD DDOG DE DECK DELL DG DGX DHI DHR DIS DLR DLTR DOC DOV DOW DPZ DRI DTE DUK DVA DVN DXCM EBAY ECHO ECL ED EFX EG EIX EL ELV EME EMR EOG EQIX EQT ERIE ES ESS ETN ETR EVRG EW EXC EXE EXPD EXPE EXR F FANG FAST FCX FDS FDX FDXF FE FERG FFIV FICO FIS FISV FITB FIX FLEX FOX FOXA FRT FSLR FTNT FTV GD GDDY GE GEHC GEN GEV GILD GIS GL GLW GM GNRC GOOG GOOGL GPC GPN GRMN GS GWW HAL HAS HBAN HCA HD HIG HII HLT HON HONA HOOD HPE HPQ HRL HSIC HST HSY HUBB HUM HWM IBKR IBM ICE IDXX IEX IFF ILMN INCY INTC INTU INVH IP IQV IR IRM ISRG IT ITW IVZ J JBHT JBL JCI JKHY JNJ JPM KDP KEY KEYS KHC KIM KKR KLAC KMB KMI KO KR L LDOS LEN LH LHX LII LIN LITE LLY LMT LNT LOW LRCX LULU LUV LVS LYB LYV MA MAA MAR MAS MCD MCHP MCK MCO MDLZ MDT MET META MGM MKC MLM MMM MNST MO MOS MPC MPWR MRK MRNA MRSH MRVL MS MSCI MSFT MSI MTB MTD MU NCLH NDAQ NDSN NEE NEM NFLX NI NKE NOC NOW NRG NSC NTAP NTRS NUE NVDA NVR NWS NWSA NXPI O ODFL OKE OKTA OMC ON ORCL ORLY OTIS OXY P PANW PAYX PCAR PCG PEG PEP PFE PFG PG PGR PH PHM PKG PLD PLTR PM PNC PNR PNW PODD PPG PPL PRU PSA PSX PTC PWR PYPL Q QCOM RCL RDDT REG REGN RF RJF RL RMD ROK ROL ROP ROST RSG RTX RVTY SBAC SBUX SCHW SHW SJM SKYD SLB SMCI SNA SNDK SNPS SO SOLV SPG SPGI SRE STE STLD STT STX STZ SW SWK SWKS SYF SYK SYY T TDG TDY TECH TEL TER TFC TGT TJX TKO TMO TMUS TPL TPR TRGP TRMB TROW TRV TSCO TSLA TSN TT TTWO TWLO TXN TXT TYL UAL UBER UDR UHS ULTA UNH UNP UPS URI USB V VEEV VICI VLO VLTO VMC VMRK VRSK VRSN VRT VRTX VST VTR VTRS VYLR VZ WAB WAT WDAY WDC WEC WELL WFC WM WMB WMT WRB WSM WST WTW WY WYNN XEL XOM XYL XYZ YUM ZBH ZBRA ZTS".split(" "));
// SP500-END
const isStock = sym => TICKERS.has(sym) || SP500.has(sym);

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

function shape(bars, kind, ext) {
  bars = bars.map(b => [nyWall(b.t), b.o, b.h, b.l, b.c, b.v ?? 0]).sort((a, b) => a[0] - b[0]);
  const out = [];
  if (kind === "session") {
    let prev = null;
    for (const b of bars) {
      if (b[0] === prev) continue;                         // duplicate timestamp: keep the first
      prev = b[0];
      const m = Math.floor((b[0] % 86400) / 60);
      if (ext ? m >= 240 && m < 1200 : m >= 570 && m < 960) out.push(b);   // 9:30-16:00 New York (extended: 4:00-20:00)
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
  return rows.map(k => [Math.floor(k[0] / 1000), +k[1], +k[2], +k[3], +k[4], +k[5]]);   // + coin volume
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
  return js.result[key].map(k => [+k[0], +k[1], +k[2], +k[3], +k[4], +k[6]]);   // + coin volume
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

const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type" };
const json = (body, status = 200, extra = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...cors, ...extra } });

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });
    const url = new URL(request.url);
    if (url.searchParams.has("options")) return options(url.searchParams.get("options"), env, ctx);
    if (url.searchParams.has("vp")) return volumeBars(url.searchParams, env, ctx);
    if (url.searchParams.has("quotes")) return quotes(url.searchParams.get("quotes"), env, ctx);
    if (url.searchParams.has("px")) return priceAlerts(request, url, env);
    if (url.searchParams.has("volscan")) return volScan(url.searchParams.get("volscan"), env, ctx);
    if (url.searchParams.has("grid")) return gridBars(url.searchParams, env, ctx);
    if (url.searchParams.has("replay")) return replayBars(url.searchParams, env, ctx);
    let sym = (url.searchParams.get("symbol") || "").trim().toUpperCase().replace(/\./g, "-");
    if (["SPX", "^SPX", "^GSPC"].includes(sym)) sym = "SPY";       // index: no Alpaca data
    const coin = isStock(sym) ? null : coinOf(sym);
    if (coin) return crypto(coin, ctx);
    if (!isStock(sym)) return json({ error: `${sym || "No ticker"}: only S&P 500 stocks, SPY and the app's 19 coins are available.` }, 400);
    if (!env.ALPACA_KEY_ID || !env.ALPACA_SECRET_KEY) return json({ error: "The Alpaca keys are not set on the Worker." }, 500);

    // same request within a minute: answer from the cache (older candles are cached separately for the day)
    const cache = typeof caches !== "undefined" ? caches.default : null;
    const ext = url.searchParams.get("hours") === "all";
    const key = new Request(`https://trendlines.cache/${sym}${ext ? "/ext" : ""}`);
    if (cache) { const hit = await cache.match(key); if (hit) return hit; }

    const end = Date.now() - 16 * 60e3;                    // free plan: SIP data 15+ minutes old
    const alpacaSym = sym.replace(/-/g, ".");              // BRK-B -> BRK.B
    try {
      const names = Object.keys(SERIES);
      const got = await Promise.all(names.map(k => fetchSeriesFast(alpacaSym, SERIES[k][0], SERIES[k][1], end, env, cache)));
      const frames = {};
      names.forEach((k, i) => {
        frames[k] = shape(got[i], SERIES[k][2], ext);
      });
      if (!frames.D.length) return json({ error: `No data for ${sym}.` }, 404);
      const res = json({ symbol: sym, end: new Date(end).toISOString(), hours: ext ? "all" : "rth", frames }, 200, { "Cache-Control": "max-age=60" });
      if (cache) ctx?.waitUntil?.(cache.put(key, res.clone()));
      return res;
    } catch (e) {
      return json({ error: String(e.message || e) }, 502);
    }
  },
  // Cron Trigger: check the price alerts
  async scheduled(event, env, ctx) {
    ctx.waitUntil(checkPriceAlerts(env));
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
  if (!isStock(sym)) return json({ error: `${sym || "No ticker"}: options are available for S&P 500 stocks and SPY.` }, 400);
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
  const coin = isStock(sym) ? null : coinOf(sym);
  if (!coin && !isStock(sym)) return json({ error: `${sym || "No ticker"}: only S&P 500 stocks, SPY and the app's 19 coins are available.` }, 400);
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
// ---------------------------------------------------------------- volume screener (VOLS)
// several stocks in one Alpaca request: { "AAPL": [bars], ... } (keys with "-" as in the app)
async function fetchMulti(syms, tf, start, end, env) {
  const fmt = d => new Date(d).toISOString().replace(/\.\d{3}Z$/, "Z");
  const params = new URLSearchParams({ symbols: syms.map(x => x.replace(/-/g, ".")).join(","), timeframe: tf, start: fmt(start),
    end: fmt(end), limit: "10000", adjustment: "all", feed: "sip" });
  const headers = { "APCA-API-KEY-ID": env.ALPACA_KEY_ID, "APCA-API-SECRET-KEY": env.ALPACA_SECRET_KEY };
  const out = {};
  for (let page = 0; page < 40; page++) {
    let r;
    for (let tries = 0; tries < 5; tries++) {
      r = await fetch(`${ALPACA}?${params}`, { headers });
      if (r.status !== 429) break;
      await new Promise(ok => setTimeout(ok, 1500 * (tries + 1)));
    }
    if (!r.ok) throw new Error(`Alpaca ${tf}: HTTP ${r.status} ${(await r.text()).slice(0, 200)}`);
    const js = await r.json();
    for (const [k, v] of Object.entries(js.bars || {})) (out[k.replace(/\./g, "-")] = out[k.replace(/\./g, "-")] || []).push(...v);
    if (!js.next_page_token) break;
    params.set("page_token", js.next_page_token);
  }
  return out;
}

async function volScan(list, env, ctx) {
  if (!env.ALPACA_KEY_ID || !env.ALPACA_SECRET_KEY) return json({ error: "The Alpaca keys are not set on the Worker." }, 500);
  const which = list === "sp500" ? "sp500" : "sp100";
  const cache = typeof caches !== "undefined" ? caches.default : null;
  const key = new Request(`https://trendlines.cache/volscan/${which}`);
  if (cache) { const hit = await cache.match(key).catch(() => null); if (hit) return hit; }
  try {
    const syms = [...new Set([...TICKERS, ...(which === "sp500" ? SP500 : [])])].filter(x => x !== "SPY");
    const end = Date.now() - 16 * 60e3, start = end - 60 * DAY;
    const chunks = []; for (let i = 0; i < syms.length; i += 100) chunks.push(syms.slice(i, i + 100));
    const all = {};
    for (let i = 0; i < chunks.length; i += 3) {
      const got = await Promise.all(chunks.slice(i, i + 3).map(c => fetchMulti(c, "1Day", start, end, env)));
      got.forEach(g => Object.assign(all, g));
    }
    // is the last bar today's unfinished session?
    const nowNy = nyWall(new Date(end).toISOString()), today = new Date(nowNy * 1000).toISOString().slice(0, 10);
    const mins = Math.floor((nowNy % 86400) / 60), open = mins >= 570 && mins < 960;
    const pace = open ? Math.min(1, Math.max(0.04, (mins - 570) / 390)) : 1;
    const avg = xs => xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
    const rows = {};
    for (const [k, bars] of Object.entries(all)) {
      if (!bars.length) continue;
      const last = bars[bars.length - 1], d = new Date(nyWall(last.t) * 1000).toISOString().slice(0, 10);
      const partial = open && d === today;
      const done = partial ? bars.slice(0, -1) : bars;
      const prev = bars.length > 1 ? bars[bars.length - 2].c : null;
      const base = done.slice(partial ? -10 : -11, partial ? undefined : -1);   // the 10 sessions before the latest
      const a10 = avg(done.slice(-10).map(b => b.v)), a30 = avg(done.slice(-30).map(b => b.v));
      const d10 = avg(done.slice(-10).map(b => b.v * b.c)), d30 = avg(done.slice(-30).map(b => b.v * b.c));
      const ref = avg(base.map(b => b.v));
      const rel = ref ? last.v / (ref * (partial ? pace : 1)) : null;
      rows[k] = [short(last.c), prev == null ? null : short(prev), prev ? +((last.c / prev - 1) * 100).toFixed(2) : null, last.v,
        a10 == null ? null : Math.round(a10), a30 == null ? null : Math.round(a30), d10 == null ? null : Math.round(d10),
        d30 == null ? null : Math.round(d30), rel == null ? null : +rel.toFixed(2), d];
    }
    const res = json({ list: which, end: new Date(end).toISOString(), partial: open, rows }, 200, { "Cache-Control": "max-age=300" });
    if (cache) ctx?.waitUntil?.(cache.put(key, res.clone()));
    return res;
  } catch (e) {
    return json({ error: String(e.message || e) }, 502);
  }
}

// tf -> [Alpaca timeframe, calendar days to ask for, regular-hours only, sessions/bars to keep (0 = all)]
const GRID_TF = { "1D": ["5Min", 7, true, 1], "5D": ["15Min", 12, true, 5], "1M": ["30Min", 38, true, 22],
  "3M": ["1Day", 100, false, 63], "6M": ["1Day", 190, false, 126], "YTD": ["1Day", 0, false, 0], "1Y": ["1Day", 370, false, 252], "5Y": ["1Week", 1835, false, 0] };
async function gridBars(q, env, ctx) {
  if (!env.ALPACA_KEY_ID || !env.ALPACA_SECRET_KEY) return json({ error: "The Alpaca keys are not set on the Worker." }, 500);
  const tf = GRID_TF[q.get("tf")] ? q.get("tf") : "5D";
  const syms = [...new Set((q.get("grid") || "").toUpperCase().replace(/\./g, "-").split(",").map(x => x.trim()).filter(isStock))].sort().slice(0, 30);
  if (!syms.length) return json({ error: "No S&P 500 tickers given." }, 400);
  const cache = typeof caches !== "undefined" ? caches.default : null;
  const key = new Request(`https://trendlines.cache/grid/${tf}/${syms.join(",")}`);
  if (cache) { const hit = await cache.match(key).catch(() => null); if (hit) return hit; }
  const [atf, days, rth, keep] = GRID_TF[tf];
  try {
    const end = Date.now() - 16 * 60e3;
    const start = days ? end - days * DAY : Date.UTC(new Date(end).getUTCFullYear(), 0, 1) - 5 * DAY;
    const raw = await fetchMulti(syms, atf, start, end, env);
    const year = String(new Date(end).getUTCFullYear());
    const bars = {};
    for (const s of syms) {
      let b = (raw[s] || []).map(x => [nyWall(x.t), short(x.o), short(x.h), short(x.l), short(x.c), x.v]);
      if (rth) {
        b = b.filter(x => { const m = Math.floor((x[0] % 86400) / 60); return m >= 570 && m < 960; });
        const day = t => new Date(t * 1000).toISOString().slice(0, 10);
        const ses = [...new Set(b.map(x => day(x[0])))].slice(-keep);
        b = b.filter(x => day(x[0]) >= ses[0]);
      } else if (tf === "YTD") b = b.filter(x => new Date(x[0] * 1000).toISOString().slice(0, 4) === year);
      else if (keep) b = b.slice(-keep);
      bars[s] = b;
    }
    const res = json({ tf, end: new Date(end).toISOString(), bars }, 200, { "Cache-Control": `max-age=${rth ? 60 : 600}` });
    if (cache) ctx?.waitUntil?.(cache.put(key, res.clone()));
    return res;
  } catch (e) {
    return json({ error: String(e.message || e) }, 502);
  }
}

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

// ------------------------------------------------------------------ live quotes
async function stockQuotes(syms, env) {
  const headers = { "APCA-API-KEY-ID": env.ALPACA_KEY_ID, "APCA-API-SECRET-KEY": env.ALPACA_SECRET_KEY };
  const list = syms.map(s => s.replace(/-/g, "."));
  const out = {};
  for (let i = 0; i < list.length; i += 100) {
    const r = await fetch(`https://data.alpaca.markets/v2/stocks/snapshots?symbols=${encodeURIComponent(list.slice(i, i + 100).join(","))}&feed=iex`, { headers });
    if (!r.ok) throw new Error(`Alpaca ${r.status}`);
    const d = await r.json();
    for (const [k, v] of Object.entries(d || {})) {
      if (!v) continue;
      const p = v.latestTrade?.p ?? v.minuteBar?.c ?? v.dailyBar?.c, prev = v.prevDailyBar?.c;
      if (p == null) continue;
      out[k.replace(/\./g, "-")] = [p, prev ?? null, prev ? +((p / prev - 1) * 100).toFixed(2) : null, v.latestTrade?.t || v.minuteBar?.t || null];
    }
  }
  return out;
}
async function coinQuotes(coins) {
  const out = {};
  const bin = coins.filter(c => c !== "XMR");
  try {
    const r = await fetch(`https://data-api.binance.vision/api/v3/ticker/24hr?symbols=${encodeURIComponent(JSON.stringify(bin.map(c => c + "USDT")))}`);
    if (r.ok) for (const x of await r.json()) {
      const c = x.symbol.replace(/USDT$/, ""), p = +x.lastPrice, o = +x.openPrice;
      out[`${c}-USD`] = [p, o, o ? +((p / o - 1) * 100).toFixed(2) : null, new Date(x.closeTime).toISOString()];
    }
  } catch {}
  const missing = coins.filter(c => !out[`${c}-USD`]);
  if (missing.length) {
    try {
      const pairs = missing.map(c => (KRAKEN_NAME[c] || c) + "USD");
      const r = await fetch(`https://api.kraken.com/0/public/Ticker?pair=${pairs.join(",")}`);
      const d = await r.json();
      for (const c of missing) {
        const k = Object.keys(d.result || {}).find(k => k.includes(KRAKEN_NAME[c] || c));
        if (!k) continue;
        const p = +d.result[k].c[0], o = +d.result[k].o;
        out[`${c}-USD`] = [p, o, o ? +((p / o - 1) * 100).toFixed(2) : null, new Date().toISOString()];
      }
    } catch {}
  }
  return out;
}
async function quotes(kind, env, ctx) {
  kind = kind === "crypto" ? "crypto" : kind === "sp500" ? "sp500" : "stocks";   // ?quotes=sp500: every S&P 500 stock
  const cache = typeof caches !== "undefined" ? caches.default : null;
  const key = new Request(`https://trendlines.cache/quotes-${kind}`);
  if (cache) { const hit = await cache.match(key); if (hit) return hit; }
  try {
    if (kind !== "crypto" && (!env.ALPACA_KEY_ID || !env.ALPACA_SECRET_KEY)) return json({ error: "The Alpaca keys are not set on the Worker." }, 500);
    const q = kind === "crypto" ? await coinQuotes([...COINS]) : await stockQuotes(kind === "sp500" ? [...new Set([...TICKERS, ...SP500])] : [...TICKERS], env);
    const res = json({ source: kind === "crypto" ? "Binance (Kraken for XMR)" : "Alpaca, IEX real-time trades", at: new Date().toISOString(), q }, 200, { "Cache-Control": "max-age=15" });
    if (cache) ctx?.waitUntil?.(cache.put(key, res.clone()));
    return res;
  } catch (e) {
    return json({ error: String(e.message || e) }, 502);
  }
}

// ------------------------------------------------------------------ price alerts
const PX_KEY = "alerts", PX_MAX = 2000, PX_PER_TOPIC = 40;
const topicOk = t => typeof t === "string" && /^[a-z0-9_-]{12,64}$/.test(t);
async function pxLoad(env) { try { return JSON.parse(await env.PX.get(PX_KEY) || "[]"); } catch { return []; } }
async function priceAlerts(request, url, env) {
  if (!env.PX) return json({ error: "Price alerts aren't set up on the Worker yet (no PX storage)." }, 501);
  const op = url.searchParams.get("px");
  if (op === "list") {
    const topic = url.searchParams.get("topic");
    if (!topicOk(topic)) return json({ error: "bad channel" }, 400);
    return json({ alerts: (await pxLoad(env)).filter(a => a.topic === topic) });
  }
  if (request.method !== "POST") return json({ error: "use POST" }, 405);
  let b; try { b = await request.json(); } catch { return json({ error: "bad body" }, 400); }
  if (!topicOk(b.topic)) return json({ error: "bad channel" }, 400);
  const all = await pxLoad(env);
  if (op === "del") {
    const left = all.filter(a => !(a.topic === b.topic && (b.id === "*" || a.id === b.id)));
    await env.PX.put(PX_KEY, JSON.stringify(left));
    return json({ ok: true });
  }
  if (op === "add") {
    let sym = String(b.sym || "").toUpperCase().replace(/\./g, "-");
    const coin = isStock(sym) ? null : coinOf(sym);
    if (coin) sym = `${coin}-USD`;
    const level = +b.level;
    if (!isStock(sym) && !coin) return json({ error: "Only S&P 500 stocks and the app's coins." }, 400);
    if (!(level > 0)) return json({ error: "bad level" }, 400);
    if (all.length >= PX_MAX) return json({ error: "Too many alerts on the server right now." }, 429);
    if (all.filter(a => a.topic === b.topic).length >= PX_PER_TOPIC) return json({ error: `Up to ${PX_PER_TOPIC} alerts per channel.` }, 429);
    const a = { id: Math.random().toString(36).slice(2, 10), topic: b.topic, sym, level, dir: b.dir === "below" ? "below" : "above",
                note: String(b.note || "").slice(0, 80), created: new Date().toISOString() };
    all.push(a);
    await env.PX.put(PX_KEY, JSON.stringify(all));
    return json({ ok: true, alert: a });
  }
  return json({ error: "unknown" }, 400);
}
async function checkPriceAlerts(env) {
  if (!env.PX) return;
  const all = await pxLoad(env);
  if (!all.length) return;
  const stocks = [...new Set(all.filter(a => !a.sym.endsWith("-USD")).map(a => a.sym))];
  const coins = [...new Set(all.filter(a => a.sym.endsWith("-USD")).map(a => a.sym.replace(/-USD$/, "")))];
  const px = {};
  try { if (stocks.length && env.ALPACA_KEY_ID) Object.assign(px, await stockQuotes(stocks, env)); } catch {}
  try { if (coins.length) Object.assign(px, await coinQuotes(coins)); } catch {}
  const keep = [];
  for (const a of all) {
    const p = px[a.sym]?.[0];
    const hit = p != null && (a.dir === "above" ? p >= a.level : p <= a.level);
    if (!hit) { keep.push(a); continue; }
    const name = a.sym.replace(/-USD$/, "");
    try {
      await fetch(`https://ntfy.sh/${a.topic}`, { method: "POST", body: `${name} is ${a.dir} ${a.level}: last ${p}${a.note ? ` · ${a.note}` : ""}`,
        headers: { Title: `${name} ${a.dir === "above" ? "▲" : "▼"} ${a.level}`, Tags: a.dir === "above" ? "chart_with_upwards_trend" : "chart_with_downwards_trend",
                   Click: `https://thejackwar.github.io/signal-desk/#terminal-${a.sym}` } });
    } catch { keep.push(a); }
  }
  if (keep.length !== all.length) await env.PX.put(PX_KEY, JSON.stringify(keep));
}

// ---------------------------------------------------------------- bar replay (docs/replay.html)
// tf: [days of history before start, days after start]; sized to stay under one Alpaca page (10,000 bars)
const REPLAY = { "1Day": [400, 3650], "1Hour": [30, 365], "15Min": [8, 90], "5Min": [4, 30] };
async function replayBars(q, env, ctx) {
  let sym = (q.get("replay") || "").trim().toUpperCase().replace(/\./g, "-");
  if (["SPX", "^SPX", "^GSPC"].includes(sym)) sym = "SPY";
  const coin = isStock(sym) ? null : coinOf(sym);
  if (coin && coin !== "BTC") return json({ error: `${coin}: only bitcoin (BTC) is available for crypto so far.` }, 400);
  if (!coin && !isStock(sym)) return json({ error: `${sym || "No ticker"}: only S&P 500 stocks, SPY and BTC are available.` }, 400);
  if (!coin && (!env.ALPACA_KEY_ID || !env.ALPACA_SECRET_KEY)) return json({ error: "The Alpaca keys are not set on the Worker." }, 500);
  const tf = REPLAY[q.get("tf")] ? q.get("tf") : "1Day";
  const day = (q.get("start") || "").slice(0, 10);
  const start = Date.parse(day + "T00:00:00Z");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !isFinite(start)) return json({ error: "Add a start date (start=YYYY-MM-DD)." }, 400);
  const now = Date.now() - 16 * 60e3;                      // free plan: SIP data 15+ minutes old
  if (start > now) return json({ error: "The start date is in the future." }, 400);
  const [back, fwd] = REPLAY[tf];
  const from = Math.max(Date.UTC(2016, 0, 1), start - back * DAY), to = Math.min(now, start + fwd * DAY);
  const past = to < now - DAY;
  const cache = typeof caches !== "undefined" ? caches.default : null;
  const key = new Request(`https://trendlines.cache/replay/${coin ? coin + "-USD" : sym}/${tf}/${day}${past ? "" : "/" + Math.floor(now / 60e3)}`);
  if (cache) { const hit = await cache.match(key).catch(() => null); if (hit) return hit; }
  if (coin) {
    // Binance coin/USDT, around the clock, times in UTC (Binance history starts in August 2017)
    try {
      const iv = { "1Day": "1d", "1Hour": "1h", "15Min": "15m", "5Min": "5m" }[tf];
      const bars = await binanceRange(coin, iv, Math.max(from, Date.UTC(2017, 7, 17)), Math.min(to, Date.now()));
      if (bars.length < 5) return json({ error: `No ${tf} candles for ${coin} around ${day}.` }, 404);
      const res = json({ symbol: `${coin}-USD`, market: "crypto", tf, start: day, end: new Date(to).toISOString(), source: `Binance ${coin}/USDT`, bars },
        200, { "Cache-Control": `max-age=${past ? 86400 : 60}` });
      if (cache) ctx?.waitUntil?.(cache.put(key, res.clone()));
      return res;
    } catch (e) {
      return json({ error: String(e.message || e) }, 502);
    }
  }
  try {
    const raw = await fetchSeries(sym.replace(/-/g, "."), tf, from, to, env);
    const intraday = tf !== "1Day";
    const bars = [];
    for (const b of raw) {
      if (intraday) { const m = Math.floor((nyWall(b.t) % 86400) / 60); if (m < 240 || m >= 1200) continue; }
      bars.push([Math.floor(Date.parse(b.t) / 1000), b.o, b.h, b.l, b.c, b.v]);
    }
    if (bars.length < 5) return json({ error: `No ${tf} candles for ${sym} around ${day}.` }, 404);
    const res = json({ symbol: sym, tf, start: day, end: new Date(to).toISOString(), source: "Alpaca (all US exchanges, SIP)", bars },
      200, { "Cache-Control": `max-age=${past ? 86400 : 60}` });
    if (cache) ctx?.waitUntil?.(cache.put(key, res.clone()));
    return res;
  } catch (e) {
    return json({ error: String(e.message || e) }, 502);
  }
}

// Binance candles between two times (ms), oldest first, 1000 per request: [t (s), o, h, l, c, coin volume]
async function binanceRange(coin, interval, from, to) {
  const rows = [];
  let start = from;
  for (let page = 0; page < 40 && start < to; page++) {
    const q = new URLSearchParams({ symbol: `${coin}USDT`, interval, startTime: String(start), endTime: String(to), limit: "1000" });
    const js = await getJson(`${BINANCE}?${q}`, `Binance ${interval}`);
    if (!Array.isArray(js) || !js.length) break;
    rows.push(...js);
    if (js.length < 1000) break;
    start = js[js.length - 1][0] + 1;
  }
  return rows.map(k => [Math.floor(k[0] / 1000), +k[1], +k[2], +k[3], +k[4], +k[5]]);
}
