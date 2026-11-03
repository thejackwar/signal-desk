// The Options page: Learn (payoff of each strategy), Builder (a strategy on a real option chain),
// Backtest (modeled, engine/options_backtest.py) and Live (paper trades, engine/options_live.py).
import * as L from "./options-lib.js?v=3";

// Address of the Cloudflare Worker (worker/trendlines-worker.js, which also serves option chains).
// The Trendlines page's "Data server" setting, if set on this device, is used instead.
const DEFAULT_API = "https://trendlines.guerragiacomo00.workers.dev";
const TICKERS = ("SPY AAPL ABBV ABT ACN ADBE AIG AMD AMGN AMT AMZN AVGO AXP BA BAC BK BKNG BLK BMY BRK-B C CAT " +
  "CHTR CL CMCSA COF COP COST CRM CSCO CVS CVX DE DHR DIS DUK EMR F FDX GD GE GILD GM GOOGL GS HD HON IBM INTC " +
  "INTU ISRG JNJ JPM KO LIN LLY LMT LOW MA MCD MDLZ MDT MET META MMM MO MRK MS MSFT NEE NFLX NKE NOW NVDA ORCL " +
  "PEP PFE PG PLTR PM PYPL QCOM RTX SBUX SCHW SO SPG T TGT TMO TMUS TSLA TXN UBER UNH UNP UPS USB V VZ WFC WMT XOM").split(" ");
const API = (() => {
  try { return (JSON.parse(localStorage.getItem("signaldesk.trendlines")) || {}).api || DEFAULT_API; } catch { return DEFAULT_API; }
})();
const RISK_LABEL = { defined: "Limited loss", stock: "Loss like owning shares", premium: "Can lose all it costs" };

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v, d = 0) => v == null ? "No limit" : (v < 0 ? "−$" : "$") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (v, d = 0) => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + "$" + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 1) => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d) + "%";
const px = v => v == null ? "–" : v.toFixed(2);
const cls = v => v > 0 ? "pos" : v < 0 ? "neg" : "";
const shortDate = s => new Date(s + "T12:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "2-digit", timeZone: "UTC" });
const nyToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());

let C = {};
function readColors() {
  const v = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  C = { card: v("--card"), fg: v("--fg"), muted: v("--muted"), faint: v("--faint"), grid: v("--c-grid"), hair: v("--hair"), up: v("--up-line"),
        dn: v("--down-line"), accent: v("--accent"), acc2: v("--accent2") || v("--accent"), res: v("--res"), font: v("--font") };
}
readColors();
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => { readColors(); route(); });

// ------------------------------------------------------------------ routing
// #learn, #learn/iron_condor, #build/SPY/iron_condor, #backtest/iron_condor, #live
let tab = "learn";
function route() {
  const [t, a, b] = decodeURIComponent(location.hash.slice(1)).split("/");
  tab = ["learn", "build", "backtest", "live"].includes(t) ? t : "learn";
  [...$("tabs").children].forEach(btn => btn.setAttribute("aria-pressed", btn.dataset.v === tab));
  for (const v of ["learn", "build", "backtest", "live"]) $("v-" + v).hidden = v !== tab;
  if (tab === "learn") learn(L.META[a] ? a : null);
  if (tab === "build") builder(a, b);
  if (tab === "backtest") backtest(L.META[a] ? a : null);
  if (tab === "live") live();
}
$("tabs").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  location.hash = b.dataset.v;
});
addEventListener("hashchange", route);

// ------------------------------------------------------------------ charts (plain SVG)
const W = 640, H = 320, PL = 64, PR = 12, PT = 14, PB = 28;
function niceTicks(lo, hi, n = 5) {
  const span = hi - lo || 1, raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(k => k * mag).find(s => span / s <= n) || mag * 10;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}
const shortMoney = v => { const a = Math.abs(v); return (v < 0 ? "−" : "") + "$" + (a >= 1e6 ? (a / 1e6).toFixed(1) + "M" : a >= 1e4 ? (a / 1e3).toFixed(0) + "k" : a >= 1e3 ? (a / 1e3).toFixed(1) + "k" : a.toFixed(0)); };

// pos: a position (options-lib); opts: { elapsed (years), ivMult, w (x range ±share of price) }
function payoffSvg(pos, a, { elapsed = 0, ivMult = 1, id = "pf" } = {}) {
  const T = a.T, S = pos.S;
  const ks = pos.legs.map(l => l.K);
  const avgIv = pos.legs.reduce((s, l) => s + l.iv, 0) / pos.legs.length;
  let w = Math.min(0.6, Math.max(0.12, 2.6 * avgIv * Math.sqrt(Math.max(T, 7 / 365))));
  w = Math.max(w, ...ks.map(k => Math.abs(k / S - 1) * 1.25));
  const x0 = S * (1 - w), x1 = S * (1 + w), N = 220;
  const xs = Array.from({ length: N + 1 }, (_, i) => x0 + (x1 - x0) * i / N);
  const now = { ...pos, legs: pos.legs.map(l => ({ ...l, iv: l.iv * ivMult })) };
  const yE = xs.map(x => L.pnlAt(pos, x, T));
  const yN = xs.map(x => L.pnlAt(now, x, Math.min(elapsed, T)));
  let lo = Math.min(...yE, ...yN, 0), hi = Math.max(...yE, ...yN, 0);
  const pad = (hi - lo) * 0.08 || 10; lo -= pad; hi += pad;
  const X = x => PL + (x - x0) / (x1 - x0) * (W - PL - PR), Y = y => PT + (hi - y) / (hi - lo) * (H - PT - PB);
  const path = ys => ys.map((y, i) => `${i ? "L" : "M"}${X(xs[i]).toFixed(1)},${Y(y).toFixed(1)}`).join("");
  const y0 = Y(0);
  const area = `${path(yE)}L${X(x1).toFixed(1)},${y0.toFixed(1)}L${X(x0).toFixed(1)},${y0.toFixed(1)}Z`;
  let g = `<defs><clipPath id="${id}a"><rect x="0" y="0" width="${W}" height="${y0}"/></clipPath><clipPath id="${id}b"><rect x="0" y="${y0}" width="${W}" height="${H}"/></clipPath></defs>`;
  for (const t of niceTicks(lo, hi, 4)) g += `<line x1="${PL}" x2="${W - PR}" y1="${Y(t)}" y2="${Y(t)}" stroke="${C.grid}"/><text x="${PL - 6}" y="${Y(t) + 4}" text-anchor="end" font-size="14" fill="${C.faint}">${shortMoney(t)}</text>`;
  for (const t of niceTicks(x0, x1, 6)) g += `<text x="${X(t)}" y="${H - 8}" text-anchor="middle" font-size="14" fill="${C.faint}">${t >= 100 ? t.toFixed(0) : t.toFixed(t >= 10 ? 1 : 2)}</text>`;
  g += `<path d="${area}" fill="${C.up}" fill-opacity=".13" clip-path="url(#${id}a)"/><path d="${area}" fill="${C.dn}" fill-opacity=".12" clip-path="url(#${id}b)"/>`;
  g += `<line x1="${PL}" x2="${W - PR}" y1="${y0}" y2="${y0}" stroke="${C.muted}" stroke-width="1"/>`;
  g += `<line x1="${X(S)}" x2="${X(S)}" y1="${PT}" y2="${H - PB}" stroke="${C.faint}" stroke-dasharray="3 4"/>`;
  g += `<text x="${X(S) + 4}" y="${PT + 10}" font-size="14" fill="${C.muted}">now ${S.toFixed(2)}</text>`;
  g += `<path d="${path(yN)}" fill="none" stroke="${C.acc2}" stroke-width="2" stroke-dasharray="6 5"/>`;
  g += `<path d="${path(yE)}" fill="none" stroke="${C.accent}" stroke-width="2.6"/>`;
  for (const b of a.breakevens) if (b > x0 && b < x1) g += `<circle cx="${X(b)}" cy="${y0}" r="4" fill="${C.card || "#fff"}" stroke="${C.fg}" stroke-width="1.5"/>`;
  g += `<line class="hx" x1="0" x2="0" y1="${PT}" y2="${H - PB}" stroke="${C.fg}" stroke-opacity=".35" visibility="hidden"/>`;
  return { svg: `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Profit and loss chart" data-x0="${x0}" data-x1="${x1}">${g}</svg>`, xs, yE, yN, X };
}

function attachTip(box, chart, label) {
  const svg = box.querySelector("svg"), tip = box.querySelector(".tip"), hx = svg.querySelector(".hx");
  const move = e => {
    const r = svg.getBoundingClientRect(), vx = (e.clientX - r.left) / r.width * W;
    if (vx < PL || vx > W - PR) return hide();
    const i = Math.round((vx - PL) / (W - PL - PR) * (chart.xs.length - 1));
    const P = chart.xs[i];
    hx.setAttribute("x1", chart.X(P)); hx.setAttribute("x2", chart.X(P)); hx.setAttribute("visibility", "visible");
    tip.hidden = false;
    tip.innerHTML = `${label} ${P.toFixed(2)}: <span class="${cls(chart.yE[i])}">${signed(chart.yE[i])}</span> at expiry · <span class="${cls(chart.yN[i])}">${signed(chart.yN[i])}</span> on the dashed line`;
  };
  const hide = () => { tip.hidden = true; hx.setAttribute("visibility", "hidden"); };
  svg.addEventListener("pointermove", move); svg.addEventListener("pointerleave", hide); svg.addEventListener("pointerdown", move);
}

function miniPayoff(id) {
  const p = L.examplePosition(id), T = L.nearT(p);
  const xs = Array.from({ length: 61 }, (_, i) => 100 * (0.8 + 0.4 * i / 60)), ys = xs.map(x => L.pnlAt(p, x, T));
  const lo = Math.min(...ys, 0), hi = Math.max(...ys, 0), w = 300, h = 54;
  const X = i => i / 60 * w, Y = y => 4 + (hi - y) / (hi - lo || 1) * (h - 8);
  const d = ys.map((y, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(y).toFixed(1)}`).join("");
  return `<svg class="mini" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><line x1="0" x2="${w}" y1="${Y(0)}" y2="${Y(0)}" stroke="${C.hair}"/><path d="${d}" fill="none" stroke="${C.accent}" stroke-width="2.2" vector-effect="non-scaling-stroke"/></svg>`;
}

// equity lines over dates: series [{ name, values, color, dash }]
function lineSvg(dates, series, { log = true } = {}) {
  const all = series.flatMap(s => s.values).filter(v => v > 0);
  const f = log ? Math.log : (v => v);
  let lo = f(Math.min(...all)), hi = f(Math.max(...all));
  const pad = (hi - lo) * 0.06 || 1; lo -= pad; hi += pad;
  const n = dates.length;
  const X = i => PL + i / (n - 1) * (W - PL - PR), Y = v => PT + (hi - f(Math.max(v, 1e-9))) / (hi - lo) * (H - PT - PB);
  let g = "";
  const ticks = log ? niceTicks(Math.exp(lo), Math.exp(hi), 5) : niceTicks(lo, hi, 5);
  for (const t of ticks) if (t > 0) g += `<line x1="${PL}" x2="${W - PR}" y1="${Y(t)}" y2="${Y(t)}" stroke="${C.grid}"/><text x="${PL - 6}" y="${Y(t) + 4}" text-anchor="end" font-size="14" fill="${C.faint}">${shortMoney(t)}</text>`;
  let lastY = "";
  dates.forEach((d, i) => {
    const y = d.slice(0, 4);
    if (y !== lastY && (+y % 2 === 0 || n < 60)) { g += `<text x="${X(i)}" y="${H - 8}" text-anchor="middle" font-size="14" fill="${C.faint}">${y}</text>`; }
    lastY = y;
  });
  for (const s of series) g += `<path d="${s.values.map((v, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("")}" fill="none" stroke="${s.color}" stroke-width="${s.dash ? 1.8 : 2.4}"${s.dash ? ' stroke-dasharray="5 4"' : ""}/>`;
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Account value over time">${g}</svg>`;
}

// ------------------------------------------------------------------ Learn
const lstate = { days: 0, vol: 25 };
function describeLegs(pos, { dte = 35, far = 63, shareWord = true } = {}) {
  const out = [];
  if (pos.shares) out.push(`Buy ${pos.shares} shares at $${pos.S.toFixed(2)}`);
  for (const l of pos.legs) {
    const days = l.exp === "far" ? far : dte;
    out.push(`${l.s > 0 ? "Buy" : "Sell"} ${l.qty} ${l.t === "C" ? "call" : "put"}, strike ${l.K}, ${days} days, for $${l.price.toFixed(2)} a share ($${(l.price * 100 * l.qty).toFixed(0)})`);
  }
  return out;
}
function learn(id) {
  const v = $("v-learn");
  if (!id) {
    lstate.days = 0; lstate.vol = 25;
    v.innerHTML = L.GROUPS.map(g => `<div class="grp"><h3>${g}</h3><div class="sgrid">${L.ORDER.filter(s => L.META[s].group === g).map(s => {
      const m = L.META[s];
      return `<a class="scard" href="#learn/${s}"><b>${esc(m.name)}</b><p>${esc(m.short)}</p><div class="pills"><span class="pill">${esc(m.outlook)}</span><span class="pill r-${m.risk}">${RISK_LABEL[m.risk]}</span></div>${miniPayoff(s)}</a>`;
    }).join("")}</div></div>`).join("") +
    `<p class="note">The charts show profit and loss at expiry against the stock price: left is lower, right is higher. Tap a strategy to see it worked out.</p>`;
    document.title = "Options";
    return;
  }
  const m = L.META[id];
  document.title = `${m.name} · Options`;
  v.innerHTML = `<a class="back2" href="#learn">‹ All strategies</a>
  <div class="panel">
    <h2>${esc(m.name)}</h2>
    <div class="pills"><span class="pill">${esc(m.group)}</span><span class="pill">${esc(m.outlook)}</span><span class="pill">${esc(m.vol)}</span><span class="pill r-${m.risk}">${RISK_LABEL[m.risk]}</span></div>
    <p class="lead">${esc(m.short)}</p>
    <div id="lex"></div>
    <div class="chartbox" id="lchart"></div>
    <div class="legend" id="llegend"></div>
    <div class="sliders">
      <label><span>Days passed: <b id="ldaysv">${lstate.days}</b> of 35</span><input type="range" id="ldays" min="0" max="35" value="${lstate.days}"></label>
      <label><span>Volatility: <b id="lvolv">${lstate.vol}%</b> (changes the prices)</span><input type="range" id="lvol" min="10" max="80" step="5" value="${lstate.vol}"></label>
    </div>
    <dl class="stats" id="lgreeks"></dl>
    <p class="note">Delta: how many shares the position behaves like right now. Time decay: what a day passing does to it with nothing else changing. Volatility: what a 1-point rise in implied volatility does.</p>
    <h4>How it works</h4><ol>${m.how.map(s => `<li>${esc(s)}</li>`).join("")}</ol>
    <h4>Works best</h4><p>${esc(m.good)}</p>
    <h4>Goes wrong</h4><p>${esc(m.bad)}</p>
    <div class="btnrow"><a class="btn ghost" href="#build/SPY/${id}">Try it on a real stock ›</a><a class="btn ghost" href="#backtest/${id}">How it did since 2010 ›</a></div>
  </div>`;
  const draw = () => {
    const pos = L.examplePosition(id, { atm: lstate.vol / 100 });
    const a = L.analyze(pos);
    const elapsed = lstate.days / 365;
    const now = { ...pos, legs: pos.legs.map(l => ({ ...l, T: Math.max(l.T - elapsed, 0) })) };
    const g = L.netGreeks(now);
    const cap = L.capital(id, pos, a);
    const prem = pos.legs.reduce((s, l) => s - l.s * l.qty * 100 * l.price, 0);
    $("lex").innerHTML = `<h4>Example: a $100 stock, ${lstate.vol}% volatility, 35 days to expiry</h4>
      <ol>${describeLegs(pos).map(s => `<li>${esc(s)}</li>`).join("")}</ol>
      <p class="note">${prem >= 0 ? `You receive $${prem.toFixed(0)} up front.` : `You pay $${(-prem).toFixed(0)} up front.`} Prices from Black-Scholes; real ones differ a little and you also pay the bid/ask spread and a commission.</p>
      <dl class="stats">
        <div><dt>Max gain</dt><dd class="pos">${money(a.maxGain)}</dd></div>
        <div><dt>Max loss</dt><dd class="neg">${money(a.maxLoss)}</dd></div>
        <div><dt>Breakeven${a.breakevens.length > 1 ? "s" : ""}</dt><dd>${a.breakevens.map(b => "$" + b.toFixed(2)).join(" · ") || "–"}</dd></div>
        <div><dt>Chance of profit</dt><dd>${(a.pop * 100).toFixed(0)}%</dd></div>
        <div><dt>Money tied up</dt><dd>${cap == null ? "–" : money(cap)}</dd></div>
      </dl>`;
    const ch = payoffSvg(pos, a, { elapsed, id: "lp" });
    $("lchart").innerHTML = ch.svg + `<div class="tip" hidden></div>`;
    attachTip($("lchart"), ch, "Stock at");
    $("llegend").innerHTML = `<span><i style="border-color:${C.accent}"></i>At expiry</span><span><i class="dash" style="border-color:${C.acc2}"></i>After ${lstate.days} day${lstate.days === 1 ? "" : "s"} (${35 - lstate.days} left)</span><span>○ breakeven</span>`;
    $("lgreeks").innerHTML = greekStats(g);
    $("ldaysv").textContent = lstate.days; $("lvolv").textContent = lstate.vol + "%";
  };
  draw();
  $("ldays").addEventListener("input", e => { lstate.days = +e.target.value; draw(); });
  $("lvol").addEventListener("input", e => { lstate.vol = +e.target.value; draw(); });
}
const greekStats = (g, extra = "") => `<div><dt>Delta (like owning)</dt><dd>${g.delta >= 0 ? "" : "−"}${Math.abs(g.delta).toFixed(0)} shares</dd></div>
  <div><dt>Time decay a day</dt><dd class="${cls(g.theta)}">${signed(g.theta, 2)}</dd></div>
  <div><dt>If volatility +1 pt</dt><dd class="${cls(g.vega)}">${signed(g.vega, 2)}</dd></div>${extra}`;

// ------------------------------------------------------------------ Builder
const B = { sym: "SPY", sid: "covered_call", exp: null, qty: 1, mode: "mid", over: {}, days: 0, chain: null, seq: 0 };
async function fetchJson(url, ms = 25000) {
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), ms);
  try { const r = await fetch(url, { signal: ctl.signal, cache: "no-store" }); const js = await r.json().catch(() => ({})); if (!r.ok || js.error) throw new Error(js.error || `HTTP ${r.status}`); return js; }
  finally { clearTimeout(t); }
}

// live chain from the Worker, or a modeled one from the latest daily closes
async function loadChain(sym) {
  try {
    const js = await fetchJson(`${API}/?options=${encodeURIComponent(sym)}`);
    const q = new Map();
    for (const [e, t, K, bid, ask, iv, delta, gamma, theta, vega] of js.chain) q.set(`${e}|${t}|${K}`, { bid, ask, iv, delta, gamma, theta, vega });
    return { sym, spot: js.spot, expirations: js.expirations, q, source: "live", asOf: js.end };
  } catch (e) {
    const m = await modeledChain(sym);
    m.why = e.message;
    return m;
  }
}
async function modeledChain(sym) {
  let S, atm;
  if (sym === "SPY") {
    const lv = await fetchJson("data/options/live.json", 15000).catch(() => ({}));
    if (!lv.spot) throw new Error("live quotes and SPY's last close are both unavailable right now");
    S = lv.spot.S; atm = L.ATM_VIX * lv.spot.vix / 100;
  } else {
    const js = await fetchJson(`data/stocks/${encodeURIComponent(sym)}.json`, 15000);
    const c = js.c, r = c.slice(1).map((v, i) => Math.log(v / c[i]));
    const sd = a => { const m = a.reduce((s, x) => s + x, 0) / a.length; return Math.sqrt(a.reduce((s, x) => s + (x - m) ** 2, 0) / (a.length - 1)) * Math.sqrt(252); };
    S = c[c.length - 1];
    atm = Math.min(1.5, Math.max(0.12, (0.5 * sd(r.slice(-20)) + 0.5 * sd(r.slice(-60))) * 1.15));
  }
  const today = nyToday(), exps = [];
  for (let d = today; exps.length < 4;) { const e = L.nextExpiry(d, 0); if (e >= today) exps.push(e); d = L.isoDay(new Date(Date.parse(e + "T00:00:00Z") + 864e5)); }
  const q = new Map(), slope = sym === "SPY" ? L.SKEW_INDEX : L.SKEW_STOCK, step = sym === "SPY" ? 1 : L.strikeStep(S);
  for (const e of exps) {
    const T = Math.max(L.daysBetween(today, e), 0.5) / 365;
    for (const K of L.strikeGrid(S, step)) {
      if (K < S * 0.65 || K > S * 1.35) continue;
      for (const t of ["C", "P"]) {
        const iv = L.skewIv(atm, S, K, T, slope), mid = L.bs(S, K, T, iv, t), h = L.halfSpread(mid), g = L.greeks(S, K, T, iv, t);
        q.set(`${e}|${t}|${K}`, { bid: Math.max(mid - h, 0), ask: mid + h, iv, ...g });
      }
    }
  }
  return { sym, spot: S, expirations: exps, q, source: "model", atm };
}

function strikesFor(ch, exp, t) {
  const out = [];
  for (const k of ch.q.keys()) { const [e, tt, K] = k.split("|"); if (e === exp && tt === t) out.push(+K); }
  return out.sort((a, b) => a - b);
}
function buildPosition(ch) {
  const st = L.STRATEGIES[B.sid], today = nyToday();
  const exps = ch.expirations;
  if (!B.exp || !exps.includes(B.exp)) {
    const want = L.nextExpiry(today);
    B.exp = exps.find(e => e >= want) || exps[exps.length - 1];
  }
  const far = exps[exps.indexOf(B.exp) + 1] || null;
  const legsDef = st.legs;
  if (legsDef.some(l => l.exp === "far") && !far) return { error: "There's no later expiry in the chain for the second leg: pick an earlier expiry." };
  const expOf = l => l.exp === "far" ? far : B.exp;
  const S = ch.spot;
  const Tof = e => Math.max(L.daysBetween(today, e), 0.5) / 365;
  const quote = (e, t, K) => ch.q.get(`${e}|${t}|${K}`);
  const deltaOf = (leg, K) => {
    const q = quote(expOf(leg), leg.t, K);
    if (!q || !(q.bid > 0) || !(q.ask > 0)) return null;
    if (q.delta != null) return Math.abs(q.delta);
    const iv = q.iv || L.impliedVol((q.bid + q.ask) / 2, S, K, Tof(expOf(leg)), leg.t);
    return iv ? Math.abs(L.greeks(S, K, Tof(expOf(leg)), iv, leg.t).delta) : null;
  };
  const grid = [...new Set([...strikesFor(ch, B.exp, "C"), ...strikesFor(ch, B.exp, "P")])];
  if (!grid.length) return { error: "No strikes in this expiry." };
  const auto = L.pickStrikesBy(legsDef, S, grid, deltaOf);
  const legs = legsDef.map((l, i) => {
    const e = expOf(l), K = B.over[i] ?? auto[i], q = quote(e, l.t, K) || {};
    const bid = q.bid ?? null, ask = q.ask ?? null, T = Tof(e);
    const mid = bid != null && ask != null ? (bid + ask) / 2 : (q.ask ?? q.bid ?? 0);
    const price = B.mode === "natural" ? (l.s > 0 ? (ask ?? mid) : (bid ?? mid)) : mid;
    const iv = q.iv || L.impliedVol(mid, S, K, T, l.t) || ch.atm || 0.25;
    return { t: l.t, s: l.s, K, qty: B.qty, T, iv, price, bid, ask, mid, delta: q.delta, e, auto: B.over[i] == null };
  });
  return { pos: { S, shares: 100 * st.stock * B.qty, legs }, far };
}

function builderShell() {
  const v = $("v-build");
  if (v.dataset.ready) return;
  v.dataset.ready = "1";
  v.innerHTML = `<div class="panel">
    <form class="form" id="bform" autocomplete="off">
      <input id="bsym" list="btick" aria-label="Ticker" value="${B.sym}" spellcheck="false" autocapitalize="characters">
      <datalist id="btick">${TICKERS.map(t => `<option value="${t}">`).join("")}</datalist>
      <select id="bsid" aria-label="Strategy">${L.GROUPS.map(g => `<optgroup label="${g}">${L.ORDER.filter(s => L.META[s].group === g).map(s => `<option value="${s}">${esc(L.META[s].name)}</option>`).join("")}</optgroup>`).join("")}</select>
      <button class="btn">Show</button>
    </form>
    <p class="note" id="bstatus"></p>
    <div id="bout"></div>
  </div>`;
  $("bform").addEventListener("submit", e => {
    e.preventDefault();
    const s = $("bsym").value.trim().toUpperCase().replace(/\./g, "-");
    location.hash = `build/${s}/${$("bsid").value}`;
  });
  $("bsid").addEventListener("change", () => { location.hash = `build/${B.sym}/${$("bsid").value}`; });
}

async function builder(sym, sid) {
  builderShell();
  sym = (sym || B.sym).toUpperCase(); sid = L.META[sid] ? sid : B.sid;
  if (["SPX", "^SPX"].includes(sym)) sym = "SPY";
  $("bsym").value = sym; $("bsid").value = sid;
  if (sid !== B.sid) { B.over = {}; B.sid = sid; }
  document.title = `${sym} ${L.META[sid].name} · Options`;
  if (!TICKERS.includes(sym)) { $("bstatus").textContent = `${sym} isn't available: pick SPY or an S&P 100 stock.`; $("bstatus").className = "note err"; return; }
  if (!B.chain || B.chain.sym !== sym) {
    B.sym = sym; B.over = {}; B.exp = null; B.days = 0;
    const seq = ++B.seq;
    $("bstatus").className = "note"; $("bstatus").textContent = `Loading ${sym}'s option chain…`;
    $("bout").innerHTML = "";
    try {
      const ch = await loadChain(sym);
      if (seq !== B.seq) return;
      B.chain = ch;
    } catch (e) {
      if (seq !== B.seq) return;
      $("bstatus").className = "note err"; $("bstatus").textContent = `Couldn't load ${sym}: ${e.message}`; return;
    }
  }
  renderBuilder();
}

function renderBuilder() {
  const ch = B.chain, m = L.META[B.sid], st = L.STRATEGIES[B.sid];
  $("bstatus").className = "note";
  $("bstatus").innerHTML = ch.source === "live"
    ? `${esc(ch.sym)} $${ch.spot.toFixed(2)} · live option quotes (Alpaca, indicative feed, may lag) · ${new Date(ch.asOf).toLocaleString("en-GB", { hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" })}`
    : `${esc(ch.sym)} $${ch.spot.toFixed(2)} · <b>modeled prices</b>: live quotes aren't available right now${ch.why ? ` (${esc(ch.why)})` : ""}, so these are Black-Scholes estimates from the last close.`;
  const built = buildPosition(ch);
  if (built.error) { $("bout").innerHTML = `<p class="note err">${esc(built.error)}</p>`; return; }
  const pos = built.pos;
  const a = L.analyze(pos);
  const T = a.T, dte = Math.round(T * 365);
  B.days = Math.min(B.days, dte);
  const cap = L.capital(B.sid, pos, a);
  const prem = pos.legs.reduce((s, l) => s - l.s * l.qty * 100 * l.price, 0);
  const commission = L.COMMISSION * pos.legs.length * B.qty;
  const expBtns = ch.expirations.map(e => `<button type="button" data-v="${e}" aria-pressed="${e === B.exp}">${shortDate(e).replace(/ \d\d$/, "")} · ${L.daysBetween(nyToday(), e)}d</button>`).join("");
  const rows = pos.legs.map((l, i) => {
    const ks = strikesFor(ch, l.e, l.t);
    return `<tr><td>${l.s > 0 ? "Buy" : "Sell"} ${l.qty} ${l.t === "C" ? "call" : "put"}${l.e !== B.exp ? ` <span class="dim">(${shortDate(l.e)})</span>` : ""}</td>
      <td><select class="k" data-i="${i}" aria-label="Strike">${ks.map(K => `<option value="${K}"${K === l.K ? " selected" : ""}>${K}</option>`).join("")}</select>${l.auto ? "" : ` <a href="#" data-reset="${i}" class="dim" title="Back to the strategy's rule">↺</a>`}</td>
      <td>${px(l.bid)}</td><td>${px(l.ask)}</td><td><b>${px(l.price)}</b></td><td>${(l.iv * 100).toFixed(1)}%</td><td>${l.delta != null ? l.delta.toFixed(2) : L.greeks(pos.S, l.K, l.T, l.iv, l.t).delta.toFixed(2)}</td></tr>`;
  }).join("");
  $("bout").innerHTML = `
    ${st.wheel ? `<p class="note">The wheel starts with a cash-secured put: this is that first step. If you're assigned, the next step is a covered call on the shares.</p>` : ""}
    <div class="subrow"><span class="lbl">Expiry</span><div class="seg" id="bexp">${expBtns}</div></div>
    <div class="subrow"><span class="lbl">Contracts</span><span class="qty"><button type="button" id="qm" aria-label="Fewer">−</button><b>${B.qty}</b><button type="button" id="qp" aria-label="More">+</button></span>
      <span class="lbl">Prices</span><div class="seg" id="bmode"><button type="button" data-v="mid" aria-pressed="${B.mode === "mid"}">Mid</button><button type="button" data-v="natural" aria-pressed="${B.mode === "natural"}">Bid / ask</button></div></div>
    <div class="tbl"><table><thead><tr><th>Leg</th><th>Strike</th><th>Bid</th><th>Ask</th><th>Price</th><th>IV</th><th>Delta</th></tr></thead><tbody>
      ${pos.shares ? `<tr><td>Own ${pos.shares} shares</td><td class="dim">–</td><td></td><td></td><td><b>${pos.S.toFixed(2)}</b></td><td></td><td>1.00</td></tr>` : ""}${rows}</tbody></table></div>
    <p class="note">Strikes are picked by the strategy's rule (${st.legs.map(l => l.d === "atm" ? "at the money" : `${Math.round(l.d * 100)} delta`).join(", ")}); change any of them above. ${B.mode === "mid" ? "Mid = halfway between bid and ask; in practice you often get a bit less." : "Bid / ask = buying at the ask, selling at the bid: what a market order would cost."}</p>
    <dl class="stats">
      <div><dt>${prem >= 0 ? "You receive" : "You pay"}</dt><dd>${money(Math.abs(prem))}</dd></div>
      <div><dt>Max gain</dt><dd class="pos">${money(a.maxGain)}</dd></div>
      <div><dt>Max loss</dt><dd class="neg">${money(a.maxLoss)}</dd></div>
      <div><dt>Breakeven${a.breakevens.length > 1 ? "s" : ""}</dt><dd>${a.breakevens.map(b => b.toFixed(2)).join(" · ") || "–"}</dd></div>
      <div><dt>Chance of profit</dt><dd>${(a.pop * 100).toFixed(0)}%</dd></div>
      <div><dt>Money tied up</dt><dd>${cap == null ? "–" : money(cap)}</dd></div>
      ${a.maxGain != null && cap ? `<div><dt>Max gain / money</dt><dd>${(100 * a.maxGain / cap).toFixed(1)}%</dd></div>` : ""}
    </dl>
    <div class="chartbox" id="bchart"></div>
    <div class="legend" id="blegend"></div>
    <div class="sliders"><label><span>Days from now: <b id="bdaysv">${B.days}</b> of ${dte}</span><input type="range" id="bdays" min="0" max="${dte}" value="${B.days}"></label></div>
    <dl class="stats" id="bgreeks"></dl>
    <p class="note">Chance of profit assumes the market's own volatility (no direction): it isn't a forecast. Most US options are American-style, so a short option can be exercised early, typically just before a dividend. ${esc(m.bad)}</p>
    <div class="btnrow"><a class="btn ghost" href="#learn/${B.sid}">How ${esc(m.name.toLowerCase())} works ›</a><a class="btn ghost" href="#backtest/${B.sid}">Its backtest ›</a><a class="btn ghost" href="index.html#brokers">Brokers ›</a></div>`;
  const draw = () => {
    const now = { ...pos, legs: pos.legs.map(l => ({ ...l, T: Math.max(l.T - B.days / 365, 0) })) };
    const ch2 = payoffSvg(pos, a, { elapsed: B.days / 365, id: "bp" });
    $("bchart").innerHTML = ch2.svg + `<div class="tip" hidden></div>`;
    attachTip($("bchart"), ch2, `${ch.sym} at`);
    $("blegend").innerHTML = `<span><i style="border-color:${C.accent}"></i>At expiry (${shortDate(B.exp)})</span><span><i class="dash" style="border-color:${C.acc2}"></i>${B.days ? `In ${B.days} day${B.days === 1 ? "" : "s"}` : "Today"}, same volatility</span><span>○ breakeven</span>`;
    $("bgreeks").innerHTML = greekStats(L.netGreeks(now), `<div><dt>Commissions (about)</dt><dd>${money(commission, 2)}</dd></div>`);
    $("bdaysv").textContent = B.days;
  };
  draw();
  $("bexp").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; B.exp = b.dataset.v; B.over = {}; renderBuilder(); });
  $("bmode").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; B.mode = b.dataset.v; renderBuilder(); });
  $("qm").addEventListener("click", () => { B.qty = Math.max(1, B.qty - 1); renderBuilder(); });
  $("qp").addEventListener("click", () => { B.qty = Math.min(50, B.qty + 1); renderBuilder(); });
  $("bdays").addEventListener("input", e => { B.days = +e.target.value; draw(); });
  $("bout").querySelectorAll("select.k").forEach(s => s.addEventListener("change", () => { B.over[+s.dataset.i] = +s.value; renderBuilder(); }));
  $("bout").querySelectorAll("[data-reset]").forEach(x => x.addEventListener("click", e => { e.preventDefault(); delete B.over[+x.dataset.reset]; renderBuilder(); }));
}

// ------------------------------------------------------------------ Backtest
const T_ = { uni: "spy", variant: "hold", sid: "covered_call", data: null };
async function backtest(sid) {
  const v = $("v-backtest");
  if (sid) T_.sid = sid;
  if (!T_.data) {
    v.innerHTML = `<p class="msg small">Loading the backtest…</p>`;
    try { T_.data = await fetchJson("data/options/backtest.json"); }
    catch { v.innerHTML = `<div class="msg"><strong>Not ready yet</strong>The first options backtest runs on GitHub within the hour after this page was published. Come back a little later.</div>`; return; }
  }
  renderBacktest();
}
function renderBacktest() {
  const d = T_.data, v = $("v-backtest");
  const U = T_.uni === "spy" ? d.spy : d.sp100;
  const st = d.strategies;
  const blockOf = sid => { const b = U.strat[sid]; return (T_.variant === "managed" && b?.managed) ? { b: b.managed, used: "managed" } : { b: b?.hold, used: "hold" }; };
  const sel = T_.sid, selB = blockOf(sel), m = L.META[sel];
  const benchName = T_.uni === "spy" ? "Buy & hold SPY" : "Buy & hold the stocks (equal weight)";
  const rows = L.ORDER.map(sid => {
    const { b, used } = blockOf(sid); if (!b) return "";
    const s = b.stats, dim = T_.variant === "managed" && used === "hold";
    return `<tr class="click${sid === sel ? " sel" : ""}" data-s="${sid}"><td>${esc(L.META[sid].name)}${dim ? ' <span class="dim">(held)</span>' : ""}</td>
      <td class="${cls(s.cagr)}">${pct(s.cagr)}</td><td class="${cls(s.cagr_tax)}">${pct(s.cagr_tax)}</td><td class="neg">${pct(s.maxdd, 0)}</td>
      <td>${s.win == null ? "–" : s.win.toFixed(0) + "%"}</td><td>${s.trades}</td><td>${money(s.final)}</td></tr>`;
  }).join("");
  const bs = U.bench_stats;
  const sum = selB.b.stats;
  const series = [{ name: m.name, values: selB.b.eq, color: C.accent }, { name: benchName, values: U.bench, color: C.faint, dash: true }];
  let detail = "";
  if (T_.uni === "spy") {
    const tr = (selB.b.trades || []).slice().reverse();
    const row = t =>
      `<tr><td>${shortDate(t.open)}</td><td>${shortDate(t.expiry)}</td><td>${t.leg ? t.leg + " " : ""}${t.strikes.join(" / ")}</td><td class="${cls(t.premium)}">${t.premium >= 0 ? "+" : "−"}${Math.abs(t.premium).toFixed(2)}</td>
       <td>${t.S0.toFixed(0)} → ${t.S1.toFixed(0)}</td><td class="${cls(t.pnl)}">${signed(t.pnl)}</td><td class="${cls(t.ret)}">${pct(t.ret)}</td><td class="dim">${t.how === "expiry" ? "" : esc(t.how)}</td></tr>`;
    const head = `<thead><tr><th>Opened</th><th>Expiry</th><th>Strikes</th><th>Premium</th><th>SPY</th><th>P&amp;L</th><th>Return</th><th></th></tr></thead>`;
    detail = `<h4>Latest trades (one contract)</h4><div class="tbl"><table>${head}<tbody>${tr.slice(0, 12).map(row).join("")}</tbody></table></div>` +
      (tr.length > 12 ? `<details class="ass"><summary>${tr.length - 12} earlier trades</summary><div class="tbl"><table>${head}<tbody>${tr.slice(12).map(row).join("")}</tbody></table></div></details>` : "");
  } else {
    const tk = U.strat[sel]?.tickers || [];
    const t5 = r => `<tr><td><a href="#build/${r[0]}/${sel}">${r[0]}</a></td><td class="${cls(r[1])}">${pct(r[1])}</td><td class="neg">${pct(r[2], 0)}</td><td>${r[3]?.toFixed(0) ?? "–"}%</td><td class="${cls(r[4])}">${pct(r[4])}</td><td class="${cls((r[1] ?? 0) - (r[4] ?? 0))}">${pct((r[1] ?? 0) - (r[4] ?? 0))}</td></tr>`;
    const head = `<thead><tr><th>Stock</th><th>A year</th><th>Max drop</th><th>Wins</th><th>Holding it</th><th>Difference</th></tr></thead>`;
    const beat = tk.filter(r => r[1] != null && r[4] != null && r[1] > r[4]).length;
    detail = `<p class="note">${m.name} beat simply holding the stock on ${beat} of ${tk.length} stocks (held to expiry, each stock on its own).</p>
      <div class="split"><div><h4>Best 10</h4><div class="tbl"><table>${head}<tbody>${tk.slice(0, 10).map(t5).join("")}</tbody></table></div></div>
      <div><h4>Worst 10</h4><div class="tbl"><table>${head}<tbody>${tk.slice(-10).reverse().map(t5).join("")}</tbody></table></div></div></div>`;
  }
  const mdl = d.model;
  v.innerHTML = `${d.demo ? `<p class="note err">These are simulated test numbers, not real results.</p>` : ""}
  <div class="panel">
    <div class="subrow"><div class="seg" id="tuni"><button type="button" data-v="spy" aria-pressed="${T_.uni === "spy"}">SPY</button><button type="button" data-v="sp100" aria-pressed="${T_.uni === "sp100"}">S&amp;P 100 stocks</button></div>
      <div class="seg" id="tvar"><button type="button" data-v="hold" aria-pressed="${T_.variant === "hold"}">Hold to expiry</button><button type="button" data-v="managed" aria-pressed="${T_.variant === "managed"}">Take profit / stop</button></div></div>
    <p class="note">$${d.account.toLocaleString("en-US")} account from ${d.spy.dates[0].slice(0, 4)}, a new position every month on the monthly expiry. Strategies that own shares or hold cash for an assignment use the whole account; spreads, condors, straddles, strangles and calendars risk ${(d.risk * 100).toFixed(0)}% of it each month (their max loss). ${T_.uni === "sp100" ? `Average of ${U.count} stocks, each run on its own.` : ""} ${T_.variant === "managed" ? "Take profit / stop: credit trades close at 50% of the premium kept or a loss of twice the premium; debit trades at +100% or −50%. Strategies with shares are always held." : ""}</p>
    <div class="tbl"><table><thead><tr><th>Strategy</th><th>A year</th><th>After tax</th><th>Max drop</th><th>Wins</th><th>Months</th><th>Final</th></tr></thead><tbody>${rows}
      <tr class="bench"><td>${benchName}</td><td>${pct(bs.cagr)}</td><td>${pct(bs.cagr_tax)}</td><td>${pct(bs.maxdd, 0)}</td><td>–</td><td>–</td><td>${money(bs.final)}</td></tr></tbody></table></div>
    <p class="note">After tax: 26% Italian tax on each year's net gain (losses carried forward 4 years) and 0.2% a year stamp duty, as in the Strategy Lab. Tap a strategy for its chart.</p>
  </div>
  <div class="panel">
    <h2>${esc(m.name)}</h2>
    <p class="lead">${esc(m.short)}</p>
    <dl class="stats"><div><dt>A year</dt><dd class="${cls(sum.cagr)}">${pct(sum.cagr)}</dd></div><div><dt>${benchName.replace(" (equal weight)", "")}</dt><dd>${pct(bs.cagr)}</dd></div>
      <div><dt>Max drop</dt><dd class="neg">${pct(sum.maxdd, 0)}</dd></div><div><dt>Winning months</dt><dd>${sum.win?.toFixed(0)}%</dd></div>
      <div><dt>Best / worst month</dt><dd><span class="pos">${pct(sum.best, 0)}</span> / <span class="neg">${pct(sum.worst, 0)}</span></dd></div></dl>
    <div class="chartbox">${lineSvg(U.dates, series)}</div>
    <div class="legend"><span><i style="border-color:${C.accent}"></i>${esc(m.name)}${selB.used === "managed" ? " (take profit / stop)" : ""}</span><span><i class="dash" style="border-color:${C.faint}"></i>${benchName}</span><span>log scale</span></div>
    ${detail}
    <div class="btnrow"><a class="btn ghost" href="#learn/${sel}">How it works ›</a><a class="btn ghost" href="#build/SPY/${sel}">Price it today ›</a></div>
  </div>
  <details class="ass"><summary>How the prices are modeled</summary><ul>
    <li>Real historical option quotes cost a lot, so prices come from Black-Scholes (rate ${(mdl.rate * 100).toFixed(0)}%, no dividends, European exercise).</li>
    <li>Volatility at the money: ${mdl.atm_vix ? Math.round(mdl.atm_vix * 100) + '% of ' : ''}VIX for SPY. For a stock: its own recent volatility (20 and 60 days) times how far that was above SPY's recent volatility that day.</li>
    <li>Skew: strikes below the price get a higher volatility and strikes above it a lower one, fitted to SPY's real option chain (stocks get a flatter version). Puts cost more than calls, as in real markets.</li>
    <li>Costs: half a bid/ask spread on every option bought or sold (${mdl.spread_pct * 100}% of its price, between $${mdl.spread_min} and $${mdl.spread_max} a share) and $${mdl.commission} a contract.</li>
    <li>Not modeled: earnings jumps in volatility, early assignment, dividends on the option side, currency (results are in % terms).</li>
    <li>Data up to ${shortDate(d.as_of)}. Rerun every Saturday.</li></ul></details>`;
  v.querySelectorAll("tr[data-s]").forEach(r => r.addEventListener("click", () => { location.hash = `backtest/${r.dataset.s}`; }));
  $("tuni").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; T_.uni = b.dataset.v; renderBacktest(); });
  $("tvar").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; T_.variant = b.dataset.v; renderBacktest(); });
}

// ------------------------------------------------------------------ Live (paper)
async function live() {
  const v = $("v-live");
  v.innerHTML = `<p class="msg small">Loading…</p>`;
  let d;
  try { d = await fetchJson("data/options/live.json"); }
  catch { v.innerHTML = `<div class="msg"><strong>Starting soon</strong>Paper trading starts after the next daily scan (after the US close).</div>`; return; }
  const legTxt = p => (p.shares ? `${p.shares} shares + ` : "") + p.legs.map(l => `${l.s > 0 ? "buy" : "sell"} ${l.K} ${l.t === "C" ? "call" : "put"}${l.exp !== p.expiry ? ` (${shortDate(l.exp)})` : ""}`).join(", ");
  const rows = L.ORDER.map(sid => {
    const closed = d.trades.filter(t => t.strategy === sid), o = d.open[sid];
    const tot = closed.reduce((s, t) => s + t.pnl, 0), wins = closed.filter(t => t.pnl > 0).length;
    return `<tr><td><a href="#learn/${sid}">${esc(L.META[sid].name)}</a></td><td>${o ? esc(legTxt(o)) + ` <span class="dim">→ ${shortDate(o.expiry)}</span>` : "–"}</td>
      <td class="${cls(o?.mark?.pnl)}">${o?.mark ? signed(o.mark.pnl) : "–"}</td><td>${closed.length}</td><td>${closed.length ? Math.round(100 * wins / closed.length) + "%" : "–"}</td><td class="${cls(tot)}">${closed.length ? signed(tot) : "–"}</td></tr>`;
  }).join("");
  const recent = d.trades.slice().reverse().slice(0, 40);
  v.innerHTML = `<div class="panel">
    <h2>Paper trades on SPY</h2>
    <p class="lead">Every strategy trades one SPY contract from ${shortDate(d.start)}, opening a new position on each monthly expiry and holding it to the next, on real option quotes (buying at the ask, selling at the bid, plus commissions). Nothing is backfilled and closed trades are never changed.</p>
    <p class="note">Updated ${shortDate(d.as_of)} · SPY ${d.spot?.S?.toFixed(2) ?? ""}. Paper only: no orders are sent.</p>
    <div class="tbl"><table><thead><tr><th>Strategy</th><th>Open position</th><th>Open P&amp;L</th><th>Closed</th><th>Wins</th><th>Closed P&amp;L</th></tr></thead><tbody>${rows}</tbody></table></div>
  </div>
  <div class="panel"><h2>Closed trades</h2>${recent.length ? `<div class="tbl"><table><thead><tr><th>Strategy</th><th>Opened</th><th>Closed</th><th>Legs</th><th>SPY</th><th>P&amp;L</th><th>Return</th><th>Prices</th></tr></thead><tbody>${recent.map(t =>
      `<tr><td>${esc(L.META[t.strategy]?.name || t.strategy)}</td><td>${shortDate(t.open)}</td><td>${shortDate(t.close)}</td><td>${esc(legTxt(t))}</td><td>${t.S0.toFixed(0)} → ${t.S1.toFixed(0)}</td>
       <td class="${cls(t.pnl)}">${signed(t.pnl)}</td><td class="${cls(t.ret)}">${pct(t.ret)}</td><td class="dim">${t.source === "quotes" ? "quotes" : t.source}${t.how !== "expiry" ? " · " + esc(t.how) : ""}</td></tr>`).join("")}</tbody></table></div>`
    : `<p class="note">None yet: the first positions close on the next monthly expiry.</p>`}
    <p class="note">"model" means real quotes weren't available that day and Black-Scholes was used instead.</p></div>`;
}

route();
