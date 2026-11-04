// Max Pain page (docs/maxpain.html). Data: docs/data/maxpain/ (engine/maxpain.py, every weekday before the open).
// Hash routes: #today, #learn, #test, #stock or #stock/AAPL (a stock), #stock/AAPL/2026-11-20 (and an expiry).

const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pct = (x, d = 1) => (x == null || !isFinite(x)) ? "–" : `${x > 0 ? "+" : ""}${(x * 100).toFixed(d)}%`;
const pct0 = (x, d = 0) => (x == null || !isFinite(x)) ? "–" : `${(x * 100).toFixed(d)}%`;
const usd = x => x == null ? "–" : `$${(+x).toLocaleString("en-US", { minimumFractionDigits: x < 1000 ? 2 : 0, maximumFractionDigits: 2 })}`;
const strike = x => x == null ? "–" : `$${(+x).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
const big = x => x >= 1e9 ? `$${(x / 1e9).toFixed(1)}bn` : x >= 1e6 ? `$${(x / 1e6).toFixed(1)}m` : x >= 1e3 ? `$${(x / 1e3).toFixed(0)}k` : `$${Math.round(x)}`;
const num = x => (+x).toLocaleString("en-US");
const fmtDate = d => new Date(d + "T12:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
const fmtDay = d => new Date(d + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const dteTxt = n => n === 0 ? "today" : n === 1 ? "tomorrow" : `in ${n} days`;

const views = ["today", "stock", "learn", "test"];
let INDEX = null, SCORE = null;
const cache = {};
const ui = { scope: "all", when: "next", sort: "gap" };
let cur = { sym: null, exp: null };

async function getJSON(u) {
  try { const r = await fetch(u, { cache: "no-cache" }); return r.ok ? r.json() : null; } catch { return null; }
}

// ------------------------------------------------------------------ Today: every stock
function renderToday() {
  const el = $("#v-today");
  let rows = INDEX.stocks.filter(s => ui.scope === "all" || (ui.scope === "etf") === s.etf)
    .map(s => ({ s, e: ui.when === "monthly" ? s.monthly : s.next })).filter(r => r.e);
  if (ui.sort === "gap") rows.sort((a, b) => Math.abs(b.e.dist) - Math.abs(a.e.dist));
  else if (ui.sort === "az") rows.sort((a, b) => a.s.symbol.localeCompare(b.s.symbol));
  else if (ui.sort === "oi") rows.sort((a, b) => (b.e.call_oi + b.e.put_oi) - (a.e.call_oi + a.e.put_oi));
  const seg = (name, opts) => `<div class="seg" role="group" data-k="${name}">${opts.map(([v, t]) =>
    `<button type="button" data-v="${v}" aria-pressed="${ui[name] === v}">${t}</button>`).join("")}</div>`;
  const bar = d => {
    const w = Math.min(Math.abs(d) / 0.1, 1) * 50;
    return `<span class="dbar"><i class="${d >= 0 ? "u" : "d"}" style="${d >= 0 ? `left:50%` : `left:${50 - w}%`};width:${w}%"></i></span>`;
  };
  el.innerHTML = `
    <div class="toolbar">${seg("scope", [["all", "All"], ["etf", "ETFs"], ["stock", "Stocks"]])}
      ${seg("when", [["next", "Next expiry"], ["monthly", "Monthly expiry"]])}
      ${seg("sort", [["gap", "Biggest gap"], ["oi", "Most open interest"], ["az", "A–Z"]])}</div>
    <div class="summary"><table>
      <thead><tr><th>Ticker</th><th>Price</th><th class="hm">Expiry</th><th>Max pain</th><th>Gap</th><th class="hm">Put/call OI</th></tr></thead>
      <tbody>${rows.map(({ s, e }) => `<tr class="click" data-go="stock/${esc(s.symbol)}/${e.expiry}">
        <td class="tf tk"><a href="#stock/${esc(s.symbol)}/${e.expiry}">${esc(s.symbol)}</a><small>${fmtDate(e.expiry)} · ${dteTxt(e.dte)}</small></td>
        <td>${usd(s.spot)}</td><td class="hm dim">${fmtDate(e.expiry)}</td><td><b>${strike(e.max_pain)}</b></td>
        <td class="${e.dist > 0 ? "up" : e.dist < 0 ? "down" : ""}">${pct(e.dist)}${bar(e.dist)}</td>
        <td class="hm dim">${e.pc ?? "–"}</td></tr>`).join("")}</tbody></table></div>
    <p class="note">Gap = how far max pain sits from the price: <span class="pos">green</span> above it, <span class="neg">red</span> below. Tap a stock for open interest by strike and every expiry. Data from ${fmtDay(INDEX.date)}, open interest as of ${INDEX.oi_date ? fmtDay(INDEX.oi_date) : "the last session"}.</p>`;
  el.querySelectorAll(".seg[data-k] button").forEach(b => b.addEventListener("click", () => {
    ui[b.parentElement.dataset.k] = b.dataset.v; renderToday();
  }));
  el.querySelectorAll("tr[data-go]").forEach(tr => tr.addEventListener("click", ev => {
    if (!ev.target.closest("a")) location.hash = tr.dataset.go;
  }));
}

// ------------------------------------------------------------------ By stock: one chain
async function renderStock() {
  const el = $("#v-stock");
  const syms = INDEX.stocks.map(s => s.symbol);
  const sym = cur.sym && syms.includes(cur.sym) ? cur.sym : "SPY";
  el.innerHTML = `
    <form class="pick" id="pick" autocomplete="off">
      <label class="field"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
        <input id="sym" list="syms" value="${esc(sym)}" placeholder="Ticker, e.g. AAPL" aria-label="Ticker" spellcheck="false"></label>
      <button class="btn">Show</button>
      <datalist id="syms">${syms.map(s => `<option value="${s}">`).join("")}</datalist>
    </form>
    <p class="status" id="st"></p>
    <div id="sbody"></div>`;
  $("#pick").addEventListener("submit", ev => {
    ev.preventDefault();
    const s = $("#sym").value.trim().toUpperCase().replace(/\./g, "-");
    if (!syms.includes(s)) { $("#st").className = "status err"; $("#st").textContent = `${s || "That"}: max pain is available for SPY, QQQ, IWM and the S&P 100.`; return; }
    location.hash = `stock/${s}`;
  });
  $("#st").textContent = "Loading…";
  const d = cache[sym] || (cache[sym] = await getJSON(`data/maxpain/${sym}.json`));
  if (!d) { $("#st").className = "status err"; $("#st").textContent = `No data for ${sym} today.`; return; }
  $("#st").textContent = "";
  const e = d.expiries.find(x => x.expiry === cur.exp) || d.expiries[0];
  cur = { sym, exp: e.expiry };
  const tot = e.call_oi + e.put_oi;
  const pay = Object.fromEntries(e.curve);
  const atSpot = interp(e.curve, d.spot);
  $("#sbody").innerHTML = `
    <div class="panel">
      <h2>${esc(sym)} <span class="dim" style="font-weight:500;font-size:20px">${usd(d.spot)}</span></h2>
      <p class="big">Max pain for ${fmtDate(e.expiry)}${e.monthly ? " (monthly)" : ""}: <b>${strike(e.max_pain)}</b>,
        <span class="${e.dist > 0 ? "pos" : e.dist < 0 ? "neg" : ""}">${pct(e.dist)}</span> from the price.</p>
      <div class="exps" role="group" aria-label="Expiry">${d.expiries.map(x => `<button type="button" class="${x.monthly ? "m" : ""}" data-e="${x.expiry}" aria-pressed="${x.expiry === e.expiry}">${fmtDate(x.expiry)}<small>${x.monthly ? "monthly" : `${x.dte}d`}</small></button>`).join("")}</div>
      <dl class="stats">
        <div><dt>Max pain</dt><dd>${strike(e.max_pain)}</dd></div>
        <div><dt>Gap from price</dt><dd class="${e.dist > 0 ? "pos" : e.dist < 0 ? "neg" : ""}">${pct(e.dist)}</dd></div>
        <div><dt>Most call OI</dt><dd>${strike(e.call_wall)}</dd></div>
        <div><dt>Most put OI</dt><dd>${strike(e.put_wall)}</dd></div>
        <div><dt>Put/call OI</dt><dd>${e.pc ?? "–"}</dd></div>
        <div><dt>Contracts open</dt><dd>${num(tot)}</dd></div>
      </dl>
      <div class="mpchart" id="mpc"></div>
      <div class="legend"><span><i class="box" style="background:var(--up-line)"></i>Call open interest</span><span><i class="box" style="background:var(--down-line)"></i>Put open interest</span>
        <span><i style="border-color:var(--res)"></i>What option holders would collect</span><span><i class="dash" style="border-color:var(--sup)"></i>Price</span><span><i class="dash" style="border-color:var(--res)"></i>Max pain</span></div>
      <p class="note">If ${esc(sym)} closed at max pain on ${fmtDate(e.expiry)}, option holders would collect ${big(pay[e.max_pain] ?? 0)} in total; at today's price, ${big(atSpot)}. ${e.dte <= 2 ? "Expiry is close: this is when any pull toward max pain is said to be strongest." : `${e.dte} days is a long time: open interest, and so max pain, will change before then.`}</p>
    </div>`;
  $("#sbody").querySelectorAll(".exps button").forEach(b => b.addEventListener("click", () => { location.hash = `stock/${sym}/${b.dataset.e}`; }));
  drawChart($("#mpc"), e, d.spot);
}

function interp(curve, x) {
  if (!curve.length) return 0;
  if (x <= curve[0][0]) return curve[0][1];
  for (let i = 1; i < curve.length; i++) if (x <= curve[i][0]) {
    const [x0, y0] = curve[i - 1], [x1, y1] = curve[i];
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0);
  }
  return curve[curve.length - 1][1];
}

function drawChart(box, e, spot) {
  const W = Math.max(320, Math.min(980, box.clientWidth || 700)), H = W < 520 ? 300 : 360;
  const m = { l: 8, r: 8, t: 26, b: 28 };
  const ks = e.oi.map(r => r[0]);
  const x0 = Math.min(ks[0], spot), x1 = Math.max(ks[ks.length - 1], spot);
  const X = k => m.l + (k - x0) / (x1 - x0 || 1) * (W - m.l - m.r);
  const mid = m.t + (H - m.t - m.b) * 0.5;
  const maxOI = Math.max(1, ...e.oi.map(r => Math.max(r[1], r[2])));
  const hOI = (H - m.t - m.b) * 0.5 - 4;
  const bw = Math.max(1.5, Math.min(14, (W - m.l - m.r) / ks.length * 0.7));
  const pays = e.curve.map(c => c[1]), pMax = Math.max(1, ...pays), pMin = Math.min(...pays);
  const Yp = v => m.t + 4 + (1 - (v - pMin) / (pMax - pMin || 1)) * (H - m.t - m.b - 8);
  const css = n => `var(--${n})`;
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Open interest by strike and max pain">`;
  s += `<line x1="${m.l}" x2="${W - m.r}" y1="${mid}" y2="${mid}" stroke="${css("hair")}"/>`;
  for (const [k, c, p] of e.oi) {
    const x = X(k);
    if (c) s += `<rect x="${x - bw / 2}" y="${mid - c / maxOI * hOI}" width="${bw}" height="${c / maxOI * hOI}" rx="1" fill="${css("up-line")}" opacity=".75"/>`;
    if (p) s += `<rect x="${x - bw / 2}" y="${mid}" width="${bw}" height="${p / maxOI * hOI}" rx="1" fill="${css("down-line")}" opacity=".75"/>`;
  }
  s += `<path d="${e.curve.map(([k, v], i) => `${i ? "L" : "M"}${X(k).toFixed(1)},${Yp(v).toFixed(1)}`).join("")}" fill="none" stroke="${css("res")}" stroke-width="2"/>`;
  const vline = (x, col, lbl, anchor) => `<line x1="${x}" x2="${x}" y1="${m.t - 6}" y2="${H - m.b}" stroke="${col}" stroke-width="1.5" stroke-dasharray="4 4"/>
    <text x="${x + (anchor === "end" ? -5 : 5)}" y="${m.t - 10}" text-anchor="${anchor}" font-size="11" font-weight="600" fill="${col}">${lbl}</text>`;
  const right = X(e.max_pain) >= X(spot);
  s += vline(X(spot), css("sup"), `Price ${strike(+spot.toFixed(2))}`, right ? "end" : "start");
  s += vline(X(e.max_pain), css("res"), `Max pain ${strike(e.max_pain)}`, right ? "start" : "end");
  // x labels: about 6
  const step = Math.max(1, Math.round(ks.length / 6));
  for (let i = 0; i < ks.length; i += step) s += `<text x="${X(ks[i])}" y="${H - 8}" text-anchor="middle" font-size="11" fill="${css("c-txt")}">${ks[i]}</text>`;
  s += `<text x="${W - m.r - 2}" y="${mid - 6}" text-anchor="end" font-size="10" fill="${css("c-txt")}">calls ↑</text><text x="${W - m.r - 2}" y="${mid + 14}" text-anchor="end" font-size="10" fill="${css("c-txt")}">puts ↓</text>`;
  s += `<rect id="hit" x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}" fill="transparent"/><line id="cross" y1="${m.t}" y2="${H - m.b}" stroke="${css("muted")}" stroke-width="1" visibility="hidden"/></svg><div class="tip" hidden></div>`;
  box.innerHTML = s;
  const svg = box.querySelector("svg"), tip = box.querySelector(".tip"), cross = box.querySelector("#cross");
  const pay = Object.fromEntries(e.curve);
  const move = ev => {
    const r = svg.getBoundingClientRect();
    const px = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left;
    const kx = x0 + (px / r.width * W - m.l) / (W - m.l - m.r) * (x1 - x0);
    const row = e.oi.reduce((a, b) => Math.abs(b[0] - kx) < Math.abs(a[0] - kx) ? b : a);
    cross.setAttribute("x1", X(row[0])); cross.setAttribute("x2", X(row[0])); cross.setAttribute("visibility", "visible");
    tip.hidden = false;
    tip.innerHTML = `${strike(row[0])} · <span class="pos">${num(row[1])} calls</span> · <span class="neg">${num(row[2])} puts</span>${pay[row[0]] != null ? ` · holders get ${big(pay[row[0]])}` : ""}`;
  };
  const out = () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); };
  svg.addEventListener("pointermove", move); svg.addEventListener("pointerleave", out);
  svg.addEventListener("touchmove", move, { passive: true }); svg.addEventListener("touchend", out);
}

// ------------------------------------------------------------------ Learn
function renderLearn() {
  $("#v-learn").innerHTML = `
    <div class="panel">
      <h2>What max pain is</h2>
      <p class="lead">Every option is a bet that a stock ends above (call) or below (put) a strike price on a given day. On that day, most of them are worth nothing.</p>
      <p>Take all the calls and puts still open for one expiry. For every possible closing price, add up what their holders would collect. The price where that total is smallest is <b>max pain</b>: the most money expires worthless there, so option <i>buyers</i> collectively lose the most and option <i>sellers</i> keep the most.</p>
      <div class="steps">
        <div><span class="n">1</span><b>Count open interest</b><p>How many calls and puts are open at each strike, from the exchanges' count after the last session.</p></div>
        <div><span class="n">2</span><b>Try every price</b><p>At each strike, work out what all those options would pay if the stock closed there on expiry day.</p></div>
        <div><span class="n">3</span><b>Pick the lowest</b><p>The strike with the smallest total payout is max pain. The orange curve on each stock's chart is that total.</p></div>
      </div>
      <h4>A tiny example</h4>
      <p>Stock at $102. Open: 10 calls at $100, 5 calls at $105, 10 puts at $95, 5 puts at $100.</p>
      <ul class="find"><li>Close at $95: puts at $100 pay 5 × $5 × 100 = <b>$2,500</b></li>
        <li>Close at $100: nothing pays. <b>$0</b> ← max pain</li>
        <li>Close at $105: calls at $100 pay 10 × $5 × 100 = <b>$5,000</b></li></ul>
    </div>
    <div class="split">
      <div class="panel">
        <h2>Why it might pull</h2>
        <p>The usual story is about dealers, the market makers who sell most options. They hedge by trading the stock, and as expiry gets close, the hedges on heavily traded strikes can push the price back toward them ("pinning"). Max pain often sits next to those strikes.</p>
        <p>It's most talked about for weekly and monthly expiries on very liquid names like SPY, QQQ, AAPL or TSLA, in the last day or two before expiry.</p>
      </div>
      <div class="panel">
        <h2>Why to be careful</h2>
        <ul class="find">
          <li>Nobody outside the dealers knows who is long or short each option. Max pain assumes buyers are on one side; reality is messier.</li>
          <li>Open interest changes every day, so max pain moves too, especially early in an expiry's life.</li>
          <li>Earnings, news and the whole market easily overwhelm it.</li>
          <li>Studies find a pinning effect mainly in the most liquid stocks, close to expiry, and it's small. That's why this page tests it: see <a href="#test">Does it work?</a></li>
        </ul>
      </div>
    </div>
    <div class="panel">
      <h2>How to read the page</h2>
      <ul class="find">
        <li><b>Gap to max pain</b>: how far it sits from today's price. A big gap a week out says little; a big gap the day before a monthly expiry is when the idea is supposed to matter.</li>
        <li><b>Most call / put OI</b>: the strikes with the most open contracts. They often act as levels in the same way, and match the call and put walls on the <a href="trendlines.html">Trendlines</a> gamma levels for SPY, QQQ and IWM.</li>
        <li><b>Put/call OI</b>: above 1 means more puts than calls open. High readings are often read as hedging (fear), low as optimism.</li>
        <li>Use it as context next to a strategy's signal, not as a signal on its own. More on options on the <a href="options.html">Options</a> page.</li>
      </ul>
    </div>`;
}

// ------------------------------------------------------------------ Does it work?
function renderTest() {
  const el = $("#v-test");
  const g = SCORE?.groups || {};
  const any = SCORE && SCORE.expiries_scored > 0;
  const nextExp = INDEX.stocks.find(s => s.symbol === "SPY")?.next?.expiry;
  const row = (label, k) => {
    const r = g[k];
    if (!r) return `<tr><td>${label}</td><td colspan="5" class="dim">not enough yet</td></tr>`;
    return `<tr><td>${label}</td><td>${num(r.n)}</td><td class="${r.toward > 0.55 ? "pos" : r.toward < 0.45 ? "neg" : ""}"><b>${pct0(r.toward)}</b></td><td>${pct0(r.within1)}</td><td class="hm">${pct0(r.med_before, 1)}</td><td class="hm">${pct0(r.med_after, 1)}</td></tr>`;
  };
  const table = (snap, title) => `<h4>${title}</h4><div class="tbl"><table>
    <thead><tr><th></th><th>Cases</th><th>Moved toward</th><th>Closed within 1%</th><th class="hm">Gap before</th><th class="hm">Gap at close</th></tr></thead>
    <tbody>${row("All", `${snap}_all`)}${row("Monthly expiries", `${snap}_monthly`)}${row("SPY, QQQ, IWM", `${snap}_etfs`)}${row("Stocks", `${snap}_stocks`)}</tbody></table></div>`;
  el.innerHTML = `
    <div class="panel">
      <h2>Does it work?</h2>
      <p class="lead">Free history of open interest per strike doesn't exist, so we can't backtest max pain over past years. Instead we record it every morning and score every expiry once it's over.</p>
      <p><b>The test:</b> take the max pain we published on a morning before expiry, and the price that morning. On expiry day, did the stock close <i>closer</i> to max pain than it was? If max pain were meaningless, that would happen roughly half the time. It's only useful if it is clearly above half, and closes within 1% are clearly more common than chance.</p>
      ${any ? `<p class="note">${num(SCORE.expiries_scored)} expir${SCORE.expiries_scored === 1 ? "y" : "ies"} scored, ${fmtDay(SCORE.first)} to ${fmtDay(SCORE.last)}. Early numbers are noisy: give it a few months of monthly expiries before trusting a result either way.</p>
        ${table("week", "From a week before expiry")}${table("day", "From the morning of expiry day")}`
      : `<div class="verdict"><b>Recording since 4 Nov 2026.</b> The first expiry gets scored the morning after ${nextExp ? fmtDate(nextExp) : "the next expiry"}; the first monthly one after 20 Nov. Check back then.</div>`}
    </div>
    ${any && SCORE.recent?.length ? `<div class="panel"><h2>Latest scored</h2><div class="tbl"><table>
      <thead><tr><th>Stock</th><th>Expiry</th><th class="hm">From</th><th>Price then</th><th>Max pain</th><th>Close</th><th>Result</th></tr></thead>
      <tbody>${SCORE.recent.slice(0, 60).map(r => `<tr><td><b>${esc(r.symbol)}</b></td><td>${fmtDate(r.expiry)}</td><td class="hm dim">${r.snap === "week" ? "week before" : "same day"}</td>
        <td>${usd(r.spot)}</td><td>${strike(r.max_pain)}</td><td>${usd(r.close)}</td><td class="${r.toward ? "pos" : "neg"}">${r.toward ? "toward" : "away"}</td></tr>`).join("")}</tbody></table></div></div>` : ""}`;
}

// ------------------------------------------------------------------ routing
function route() {
  const [v, sym, exp] = location.hash.slice(1).split("/");
  const view = views.includes(v) ? v : "today";
  for (const k of views) $(`#v-${k}`).hidden = k !== view;
  for (const b of document.querySelectorAll("#tabs button")) b.setAttribute("aria-pressed", String(b.dataset.v === view));
  if (view === "stock") {
    cur = { sym: (sym || cur.sym || "SPY").toUpperCase(), exp: exp || null };
    renderStock();
  }
  if (!location.hash || !views.includes(v)) history.replaceState(null, "", `#${view}`);
}

async function boot() {
  [INDEX, SCORE] = await Promise.all([getJSON("data/maxpain/index.json"), getJSON("data/maxpain/scorecard.json")]);
  if (!INDEX) {
    $("#v-today").hidden = false;
    $("#v-today").innerHTML = `<div class="msg"><strong>No data yet</strong>Max pain is computed every weekday before the US open.</div>`;
    return;
  }
  renderToday(); renderLearn(); renderTest();
  document.querySelectorAll("#tabs button").forEach(b => b.addEventListener("click", () => {
    location.hash = b.dataset.v === "stock" && cur.sym ? `stock/${cur.sym}` : b.dataset.v;
  }));
  addEventListener("hashchange", route);
  route();
  let lastW = innerWidth;
  addEventListener("resize", () => {
    if (Math.abs(innerWidth - lastW) < 60) return;
    lastW = innerWidth;
    if (!$("#v-stock").hidden && cache[cur.sym]) renderStock();
  });
}

boot();
