// Volume Profile page (docs/volumeprofile.html). Data: the Trendlines Worker, GET /?vp=SYM&range=N&hours=rth|all
// (worker/trendlines-worker.js). Maths: vp-lib.js. Hash: #AAPL/5/all (ticker / sessions / whole period or each day).
import { buildProfile, findNodes, sessionProfiles, candles, where } from "./vp-lib.js?v=1";

// The Trendlines page's "Data server" setting, if set on this device, is used instead.
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
// chart candle length (minutes, or "D") for each period
const CANDLE = { stock: { 1: 5, 5: 15, 20: 60, 60: 240, 250: "D" }, crypto: { 1: 15, 5: 60, 20: 240, 60: 720, 250: "D" } };
const RANGE_TXT = { 1: "last session", 5: "last 5 sessions", 20: "last 20 sessions", 60: "last 3 months", 250: "last year" };
const PREF_KEY = "signaldesk.vp";

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const dp = p => p >= 1000 ? 2 : p >= 1 ? 2 : p >= 0.01 ? 4 : 6;
const price = p => p == null ? "–" : (+p).toLocaleString("en-US", { minimumFractionDigits: dp(p), maximumFractionDigits: dp(p) });
const pct = x => x == null || !isFinite(x) ? "–" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x * 100).toFixed(2)}%`;
const bigNum = (x, unit) => {
  const s = x >= 1e9 ? `${(x / 1e9).toFixed(2)}bn` : x >= 1e6 ? `${(x / 1e6).toFixed(1)}m` : x >= 1e3 ? `${(x / 1e3).toFixed(0)}k` : `${Math.round(x)}`;
  return unit === "USD" ? `$${s}` : unit === "shares" ? `${s} shares` : `${s} ${unit}`;
};
const dayTxt = d => new Date(d + "T12:00:00Z").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });

function loadPrefs() { try { return JSON.parse(localStorage.getItem(PREF_KEY)) || {}; } catch { return {}; } }
function savePrefs() { try { localStorage.setItem(PREF_KEY, JSON.stringify({ hours: S.hours, rows: S.rows, va: S.va })); } catch {} }
const S = { sym: "SPY", range: 5, mode: "all", hours: "rth", rows: 60, va: 0.7, ...loadPrefs() };
let DATA = null, chartObj = null, loadId = 0;

let C = {};
function readColors() {
  const v = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  C = { bg: v("--c-bg"), grid: v("--c-grid"), txt: v("--c-txt"), up: v("--c-cup"), dn: v("--c-cdn"), accent: v("--accent"),
    poc: v("--res"), faint: v("--faint"), naked: "#c084fc", font: v("--font") };
}

function normSym(raw) {
  const s = (raw || "").trim().toUpperCase().replace(/\./g, "-").replace(/\s+/g, "");
  if (["SPX", "^SPX", "^GSPC"].includes(s)) return "SPY";
  if (TICKERS.includes(s)) return s;
  const m = s.replace(/[/_]/g, "-").match(/^([A-Z0-9]+?)(?:-?USDT?)?$/);
  return m && COINS.includes(m[1]) ? `${m[1]}-USD` : null;
}

// ------------------------------------------------------------------ hash + controls
function readHash() {
  const [sym, range, mode] = decodeURIComponent(location.hash.slice(1)).split("/");
  const s = normSym(sym);
  if (s) S.sym = s;
  if (CANDLE.stock[range]) S.range = +range;
  if (mode === "day" || mode === "all") S.mode = mode;
}
function writeHash() { history.replaceState(null, "", `#${S.sym}/${S.range}/${S.mode}`); }

function syncControls() {
  document.querySelectorAll(".seg[data-k] button").forEach(b => {
    b.setAttribute("aria-pressed", String(String(S[b.parentElement.dataset.k]) === b.dataset.v));
  });
  const many = S.range >= 5 && S.range <= 20;
  document.querySelector('.seg[data-k="mode"]').hidden = !many;   // per-day profiles for 5-20 sessions
  $("sym").value = S.sym;
  $("setsum").textContent = `${S.hours === "rth" ? "Regular hours" : "Extended hours"} · ${{ 30: "Coarse", 60: "Normal", 120: "Fine" }[S.rows] || S.rows} · ${Math.round(S.va * 100)}% value area`;
}

// ------------------------------------------------------------------ data
async function load() {
  const id = ++loadId;
  const crypto = S.sym.endsWith("-USD");
  writeHash(); syncControls();
  $("status").className = "status";
  $("status").textContent = `Loading ${S.sym}…`;
  if (!DATA || DATA.symbol !== S.sym) { if (chartObj) { chartObj.remove(); chartObj = null; } $("out").innerHTML = `<div class="loading">Loading full-market volume…</div>`; }
  try {
    const u = `${API.replace(/\/$/, "")}/?vp=${encodeURIComponent(S.sym)}&range=${S.range}&hours=${crypto ? "rth" : S.hours}`;
    const r = await fetch(u);
    const js = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
    if (id !== loadId) return;
    if (js.error || !r.ok) throw new Error(js.error || `HTTP ${r.status}`);
    DATA = js;
    $("status").textContent = "";
    render();
  } catch (e) {
    if (id !== loadId) return;
    $("status").className = "status err";
    $("status").textContent = `Couldn't load ${S.sym}: ${e.message || e}`;
    if (!DATA || DATA.symbol !== S.sym) $("out").innerHTML = "";
  }
}

// ------------------------------------------------------------------ render
function render() {
  const d = DATA, bars = d.bars, last = bars[bars.length - 1][4];
  const p = buildProfile(bars, { rows: S.rows, va: S.va });
  const nodes = findNodes(p);
  const perDay = S.mode === "day" && d.range >= 5 && d.range <= 20;
  const sess = d.range >= 5 && d.range <= 60 ? sessionProfiles(bars, { rows: S.rows, va: S.va }) : [];
  const naked = sess.filter(s => s.naked).slice(-6);
  const w = where(last, p);
  const crypto = d.market === "crypto";
  const period = crypto ? { 1: "last 24 hours", 5: "last 5 days", 20: "last 20 days", 60: "last 60 days", 250: "last 250 days" }[d.range] : RANGE_TXT[d.range];
  const first = d.sessions[0], lastDay = d.sessions[d.sessions.length - 1];

  const readTxt = w === "above"
    ? `Price is <b class="up">above the value area</b>: buyers have pushed it beyond where most of the volume of the ${esc(period)} traded. If it holds up here, the value area high (${price(p.vah)}) is the level traders watch as support; a fall back inside points to the POC (${price(p.poc)}).`
    : w === "below"
    ? `Price is <b class="down">below the value area</b>: sellers have pushed it beneath where most of the volume of the ${esc(period)} traded. If it stays down here, the value area low (${price(p.val)}) is the level traders watch as resistance; a move back inside points to the POC (${price(p.poc)}).`
    : `Price is <b class="in">inside the value area</b>, where most of the volume of the ${esc(period)} traded. Inside value, prices tend to rotate between the edges: ${price(p.val)} below and ${price(p.vah)} above, with the POC (${price(p.poc)}) in between.`;

  const levels = [
    { k: "va", t: "Value area high", p: p.vah }, { k: "poc", t: "Point of control", p: p.poc }, { k: "va", t: "Value area low", p: p.val },
    ...nodes.hvn.slice().sort((a, b) => b.share - a.share).slice(0, 4).map(n => ({ k: "hvn", t: "High volume node", p: n.price })),
    ...nodes.lvn.slice(0, 4).map(n => ({ k: "lvn", t: "Low volume node", p: n.price })),
    ...naked.map(s => ({ k: "naked", t: `Naked POC · ${dayTxt(s.date)}`, p: s.p.poc })),
  ];
  levels.push({ k: "now", t: "Last price", p: last, now: true });
  levels.sort((a, b) => b.p - a.p);
  const kindTxt = { va: "Value area", poc: "POC", hvn: "HVN", lvn: "LVN", naked: "Naked POC" };

  $("out").innerHTML = `
    <div class="card vp">
      <header>
        <h2><span>${esc(crypto ? "Crypto" : "Stock")} · ${esc(period)} · ${first === lastDay ? dayTxt(first) : `${dayTxt(first)} – ${dayTxt(lastDay)}`}</span>${esc(d.symbol)}</h2>
        <dl class="lv">
          <div><dt>Last</dt><dd style="color:var(--fg)">${price(last)}</dd></div>
          <div><dt>POC</dt><dd class="r">${price(p.poc)}</dd></div>
          <div><dt>Value area</dt><dd class="s">${price(p.val)} – ${price(p.vah)}</dd></div>
          <div><dt>Volume</dt><dd style="color:var(--fg)">${bigNum(p.total, d.unit)}</dd></div>
        </dl>
      </header>
      <div class="vp-chart" id="chart"></div>
      <div class="legend">
        <span><i class="box" style="background:${C.accent}"></i>Value area (${Math.round(p.vaShare * 100)}% of volume)</span>
        <span><i class="box" style="background:${C.faint}"></i>Outside value</span>
        <span><i class="box" style="background:${C.poc}"></i>Point of control</span>
        ${naked.length ? `<span><i class="dash" style="border-color:${C.naked}"></i>Naked POC</span>` : ""}
        ${perDay ? `<span>Each day's own profile, drawn from its first candle</span>` : ""}
      </div>
      <p class="read">${readTxt}</p>
    </div>

    <div class="panel" style="margin-top:22px">
      <h2>Key levels</h2>
      <p class="lead">From the whole ${esc(period)}, highest first. Distance is from the last price.</p>
      <div class="tbl"><table>
        <thead><tr><th>Level</th><th>Price</th><th>Distance</th><th class="hm">Type</th></tr></thead>
        <tbody>${levels.map(l => `<tr class="${l.now ? "now" : ""}"><td>${esc(l.t)}</td><td>${price(l.p)}</td>
          <td class="${l.now ? "" : l.p > last ? "pos" : "neg"}">${l.now ? "" : pct(l.p / last - 1)}</td>
          <td class="hm">${l.now ? "" : `<span class="kind ${l.k}">${kindTxt[l.k]}</span>`}</td></tr>`).join("")}</tbody>
      </table></div>
      <p class="note">${d.range === 1 || d.range === 250 ? "Naked POCs are listed for the 5-day, 20-day and 3-month periods. " : ""}Data: ${esc(d.source)}, ${d.tf}-minute bars${crypto ? "" : `, ${d.hours === "rth" ? "regular hours" : "extended hours"}`}, up to ${new Date(d.end).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })} (your time).</p>
    </div>`;

  if (chartObj) chartObj.remove();
  chartObj = drawChart($("chart"), bars, p, perDay ? sess : null, naked, crypto);
}

// ------------------------------------------------------------------ chart: candles + profile drawn on a canvas on top
function drawChart(el, bars, p, sess, naked, crypto) {
  const LW = LightweightCharts;
  const cd = candles(bars, CANDLE[crypto ? "crypto" : "stock"][DATA.range]);
  const chart = LW.createChart(el, {
    autoSize: true,
    layout: { background: { type: "solid", color: C.bg }, textColor: C.txt, fontFamily: C.font, fontSize: 11 },
    grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
    rightPriceScale: { borderColor: C.grid, scaleMargins: { top: 0.06, bottom: 0.06 } },
    timeScale: { borderColor: C.grid, timeVisible: DATA.range < 250, secondsVisible: false, rightOffset: sess ? 2 : Math.max(6, Math.round(cd.length * 0.42)) },
    crosshair: { mode: LW.CrosshairMode.Normal, vertLine: { color: C.txt, labelBackgroundColor: C.txt }, horzLine: { color: C.txt, labelBackgroundColor: C.txt } },
    localization: { priceFormatter: price },
    handleScale: { axisPressedMouseMove: { time: true, price: false } },
  });
  const cs = chart.addCandlestickSeries({
    upColor: C.up + "b3", downColor: C.dn + "b3", wickUpColor: C.up + "b3", wickDownColor: C.dn + "b3", borderVisible: false,
    priceLineColor: C.txt, priceLineStyle: LW.LineStyle.Dotted,
    autoscaleInfoProvider: orig => {                           // keep the whole profile in view
      const r = orig();
      if (!r) return r;
      return { ...r, priceRange: { minValue: Math.min(r.priceRange.minValue, p.lo), maxValue: Math.max(r.priceRange.maxValue, p.hi) } };
    },
  });
  cs.setData(cd.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
  cs.createPriceLine({ price: p.poc, color: C.poc, lineWidth: 2, lineStyle: LW.LineStyle.Solid, axisLabelVisible: true, title: sess ? "POC" : "" });
  cs.createPriceLine({ price: p.vah, color: C.accent, lineWidth: 1, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: true, title: sess ? "VAH" : "" });
  cs.createPriceLine({ price: p.val, color: C.accent, lineWidth: 1, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: true, title: sess ? "VAL" : "" });
  for (const s of naked) cs.createPriceLine({ price: s.p.poc, color: C.naked, lineWidth: 1, lineStyle: LW.LineStyle.SparseDotted, axisLabelVisible: false, title: "" });

  const cv = document.createElement("canvas");
  cv.className = "ov";
  el.appendChild(cv);
  const ctx = cv.getContext("2d");
  const ts = chart.timeScale();
  const step = CANDLE[crypto ? "crypto" : "stock"][DATA.range];
  const candleKey = t => step === "D" ? Math.floor(t / 86400) * 86400 : Math.floor(t / (step * 60)) * step * 60;

  let sig = "", alive = true;
  function rows(prof, x0, wMax, maxVol, dir, alpha = 1) {             // dir 1: grow right from x0, -1: grow left
    for (let i = 0; i < prof.rows; i++) {
      const v = prof.vol[i];
      if (!(v > 0)) continue;
      const y1 = cs.priceToCoordinate(prof.lo + (i + 1) * prof.step), y0 = cs.priceToCoordinate(prof.lo + i * prof.step);
      if (y1 == null || y0 == null) continue;
      const w = (v / maxVol) * wMax;
      const inVa = i >= prof.vaRows[0] && i <= prof.vaRows[1];
      ctx.globalAlpha = alpha;
      ctx.fillStyle = i === prof.pocRow ? C.poc + "e6" : inVa ? C.accent + "73" : C.faint + "4d";
      const h = Math.max(1, Math.abs(y0 - y1) - (Math.abs(y0 - y1) > 4 ? 1 : 0));
      ctx.fillRect(dir > 0 ? x0 : x0 - w, Math.min(y0, y1), w, h);
    }
  }
  function draw() {
    if (!alive) return;
    const W = el.clientWidth, H = el.clientHeight;
    const plotW = ts.width(), plotH = H - (ts.height() || 0);
    const key = [W, H, plotW, JSON.stringify(ts.getVisibleLogicalRange()), cs.priceToCoordinate(p.lo), cs.priceToCoordinate(p.hi)].join("|");
    if (key !== sig) {
      sig = key;
      const dpr = window.devicePixelRatio || 1;
      if (cv.width !== W * dpr || cv.height !== H * dpr) { cv.width = W * dpr; cv.height = H * dpr; cv.style.width = `${W}px`; cv.style.height = `${H}px`; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      ctx.save();
      ctx.beginPath(); ctx.rect(0, 0, plotW, plotH); ctx.clip();
      if (sess) {
        sess.forEach((s, k) => {
          const xa = ts.timeToCoordinate(candleKey(s.from)), xb = ts.timeToCoordinate(candleKey(s.to));
          if (xa == null || xb == null) return;
          const next = sess[k + 1] ? ts.timeToCoordinate(candleKey(sess[k + 1].from)) : null;
          const span = Math.max(12, (next != null ? next : xb) - xa);
          rows(s.p, xa, span * 0.85, Math.max(...s.p.vol), 1, 0.7);
          ctx.globalAlpha = 1;
          ctx.strokeStyle = C.poc; ctx.lineWidth = 1.5;          // each day's POC across its own session
          const y = cs.priceToCoordinate(s.p.poc);
          if (y != null) { ctx.beginPath(); ctx.moveTo(xa, y); ctx.lineTo(Math.max(xb, xa + span * 0.85), y); ctx.stroke(); }
        });
      } else {
        const wMax = Math.min(plotW * 0.34, 260);
        rows(p, plotW, wMax, Math.max(...p.vol), -1);
        ctx.font = `600 11px ${C.font}`; ctx.textAlign = "right"; ctx.textBaseline = "middle";   // labels left of the profile
        for (const [t, pr, col] of [["VAH", p.vah, C.accent], ["POC", p.poc, C.poc], ["VAL", p.val, C.accent]]) {
          const y = cs.priceToCoordinate(pr);
          if (y == null) continue;
          const tw = ctx.measureText(t).width, x = plotW - wMax - 8;
          ctx.fillStyle = C.bg; ctx.fillRect(x - tw - 4, y - 8, tw + 8, 16);
          ctx.fillStyle = col; ctx.fillText(t, x, y);
        }
      }
      ctx.restore();
    }
    requestAnimationFrame(draw);
  }
  ts.fitContent();
  requestAnimationFrame(draw);
  return { remove() { alive = false; chart.remove(); } };
}

// ------------------------------------------------------------------ start
function init() {
  readColors();
  $("syms").innerHTML = [...TICKERS, ...COINS.map(c => `${c}-USD`)].map(s => `<option value="${s}">`).join("");
  // every S&P 500 stock (data/universe.json, engine/sp500_list.py)
  fetch("data/universe.json").then(r => r.ok ? r.json() : null).catch(() => null).then(u => {
    for (const t of u?.sp500 || []) if (!TICKERS.includes(t)) TICKERS.push(t);
    $("syms").innerHTML = [...TICKERS, ...COINS.map(c => `${c}-USD`)].map(s => `<option value="${s}">`).join("");
  });
  readHash();
  $("pick").addEventListener("submit", ev => {
    ev.preventDefault();
    const s = normSym($("sym").value);
    if (!s) { $("status").className = "status err"; $("status").textContent = "Pick SPY, an S&P 500 stock or one of the app's 19 coins (e.g. BTC)."; return; }
    S.sym = s; load();
  });
  document.querySelectorAll(".seg[data-k] button").forEach(b => b.addEventListener("click", () => {
    const k = b.parentElement.dataset.k, v = b.dataset.v;
    const before = S[k];
    S[k] = k === "mode" || k === "hours" ? v : +v;
    if (k === "hours" || k === "rows" || k === "va") savePrefs();
    if (S[k] === before) return;
    if (k === "range" || (k === "hours" && !S.sym.endsWith("-USD"))) load();
    else { writeHash(); syncControls(); if (DATA) render(); }
  }));
  window.addEventListener("hashchange", () => { const was = `${S.sym}/${S.range}`; readHash(); if (`${S.sym}/${S.range}` !== was) load(); else { syncControls(); if (DATA) render(); } });
  load();
}
init();
