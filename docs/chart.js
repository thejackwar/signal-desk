// Chart page (docs/chart.html): candles plus whichever technical analysis tools the visitor taps.
// Maths: chart-lib.js. Trendlines and support/resistance: trendlines.js (same rules as the Trendlines page).
// Data: the Trendlines Worker (stocks, crypto fallback), Binance from the browser (crypto),
// the site's nightly files (commodities, and stocks if the Worker can't be reached).
// Hash: #AAPL/D. Choices are remembered on the device (localStorage "signaldesk.chart").
import * as L from "./chart-lib.js?v=1";
import { TFS, analyze, srLevels, segments, binanceCrypto } from "./trendlines.js?v=3";
import { createDrawLayer, TOOLS, TOOL } from "./draw.js?v=2";
import { chartPatterns, majorLevels, tradeLevels } from "./chart-patterns.js?v=4";

const LW = window.LightweightCharts;
const DEFAULT_API = "https://trendlines.guerragiacomo00.workers.dev";
const API = (() => {
  try { return (JSON.parse(localStorage.getItem("signaldesk.trendlines")) || {}).api || DEFAULT_API; } catch { return DEFAULT_API; }
})();
const TICKERS = ("SPY AAPL ABBV ABT ACN ADBE AIG AMD AMGN AMT AMZN AVGO AXP BA BAC BK BKNG BLK BMY BRK-B C CAT " +
  "CHTR CL CMCSA COF COP COST CRM CSCO CVS CVX DE DHR DIS DUK EMR F FDX GD GE GILD GM GOOGL GS HD HON IBM INTC " +
  "INTU ISRG JNJ JPM KO LIN LLY LMT LOW MA MCD MDLZ MDT MET META MMM MO MRK MS MSFT NEE NFLX NKE NOW NVDA ORCL " +
  "PEP PFE PG PLTR PM PYPL QCOM RTX SBUX SCHW SO SPG T TGT TMO TMUS TSLA TXN UBER UNH UNP UPS USB V VZ WFC WMT XOM").split(" ");
const SP100 = new Set(TICKERS);   // the S&P 100 + SPY; the rest of the S&P 500 is added from data/universe.json
const COINS = "BTC ETH XRP BNB SOL DOGE TRX ADA LINK XLM BCH AVAX LTC HBAR DOT XMR ETC ATOM ALGO".split(" ");
const COMMOD = { "GC=F": "Gold", "SI=F": "Silver", "PL=F": "Platinum", "PA=F": "Palladium", "HG=F": "Copper", "CL=F": "WTI crude oil",
  "BZ=F": "Brent crude oil", "NG=F": "Natural gas", "RB=F": "Gasoline", "HO=F": "Heating oil", "ZC=F": "Corn", "ZW=F": "Wheat",
  "ZS=F": "Soybeans", "KC=F": "Coffee", "CC=F": "Cocoa", "SB=F": "Sugar", "CT=F": "Cotton", "LE=F": "Live cattle", "HE=F": "Lean hogs" };
const COMMOD_ALIAS = { GOLD: "GC=F", XAU: "GC=F", XAUUSD: "GC=F", SILVER: "SI=F", XAG: "SI=F", XAGUSD: "SI=F", OIL: "CL=F", WTI: "CL=F", CRUDE: "CL=F",
  BRENT: "BZ=F", GAS: "NG=F", NATGAS: "NG=F", COPPER: "HG=F", PLATINUM: "PL=F", PALLADIUM: "PA=F", CORN: "ZC=F", WHEAT: "ZW=F",
  SOY: "ZS=F", SOYBEANS: "ZS=F", COFFEE: "KC=F", COCOA: "CC=F", SUGAR: "SB=F", COTTON: "CT=F", GASOLINE: "RB=F" };
const QUICK = ["SPY", "AAPL", "NVDA", "TSLA", "BTC-USD", "ETH-USD", "GC=F", "CL=F"];
const TF_LIST = ["W", "D", "4H", "1H", "30m", "15m", "5m", "1m"];
const TF_TXT = { W: "Weekly", D: "Daily", "4H": "4 hours", "1H": "1 hour", "30m": "30 min", "15m": "15 min", "5m": "5 min", "1m": "1 min" };
const STEP = { W: 7 * 86400, D: 86400, "4H": 14400, "1H": 3600, "30m": 1800, "15m": 900, "5m": 300, "1m": 60 };
const SHOW = { W: 160, D: 180, "4H": 180, "1H": 200, "30m": 200, "15m": 200, "5m": 220, "1m": 240 };
const PREF_KEY = "signaldesk.chart";

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const dp = p => Math.abs(p) >= 1000 ? 2 : Math.abs(p) >= 1 ? 2 : Math.abs(p) >= 0.01 ? 4 : 6;
const fmt = (p, d) => p === null || p === undefined || !isFinite(p) ? "–" : (+p).toLocaleString("en-US", { minimumFractionDigits: d ?? dp(p), maximumFractionDigits: d ?? dp(p) });
const pctTxt = x => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x * 100).toFixed(2)}%`;
const big = x => x === null || x === undefined ? "–" : Math.abs(x) >= 1e9 ? `${(x / 1e9).toFixed(2)}B` : Math.abs(x) >= 1e6 ? `${(x / 1e6).toFixed(2)}M` : Math.abs(x) >= 1e3 ? `${(x / 1e3).toFixed(1)}K` : `${Math.round(x)}`;

// ------------------------------------------------------------------ colours
const COL = { sky: "#38bdf8", green: "#4ade80", amber: "#f59e0b", violet: "#c084fc", pink: "#f472b6", orange: "#fb923c",
  teal: "#2dd4bf", white: "#e6edf3", red: "#f87171", blue: "#60a5fa", lime: "#a3e635", gold: "#facc15", grey: "#8b98a5",
  cup: "#089981", cdn: "#f23645" };   // candles in TradingView's colours
const alpha = (hex, a) => hex + Math.round(a * 255).toString(16).padStart(2, "0");

// ------------------------------------------------------------------ the tools
// kind: overlay (on the candles), pane (own panel below), levels (lines/marks on the candles).
// p: settings [key, label, default, {min, max, step} | [options]]. vol: needs volume. intra: intraday only.
const MA_TYPES = [["SMA", "Simple (SMA)"], ["EMA", "Exponential (EMA)"]];
const DEFS = [
  { id: "ma1", grp: "Averages", name: p => `${p.type} ${p.n}`, kind: "overlay", c: COL.sky, p: [["n", "Length", 20, { min: 2, max: 400 }], ["type", "Type", "EMA", MA_TYPES]],
    info: "The average closing price over the last candles. Price above a rising average = uptrend; crossings of a fast and slow average are classic trend signals." },
  { id: "ma2", grp: "Averages", name: p => `${p.type} ${p.n}`, kind: "overlay", c: COL.amber, p: [["n", "Length", 50, { min: 2, max: 400 }], ["type", "Type", "SMA", MA_TYPES]],
    info: "The 50-candle average is the usual medium-term trend line." },
  { id: "ma3", grp: "Averages", name: p => `${p.type} ${p.n}`, kind: "overlay", c: COL.violet, p: [["n", "Length", 200, { min: 2, max: 400 }], ["type", "Type", "SMA", MA_TYPES]],
    info: "The 200-day average is the line most investors watch: above it is generally called a bull market, below it a bear market." },
  { id: "vwap", grp: "Averages", name: () => "VWAP", kind: "overlay", c: COL.pink, p: [], vol: true, intra: true,
    info: "Volume-weighted average price of the day, restarting each session. Big traders use it as the day's fair price: above it buyers are in control." },

  { id: "bb", grp: "Bands", name: p => `Bollinger ${p.n}, ${p.k}`, kind: "overlay", c: COL.teal, p: [["n", "Length", 20, { min: 2, max: 200 }], ["k", "Width (std. dev.)", 2, { min: 0.5, max: 5, step: 0.1 }]],
    info: "A 20-candle average with bands two standard deviations away. Touching a band means the move is stretched; very narrow bands (a squeeze) often come before a big move." },
  { id: "kc", grp: "Bands", name: p => `Keltner ${p.n}, ${p.m}`, kind: "overlay", c: COL.blue, p: [["n", "Length", 20, { min: 2, max: 200 }], ["m", "Width (× ATR)", 2, { min: 0.5, max: 5, step: 0.1 }]],
    info: "An exponential average with bands a multiple of the average true range away. Smoother than Bollinger; a close outside it shows a strong push." },
  { id: "dc", grp: "Bands", name: p => `Donchian ${p.n}`, kind: "overlay", c: COL.lime, p: [["n", "Length", 20, { min: 2, max: 300 }]],
    info: "The highest high and lowest low of the last candles. A new high above the channel is the Turtle traders' breakout signal." },

  { id: "st", grp: "Trend", name: p => `Supertrend ${p.n}, ${p.m}`, kind: "overlay", c: COL.green, p: [["n", "ATR length", 10, { min: 2, max: 100 }], ["m", "Multiplier", 3, { min: 0.5, max: 10, step: 0.1 }]],
    info: "A trailing line under price in an uptrend (green) and over it in a downtrend (red). It flips when price closes through it." },
  { id: "sar", grp: "Trend", name: () => "Parabolic SAR", kind: "overlay", c: COL.gold, p: [["s", "Step", 0.02, { min: 0.005, max: 0.2, step: 0.005 }], ["mx", "Maximum", 0.2, { min: 0.05, max: 1, step: 0.05 }]],
    info: "Dots that trail price and speed up as the trend goes on. Dots below = uptrend, above = downtrend; many use them as a trailing stop." },
  { id: "ich", grp: "Trend", name: () => "Ichimoku", kind: "overlay", c: COL.red, p: [["a", "Conversion", 9, { min: 2, max: 60 }], ["b", "Base", 26, { min: 2, max: 120 }], ["s", "Span B", 52, { min: 2, max: 240 }]],
    info: "Conversion (fast) and base (slow) lines plus the cloud, projected 26 candles ahead. Price above the cloud = uptrend, below = downtrend, inside = undecided." },

  { id: "tl", grp: "Levels", name: () => "Trendlines", kind: "levels", c: COL.green, p: [["sw", "Swing size", 5, { min: 2, max: 20 }]],
    info: "Drawn automatically from the absolute high and low with the Trendlines page's rules: the up line under the lows, the down line over the highs. ✕ marks a break." },
  { id: "sr", grp: "Levels", name: p => p.mode === "all" ? `Support/resistance ${p.k}` : "Major support/resistance", kind: "levels", c: COL.blue,
    p: [["mode", "Which levels", "major", [["major", "Major only"], ["all", "All on this timeframe"]]], ["k", "Levels each side", 2, { min: 1, max: 6 }], ["sw", "Swing size", 5, { min: 2, max: 20 }]],
    info: "Major only: the strongest levels, found on this timeframe and the bigger ones above it (weekly and daily levels outrank hourly ones) and ranked by touches, timeframes and how recently price respected them. All: every level on this timeframe; the label shows the touches." },
  { id: "cpat", grp: "Levels", name: () => "Chart patterns", kind: "levels", c: COL.violet,
    p: [["max", "Patterns shown", 2, { min: 1, max: 3 }], ["sw", "Swing size", 4, { min: 2, max: 12 }]],
    info: "Double tops/bottoms, head & shoulders, triangles, wedges and bull/bear flags that are forming now (or broke out in the last few candles). Only big, recent, still-valid ones are drawn, best first. Tap a pattern's name in the chart legend to show just that one, zoomed in. The dashed line is the trigger: a pattern only counts once price closes through it." },
  { id: "tlv", grp: "Levels", name: () => "Levels at the trade", kind: "levels", c: COL.sky,
    p: [["which", "Horizontal levels", "all", [["all", "Major and local"], ["major", "Major only"]]], ["lines", "Trendlines", "yes", [["yes", "Show"], ["no", "Hide"]]], ["k", "Major levels each side", 3, { min: 1, max: 6 }]],
    info: "The support and resistance that could stop, slow or cap this trade, as a trader saw them when it opened (only candles that had closed before the entry, so no hindsight). Major: levels from this timeframe and every bigger one (weekly, daily …), solid, thicker when several timeframes agree. Local: this timeframe's recent levels, dashed. Slanted lines: up and down trendlines still intact at the entry, on this timeframe and the next bigger one. Only levels in the trade's path (around the stop and the take profit) are drawn." },
  { id: "fib", grp: "Levels", name: p => `Fibonacci ${p.n}`, kind: "levels", c: COL.gold, p: [["n", "Look back (candles)", 120, { min: 20, max: 1000 }]],
    info: "Retracement levels of the biggest swing in the window. Pullbacks often pause at 38.2%, 50% or 61.8% before the trend resumes." },
  { id: "piv", grp: "Levels", name: () => "Pivot points", kind: "levels", c: COL.violet, p: [],
    info: "Classic floor-trader levels from the previous period's high, low and close (day on intraday charts, week on daily, month on weekly). P is the balance point, R1–R3 resistance, S1–S3 support." },
  { id: "prev", grp: "Levels", name: () => "Previous high/low", kind: "levels", c: COL.white, p: [],
    info: "The previous day's (intraday), week's (daily) or month's (weekly) high and low. A break of either is a common trigger." },
  { id: "pat", grp: "Levels", name: () => "Candle patterns", kind: "levels", c: COL.orange, p: [["doji", "Show dojis", "no", [["no", "No"], ["yes", "Yes"]]]],
    info: "Marks engulfing candles, hammers and shooting stars (and dojis if you want them). They matter most at a support or resistance level." },

  { id: "vol", grp: "Volume", name: p => `Volume${p.ma > 0 ? ` (avg ${p.ma})` : ""}`, kind: "overlay", c: COL.grey, p: [["ma", "Average (0 = off)", 20, { min: 0, max: 200 }]], vol: true,
    info: "How much traded on each candle, green on up candles and red on down ones. Moves on rising volume are more convincing." },
  { id: "obv", grp: "Volume", name: () => "OBV", kind: "pane", c: COL.teal, p: [], vol: true,
    info: "On-balance volume adds volume on up candles and takes it away on down ones. OBV making new highs before price is a classic sign of buying." },

  { id: "rsi", grp: "Oscillators", name: p => `RSI ${p.n}`, kind: "pane", c: COL.violet, p: [["n", "Length", 14, { min: 2, max: 100 }], ["hi", "Overbought", 70, { min: 50, max: 95 }], ["lo", "Oversold", 30, { min: 5, max: 50 }]],
    info: "Relative strength index, 0 to 100. Above 70 is 'overbought', below 30 'oversold'; above 50 means gains have outweighed losses." },
  { id: "macd", grp: "Oscillators", name: p => `MACD ${p.f}, ${p.s}, ${p.g}`, kind: "pane", c: COL.sky, p: [["f", "Fast", 12, { min: 2, max: 100 }], ["s", "Slow", 26, { min: 3, max: 200 }], ["g", "Signal", 9, { min: 2, max: 50 }]],
    info: "The gap between a fast and slow average (blue) and its own average (orange). Blue crossing above orange is a bullish signal; the bars show the gap between them." },
  { id: "stoch", grp: "Oscillators", name: p => `Stochastic ${p.n}, ${p.s}, ${p.d}`, kind: "pane", c: COL.amber, p: [["n", "%K length", 14, { min: 2, max: 100 }], ["s", "%K smoothing", 3, { min: 1, max: 10 }], ["d", "%D length", 3, { min: 1, max: 10 }]],
    info: "Where the close sits in the recent high–low range, 0 to 100. Above 80 overbought, below 20 oversold; %K crossing %D gives the signals." },
  { id: "adx", grp: "Oscillators", name: p => `ADX ${p.n}`, kind: "pane", c: COL.white, p: [["n", "Length", 14, { min: 2, max: 100 }]],
    info: "Trend strength, not direction. ADX above 25 = a real trend; the green (+DI) and red (−DI) lines show which side is winning." },
  { id: "atr", grp: "Oscillators", name: p => `ATR ${p.n}`, kind: "pane", c: COL.orange, p: [["n", "Length", 14, { min: 2, max: 100 }]],
    info: "Average true range: the usual size of a candle. Traders use 1.5–3 × ATR to set a stop loss that ordinary noise won't hit." },
  { id: "cci", grp: "Oscillators", name: p => `CCI ${p.n}`, kind: "pane", c: COL.pink, p: [["n", "Length", 20, { min: 2, max: 200 }]],
    info: "Commodity channel index: how far price is from its average, in units of its usual distance. Above +100 is strong, below −100 weak." },
];
const DEF = Object.fromEntries(DEFS.map(d => [d.id, d]));
const GROUPS = [...new Set(DEFS.map(d => d.grp))];
const PRESETS = [
  ["Clean", []],
  ["Trend", ["ma2", "ma3", "st", "vol"]],
  ["Momentum", ["ma1", "rsi", "macd", "vol"]],
  ["Swing trader", ["ma1", "ma2", "bb", "rsi", "vol"]],
  ["Levels", ["tl", "sr", "cpat", "fib", "prev"]],
];

// ------------------------------------------------------------------ state
function loadPrefs() { try { return JSON.parse(localStorage.getItem(PREF_KEY)) || {}; } catch { return {}; } }
const saved = loadPrefs();
const S = { sym: "SPY", tf: "D", on: Array.isArray(saved.on) ? saved.on.filter(id => DEF[id]) : ["ma2", "ma3", "vol", "rsi", "sr", "cpat"], params: saved.params || {}, open: null,
  tool: "cursor", magnet: saved.magnet !== false, keep: false, hideDr: false };
// prefs v2 (Nov 2026): chart patterns and major support/resistance are on by default, also for people who saved choices before
if (Array.isArray(saved.on) && (saved.pv || 1) < 2) for (const id of ["sr", "cpat"]) if (!S.on.includes(id)) S.on.push(id);
const savePrefs = () => { if (new URLSearchParams(location.search).get("pat")) return; try { localStorage.setItem(PREF_KEY, JSON.stringify({ on: S.on, params: S.params, magnet: S.magnet, pv: 2 })); } catch {} };
if ((saved.pv || 1) < 2) savePrefs();
// Signal view: chart.html?sig=<alert id>&m=<market>[&u=sp500]#TICKER/D draws one alert's trade on the chart.
// embed=1: chart only (the Terminal shows it in a panel next to its own explanation).
const Q = new URLSearchParams(location.search);
// Pattern view: chart.html?pat=<trade id>#BTC-USD/D draws one trade from the BTC pattern backtest
// (docs/data/crypto/btc_patterns.json, engine/btc_patterns.py): the pattern's shape, its stop, its target and how it ended.
const PAT_ID = Q.get("pat");
const SIG_ID = Q.get("sig"), EMBED = Q.get("embed") === "1", SIG_M = ["stocks", "crypto", "commodities"].includes(Q.get("m")) ? Q.get("m") : "stocks";
const SIG_BASE = SIG_M === "crypto" ? "data/crypto/" : SIG_M === "commodities" ? "data/commodities/" : Q.get("u") === "sp500" ? "data/sp500/" : "data/";
let SIG = null;                  // {a: the alert, chart: its chart file, trade: its paper trade, strat: the strategy text}
let DRAWL = null;                // the drawing layer on the price chart
if (EMBED) document.documentElement.classList.add("embed");
const params = id => {
  const out = {};
  for (const [k, , def] of DEF[id].p) out[k] = S.params[id]?.[k] ?? def;
  return out;
};
let DATA = null;                 // {sym, frames, source, note, kind}
let VIEW = null;                 // the drawn charts
let NAMES = {};
let loadId = 0;

// ------------------------------------------------------------------ symbols
function normSym(raw) {
  const s = (raw || "").trim().toUpperCase().replace(/\s+/g, "");
  if (!s) return null;
  if (["SPX", "^SPX", "^GSPC", "S&P500"].includes(s)) return "SPY";
  if (COMMOD[s]) return s;
  if (COMMOD_ALIAS[s.replace(/[/_-]/g, "")]) return COMMOD_ALIAS[s.replace(/[/_-]/g, "")];
  const byName = Object.entries(COMMOD).find(([, n]) => n.toUpperCase().replace(/\s/g, "") === s);
  if (byName) return byName[0];
  const t = s.replace(/\./g, "-");
  if (TICKERS.includes(t)) return t;
  const m = t.replace(/[/_]/g, "-").match(/^([A-Z0-9]+?)(?:-?USDT?)?$/);
  return m && COINS.includes(m[1]) ? `${m[1]}-USD` : null;
}
const kindOf = sym => COMMOD[sym] ? "commodity" : sym.endsWith("-USD") ? "crypto" : "stock";
const label = sym => COMMOD[sym] ? COMMOD[sym] : sym.endsWith("-USD") ? sym.replace("-USD", "") : sym;

async function loadNames() {
  const get = async u => { try { const r = await fetch(u); return r.ok ? (await r.json()).stocks || [] : []; } catch { return []; } };
  const [st, cr, uv] = await Promise.all([get("data/stocks.json"), get("data/crypto/stocks.json"),
    fetch("data/universe.json").then(r => r.ok ? r.json() : null).catch(() => null)]);
  // every S&P 500 stock can be charted (data/universe.json, engine/sp500_list.py)
  for (const t of uv?.sp500 || []) if (!TICKERS.includes(t) && !SP100.has(t)) TICKERS.push(t);
  Object.assign(NAMES, uv?.names || {});
  for (const x of [...st, ...cr]) NAMES[x.ticker] = x.name;
  Object.assign(NAMES, COMMOD);
  $("syms").innerHTML = [...TICKERS, ...COINS.map(c => `${c}-USD`), ...Object.keys(COMMOD)]
    .map(s => `<option value="${esc(COMMOD[s] ? COMMOD[s] : s.replace("-USD", ""))}">${esc(NAMES[s] || "")}</option>`).join("");
  if (DATA) drawTitle();
}

// ------------------------------------------------------------------ data
async function fetchJson(url) {
  const r = await fetch(url);
  const js = await r.json().catch(() => ({}));
  if (!r.ok || js.error) throw new Error(js.error || `HTTP ${r.status}`);
  return js;
}
const worker = sym => fetchJson(`${API.replace(/\/$/, "")}/?symbol=${encodeURIComponent(sym)}`);

async function getData(sym) {
  const kind = kindOf(sym);
  if (kind === "commodity") {
    const js = await fetchJson(`data/commodities/stocks/${encodeURIComponent(sym)}.json`);
    return { sym, kind, frames: L.framesFromDaily(js), source: "Daily futures prices from the site's nightly update (no volume)." };
  }
  if (kind === "crypto") {
    const coin = sym.replace("-USD", "");
    let payload, source;
    try { payload = await binanceCrypto(coin); source = `Binance ${coin}/USDT, volume in ${coin}.`; }
    catch { payload = await worker(sym); source = `${payload.source || "Exchange"} data via the site's data server.`; }
    return { sym, kind, frames: L.buildFrames(payload), source };
  }
  try {
    const payload = await worker(sym);
    return { sym, kind, frames: L.buildFrames(payload), source: "Alpaca, all US exchanges, regular hours, at least 15 minutes old." };
  } catch (e) {
    const folder = SP100.has(sym) ? "data/stocks/" : "data/sp500/stocks/";    // the rest of the S&P 500 has its own folder
    const js = await fetchJson(`${folder}${encodeURIComponent(sym)}.json`).catch(() => { throw new Error(`the data server didn't answer (${e.message})`); });
    return { sym, kind, frames: L.framesFromDaily(js), fallback: true,
      source: `The live data server didn't answer (${e.message}), so this is the site's nightly daily data: daily and weekly only, no volume.` };
  }
}

// 1-minute candles, fetched only when the 1m timeframe is picked: stocks from the data server's
// volume-profile endpoint (Alpaca SIP 1-minute bars, the last 5 sessions, regular hours, 15+ minutes old),
// coins straight from Binance (the last 1,500 minutes).
async function load1m(d) {
  if (!d || d.frames["1m"]?.length || d.kind === "commodity" || d.fallback) return d;
  if (d.kind === "crypto") {
    const coin = d.sym.replace("-USD", ""), rows = [];
    let end = null;
    while (rows.length < 1500) {
      const q = new URLSearchParams({ symbol: `${coin}USDT`, interval: "1m", limit: "1000" });
      if (end) q.set("endTime", String(end));
      const js = await fetchJson(`https://data-api.binance.vision/api/v3/klines?${q}`);
      if (!Array.isArray(js) || !js.length) break;
      rows.unshift(...js); end = js[0][0] - 1;
      if (js.length < 1000) break;
    }
    d.frames["1m"] = rows.map(k => ({ time: Math.floor(k[0] / 1000), open: +k[1], high: +k[2], low: +k[3], close: +k[4], volume: +k[5] }));
  } else {
    const js = await fetchJson(`${API.replace(/\/$/, "")}/?vp=${encodeURIComponent(d.sym)}&range=5&hours=rth`)
      .catch(e => { throw /No ticker/.test(e.message) ? new Error("the data server needs updating (paste worker/trendlines-worker.js into Cloudflare again)") : e; });
    d.frames["1m"] = L.toCandles(js.bars || []);
  }
  return d;
}
const can1m = d => d && (d.kind === "crypto" || (d.kind === "stock" && !d.fallback));

// older Binance candles in front of a frame, so a pattern from years ago is on the chart (one call per 1,000 candles)
async function extendBack(d, tf, fromT) {
  const fr = d.frames[tf], iv = { D: "1d", "4H": "4h", "1H": "1h", W: "1w" }[tf];
  if (!fr?.length || !iv || fr[0].time <= fromT) return;
  const first = fr[0].time, rows = [];
  let t = fromT * 1000, calls = 0;
  while (t < first * 1000 && calls++ < 6) {     // at most 6,000 candles: an old 1-hour pattern gets its own stretch, then a jump to recent candles
    const q = new URLSearchParams({ symbol: `${d.sym.replace("-USD", "")}USDT`, interval: iv, startTime: String(t), limit: "1000" });
    const js = await fetchJson(`https://data-api.binance.vision/api/v3/klines?${q}`);
    if (!Array.isArray(js) || !js.length) break;
    rows.push(...js); t = js[js.length - 1][0] + 1;
    if (js.length < 1000) break;
  }
  const old = rows.map(k => ({ time: Math.floor(k[0] / 1000), open: +k[1], high: +k[2], low: +k[3], close: +k[4], volume: +k[5] })).filter(b => b.time < first);
  d.frames[tf] = [...old, ...fr];
}
async function load(sym) {
  const id = ++loadId;
  S.sym = sym; S.patSel = null;
  if (SIG && sym !== SIG.a.ticker) { SIG = null; drawSigCard(); }
  liveStop();
  writeHash();
  $("sym").value = label(sym);
  $("status").className = "status";
  $("status").textContent = `Loading ${label(sym)}…`;
  try {
    const d = await getData(sym);
    if (id !== loadId) return;
    DATA = d;
    if (SIG?.pat && d.kind === "crypto") {
      $("status").textContent = "Loading older candles for the pattern…";
      // the pattern's timeframe and every bigger one, so the levels at the trade can be found as they stood back then
      const tfs = ["W", "D", "4H", "1H"], upto = tfs.indexOf(SIG.pat.tf);
      for (const t of tfs.slice(0, upto + 1)) { try { await extendBack(d, t, SIG.pat.ts - (t === "W" ? 320 : 420) * STEP[t]); } catch {} if (id !== loadId) return; }
    }
    if (S.tf === "1m" && can1m(DATA)) { try { await load1m(DATA); } catch {} if (id !== loadId) return; }
    if (!DATA.frames[S.tf]?.length) S.tf = "D";
    $("status").textContent = "";
    render(false);
    liveStart();
  } catch (e) {
    if (id !== loadId) return;
    $("status").className = "status err";
    $("status").textContent = `Couldn't load ${label(sym)}: ${e.message || e}`;
  }
}

function readHash() {
  const [sym, tf] = decodeURIComponent(location.hash.slice(1)).split("/");
  const s = normSym(sym);
  if (s) S.sym = s;
  if (TF_LIST.includes(tf)) S.tf = tf;
}
function writeHash() { history.replaceState(null, "", `#${encodeURIComponent(S.sym)}/${S.tf}`); }

// ------------------------------------------------------------------ picker UI
function availability(d, c) {
  if (d.id === "tlv" && !SIG?.pat) return "Only on a pattern trade's chart (open one from Chart Patterns).";
  if (d.intra && STEP[S.tf] >= 86400) return "Intraday charts only (VWAP restarts every day).";
  if (d.vol && c && !L.hasVolume(c)) return DATA?.kind === "commodity" ? "No volume data for commodities yet." : "No volume data for this chart yet.";
  return null;
}

function drawPicker() {
  const c = DATA?.frames[S.tf];
  $("groups").innerHTML = GROUPS.map(g => `<div class="grp"><em>${g}</em><div class="chips">${DEFS.filter(d => d.grp === g && (d.id !== "tlv" || SIG?.pat)).map(d => {
    const on = S.on.includes(d.id), why = availability(d, c), p = params(d.id);
    const cls = `chip${on && !why ? " on" : ""}${why ? " off" : ""}`;
    return `<span class="${cls}" style="--c:${d.c}"><button type="button" data-id="${d.id}" ${why ? `title="${esc(why)}"` : `title="${esc(d.info)}"`} aria-pressed="${on}"><i></i>${esc(d.name(p))}</button>${on && !why && d.p.length ? `<button type="button" class="gear" data-gear="${d.id}" aria-label="Settings for ${esc(d.name(p))}">⚙</button>` : ""}</span>`;
  }).join("")}</div></div>`).join("");
  $("presets").innerHTML = `<span>Quick sets:</span>` + PRESETS.map(([n], i) => `<button type="button" data-preset="${i}">${n}</button>`).join("");
  drawSettings();
}

function drawSettings() {
  const box = $("settings");
  const d = DEF[S.open];
  if (!d || !S.on.includes(d.id)) { box.hidden = true; S.open = null; return; }
  const p = params(d.id);
  box.hidden = false;
  box.innerHTML = `<h4><i style="background:${d.c}"></i>${esc(d.name(p))}</h4><p>${esc(d.info)}</p>` +
    d.p.map(([k, lab, , o]) => Array.isArray(o)
      ? `<label>${esc(lab)}<select data-k="${k}">${o.map(([v, t]) => `<option value="${v}"${String(p[k]) === v ? " selected" : ""}>${esc(t)}</option>`).join("")}</select></label>`
      : `<label>${esc(lab)}<input type="number" inputmode="decimal" data-k="${k}" value="${p[k]}" min="${o.min}" max="${o.max}" step="${o.step || 1}"></label>`).join("") +
    `<div class="acts"><button type="button" data-reset>Defaults</button><button type="button" class="rm" data-rm>Remove</button><button type="button" data-done>Done</button></div>`;
}

$("groups").addEventListener("click", e => {
  const g = e.target.closest("[data-gear]");
  if (g) { S.open = S.open === g.dataset.gear ? null : g.dataset.gear; drawSettings(); return; }
  const b = e.target.closest("[data-id]");
  if (!b) return;
  const d = DEF[b.dataset.id];
  if (availability(d, DATA?.frames[S.tf])) { $("hint").textContent = availability(d, DATA?.frames[S.tf]); setTimeout(() => $("hint").textContent = hintText(), 3500); return; }
  toggle(d.id);
});
function toggle(id, force) {
  const on = S.on.includes(id);
  if (force === undefined ? on : !force) { S.on = S.on.filter(x => x !== id); if (S.open === id) S.open = null; }
  else S.on = [...S.on, id];
  savePrefs(); render(true);
}
$("presets").addEventListener("click", e => {
  const b = e.target.closest("[data-preset]");
  if (!b) return;
  S.on = [...PRESETS[+b.dataset.preset][1]]; S.open = null;
  savePrefs(); render(true);
});
$("settings").addEventListener("change", e => {
  const k = e.target.dataset.k, id = S.open;
  if (!k || !id) return;
  const spec = DEF[id].p.find(x => x[0] === k);
  let v = e.target.value;
  if (!Array.isArray(spec[3])) {
    v = +v;
    if (!isFinite(v)) return;
    v = Math.min(spec[3].max, Math.max(spec[3].min, v));
    if (!spec[3].step || spec[3].step >= 1) v = Math.round(v);
  }
  S.params[id] = { ...(S.params[id] || {}), [k]: v };
  savePrefs(); render(true);
});
$("settings").addEventListener("click", e => {
  const id = S.open;
  if (e.target.closest("[data-done]")) { S.open = null; drawSettings(); }
  else if (e.target.closest("[data-rm]")) toggle(id, false);
  else if (e.target.closest("[data-reset]")) { delete S.params[id]; savePrefs(); render(true); }
});

function drawTfs() {
  $("tfs").innerHTML = [...TF_LIST].reverse().map(t => {   // small to large, as on TradingView
    const ok = DATA && (DATA.frames[t]?.length || (t === "1m" && can1m(DATA)));
    return `<button type="button" data-tf="${t}" aria-pressed="${t === S.tf}"${ok ? "" : " disabled"} title="${TF_TXT[t]}">${t}</button>`;
  }).join("");
}
$("tfs").addEventListener("click", e => {
  const b = e.target.closest("[data-tf]");
  if (!b || b.disabled || b.dataset.tf === S.tf) return;
  if (b.dataset.tf === "1m" && !DATA.frames["1m"]?.length) {
    const d = DATA; b.disabled = true; $("hint").textContent = "Loading 1-minute candles…";
    load1m(d).then(() => { if (DATA !== d) return; $("hint").textContent = ""; S.tf = "1m"; writeHash(); render(false); liveStart(); })
      .catch(err => { $("hint").textContent = `1-minute candles didn't load: ${err.message || err}`; b.disabled = false; });
    return;
  }
  S.tf = b.dataset.tf; S.patSel = null; writeHash(); render(false); if (DATA?.kind === "crypto") liveStart();
});

function drawTitle() {
  const c = DATA.frames[S.tf], last = c[c.length - 1], prev = c[c.length - 2];
  const ch = prev ? last.close / prev.close - 1 : null;
  const name = NAMES[DATA.sym] && NAMES[DATA.sym] !== label(DATA.sym) ? NAMES[DATA.sym] : { stock: "Stock", crypto: "Crypto", commodity: "Commodity" }[DATA.kind];
  $("title").innerHTML = `<small>${esc(name)} · ${TF_TXT[S.tf]}</small>${esc(label(DATA.sym))}<span class="px">${fmt(last.close)}</span>` +
    (ch === null ? "" : `<span class="chg ${ch >= 0 ? "up" : "down"}">${pctTxt(ch)}</span>`) + liveBadge();
}

// ------------------------------------------------------------------ drawings (kept per market)
// The tools themselves live in draw.js; this is the toolbar around them.
// A pattern trade (chart.html?pat=…) keeps its own drawings: the first time it opens, the pattern and the trade are
// written in as ordinary drawings (editable, movable, deletable), kept apart from the market's normal drawings.
const patView = () => !!(SIG?.pat && S.sym === "BTC-USD");
const drawKey = () => patView() ? `signaldesk.chart.draw.pat.${SIG.pat.id}` : `signaldesk.chart.draw.${S.sym}`;
const getDrawings = () => {
  let v = null;
  try { v = JSON.parse(localStorage.getItem(drawKey())); } catch {}
  if (patView()) {
    // the levels at the trade join the pattern's drawings once (also for trades opened before they existed)
    const lvKey = drawKey() + ".lv", hasLv = (() => { try { return localStorage.getItem(lvKey) === "1"; } catch { return false; } })();
    if (v == null) v = patDrawings();
    if (!hasLv && DATA?.frames) { try { v = [...v, ...levelDrawings()]; } catch {} try { localStorage.setItem(lvKey, "1"); } catch {} }
    setDrawings(v);
  }
  return v || [];
};
const setDrawings = list => { try { localStorage.setItem(drawKey(), JSON.stringify(list)); } catch {} };
const hintText = () => {
  const t = TOOL[S.tool];
  if (!t || t.id === "cursor") return "";
  return t.n === 1 ? `${t.name}: tap the chart.` : t.n === 3 ? `${t.name}: tap two points for the line, then a third for the width.` : `${t.name}: drag on the chart, or tap the start and then the end.`;
};
// TradingView-style left column: one button per group showing the last tool used from it, a small arrow opening the
// group's flyout menu (long-press works too), then magnet / stay / hide / remove all at the bottom.
const TGROUPS = [
  ["cursor", "Cursor", ["cursor"]],
  ["lines", "Lines", ["trend", "ray", "ext", "hline", "hray", "vline", "channel"]],
  ["fib", "Fibonacci", ["fib"]],
  ["shapes", "Shapes", ["rect", "arrow"]],
  ["proj", "Forecasting and measurement", ["long", "short", "measure"]],
  ["vp", "Volume profile", ["frvp"]],
  ["text", "Text", ["text"]],
];
const GL_KEY = "signaldesk.chart.toolgroups";
const GL = (() => { try { return JSON.parse(localStorage.getItem(GL_KEY)) || {}; } catch { return {}; } })();
const ICO = d => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
const ACTS = [
  ["magnet", "Magnet: points snap to the candle's open, high, low or close", '<path d="M6 4v8a6 6 0 0 0 12 0V4"/><path d="M6 8h4M14 8h4"/>'],
  ["keep", "Stay in drawing mode: keep the tool after drawing", '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>'],
  ["hide", "Hide all drawings", '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'],
  ["clear", "Remove all drawings on this market", '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>'],
];
function drawToolbar() {
  closeFly();
  const groups = TGROUPS.map(([g, gname, ids]) => {
    const cur = ids.includes(S.tool) ? S.tool : ids.includes(GL[g]) ? GL[g] : ids[0], t = TOOL[cur];
    return `<div class="tg" data-g="${g}"><button type="button" class="tmain" data-tool="${cur}" aria-pressed="${S.tool === cur}" title="${esc(t.name)}" aria-label="${esc(t.name)}">${ICO(t.icon)}</button>` +
      (ids.length > 1 ? `<button type="button" class="tarr" data-fly="${g}" aria-haspopup="menu" aria-expanded="false" title="${esc(gname)}" aria-label="More ${esc(gname.toLowerCase())} tools"><svg viewBox="0 0 6 10" aria-hidden="true"><path d="M1 1l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.5"/></svg></button>` : "") + `</div>`;
  }).join("");
  $("dtools").innerHTML = groups + `<span class="tsep"></span>` + ACTS.map(([a, t, i]) =>
    `<button type="button" class="tact${a === "clear" ? " del" : ""}" data-act="${a}"${a === "clear" ? "" : ` aria-pressed="false"`} title="${esc(t)}" aria-label="${esc(t)}">${ICO(i)}</button>`).join("");
}
let FLY = null;
function closeFly(refocus) {
  if (!FLY) return;
  const g = FLY.dataset.g; FLY.remove(); FLY = null;
  const b = document.querySelector(`#dtools [data-fly="${g}"]`);
  if (b) { b.setAttribute("aria-expanded", "false"); if (refocus) b.focus(); }
}
function openFly(g) {
  if (FLY?.dataset.g === g) return closeFly();
  closeFly();
  const grp = TGROUPS.find(x => x[0] === g), anchor = document.querySelector(`#dtools .tg[data-g="${g}"]`);
  if (!grp || !anchor) return;
  FLY = document.createElement("div"); FLY.className = "tfly"; FLY.dataset.g = g;
  FLY.setAttribute("role", "menu"); FLY.setAttribute("aria-label", grp[1]);
  FLY.innerHTML = `<div class="tfh">${esc(grp[1])}</div>` + grp[2].map(id =>
    `<button type="button" role="menuitemradio" aria-checked="${S.tool === id}" data-tool="${id}">${ICO(TOOL[id].icon)}<span>${esc(TOOL[id].name)}</span></button>`).join("");
  $("card").appendChild(FLY);
  const r = anchor.getBoundingClientRect(), row = getComputedStyle($("dtools")).flexDirection === "row", f = FLY.getBoundingClientRect();
  FLY.style.left = Math.max(6, Math.min(row ? r.left : r.right + 6, innerWidth - f.width - 6)) + "px";
  FLY.style.top = Math.max(6, Math.min(row ? r.bottom + 6 : r.top, innerHeight - f.height - 6)) + "px";
  anchor.querySelector("[data-fly]")?.setAttribute("aria-expanded", "true");
  (FLY.querySelector('[aria-checked="true"]') || FLY.querySelector("button")).focus({ preventScroll: true });
  FLY.addEventListener("click", e => { const b = e.target.closest("[data-tool]"); if (b) { closeFly(); setTool(b.dataset.tool); } });
  FLY.addEventListener("keydown", e => {
    const items = [...FLY.querySelectorAll("button")], i = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); items[(i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length].focus(); }
    else if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); e.stopPropagation(); closeFly(true); }
  });
}
function setTool(t) {
  S.tool = TOOL[t] ? t : "cursor";
  const g = TGROUPS.find(x => x[2].includes(S.tool));
  if (g && g[2].length > 1 && GL[g[0]] !== S.tool) { GL[g[0]] = S.tool; try { localStorage.setItem(GL_KEY, JSON.stringify(GL)); } catch {} }
  DRAWL?.cancel(); syncDrawButtons();
}
let lpT = null, lpFired = false;     // long-press a group button to open its menu (as on TradingView's app)
$("dtools").addEventListener("pointerdown", e => {
  const b = e.target.closest(".tmain"), g = b?.closest(".tg");
  lpFired = false; clearTimeout(lpT);
  if (g?.querySelector("[data-fly]")) lpT = setTimeout(() => { lpFired = true; openFly(g.dataset.g); }, 450);
});
["pointerup", "pointerleave", "pointercancel"].forEach(ev => $("dtools").addEventListener(ev, () => clearTimeout(lpT)));
$("dtools").addEventListener("contextmenu", e => { if (e.target.closest(".tmain")) e.preventDefault(); });
$("dtools").addEventListener("click", e => {
  if (lpFired) { lpFired = false; return; }
  const f = e.target.closest("[data-fly]"); if (f) return openFly(f.dataset.fly);
  const a = e.target.closest("[data-act]"); if (a) return doAct(a);
  const b = e.target.closest("[data-tool]");
  if (b) { closeFly(); setTool(S.tool === b.dataset.tool && b.dataset.tool !== "cursor" ? "cursor" : b.dataset.tool); }
});
$("draw").addEventListener("click", e => { const b = e.target.closest("button[data-act]"); if (b) doAct(b); });

// indicators panel (TradingView's "Indicators" dialog): opens over the chart, closes on ✕, Escape or a tap outside
const picker = () => document.querySelector(".picker");
function showPicker(on) {
  const p = picker(); p.hidden = !on;
  document.querySelector('[data-act="ind"]')?.setAttribute("aria-expanded", String(on));
  if (on) p.querySelector(".chip button")?.focus({ preventScroll: true });
}
picker().addEventListener("click", e => { if (e.target.closest("[data-pclose]")) { showPicker(false); document.querySelector('[data-act="ind"]')?.focus(); } });

// full screen
const fsEl = () => document.fullscreenElement || document.webkitFullscreenElement;
const isFull = () => $("card").classList.contains("full");
function setFull(on) {
  const card = $("card");
  closeFly();
  card.classList.toggle("full", on); document.documentElement.classList.toggle("tvfull", on);
  if (on) {
    const req = card.requestFullscreen || card.webkitRequestFullscreen;
    try { const r = req?.call(card); r?.catch?.(() => {}); } catch {}     // no API (iPhone): the CSS version covers the page
  } else if (fsEl()) {
    try { const r = (document.exitFullscreen || document.webkitExitFullscreen).call(document); r?.catch?.(() => {}); } catch {}
  }
  syncDrawButtons();
  requestAnimationFrame(() => { if (EMBED && DATA) render(true); });
}
["fullscreenchange", "webkitfullscreenchange"].forEach(ev => document.addEventListener(ev, () => { if (!fsEl() && isFull()) setFull(false); }));

document.addEventListener("pointerdown", e => {
  if (FLY && !FLY.contains(e.target) && !e.target.closest("[data-fly]")) closeFly();
  const p = picker();
  if (!p.hidden && !p.contains(e.target) && !e.target.closest('[data-act="ind"]')) showPicker(false);
}, true);
document.addEventListener("keydown", e => {
  if (e.target.closest("input, textarea, select, [contenteditable]")) return;
  if (e.key === "Escape") {
    if (FLY) { closeFly(true); e.stopImmediatePropagation(); return; }
    if (!picker().hidden) { showPicker(false); e.stopImmediatePropagation(); return; }
    if (isFull() && !fsEl() && S.tool === "cursor") setFull(false);
  } else if ((e.key === "F" || e.key === "f") && e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); setFull(!isFull()); }
}, true);

function doAct(b) {
  const a = b.dataset.act;
  if (a === "magnet") { S.magnet = !S.magnet; savePrefs(); }
  else if (a === "keep") S.keep = !S.keep;
  else if (a === "hide") { S.hideDr = !S.hideDr; DRAWL?.redraw(); }
  else if (a === "undo") DRAWL?.undo();
  else if (a === "clear") { if (getDrawings().length && confirm(`Remove all your drawings on ${label(S.sym)}?`)) DRAWL?.clear(); }
  else if (a === "ind") showPicker(picker().hidden);
  else if (a === "full") setFull(!isFull());
  else if (a === "fit" && VIEW) VIEW.main.timeScale().fitContent();
  else if (a === "recent" && VIEW) VIEW.showRecent();
  else if (a === "patreset" && SIG?.pat) {     // tap twice: the first tap asks, so a stray tap doesn't wipe your edits
    if (b.dataset.armed) resetPat();
    else { b.dataset.armed = "1"; b.textContent = "Tap again to reset"; setTimeout(() => { delete b.dataset.armed; b.textContent = "↺ Reset trade"; }, 3000); }
  }
  syncDrawButtons();
}
const FULL_ICO = { on: '<path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/>', off: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>' };
function syncDrawButtons() {
  const fo = document.activeElement?.closest?.("#dtools button"), key = fo && (fo.dataset.fly ? `[data-fly="${fo.dataset.fly}"]` : fo.dataset.act ? `[data-act="${fo.dataset.act}"]` : `.tg[data-g="${fo.closest(".tg")?.dataset.g}"] .tmain`);
  drawToolbar();
  if (key) $("dtools").querySelector(key)?.focus({ preventScroll: true });
  const set = (a, v) => document.querySelectorAll(`#card [data-act="${a}"]`).forEach(b => b.setAttribute("aria-pressed", String(v)));
  set("magnet", S.magnet); set("keep", S.keep); set("hide", S.hideDr); set("full", isFull());
  const fb = document.querySelector('#card [data-act="full"]');
  if (fb) { fb.innerHTML = ICO(isFull() ? FULL_ICO.on : FULL_ICO.off); fb.title = isFull() ? "Exit full screen (Shift+F or Esc)" : "Full screen (Shift+F)"; fb.setAttribute("aria-label", isFull() ? "Exit full screen" : "Full screen"); }
  $("hint").textContent = hintText();
  DRAWL?.redraw();
}

// ------------------------------------------------------------------ drawing the chart
function baseOptions(showTime, logo = false) {
  const v = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  return {
    autoSize: true,
    layout: { background: { type: "solid", color: v("--c-bg") }, textColor: v("--c-txt"), fontFamily: v("--font"), fontSize: 11, attributionLogo: logo },
    grid: { vertLines: { color: v("--c-grid") }, horzLines: { color: v("--c-grid") } },
    rightPriceScale: { borderColor: v("--c-grid"), minimumWidth: 78 },
    timeScale: { borderColor: v("--c-grid"), visible: showTime, timeVisible: STEP[S.tf] < 86400, secondsVisible: false, minBarSpacing: 0.3, rightOffset: 0 },
    crosshair: { mode: LW.CrosshairMode.Normal, vertLine: { color: "#9598a1", width: 1, style: LW.LineStyle.LargeDashed, labelBackgroundColor: "#363a45" }, horzLine: { color: "#9598a1", width: 1, style: LW.LineStyle.LargeDashed, labelBackgroundColor: "#363a45" } },
    handleScale: { axisPressedMouseMove: { time: true, price: true } },
  };
}

function render(keepRange) {
  if (!DATA) return;
  drawTfs(); drawTitle(); drawPicker();
  const prevRange = keepRange && VIEW ? VIEW.main.timeScale().getVisibleLogicalRange() : null;
  let patFocus = null;     // set when a tapped pattern should be zoomed to
  if (VIEW) VIEW.destroy();
  VIEW = null;

  const c = DATA.frames[S.tf], n = c.length, tf = S.tf, step = STEP[tf];
  const active = S.on.filter(id => !availability(DEF[id], c));
  const ichOn = active.includes("ich");
  const fut = Math.max(12, Math.round(Math.min(n, SHOW[tf]) / 12), ichOn ? params("ich").b + 2 : 0);
  const lastT = c[n - 1].time;
  const T = i => i <= n - 1 ? c[Math.max(0, i)].time : lastT + (i - (n - 1)) * step;
  const N = n + fut;
  const ws = Array.from({ length: fut }, (_, k) => ({ time: T(n + k) }));
  // array (null = empty) -> full-length series data, so every panel lines up candle for candle
  const line = (arr, color) => {
    const out = [];
    for (let i = 0; i < N; i++) {
      const v = i < arr.length ? arr[i] : null;
      out.push(v === null || v === undefined || !isFinite(v) ? { time: T(i) } : color ? { time: T(i), value: v, color: color(i) } : { time: T(i), value: v });
    }
    return out;
  };
  const lineOpts = (color, extra = {}) => ({ color, lineWidth: 2, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, ...extra });
  const noScale = { autoscaleInfoProvider: () => null };

  const stage = $("stage");
  stage.innerHTML = "";
  const panesOn = active.filter(id => DEF[id].kind === "pane");
  const mainEl = document.createElement("div"); mainEl.className = "main"; stage.appendChild(mainEl);
  if (EMBED) {   // fill the frame: price chart gets what the indicator panes leave
    const top = stage.getBoundingClientRect().top + scrollY, paneH = 110;
    mainEl.style.height = Math.max(240, innerHeight - top - panesOn.length * paneH - 2) + "px";
    document.documentElement.style.setProperty("--pane-h", paneH + "px");
  }
  const mainLg = document.createElement("div"); mainLg.className = "lg"; mainEl.appendChild(mainLg);
  const main = LW.createChart(mainEl, baseOptions(!panesOn.length, true));   // TradingView credit once, on the price chart
  main.priceScale("right").applyOptions({ scaleMargins: { top: 0.06, bottom: active.includes("vol") ? 0.2 : 0.06 } });
  const cs = main.addCandlestickSeries({ upColor: COL.cup, downColor: COL.cdn, wickUpColor: COL.cup, wickDownColor: COL.cdn, borderVisible: false,
    priceLineColor: COL.grey, priceLineStyle: LW.LineStyle.Dotted, priceFormat: { type: "price", precision: dp(c[n - 1].close), minMove: 10 ** -dp(c[n - 1].close) } });
  cs.setData([...c.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })), ...ws]);

  const legend = [], legendIds = [];   // main legend rows (html for candle i) and the tool each belongs to
  const reads = [];       // readings: {id, color, name, text, tone}
  const markers = [];     // candle markers
  const marks = [];       // text marks on the chart (✕, labels)
  const at = (arr, i) => arr && i < arr.length ? arr[i] : null;
  const last = n - 1, close = c[last].close;
  const rel = (v, ref) => ref ? `${pctTxt(v / ref - 1)}` : "";
  const addRead = (d, text, tone) => reads.push({ id: d.id, color: d.c, name: d.name(params(d.id)), text, tone });

  for (const id of active) {
    const d = DEF[id], p = params(id);
    if (d.kind === "pane") continue;
    const nm = d.name(p);
    if (id === "ma1" || id === "ma2" || id === "ma3") {
      const v = p.type === "EMA" ? L.EMA(c, p.n) : L.SMA(c, p.n);
      main.addLineSeries(lineOpts(d.c)).setData(line(v));
      legend.push(i => `<b style="color:${d.c}">${nm}</b> ${fmt(at(v, i))}`);
      const m = v[last], slope = v[last - 5] != null ? m - v[last - 5] : 0;
      if (m == null) addRead(d, `Needs ${p.n} candles; this chart has ${n}.`, "neu");
      else addRead(d, `Price is ${rel(close, m)} ${close >= m ? "above" : "below"} it, and the average is ${slope > 0 ? "rising" : slope < 0 ? "falling" : "flat"}.`,
        close >= m && slope >= 0 ? "bull" : close < m && slope <= 0 ? "bear" : "neu");
    } else if (id === "vwap") {
      const v = L.vwap(c);
      main.addLineSeries(lineOpts(d.c)).setData(line(v));
      legend.push(i => `<b style="color:${d.c}">VWAP</b> ${fmt(at(v, i))}`);
      addRead(d, `Price is ${rel(close, v[last])} ${close >= v[last] ? "above" : "below"} today's VWAP: ${close >= v[last] ? "buyers" : "sellers"} have the upper hand today.`, close >= v[last] ? "bull" : "bear");
    } else if (id === "bb" || id === "kc" || id === "dc") {
      const b = id === "bb" ? L.bollinger(c, p.n, p.k) : id === "kc" ? L.keltner(c, p.n, p.m) : L.donchian(c, p.n);
      for (const [arr, a, st] of [[b.upper, 1, 0], [b.mid, 0.6, 2], [b.lower, 1, 0]])
        main.addLineSeries(lineOpts(alpha(d.c, a), { lineWidth: st ? 1 : 1.5, lineStyle: st ? LW.LineStyle.Dashed : LW.LineStyle.Solid })).setData(line(arr));
      legend.push(i => `<b style="color:${d.c}">${nm}</b> ${fmt(at(b.upper, i))} / ${fmt(at(b.mid, i))} / ${fmt(at(b.lower, i))}`);
      const up = b.upper[last], lo = b.lower[last];
      if (up == null) addRead(d, "Not enough candles yet.", "neu");
      else if (id === "dc") {
        const brk = c[last].high >= up && b.upper[last - 1] != null && c[last].high > b.upper[last - 1];
        const brkDn = c[last].low <= lo && b.lower[last - 1] != null && c[last].low < b.lower[last - 1];
        addRead(d, brk ? `New ${p.n}-candle high: a breakout signal.` : brkDn ? `New ${p.n}-candle low: a breakdown signal.` :
          `Inside the channel, ${Math.round(100 * (close - lo) / (up - lo))}% of the way from the low to the high.`, brk ? "bull" : brkDn ? "bear" : "neu");
      } else {
        const pos = (close - lo) / (up - lo), width = (up - lo) / b.mid[last];
        const widths = b.upper.slice(-120).map((u, k) => { const j = n - 120 + k; return u == null || j < 0 ? null : (u - b.lower[j]) / b.mid[j]; }).filter(x => x !== null);
        const squeeze = widths.length > 40 && width <= Math.min(...widths) * 1.1;
        addRead(d, (close > up ? "Closed above the upper band: a strong push, but stretched." : close < lo ? "Closed below the lower band: a strong drop, but stretched." :
          `Inside the bands, ${Math.round(pos * 100)}% of the way from the lower to the upper band.`) + (squeeze ? " The bands are about as narrow as they've been in 120 candles: a squeeze, which often comes before a big move." : ""),
          squeeze ? "warn" : close > up ? "bull" : close < lo ? "bear" : "neu");
      }
    } else if (id === "st") {
      const s = L.supertrend(c, p.n, p.m);
      main.addLineSeries(lineOpts(d.c, { lineWidth: 2 })).setData(line(s.line, i => s.up[i] ? COL.green : COL.red));
      legend.push(i => `<b style="color:${at(s.up, i) === false ? COL.red : COL.green}">${nm}</b> ${fmt(at(s.line, i))}`);
      let k = last; while (k > 0 && s.up[k - 1] === s.up[last]) k--;
      addRead(d, `${s.up[last] ? "Up" : "Down"}trend for ${last - k + 1} candle${last - k ? "s" : ""}; it flips if price closes ${s.up[last] ? "below" : "above"} ${fmt(s.line[last])}.`, s.up[last] ? "bull" : "bear");
    } else if (id === "sar") {
      const s = L.psar(c, p.s, p.mx);
      main.addLineSeries(lineOpts(d.c, { lineVisible: false, pointMarkersVisible: true, pointMarkersRadius: 1.6 }))
        .setData(line(s.map(x => x ? x.value : null), i => s[i]?.up ? COL.green : COL.red));
      legend.push(i => `<b style="color:${d.c}">SAR</b> ${fmt(at(s, i)?.value)}`);
      addRead(d, `Dots are ${s[last].up ? "below" : "above"} price (${s[last].up ? "uptrend" : "downtrend"}); trailing stop at ${fmt(s[last].value)}.`, s[last].up ? "bull" : "bear");
    } else if (id === "ich") {
      const k = L.ichimoku(c, p.a, p.b, p.s), sh = p.b;
      const shift = arr => { const o = new Array(n + sh).fill(null); arr.forEach((v, i) => o[i + sh] = v); return o; };
      const A = shift(k.spanA), B = shift(k.spanB);
      main.addLineSeries(lineOpts(COL.sky, { lineWidth: 1.5 })).setData(line(k.tenkan));
      main.addLineSeries(lineOpts(COL.red, { lineWidth: 1.5 })).setData(line(k.kijun));
      main.addLineSeries(lineOpts(alpha(COL.green, .8), { lineWidth: 1 })).setData(line(A));
      main.addLineSeries(lineOpts(alpha(COL.red, .8), { lineWidth: 1 })).setData(line(B));
      legend.push(i => `<b style="color:${COL.red}">Ichimoku</b> <span style="color:${COL.sky}">${fmt(at(k.tenkan, i))}</span> <span style="color:${COL.red}">${fmt(at(k.kijun, i))}</span> · cloud ${fmt(at(A, i))}–${fmt(at(B, i))}`);
      const top = Math.max(A[last] ?? -Infinity, B[last] ?? -Infinity), bot = Math.min(A[last] ?? Infinity, B[last] ?? Infinity);
      if (!isFinite(top)) addRead(d, "Not enough candles yet.", "neu");
      else addRead(d, close > top ? "Price is above the cloud: uptrend." : close < bot ? "Price is below the cloud: downtrend." : "Price is inside the cloud: no clear trend.",
        close > top ? "bull" : close < bot ? "bear" : "neu");
    } else if (id === "vol") {
      const vs = main.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
      main.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false });
      vs.setData([...c.map(b => ({ time: b.time, value: b.volume, color: b.close >= b.open ? alpha(COL.cup, .5) : alpha(COL.cdn, .5) })), ...ws]);
      const avg = p.ma > 0 ? L.sma(c.map(b => b.volume), p.ma) : null;
      if (avg) main.addLineSeries(lineOpts(alpha(COL.white, .55), { lineWidth: 1, priceScaleId: "vol" })).setData(line(avg));
      legend.push(i => `<b style="color:${d.c}">Vol</b> ${big(at(c, i)?.volume)}${avg ? ` <span class="dim">avg ${big(at(avg, i))}</span>` : ""}`);
      const ref = avg ? avg[last - 1] : null, x = ref ? c[last].volume / ref : null;
      addRead(d, x === null ? `${big(c[last].volume)} on the latest candle.` : `Latest candle ${x.toFixed(1)}× the ${p.ma}-candle average${x >= 1.5 ? ": heavy volume, the move has conviction" : x < 0.6 ? ": light volume" : ""}.`,
        x !== null && x >= 1.5 ? (c[last].close >= c[last].open ? "bull" : "bear") : "neu");
    } else if (id === "tl") {
      const lb = TFS[tf].lookback, r = analyze(c, lb, p.sw, "close");
      for (const [side, col] of [["up", COL.green], ["down", COL.red]]) {
        const ch = r[side], segs = segments(ch, last + fut);
        for (const s of segs) {
          main.addLineSeries(lineOpts(s.last ? col : alpha(col, .45), { lineWidth: 2, ...noScale }))
            .setData([{ time: T(s.from.index), value: s.from.price }, { time: T(s.to.index), value: s.to.price }]);
        }
        if (ch.broken) marks.push({ i: ch.broken.index, p: ch.broken.price, text: "✕", cls: "x", col });
      }
      legend.push(() => `<b style="color:${COL.green}">Up line</b> ${r.up.level !== null ? fmt(r.up.level) : r.up.broken ? "broken" : "–"} · <b style="color:${COL.red}">Down line</b> ${r.down.level !== null ? fmt(r.down.level) : r.down.broken ? "broken" : "–"}`);
      addRead(d, r.bias === "Up" ? `Up line intact at ${fmt(r.up.level)}, the down line is broken: uptrend.` : r.bias === "Down" ? `Down line intact at ${fmt(r.down.level)}, the up line is broken: downtrend.` :
        r.bias === "Squeeze" ? `Squeezed between the up line (${fmt(r.up.level)}) and the down line (${fmt(r.down.level)}): a break of either is the signal.` : "Both lines are broken: no clear trend line.",
        r.bias === "Up" ? "bull" : r.bias === "Down" ? "bear" : r.bias === "Squeeze" ? "warn" : "neu");
    } else if (id === "sr") {
      const major = p.mode !== "all";
      const r = major ? majorLevels(DATA.frames, tf, { k: p.k, swing: p.sw }) : srLevels(c, TFS[tf].lookback, p.sw);
      const tfTxt = l => l.tfs ? l.tfs.join("+") : `${l.touches}×`;
      for (const [list, tag, col] of [[r.support, "S", COL.blue], [r.resistance, "R", COL.amber]])
        list.slice(0, p.k).forEach((l, k) => cs.createPriceLine({ price: l.price, color: major ? col : alpha(col, .85), lineWidth: major ? (l.tfs.length > 1 ? 3 : 2) : l.touches >= 4 ? 2 : 1,
          lineStyle: major ? LW.LineStyle.Solid : LW.LineStyle.Dashed, axisLabelVisible: true, title: major ? `${tag}${k + 1} · ${tfTxt(l)}` : `${tag}${k + 1} · ${l.touches}×` }));
      const s1 = r.support[0], r1 = r.resistance[0];
      legend.push(() => `<b style="color:${COL.blue}">S1</b> ${s1 ? fmt(s1.price) : "–"} · <b style="color:${COL.amber}">R1</b> ${r1 ? fmt(r1.price) : "–"}${major ? ' <span class="dim">major</span>' : ""}`);
      const desc = l => major ? `${fmt(l.price)} (${rel(l.price, close)}, ${l.tfs.join(" + ")} level, ${l.touches} touches)` : `${fmt(l.price)} (${rel(l.price, close)}, ${l.touches} touches)`;
      addRead(d, `${major ? "Major support" : "Nearest support"} ${s1 ? desc(s1) : "none found"}; ${major ? "major resistance" : "nearest resistance"} ${r1 ? desc(r1) : "none found"}.`, "neu");
    } else if (id === "cpat") {
      // every pattern found (up to 6, non-overlapping); the best p.max are drawn. Tap one in the legend to show
      // only that one, zoomed in; tap it again to go back.
      const all = chartPatterns(c, { swing: p.sw, lookback: TFS[tf]?.lookback || 300, max: 6 });
      const key = pt => `${pt.kind}@${pt.start}`;
      const sel = all.find(pt => key(pt) === S.patSel) || null;
      if (!sel) S.patSel = null;
      const pats = sel ? [sel] : all.slice(0, p.max);
      if (sel && S.patFocus) patFocus = { from: Math.max(0, sel.start - 15), to: last + fut };
      const pcol = pt => pt.bull === true ? COL.green : pt.bull === false ? COL.red : COL.violet;
      for (const pt of pats) {
        const col = pcol(pt), w = sel ? 3 : 2;
        const seg = (pts, o = {}) => main.addLineSeries(lineOpts(o.c || alpha(col, .85), { lineWidth: o.w || w, lineStyle: o.style ?? LW.LineStyle.Solid, ...noScale }))
          .setData(pts.map(q => ({ time: T(q.index), value: q.price })));
        const end = last + Math.min(fut, 8);
        if (pt.neck) {
          seg([...pt.points].sort((a, b) => a.index - b.index), { c: alpha(col, .7) });                // the shape itself
          const [a, b] = pt.neck, slope = (b.price - a.price) / (b.index - a.index);
          seg([a, { index: end, price: a.price + slope * (end - a.index) }], { w: sel ? 2 : 1, style: LW.LineStyle.Dashed });   // neckline = trigger
        } else {
          if (pt.pole) seg(pt.pole, { c: alpha(col, .55) });                     // a flag's pole
          const stop = Math.min(end, pt.brokeAt ?? end, Math.floor(pt.apex));   // lines stop at the break, never past where they meet
          for (const [a, b] of [pt.upper, pt.lower]) {
            const slope = (b.price - a.price) / (b.index - a.index);
            seg([a, { index: stop, price: a.price + slope * (stop - a.index) }]);
          }
        }
        seg([{ index: last, price: pt.target }, { index: end, price: pt.target }], { c: alpha(col, .6), w: 1, style: LW.LineStyle.Dotted });
        const topP = Math.max(...pt.points.map(q => q.price)), botP = Math.min(...pt.points.map(q => q.price));
        const mid = Math.round((pt.start + pt.end) / 2);
        marks.push({ i: mid, p: pt.bull === false ? topP : botP, text: `${pt.name} · ${pt.status}`, cls: pt.bull === false ? "pt up" : "pt dn", col });
        marks.push({ i: end, p: pt.target, text: "target", cls: "pt sm", col: alpha(col, .8) });
        if (sel && pt.invalid != null) cs.createPriceLine({ price: pt.invalid, color: alpha(COL.grey, .8), lineWidth: 1, lineStyle: LW.LineStyle.SparseDotted, axisLabelVisible: true, title: "cancelled past" });
        if (pt.brokeAt != null) markers.push({ time: c[pt.brokeAt].time, position: pt.status === "breakout" ? "belowBar" : "aboveBar", color: col,
          shape: pt.status === "breakout" ? "arrowUp" : "arrowDown", text: pt.status === "breakout" ? "Breakout" : "Breakdown" });
      }
      const tx = pt => {
        const watch = pt.neck ? `neckline ${fmt(pt.trigger)}` : `lines ${fmt(pt.triggerLo)}–${fmt(pt.triggerHi)}`;
        if (pt.status === "forming") return `${pt.name} forming: confirms on a close ${pt.bull === false ? "below" : pt.bull ? "above" : "out of the"} ${watch}${pt.neck ? ` (${rel(pt.trigger, close)})` : ""}, target about ${fmt(pt.target)}${pt.invalid != null ? `, cancelled past ${fmt(pt.invalid)}` : ""}.`;
        return `${pt.name}: ${pt.status} ${last - pt.brokeAt === 0 ? "on the latest candle" : `${last - pt.brokeAt} candle${last - pt.brokeAt > 1 ? "s" : ""} ago`}, target about ${fmt(pt.target)} (${rel(pt.target, close)}).`;
      };
      const tone = pt => pt.status === "breakout" ? "bull" : pt.status === "breakdown" ? "bear" : "warn";
      if (!all.length) addRead(d, "No clear pattern forming right now.", "neu");
      for (const pt of pats) addRead(d, tx(pt), tone(pt));
      const chip = pt => `<span class="pchip${sel === pt ? " on" : !sel && pats.includes(pt) ? " shown" : ""}" data-pat="${esc(key(pt))}" style="--pc:${pcol(pt)}" title="${esc(tx(pt))}">${esc(pt.name)} · ${pt.status}</span>`;
      legend.push(() => all.length ? `<b style="color:${COL.violet}">Patterns</b> ${all.map(chip).join("")}${sel ? `<span class="pchip back" data-pat="">Show all</span>` : ""}`
        : `<b style="color:${COL.violet}">Patterns</b> <span class="dim">none forming</span>`);
    } else if (id === "tlv") {
      if (!SIG?.pat) continue;
      const P = SIG.pat, long = P.dir === "LONG", near = sigIndex(c);
      const ie = near(P.te) ?? near(P.ts), ix = near(P.tx) ?? ie;
      if (ie == null) { addRead(d, "The trade is outside the candles on this chart.", "neu"); continue; }
      const r = tradeLevels(DATA.frames, tf, P.te, { entry: P.entry, stop: P.stop, target: P.target, k: p.k });
      const w = Math.max(ix - ie, 20), i0 = Math.max(0, ie - Math.round(w * 1.2) - 30), i1 = ix + Math.round(w * 0.4) + 6;
      const items = [], asDr = patView();     // on a pattern trade the lines are editable drawings: here only the readings
      for (const l of [...r.major, ...(p.which === "major" ? [] : r.local)]) {
        const maj = l.kind === "major", col = l.side === "support" ? COL.blue : COL.amber;
        if (!asDr) main.addLineSeries(lineOpts(maj ? col : alpha(col, .75), { lineWidth: maj ? (l.tfs.length > 1 ? 3 : 2) : 1, lineStyle: maj ? LW.LineStyle.Solid : LW.LineStyle.Dashed, ...noScale }))
          .setData([{ time: T(i0), value: l.price }, { time: T(i1), value: l.price }]);
        const nm = `${maj ? "Major" : "Local"} ${l.side} ${fmt(l.price)} (${l.tfs.join(" + ")}, ${l.touches} touches)`;
        if (!asDr) marks.push({ i: i1, p: l.price, text: `${maj ? "Major" : "Local"} ${l.side === "support" ? "S" : "R"} · ${l.tfs.join("+")}`, cls: "pt sm", col });
        items.push({ nm, at: () => l.price });
      }
      if (p.lines !== "no") for (const ln of r.lines) {
        const maj = ln.kind === "major", col = ln.dir === "up" ? COL.green : COL.red;
        const a = Math.min(Math.max(i0, near(ln.t1) ?? i0), ie), val = i => ln.p1 + ln.slope * (T(i) - ln.t1);
        if (!asDr) main.addLineSeries(lineOpts(maj ? col : alpha(col, .75), { lineWidth: maj ? 2 : 1.5, lineStyle: maj ? LW.LineStyle.Solid : LW.LineStyle.Dashed, ...noScale }))
          .setData([{ time: T(a), value: val(a) }, { time: T(i1), value: val(i1) }]);
        if (!asDr) marks.push({ i: i1, p: val(i1), text: `${maj ? "Major" : "Local"} ${ln.dir} line · ${ln.tf}`, cls: "pt sm", col });
        items.push({ nm: `${maj ? "Major" : "Local"} ${ln.dir} trendline (${ln.side}) on the ${{ W: "weekly", D: "daily", "4H": "4-hour", "1H": "1-hour" }[ln.tf] || ln.tf} chart, at ${fmt(ln.price)} on entry`, at: i => val(i), line: true });
      }
      legend.push(() => `<b style="color:${COL.sky}">At the trade</b> ${r.major.length} major · ${p.which === "major" ? "" : `${r.local.length} local · `}${p.lines === "no" ? "" : `${r.lines.length} trendline${r.lines.length === 1 ? "" : "s"}`}`);
      if (!items.length) { addRead(d, "No support or resistance in the trade's path when it opened: clear air between the stop and the take profit.", "bull"); continue; }
      // where each one sat for this trade: in the way of the take profit, a cushion above/below the stop, or past either
      const sgn = long ? 1 : -1, reach = Math.abs(P.target - P.entry) || 1, su = (P.stop - P.entry) * sgn / reach;
      const touched = lv => { for (let i = ie; i <= ix && i < n; i++) { const v = lv(i); if (c[i].low <= v && c[i].high >= v) return i; } return null; };
      const rows = items.map(x => {
        const u = (x.at(ie) - P.entry) * sgn / reach, hit = touched(x.at);
        let text, tone, rank;
        if (u > 0 && u <= 1) {
          text = `In the way of the take profit: ${x.nm}, ${Math.round(u * 100)}% of the way there.` +
            (hit == null ? " Price never reached it." : P.why === "target" ? " Price reached it and pushed through." : ` Price reached it${(P.why === "stop" || P.why === "time") ? " and the trade didn't make it to the target" : ""}.`);
          tone = "warn"; rank = u;
        } else if (u > 1) { text = `Just past the take profit: ${x.nm}.`; tone = "neu"; rank = 2 + u; }
        else if (u >= su) {
          text = `Between the entry and the stop, a cushion for the trade: ${x.nm}.` + (hit == null ? "" : P.why === "stop" ? " Price broke through it to the stop." : " Price tested it and it held.");
          tone = long ? "bull" : "bear"; rank = 1 + Math.abs(u);
        } else { text = `Past the stop: ${x.nm}.`; tone = "neu"; rank = 3 + Math.abs(u); }
        return { text, tone, rank };
      }).sort((a, b) => a.rank - b.rank);
      const block = rows.filter(x => x.tone === "warn").length;
      addRead(d, `${items.length} level${items.length === 1 ? "" : "s"} in this trade's path when it opened${block ? `, ${block} of them between the entry and the take profit` : ", none between the entry and the take profit"}.`, block ? "warn" : "bull");
      for (const x of rows.slice(0, 7)) addRead(d, x.text, x.tone);
    } else if (id === "fib") {
      const f = L.fibonacci(c, p.n);
      if (!f) continue;
      for (const l of f.levels) cs.createPriceLine({ price: l.price, color: [0.5, 0.618].includes(l.ratio) ? COL.gold : alpha(COL.gold, .5), lineWidth: 1,
        lineStyle: l.ratio === 0 || l.ratio === 1 ? LW.LineStyle.Solid : LW.LineStyle.Dotted, axisLabelVisible: false, title: `${(l.ratio * 100).toFixed(1)}%` });
      main.addLineSeries(lineOpts(alpha(COL.gold, .35), { lineWidth: 1, lineStyle: LW.LineStyle.Dashed, ...noScale }))
        .setData([{ time: T(f.from), value: f.up ? f.low : f.high }, { time: T(f.to), value: f.up ? f.high : f.low }]);
      const below = f.levels.filter(l => l.price <= close).sort((a, b) => b.price - a.price)[0], above = f.levels.filter(l => l.price > close).sort((a, b) => a.price - b.price)[0];
      legend.push(() => `<b style="color:${COL.gold}">Fib</b> ${fmt(f.low)} → ${fmt(f.high)}`);
      const retr = f.up ? (f.high - close) / (f.high - f.low) : (close - f.low) / (f.high - f.low);
      addRead(d, `Last big swing ${f.up ? "up" : "down"} from ${fmt(f.up ? f.low : f.high)} to ${fmt(f.up ? f.high : f.low)}; price has given back ${Math.round(retr * 100)}% of it, ` +
        `between the ${below ? (below.ratio * 100).toFixed(1) + "%" : "–"} and ${above ? (above.ratio * 100).toFixed(1) + "%" : "–"} levels.`, retr < 0.382 ? (f.up ? "bull" : "bear") : retr > 0.618 ? (f.up ? "bear" : "bull") : "neu");
    } else if (id === "piv" || id === "prev") {
      const period = STEP[tf] < 86400 ? "D" : tf === "D" ? "W" : "M";
      const pv = L.pivots(c, period), word = { D: "day", W: "week", M: "month" }[period];
      if (!pv) { addRead(d, "Not enough history.", "neu"); continue; }
      if (id === "piv") {
        for (const [k, col] of [["R3", COL.red], ["R2", COL.red], ["R1", COL.red], ["P", COL.violet], ["S1", COL.green], ["S2", COL.green], ["S3", COL.green]])
          cs.createPriceLine({ price: pv[k], color: alpha(col, k === "P" ? 1 : .7), lineWidth: 1, lineStyle: k === "P" ? LW.LineStyle.Solid : LW.LineStyle.LargeDashed, axisLabelVisible: false, title: k });
        legend.push(() => `<b style="color:${COL.violet}">Pivots (${word})</b> P ${fmt(pv.P)} · R1 ${fmt(pv.R1)} · S1 ${fmt(pv.S1)}`);
        addRead(d, `Price is ${close >= pv.P ? "above" : "below"} the ${word}'s pivot (${fmt(pv.P)}); next ${close >= pv.P ? `resistance R1 ${fmt(pv.R1)}` : `support S1 ${fmt(pv.S1)}`}.`, close >= pv.P ? "bull" : "bear");
      } else {
        const t = { D: "PD", W: "PW", M: "PM" }[period];
        cs.createPriceLine({ price: pv.high, color: alpha(COL.white, .75), lineWidth: 1, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: true, title: `${t}H` });
        cs.createPriceLine({ price: pv.low, color: alpha(COL.white, .75), lineWidth: 1, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: true, title: `${t}L` });
        legend.push(() => `<b>Prev ${word}</b> H ${fmt(pv.high)} · L ${fmt(pv.low)}`);
        addRead(d, close > pv.high ? `Above last ${word}'s high (${fmt(pv.high)}): breaking out.` : close < pv.low ? `Below last ${word}'s low (${fmt(pv.low)}): breaking down.` :
          `Inside last ${word}'s range (${fmt(pv.low)}–${fmt(pv.high)}).`, close > pv.high ? "bull" : close < pv.low ? "bear" : "neu");
      }
    } else if (id === "pat") {
      const pats = L.candlePatterns(c).filter(x => x.index >= n - 300 && (p.doji === "yes" || x.kind !== "Doji"));
      const short = { "Bullish engulfing": "Engulf", "Bearish engulfing": "Engulf", Hammer: "Hammer", "Shooting star": "Star", Doji: "Doji" };
      for (const x of pats) markers.push({ time: c[x.index].time, position: x.bull === false ? "aboveBar" : "belowBar", color: x.bull === true ? COL.green : x.bull === false ? COL.red : COL.grey,
        shape: x.bull === true ? "arrowUp" : x.bull === false ? "arrowDown" : "circle", text: short[x.kind] });
      const recent = pats.filter(x => x.index >= n - 5).pop();
      addRead(d, recent ? `${recent.kind} ${last - recent.index === 0 ? "on the latest candle" : `${last - recent.index} candle${last - recent.index > 1 ? "s" : ""} ago`}.` : "No pattern in the last 5 candles.",
        recent ? (recent.bull === true ? "bull" : recent.bull === false ? "bear" : "neu") : "neu");
    }
    while (legendIds.length < legend.length) legendIds.push(id);
  }
  // the signal's own lines (its indicators on the daily chart) and markers
  if (SIG) {
    const sc = SIG.chart, a = SIG.a, near = sigIndex(c);
    if (sc && tf === "D") {
      const map = sc.dates.map(d => near(dayTime(d)));
      sc.overlays.forEach((ov, k) => {
        const col = [COL.amber, COL.violet, COL.teal, COL.pink, COL.blue][k % 5], arr = new Array(n).fill(null);
        ov.values.forEach((v, i) => { const j = map[i]; if (j != null && v != null) arr[j] = v; });
        main.addLineSeries(lineOpts(col, { lineWidth: 1.5, lineStyle: ov.dash ? LW.LineStyle.Dashed : LW.LineStyle.Solid })).setData(line(arr));
        legend.push(i => `<b style="color:${col}">${esc(ov.name)}</b> ${fmt(at(arr, i))}`); legendIds.push("sig");
      });
    }
    const ai = near(sigTime(a));
    if (ai != null) markers.push({ time: c[ai].time, position: a.side === "bullish" ? "belowBar" : "aboveBar", shape: a.side === "bullish" ? "arrowUp" : "arrowDown",
      color: a.side === "bullish" ? COL.green : COL.red, text: SIG.pat ? `${SIG.pat.name} · ${a.side === "bullish" ? "buy" : "sell"}` : `${a.side === "bullish" ? "Buy" : "Sell"} signal` });
    (sc?.markers || []).forEach(m => {
      const j = sc.dates[m.i] ? near(dayTime(sc.dates[m.i])) : null;
      if (j != null && j !== ai) markers.push({ time: c[j].time, position: m.side > 0 ? "belowBar" : "aboveBar", shape: "circle", color: m.side > 0 ? COL.green : COL.red, text: "" });
    });
    if (SIG.pat && SIG.pat.why !== "open") { const j = near(SIG.pat.tx); if (j != null) markers.push({ time: c[j].time, position: a.side === "bullish" ? "aboveBar" : "belowBar", shape: "square", color: SIG.pat.ret >= 0 ? COL.green : COL.red, text: `${{ stop: "Stop", target: "Target", time: "Time exit" }[SIG.pat.why]} ${SIG.pat.ret > 0 ? "+" : ""}${SIG.pat.ret.toFixed(1)}%` }); }
    if (SIG.trade?.d1) { const j = near(dayTime(SIG.trade.d1)); if (j != null) markers.push({ time: c[j].time, position: "aboveBar", shape: "square", color: COL.white, text: `Closed ${SIG.trade.ret > 0 ? "+" : ""}${(+SIG.trade.ret).toFixed(1)}%` }); }
  }
  if (markers.length) cs.setMarkers(markers.sort((a, b) => a.time - b.time));

  // ---------------- panes
  const charts = [{ chart: main, series: cs, data: null }];
  const paneLegends = [];
  for (const id of panesOn) {
    const d = DEF[id], p = params(id), nm = d.name(p);
    const el = document.createElement("div"); el.className = "pane"; stage.appendChild(el);
    const lg = document.createElement("div"); lg.className = "lg"; el.appendChild(lg);
    const ch = LW.createChart(el, baseOptions(id === panesOn[panesOn.length - 1]));
    ch.priceScale("right").applyOptions({ scaleMargins: { top: 0.12, bottom: 0.08 } });
    const fixed = (lo, hi) => ({ autoscaleInfoProvider: () => ({ priceRange: { minValue: lo, maxValue: hi } }) });
    let first, firstVals, rows;
    if (id === "rsi") {
      const v = L.rsi(c, p.n);
      first = ch.addLineSeries(lineOpts(d.c, { ...fixed(0, 100) })); first.setData(line(v)); firstVals = v;
      for (const [y, s] of [[p.hi, LW.LineStyle.Dashed], [50, LW.LineStyle.Dotted], [p.lo, LW.LineStyle.Dashed]]) first.createPriceLine({ price: y, color: alpha(COL.grey, .6), lineWidth: 1, lineStyle: s, axisLabelVisible: false });
      rows = i => `<b style="color:${d.c}">${nm}</b> ${fmt(at(v, i), 1)}`;
      const r = v[last];
      addRead(d, r === null ? "Not enough candles yet." : `${r.toFixed(1)}: ${r >= p.hi ? "overbought, the move up is stretched" : r <= p.lo ? "oversold, the move down is stretched" : r >= 50 ? "buyers have had the edge lately" : "sellers have had the edge lately"}.`,
        r === null ? "neu" : r >= p.hi || r <= p.lo ? "warn" : r >= 50 ? "bull" : "bear");
    } else if (id === "macd") {
      const m = L.macd(c, p.f, p.s, p.g);
      const h = ch.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
      h.setData(line(m.hist, i => { const x = m.hist[i], y = m.hist[i - 1] ?? 0; return x >= 0 ? (x >= y ? alpha(COL.green, .7) : alpha(COL.green, .35)) : (x <= y ? alpha(COL.red, .7) : alpha(COL.red, .35)); }));
      first = ch.addLineSeries(lineOpts(COL.sky, { lineWidth: 1.5 })); first.setData(line(m.line)); firstVals = m.line;
      ch.addLineSeries(lineOpts(COL.orange, { lineWidth: 1.5 })).setData(line(m.signal));
      rows = i => `<b style="color:${d.c}">${nm}</b> <span style="color:${COL.sky}">${fmt(at(m.line, i))}</span> <span style="color:${COL.orange}">${fmt(at(m.signal, i))}</span> <span class="dim">${fmt(at(m.hist, i))}</span>`;
      const a = m.line[last], s = m.signal[last], crossed = m.line[last - 1] != null && Math.sign(m.line[last - 1] - m.signal[last - 1]) !== Math.sign(a - s);
      addRead(d, a === null || s === null ? "Not enough candles yet." : `MACD ${a > s ? "above" : "below"} its signal line${crossed ? " (it just crossed)" : ""}, and ${a > 0 ? "above" : "below"} zero.`,
        a === null || s === null ? "neu" : a > s && a > 0 ? "bull" : a < s && a < 0 ? "bear" : a > s ? "bull" : "bear");
    } else if (id === "stoch") {
      const s = L.stochastic(c, p.n, p.s, p.d);
      first = ch.addLineSeries(lineOpts(COL.sky, { lineWidth: 1.5, ...fixed(0, 100) })); first.setData(line(s.k)); firstVals = s.k;
      ch.addLineSeries(lineOpts(COL.orange, { lineWidth: 1.5 })).setData(line(s.d));
      for (const y of [80, 20]) first.createPriceLine({ price: y, color: alpha(COL.grey, .6), lineWidth: 1, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: false });
      rows = i => `<b style="color:${d.c}">${nm}</b> <span style="color:${COL.sky}">%K ${fmt(at(s.k, i), 1)}</span> <span style="color:${COL.orange}">%D ${fmt(at(s.d, i), 1)}</span>`;
      const k = s.k[last], dd = s.d[last];
      addRead(d, k === null || dd === null ? "Not enough candles yet." : `%K ${k.toFixed(0)}, ${k > dd ? "above" : "below"} %D${k >= 80 ? ": overbought" : k <= 20 ? ": oversold" : ""}.`,
        k === null || dd === null ? "neu" : k >= 80 || k <= 20 ? "warn" : k > dd ? "bull" : "bear");
    } else if (id === "adx") {
      const a = L.adx(c, p.n);
      first = ch.addLineSeries(lineOpts(COL.white, { lineWidth: 2 })); first.setData(line(a.adx)); firstVals = a.adx;
      ch.addLineSeries(lineOpts(COL.green, { lineWidth: 1 })).setData(line(a.plus));
      ch.addLineSeries(lineOpts(COL.red, { lineWidth: 1 })).setData(line(a.minus));
      first.createPriceLine({ price: 25, color: alpha(COL.grey, .6), lineWidth: 1, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: false });
      rows = i => `<b style="color:${d.c}">${nm}</b> ${fmt(at(a.adx, i), 1)} <span style="color:${COL.green}">+DI ${fmt(at(a.plus, i), 1)}</span> <span style="color:${COL.red}">−DI ${fmt(at(a.minus, i), 1)}</span>`;
      const x = a.adx[last], up = a.plus[last] > a.minus[last];
      addRead(d, x === null ? "Not enough candles yet." : x >= 25 ? `${x.toFixed(0)}: a strong ${up ? "up" : "down"}trend.` : x < 20 ? `${x.toFixed(0)}: weak or no trend, a range.` : `${x.toFixed(0)}: a trend may be forming, ${up ? "buyers" : "sellers"} ahead.`,
        x === null || x < 20 ? "neu" : up ? "bull" : "bear");
    } else if (id === "atr") {
      const a = L.atr(c, p.n);
      first = ch.addLineSeries(lineOpts(d.c)); first.setData(line(a)); firstVals = a;
      rows = i => `<b style="color:${d.c}">${nm}</b> ${fmt(at(a, i))}`;
      const x = a[last], ago = a[last - 20];
      addRead(d, x === null ? "Not enough candles yet." : `${fmt(x)} (${(x / close * 100).toFixed(2)}% of the price) per candle, ${ago ? (x > ago * 1.15 ? "rising" : x < ago * 0.85 ? "falling" : "steady") : ""}. A 2× ATR stop would sit near ${fmt(close - 2 * x)} for a long.`, "neu");
    } else if (id === "cci") {
      const v = L.cci(c, p.n);
      first = ch.addLineSeries(lineOpts(d.c)); first.setData(line(v)); firstVals = v;
      for (const y of [100, 0, -100]) first.createPriceLine({ price: y, color: alpha(COL.grey, .6), lineWidth: 1, lineStyle: y ? LW.LineStyle.Dashed : LW.LineStyle.Dotted, axisLabelVisible: false });
      rows = i => `<b style="color:${d.c}">${nm}</b> ${fmt(at(v, i), 0)}`;
      const x = v[last];
      addRead(d, x === null ? "Not enough candles yet." : `${x.toFixed(0)}: ${x > 100 ? "strong upward push" : x < -100 ? "strong downward push" : "within its normal range"}.`, x === null ? "neu" : x > 100 ? "bull" : x < -100 ? "bear" : "neu");
    } else if (id === "obv") {
      const v = L.obv(c), avg = L.sma(v, 20);
      first = ch.addLineSeries(lineOpts(d.c, { priceFormat: { type: "volume" } })); first.setData(line(v)); firstVals = v;
      ch.addLineSeries(lineOpts(alpha(COL.white, .4), { lineWidth: 1, priceFormat: { type: "volume" } })).setData(line(avg));
      rows = i => `<b style="color:${d.c}">OBV</b> ${big(at(v, i))}`;
      addRead(d, `On-balance volume is ${v[last] > avg[last] ? "above" : "below"} its 20-candle average: volume is flowing ${v[last] > avg[last] ? "in" : "out"}.`, v[last] > avg[last] ? "bull" : "bear");
    }
    lg.innerHTML = `<div class="r"></div>`;
    paneLegends.push({ lg, rows, id });
    charts.push({ chart: ch, series: first, vals: firstVals });
  }

  // ---------------- legends + crosshair
  const ohlc = i => {
    const b = c[Math.min(i, last)];
    const ch = i > 0 ? b.close / c[Math.min(i, last) - 1].close - 1 : 0;
    return `<div class="o"><span>O ${fmt(b.open)}</span><span>H ${fmt(b.high)}</span><span>L ${fmt(b.low)}</span><span>C <b class="${b.close >= b.open ? "up" : "down"}">${fmt(b.close)}</b></span><span class="${ch >= 0 ? "up" : "down"}">${pctTxt(ch)}</span></div>`;
  };
  const xBtn = id => id === "sig" ? "" : `<span class="x" data-x="${id}" title="Remove">×</span>`;
  const updateLegends = i => {
    i = Math.max(0, Math.min(last, i ?? last));
    mainLg.innerHTML = ohlc(i) + legend.map((f, k) => `<div>${f(i)}${xBtn(legendIds[k])}</div>`).join("");
    for (const pl of paneLegends) pl.lg.innerHTML = `<div>${pl.rows(i)}${xBtn(pl.id)}</div>`;
  };
  updateLegends();

  let syncing = false;
  for (const a of charts) {
    a.chart.timeScale().subscribeVisibleLogicalRangeChange(r => {
      if (syncing || !r) return;
      syncing = true;
      for (const b of charts) if (b !== a) b.chart.timeScale().setVisibleLogicalRange(r);
      syncing = false;
      placeMarks();
    });
    a.chart.subscribeCrosshairMove(prm => {
      if (syncing) return;
      const i = prm.logical === undefined || prm.logical === null || !prm.point ? null : Math.round(prm.logical);
      updateLegends(i);
      syncing = true;
      for (const b of charts) {
        if (b === a) continue;
        if (i === null || !prm.time) { b.chart.clearCrosshairPosition(); continue; }
        const v = b === charts[0] ? c[Math.min(i, last)].close : b.vals?.[i];
        if (v === null || v === undefined) b.chart.clearCrosshairPosition();
        else b.chart.setCrosshairPosition(v, prm.time, b.series);
      }
      syncing = false;
    });
  }

  // text marks (trendline breaks, first point of a line being drawn)
  const mk = document.createElement("div"); mk.className = "marks"; mainEl.appendChild(mk);
  mk.innerHTML = marks.map(m => `<span class="${m.cls}" style="color:${m.col}">${esc(m.text)}</span>`).join("");
  const spans = [...mk.children];
  function placeMarks() {
    marks.forEach((m, k) => {
      const x = main.timeScale().logicalToCoordinate(m.i), y = cs.priceToCoordinate(m.p);
      spans[k].style.display = x === null || y === null ? "none" : "";
      spans[k].style.left = `${x}px`; spans[k].style.top = `${y}px`;
    });
  }
  const ro = new ResizeObserver(() => requestAnimationFrame(placeMarks)); ro.observe(mainEl);

  const sigAt = SIG ? sigIndex(c)(sigTime(SIG.a)) : null;
  // a pattern trade: zoom to the pattern and its trade, from the first swing point to a little after the exit
  const patRange = () => {
    const near = sigIndex(c), P = SIG.pat, t0 = Math.min(P.ts, ...(P.shape?.pts || []).map(q => q[0]), ...(P.shape?.box ? [P.shape.box[0]] : []), ...(P.shape?.lines || []).map(q => q[0]));
    const a0 = near(t0) ?? sigAt, a1 = near(P.tx) ?? sigAt, w = Math.max(a1 - a0, 30);
    return { from: Math.max(0, a0 - w * 0.35), to: a1 + w * 0.45 + 6 };
  };
  const showRecent = () => sigAt != null && !keepRange && SIG?.pat ? main.timeScale().setVisibleLogicalRange(patRange())
    : sigAt != null && !keepRange
    ? main.timeScale().setVisibleLogicalRange({ from: Math.max(0, Math.min(sigAt - SHOW[tf] * 0.7, n - SHOW[tf])), to: Math.max(last, sigAt + SHOW[tf] * 0.3) + fut })
    : main.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - SHOW[tf]), to: last + fut });
  if (patFocus) main.timeScale().setVisibleLogicalRange(patFocus); else if (prevRange) main.timeScale().setVisibleLogicalRange(prevRange); else showRecent();
  S.patFocus = false;
  requestAnimationFrame(placeMarks);

  // your own drawings (draw.js), plus the signal's trade when this is a signal view
  DRAWL = createDrawLayer({ chart: main, series: cs, host: mainEl, bars: c, step, store: { get: getDrawings, set: setDrawings },
    getTool: () => S.tool, setTool, magnet: () => S.magnet, keep: () => S.keep, hidden: () => S.hideDr, extra: () => [...(SIG ? sigShapes(c) : []), ...lvShapes()] });
  const layer = DRAWL;
  syncDrawButtons();

  // live prices: redraw the candles in place (indicators catch up when a new candle opens)
  const setCandles = () => cs.setData([...c.map(b => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })), ...ws]);
  VIEW = { main, showRecent, setCandles, destroy() { ro.disconnect(); layer.destroy(); for (const a of charts) a.chart.remove(); } };
  drawReads(reads);
  $("note").textContent = `${DATA.source} ${c.length} candles. ${STEP[tf] < 86400 && DATA.kind === "stock" ? "Times are New York time." : DATA.kind === "crypto" && STEP[tf] < 86400 ? "Times are UTC." : ""}`;
}

// ------------------------------------------------------------------ live prices
// Crypto: Binance's live kline stream for the timeframe on screen (every trade). Stocks: the real-time
// IEX price from the data server every 15 seconds in US hours, applied to the last candle of every
// timeframe (a new candle opens when its period starts); the full delayed data is reloaded every
// 5 minutes so intraday candles stay complete. Commodities: daily data only, nothing live.
const KL = { W: "1w", D: "1d", "4H": "4h", "1H": "1h", "30m": "30m", "15m": "15m", "5m": "5m", "1m": "1m" };
let LIVE = null;                 // {kind, stop(), at: last update ms}
const nyNow = () => {
  const p = {};
  for (const { type, value } of new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit" }).formatToParts(new Date())) p[type] = +value;
  return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) / 1000;   // New York wall clock, "as if UTC" like the candles
};
const usOpen = ny => { const d = new Date(ny * 1000), m = d.getUTCHours() * 60 + d.getUTCMinutes(), wd = d.getUTCDay(); return wd > 0 && wd < 6 && m >= 570 && m < 960; };
// start of the candle a New York wall time falls in (same anchors as chart-lib: sessions from 9:30, weeks from Monday)
function periodStart(ny, tf) {
  const day = Math.floor(ny / 86400) * 86400;
  if (tf === "D") return day;
  if (tf === "W") return day - ((new Date(day * 1000).getUTCDay() + 6) % 7) * 86400;
  const mins = STEP[tf] / 60, m = Math.floor((ny - day) / 60);
  return day + (570 + Math.floor((m - 570) / mins) * mins) * 60;
}
function liveBadge() {
  if (!LIVE) return "";
  const fresh = LIVE.at && Date.now() - LIVE.at < (LIVE.kind === "crypto" ? 30e3 : 60e3);
  return `<span class="live${fresh ? "" : " idle"}" title="${esc(LIVE.kind === "crypto" ? "Live from Binance, every trade" : "Last candle follows the real-time IEX price every 15 seconds; the rest of the chart is Alpaca's full data, 15 minutes delayed")}">${fresh ? "LIVE" : LIVE.kind === "stock" && !usOpen(nyNow()) ? "Market closed" : "Connecting…"}</span>`;
}
let paintQ = false;
function paint(newCandle) {
  if (newCandle) { render(true); return; }
  if (paintQ) return;
  paintQ = true;
  requestAnimationFrame(() => { paintQ = false; if (!VIEW || !DATA) return; try { VIEW.setCandles(); } catch {} drawTitle(); });
}
function liveStop() { LIVE?.stop(); LIVE = null; }
function liveStart() {
  liveStop();
  if (!DATA) return;
  const sym = DATA.sym;
  if (DATA.kind === "crypto") {
    const coin = sym.replace("-USD", "").toLowerCase(), tf = S.tf;
    let ws, dead = false, retry;
    const open = () => {
      if (dead) return;
      try { ws = new WebSocket(`wss://data-stream.binance.vision/ws/${coin}usdt@kline_${KL[tf]}`); } catch { return; }
      ws.onmessage = e => {
        const k = JSON.parse(e.data)?.k;
        if (!k || !DATA || DATA.sym !== sym || S.tf !== tf) return;
        const c = DATA.frames[tf], last = c[c.length - 1], t = Math.floor(k.t / 1000);
        const bar = { time: t, open: +k.o, high: +k.h, low: +k.l, close: +k.c, volume: +k.v };
        if (!last || t < last.time) return;
        LIVE.at = Date.now();
        if (t === last.time) { Object.assign(last, bar); paint(false); }
        else { c.push(bar); paint(true); }
      };
      ws.onclose = () => { if (!dead) retry = setTimeout(open, 5000); };
    };
    LIVE = { kind: "crypto", at: 0, stop() { dead = true; clearTimeout(retry); try { ws?.close(); } catch {} } };
    open();
    return;
  }
  if (DATA.kind !== "stock") return;
  const kindQ = SP100.has(sym) ? "stocks" : "sp500";
  let timer, reload, dead = false;
  const tick = async () => {
    if (dead || document.hidden || !usOpen(nyNow())) { drawTitle(); return; }
    try {
      const r = await fetch(`${API.replace(/\/$/, "")}/?quotes=${kindQ}`);
      const q = (await r.json())?.q?.[sym];
      if (dead || !q || !DATA || DATA.sym !== sym) return;
      const p = +q[0], ny = nyNow();
      let fresh = false;
      for (const [tf, c] of Object.entries(DATA.frames)) {
        const last = c[c.length - 1]; if (!last) continue;
        const t0 = periodStart(ny, tf);
        if (t0 < last.time) continue;                       // the data is already past this point
        if (t0 === last.time) { last.close = p; last.high = Math.max(last.high, p); last.low = Math.min(last.low, p); }
        else { c.push({ time: t0, open: p, high: p, low: p, close: p, volume: last.volume == null ? null : 0 }); if (tf === S.tf) fresh = true; }
      }
      LIVE.at = Date.now();
      paint(fresh);
    } catch {}
  };
  // every 5 minutes in US hours: the full (15-minute delayed) candles, so the intraday ones fill in
  const refresh = async () => {
    if (dead || document.hidden || !usOpen(nyNow()) || STEP[S.tf] >= 86400) return;
    try {
      const d = await getData(sym);
      if (S.tf === "1m") await load1m(d).catch(() => { d.frames["1m"] = DATA?.frames["1m"]; });
      if (!dead && DATA?.sym === sym && !d.fallback) { DATA = d; render(true); tick(); }
    } catch {}
  };
  LIVE = { kind: "stock", at: 0, stop() { dead = true; clearInterval(timer); clearInterval(reload); } };
  timer = setInterval(tick, 15000); reload = setInterval(refresh, 300000);
  tick();
}

// ------------------------------------------------------------------ levels from the Terminal
// The Terminal sends the options levels of the stock it shows (max pain, call and put walls) with
// postMessage({type: "ics-levels", sym, levels: [[price, label, colour], ...]}); drawn as dashed lines.
let LEVELS = { sym: null, levels: [] };
const lvShapes = () => LEVELS.sym === S.sym ? LEVELS.levels.map(([p, label, color]) => ({ type: "hline", pts: [{ t: 0, p }], color, w: 1, dash: 1, label })) : [];
addEventListener("message", e => {
  if (e.origin !== location.origin || e.data?.type !== "ics-levels") return;
  LEVELS = { sym: e.data.sym, levels: (e.data.levels || []).filter(l => Array.isArray(l) && isFinite(l[0])) };
  DRAWL?.redraw();
});

// ------------------------------------------------------------------ signal view
const dayTime = d => Date.parse(d + "T00:00:00Z") / 1000;
const sigTime = a => a.t != null ? a.t : a.found_at && a.tf && a.tf !== "D" && a.tf !== "W" ? Date.parse(a.found_at.replace(/\+00:00$/, "Z")) / 1000 : dayTime(a.date);
// candles -> "which candle holds this time" (same UTC day for daily candles, else the candle the time falls in)
function sigIndex(c) {
  const daily = STEP[S.tf] >= 86400;
  return t => {
    if (!c.length || t == null || isNaN(t)) return null;
    let lo = 0, hi = c.length - 1;
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (c[m].time <= t + (daily ? 86399 : 0)) lo = m; else hi = m - 1; }
    if (c[lo].time > t + (daily ? 86399 : 0)) return null;
    if (S.tf === "D" && new Date(c[lo].time * 1000).toISOString().slice(0, 10) !== new Date(t * 1000).toISOString().slice(0, 10)) return t - c[lo].time < 4 * 86400 ? lo : null;
    return lo;
  };
}
// the trade drawn as a TradingView long/short box: entry at the alert, the "wrong if" stop, a 2R mark and the result so far
// a pattern trade: the pattern's own swing points and lines, then the trade box (entry, stop loss, take profit, exit)
function patShapes(c) {
  const T = SIG.pat, long = T.dir === "LONG", step = STEP[S.tf], out = [], amber = COL.amber;
  const sh = T.shape || {};
  if (sh.box) out.push({ type: "rect", pts: [{ t: sh.box[0], p: sh.box[3] }, { t: sh.box[1] + step, p: sh.box[2] }], color: amber, w: 1.5, dash: 0, label: T.name });
  const pts = (sh.pts || []).map(([t, p, lb]) => ({ t, p, lb })).sort((x, y) => x.t - y.t);
  for (let k = 0; k + 1 < pts.length; k++) out.push({ type: "trend", pts: [pts[k], pts[k + 1]], color: amber, w: 2, dash: 0, label: k === 0 && !sh.box ? T.name : "" });
  for (const q of pts) if (q.lb) out.push({ type: "text", pts: [{ t: q.t, p: q.p }], color: amber, text: q.lb });
  for (const [t1, p1, t2, p2] of sh.lines || []) out.push({ type: "trend", pts: [{ t: t1, p: p1 }, { t: t2, p: p2 }], color: alpha(COL.white, .7), w: 1.5, dash: 1, label: "" });
  const tEnd = Math.max(T.tx, T.te) + step * 3;
  out.push({ type: long ? "long" : "short", pts: [{ t: T.te, p: T.entry }, { t: tEnd, p: T.target }, { t: tEnd, p: T.stop }],
    tpLabel: T.tr === "2R" ? "Take profit (2R)" : "Take profit", slLabel: "Stop loss", now: { t: T.tx, p: T.exit },
    nowLabel: `${T.why === "open" ? "now" : { stop: "stopped", target: "target hit", time: "time exit" }[T.why]} ${T.ret >= 0 ? "+" : "−"}${Math.abs(T.ret).toFixed(2)}%` });
  out.push({ type: "vline", pts: [{ t: T.ts, p: T.entry }], color: alpha(COL.white, .35), w: 1, dash: 2, label: "Breakout" });
  return out;
}
// the pattern as editable drawings: each gets an id and the usual colour / width / dash settings
function patDrawings() {
  return patShapes().map((d, k) => ({ id: "p" + k, w: 2, dash: 0, ...d, ...(d.type === "long" || d.type === "short" ? { color: COL.green } : {}) }));
}
// the support/resistance and trendlines in the trade's path when it opened (see tradeLevels), as editable drawings
function levelDrawings() {
  const P = SIG.pat, tf = P.tf, step = STEP[tf];
  const r = tradeLevels(DATA.frames, tf, P.te, { entry: P.entry, stop: P.stop, target: P.target, k: 3 });
  const w = Math.max(Math.round((P.tx - P.te) / step), 20);
  const t0 = P.te - (Math.round(w * 1.2) + 30) * step, t1 = P.tx + (Math.round(w * 0.4) + 6) * step;
  const out = [];
  for (const l of [...r.major, ...r.local]) {
    const maj = l.kind === "major";
    out.push({ type: "trend", pts: [{ t: t0, p: l.price }, { t: t1, p: l.price }], color: l.side === "support" ? COL.blue : COL.amber,
      w: maj ? (l.tfs.length > 1 ? 3 : 2) : 1, dash: maj ? 0 : 1, label: `${maj ? "Major" : "Local"} ${l.side} · ${l.tfs.join("+")}` });
  }
  for (const ln of r.lines) {
    const a = Math.max(ln.t1, t0);
    out.push({ type: "trend", pts: [{ t: a, p: ln.p1 + ln.slope * (a - ln.t1) }, { t: t1, p: ln.p1 + ln.slope * (t1 - ln.t1) }],
      color: ln.dir === "up" ? COL.green : COL.red, w: ln.kind === "major" ? 2 : 1.5, dash: ln.kind === "major" ? 0 : 1,
      label: `${ln.kind === "major" ? "Major" : "Local"} ${ln.dir} line · ${ln.tf}` });
  }
  return out.map((d, k) => ({ id: "lv" + k, ...d }));
}
// back to the trade as it first opened: the original drawings, the pattern's timeframe, zoomed on the trade and
// your usual indicators (indicator changes on a pattern chart aren't saved, so a fresh load restores them)
function resetPat() {
  try { localStorage.removeItem(`signaldesk.chart.draw.pat.${SIG.pat.id}`); localStorage.removeItem(`signaldesk.chart.draw.pat.${SIG.pat.id}.lv`); } catch {}
  history.replaceState(null, "", `${location.pathname}${location.search}#BTC-USD/${SIG.pat.tf}`);
  location.reload();
}
function sigShapes(c) {
  if (SIG.pat) return patView() ? [] : patShapes(c);
  const a = SIG.a, out = [], t0 = sigTime(a), long = a.side === "bullish", step = STEP[S.tf];
  const last = c[c.length - 1], tr = SIG.trade;
  const nowP = tr?.p1 ?? tr?.px ?? last.close, nowT = tr?.d1 ? dayTime(tr.d1) : last.time;
  const tEnd = Math.max(nowT, t0) + step * 12;
  if (a.stop && !a.exit && (long ? a.stop < a.price : a.stop > a.price)) {
    const risk = Math.abs(a.price - a.stop);
    const ret = (nowP / a.price - 1) * 100 * (long ? 1 : -1);
    out.push({ type: long ? "long" : "short", pts: [{ t: t0, p: a.price }, { t: tEnd, p: a.price + (long ? 2 : -2) * risk }, { t: tEnd, p: a.stop }],
      tpLabel: "2R mark", slLabel: "Wrong if", now: { t: nowT, p: nowP }, nowLabel: `${tr?.d1 ? "closed" : "now"} ${ret >= 0 ? "+" : "−"}${Math.abs(ret).toFixed(2)}%` });
  } else {
    out.push({ type: "hray", pts: [{ t: t0, p: a.price }], color: long ? COL.green : COL.red, w: 1.5, dash: 1, label: a.exit ? "Exit signal" : "Signal price" });
    if (a.stop) out.push({ type: "hray", pts: [{ t: t0, p: a.stop }], color: COL.red, w: 1.5, dash: 1, label: "Wrong if" });
  }
  if (a.level) out.push({ type: "hline", pts: [{ t: t0, p: a.level }], color: COL.amber, w: 1.5, dash: 0, label: "Line" });
  out.push({ type: "vline", pts: [{ t: t0, p: a.price }], color: alpha(COL.white, .35), w: 1, dash: 2, label: a.date });
  for (const sh of SIG.chart?.shapes || []) {
    const P = sh.points.map(([i, p]) => ({ t: SIG.chart.dates[i] ? dayTime(SIG.chart.dates[i]) : null, p })).filter(q => q.t);
    for (let k = 0; k + 1 < P.length; k++) out.push({ type: "trend", pts: [P[k], P[k + 1]], color: COL.amber, w: 2, dash: sh.dash ? 1 : 0, label: k === 0 ? sh.name : "" });
  }
  return out;
}
async function loadSignal() {
  const opt = u => fetch(u).then(r => r.ok ? r.json() : null).catch(() => null);
  const [al, lv, st] = await Promise.all([opt(SIG_BASE + "alerts.json"), opt(SIG_BASE + "live.json"), opt("data/strategies.json")]);
  const a = (al?.alerts || []).find(x => x.id === SIG_ID) || (lv?.alerts || []).find(x => x.id === SIG_ID);
  if (!a) return null;
  const [chart, lt] = await Promise.all([a.chart ? a.chart : a.file ? opt(SIG_BASE + "charts/" + a.file) : null, opt(SIG_BASE + "live_trades.json")]);
  const side = a.side === "bullish" ? 1 : -1, d0 = dayTime(a.date);
  const trade = [...(lt?.open || []), ...(lt?.closed || [])].filter(x => x.t === a.ticker && x.s === a.strategy && x.side === side && dayTime(x.d0) >= d0 && dayTime(x.d0) - d0 <= 5 * 86400)
    .sort((x, y) => x.d0.localeCompare(y.d0))[0] || null;
  return { a, chart, trade, strat: (st?.strategies || []).find(x => x.id === a.strategy) || null, liveStart: lt?.start };
}
function drawSigCard() {
  const box = $("sigcard");
  if (!box) return;
  if (!SIG || EMBED) { box.hidden = true; return; }
  if (SIG.pat) {
    const T = SIG.pat, long = T.dir === "LONG", R = SIG.rules || {}, why = { stop: "stop loss hit", target: "take profit hit", time: "closed after the holding time", open: "still open" };
    box.hidden = false;
    box.innerHTML = `<div class="sghd"><span class="pill ${long ? "bull" : "bear"}">${long ? "▲ Long" : "▼ Short"}</span><h2>BTC · ${esc(T.name)}</h2><span class="dim">${esc(new Date(T.ts * 1000).toISOString().slice(0, T.tf === "D" ? 10 : 16).replace("T", " "))} UTC · ${{ D: "daily", "4H": "4-hour", "1H": "1-hour" }[T.tf]} chart</span></div>
      <div class="sggrid"><div><span>Entry</span><b>${fmt(T.entry)}</b><small>next candle's open</small></div><div><span>Stop loss</span><b class="down">${fmt(T.stop)}</b><small>${esc(R.stop || "")}</small></div><div><span>Take profit</span><b class="up">${fmt(T.target)}</b><small>${esc(R.target || "")}</small></div>
      <div><span>Result</span><b class="${T.ret >= 0 ? "up" : "down"}">${T.ret > 0 ? "+" : ""}${T.ret.toFixed(2)}%</b><small>${why[T.why]} at ${fmt(T.exit)}, after fees</small></div></div>
      <p class="dim sgfoot">The blue/amber and green/red lines are the support, resistance and trendlines that stood in this trade's way when it opened (solid = major, dashed = local); what each meant for the trade is listed under the chart. These lines, the pattern and the trade are all ordinary drawings: tap one to move it, drag its handles, recolour or delete it. Your changes stay on this device for this trade. <button type="button" class="btn ghost sm" id="patReset">↺ Reset the trade</button></p>
      <p class="dim sgfoot">From the BTC pattern backtest (every pattern the site detects, since 2021). <a href="patterns.html#${esc(new URLSearchParams({ tf: T.tf, p: `${T.fam}|${T.name}|${T.dir}` }).toString())}">Every ${esc(T.name)} trade ›</a></p>`;
    $("patReset").onclick = resetPat;
    return;
  }
  const { a, trade, strat } = SIG, long = a.side === "bullish", st = a.strength;
  const risk = a.stop ? Math.abs(a.price / a.stop - 1) * 100 : null;
  const why = { sl: "its stop was hit", tp: "it reached its take profit", rule: "the strategy's exit rule fired" };
  box.hidden = false;
  box.innerHTML = `<div class="sghd"><span class="pill ${long ? "bull" : "bear"}">${long ? "▲ Buy" : "▼ Sell"}${a.exit ? " (exit)" : ""}</span><h2>${esc(label(a.ticker))} · ${esc(strat?.name || a.strategy)}</h2><span class="dim">${esc(a.date)}${a.tf ? " · " + esc(a.tf) + " chart" : ""}</span></div>
    <p class="sgnote">${esc(a.note)}</p>
    <div class="sggrid">
      <div><span>Signal price</span><b>${fmt(a.price)}</b></div>
      <div><span>Wrong if</span><b class="down">${a.stop ? fmt(a.stop) : "–"}</b><small>${esc(a.stop_why || "")}</small></div>
      <div><span>Risk to the stop</span><b>${risk != null ? risk.toFixed(2) + "%" : "–"}</b></div>
      <div><span>Paper trade</span>${trade ? `<b class="${trade.ret >= 0 ? "up" : "down"}">${trade.ret > 0 ? "+" : ""}${(+trade.ret).toFixed(2)}%</b><small>${trade.d1 ? `closed ${esc(trade.d1)}: ${why[trade.why] || "closed"}` : `open since ${esc(trade.d0)}, ${trade.days} day${trade.days === 1 ? "" : "s"}`}</small>` : `<b class="dim">–</b><small>${SIG.liveStart && a.date < SIG.liveStart ? "before the live record started" : "not traded (vaulted or filtered)"}</small>`}</div>
    </div>
    ${st ? `<h3>Why it fired, and what backs it up · ${esc(st.label)} ${st.score}/4</h3><ul class="sgchk">${st.checks.map(k => `<li class="${k.ok ? "ok" : "no"}">${esc(k.text)}</li>`).join("")}</ul>` : ""}
    ${strat ? `<h3>The rule</h3><p>${esc(long ? strat.bullish : strat.bearish)}</p>${strat.why ? `<p class="dim">${esc(strat.why)}</p>` : ""}${strat.watch_out?.length ? `<h3>Watch out</h3><ul class="sgchk">${strat.watch_out.slice(0, 2).map(w => `<li class="warn">${esc(w)}</li>`).join("")}</ul>` : ""}` : ""}
    <p class="dim sgfoot">On the chart: the shaded box is the trade (green above/below the entry towards a 2× risk mark, red down to the “wrong if” level), the arrow is the signal candle, and the strategy's own lines are drawn on the daily chart. Draw on it with the tools on the left. <a href="index.html#terminal">Open the Terminal ›</a></p>`;
}

// ------------------------------------------------------------------ readings
function drawReads(reads) {
  const n = { bull: 0, bear: 0 };
  reads.forEach(r => { if (r.tone in n) n[r.tone]++; });
  const word = { bull: "Bullish", bear: "Bearish", neu: "Neutral", warn: "Watch" };
  $("reads").innerHTML = reads.length ? reads.map(r => `<div class="r"><i style="background:${r.color}"></i><b>${esc(r.name)}</b><span>${esc(r.text)}</span><em class="pill ${r.tone}">${word[r.tone]}</em></div>`).join("")
    : `<div class="empty">Nothing on the chart yet. Tap a tool above, or a quick set like <b>Trend</b> or <b>Momentum</b>.</div>`;
  const tot = n.bull + n.bear;
  $("tally").innerHTML = !tot ? "" : `<span class="pill ${n.bull > n.bear ? "bull" : n.bear > n.bull ? "bear" : "neu"}">${n.bull > n.bear * 1.5 ? "Mostly bullish" : n.bear > n.bull * 1.5 ? "Mostly bearish" : "Mixed"}</span>` +
    `<span class="dim">${n.bull} bullish · ${n.bear} bearish · ${reads.length - tot} neutral or watch, on the ${TF_TXT[S.tf].toLowerCase()} chart</span>`;
}

// ------------------------------------------------------------------ start
$("stage").addEventListener("click", e => {
  const pc = e.target.closest("[data-pat]");
  if (pc) {   // a pattern in the legend: show just that one, zoomed to it (tap again, or "Show all", to go back)
    const k = pc.dataset.pat;
    S.patSel = k && S.patSel !== k ? k : null; S.patFocus = !!S.patSel;
    render(true); return;
  }
  const x = e.target.closest("[data-x]"); if (x) toggle(x.dataset.x, false);
});
$("quick").innerHTML = QUICK.map(s => `<button type="button" data-s="${s}">${esc(label(s))}</button>`).join("");
$("quick").addEventListener("click", e => { const b = e.target.closest("[data-s]"); if (b) load(b.dataset.s); });
$("pick").addEventListener("submit", e => {
  e.preventDefault();
  const s = normSym($("sym").value);
  if (!s) { $("status").className = "status err"; $("status").textContent = "Try an S&P 500 ticker or SPY, one of the site's 19 coins (BTC, ETH…) or a commodity (Gold, Oil, GC=F…)."; return; }
  $("sym").blur(); load(s);
});
let rzT; if (EMBED) addEventListener("resize", () => { clearTimeout(rzT); rzT = setTimeout(() => DATA && render(true), 150); });
window.addEventListener("hashchange", () => { const old = S.sym, oldTf = S.tf; readHash(); if (S.sym !== old) load(S.sym); else if (S.tf !== oldTf && DATA) {
  if (S.tf === "1m" && !DATA.frames["1m"]?.length) { if (!can1m(DATA)) { S.tf = oldTf; writeHash(); return; } load1m(DATA).then(() => { render(false); liveStart(); }).catch(() => { S.tf = oldTf; writeHash(); }); return; }
  render(false); if (DATA.kind === "crypto") liveStart(); } });
readHash();
syncDrawButtons();
drawPicker();
loadNames();
if (EMBED) document.querySelector(".picker").hidden = true;
if (PAT_ID) {
  $("status").textContent = "Loading the pattern…";
  const get = u => fetch(u).then(r => r.ok ? r.json() : null).catch(() => null);
  Promise.all([get("data/crypto/btc_patterns.json"), get(`data/crypto/btc_patterns_${PAT_ID.split("-")[0]}.json`)]).then(([js, tj]) => {
    const T = tj?.trades?.find(x => x.id === PAT_ID);
    if (!T) { $("status").className = "status err"; $("status").textContent = "That pattern isn't in the backtest file any more."; load(S.sym); return; }
    SIG = { a: { id: T.id, ticker: "BTC-USD", side: T.dir === "LONG" ? "bullish" : "bearish", price: T.entry, stop: T.stop, date: new Date(T.ts * 1000).toISOString().slice(0, 10), tf: T.tf, t: T.te, note: T.name },
      pat: T, rules: js.rules?.[`${T.fam}|${T.name}`], chart: null, trade: null, strat: null };
    S.tf = T.tf; writeHash();
    if ($("patResetBar")) $("patResetBar").hidden = false;
    if (!EMBED) {     // the trade first: no page intro, the pattern's card right above the chart
      document.querySelectorAll(".hero > :not(#status)").forEach(el => { el.style.display = "none"; });
      const sc = $("sigcard"), cd = $("card");
      if (sc && cd) cd.before(sc);
    }
    const bk = document.querySelector("nav.bar .back");     // back to this pattern's page on Chart Patterns
    if (bk && !EMBED) { bk.href = `patterns.html#${new URLSearchParams({ tf: T.tf, p: `${T.fam}|${T.name}|${T.dir}` })}`; bk.textContent = "‹ Patterns"; bk.setAttribute("aria-label", `Back to ${T.name} on Chart Patterns`); }
    S.on = S.on.filter(id => id !== "sr" && id !== "cpat");    // today's levels and forming patterns would only clutter an old trade (not saved)
    if (!S.on.includes("tlv")) S.on.push("tlv");              // instead: the support/resistance as it stood when this trade opened
    drawSigCard();
    load("BTC-USD");
  });
} else if (SIG_ID) {
  $("status").textContent = "Loading the signal…";
  loadSignal().then(sg => {
    SIG = sg;
    if (!sg) { $("status").className = "status err"; $("status").textContent = "That signal isn't in today's list any more."; load(S.sym); return; }
    if (sg.a.tf && TF_LIST.includes(sg.a.tf) && !location.hash.includes("/")) S.tf = sg.a.tf;
    drawSigCard();
    load(sg.a.ticker);
  });
} else load(S.sym);
