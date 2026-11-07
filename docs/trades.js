// Strategy Trades page (trades.html): every Strategy Lab strategy, run with its default settings, trade by trade.
// Same idea as Chart Patterns (patterns.js): a table of strategies, then one strategy's trades as mini charts,
// each opening the full chart (chart.html?st=…) with the trade drawn.
//   trades.html#m=stocks                  overview: one row per strategy
//   trades.html#m=crypto&s=donchian       one strategy: its stats, then every trade as a mini chart
// The trades come from lab.js run on the market's lab.json (the same daily prices and rules as the Strategy Lab),
// so nothing is precomputed: the page and the Lab always agree. Mini charts are drawn from the same prices.
// Start year: all the history, but a strategy with more than LIMIT trades starts in 2021, and if it still has
// more, in 2023 (crypto starts in 2021 by default, like the rest of the site's crypto tests).

import * as M from "./lab.js";

const $ = id => document.getElementById(id);
const esc = t => String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const LIMIT = 1000;
const MKS = { stocks: "Stocks", crypto: "Crypto", commodities: "Commodities" };
const uni = () => window.ICS?.uni?.() === "sp500" ? "sp500" : "sp100";
const base = mk => mk === "crypto" ? "data/crypto/" : mk === "commodities" ? "data/commodities/" : uni() === "sp500" ? "data/sp500/" : "data/";
const mkName = mk => mk === "stocks" ? (uni() === "sp500" ? "S&P 500 stocks" : "S&P 100 stocks") : mk === "crypto" ? "the site's 19 coins" : "19 commodities";
const label = (mk, t) => mk === "crypto" ? t.replace("-USD", "") : t;
const WHY = { rule: "Exit signal", tp: "Take profit hit", sl: "Stop loss hit", trail: "Trailing stop hit", be: "Break-even stop", time: "Time exit", open: "Still open" };
const LS = "ics.trades";
const store = (() => { try { return JSON.parse(localStorage.getItem(LS)) || {}; } catch { return {}; } })();
const save = () => { try { localStorage.setItem(LS, JSON.stringify(store)); } catch {} };

const st = { side: store.side === "all" ? "all" : "long", per: store.per || "auto", sort: store.sort || "eur", res: "", order: "new", q: "", shown: 24 };
const DATA = {};          // market -> decoded lab data (promise while loading)
const RUN = {};           // market|strategy|from -> lab run result

const pct = (v, d = 2) => v == null ? "–" : `<span class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}%</span>`;
const eur = v => v == null ? "–" : `<span class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "+" : v < 0 ? "−" : ""}€${Math.abs(Math.round(v)).toLocaleString("en-US")}</span>`;
const money = v => v >= 1000 ? Math.round(v).toLocaleString("en-US") : v >= 1 ? v.toLocaleString("en-US", { maximumFractionDigits: 2 }) : v.toPrecision(3);
const fmtD = d => new Date(d + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const yieldUI = () => new Promise(r => setTimeout(r, 0));

function route() {
  const q = new URLSearchParams(location.hash.slice(1));
  return { mk: MKS[q.get("m")] ? q.get("m") : (MKS[store.mk] ? store.mk : "stocks"), s: q.get("s") || "" };
}
const go = (mk, s) => { location.hash = new URLSearchParams(s ? { m: mk, s } : { m: mk }).toString(); };

async function data(mk) {
  return (DATA[mk] ||= fetch(`${base(mk)}lab.json`, { cache: "no-cache" }).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(raw => { const d = M.decode(raw); d.byT = Object.fromEntries(d.tickers.map(T => [T.t, T])); return d; })
    .catch(e => { delete DATA[mk]; throw e; }));
}
const strategies = (d, mk) => Object.keys(M.INDICATORS).filter(k => k !== "custom" && M.available(d, k, mk));
const cfgOf = ind => M.clean(M.defaults(ind));
const firstIdx = (d, y) => { const i = d.dates.findIndex(x => x >= `${y}-01-01`); return i < 0 ? d.dates.length : i; };

// every trade of a strategy from date index `from`: closed and still-open ones, long only or long & short
function runAt(d, mk, ind, from) {
  const k = `${mk}|${uni()}|${ind}|${from}`;
  if (RUN[k]) return RUN[k];
  // only Average down (fixed money slots) depends on the start; everything else is the same run, cut later
  const R = M.run(d, cfgOf(ind), M.INDICATORS[ind].avg ? from : 0);
  return (RUN[k] = R);
}
function tradesOf(d, mk, ind, side, from) {
  const R = runAt(d, mk, ind, from), I = M.INDICATORS[ind], sd = I.buysOnly ? "long" : side;
  const closed = R[sd].list.filter(t => t.g0 >= from).map(t => ({ ...t, open: false }));
  const open = R.openTrades.filter(t => t.g0 >= from && (sd === "all" || t.side > 0)).map(t => ({ ...t, g1: d.dates.length - 1, p1: t.px, why: "open", open: true }));
  return [...closed, ...open];
}
// the start for this strategy: the chosen period, or the auto rule (all → 2021 → 2023, crypto from 2021)
function startOf(d, mk, ind, side) {
  const all = 0, y21 = firstIdx(d, 2021), y23 = firstIdx(d, 2023);
  if (st.per === "all") return { from: all, why: "" };
  if (st.per === "2021") return { from: y21, why: "" };
  if (st.per === "2023") return { from: y23, why: "" };
  const R = runAt(d, mk, ind, 0), sd = M.INDICATORS[ind].buysOnly ? "long" : side, list = R[sd].list;
  const n = from => list.filter(t => t.g0 >= from).length;
  if (mk !== "crypto" && n(all) <= LIMIT) return { from: all, why: "" };
  if (n(y21) <= LIMIT) return { from: y21, why: mk === "crypto" ? "crypto starts in 2021" : `over ${LIMIT.toLocaleString("en-US")} trades since ${d.dates[0].slice(0, 4)}` };
  return { from: y23, why: `over ${LIMIT.toLocaleString("en-US")} trades since ${mk === "crypto" ? "2021" : d.dates[0].slice(0, 4)}` };
}
function stats(L) {
  const C = L.filter(x => !x.open), rets = C.map(x => x.ret * 100), sum = rets.reduce((s, v) => s + v, 0);
  return { n: C.length, open: L.length - C.length, win: C.length ? C.filter(x => x.ret > 0).length / C.length * 100 : null, avg: C.length ? sum / C.length : null,
    eur: sum * 10, best: rets.length ? Math.max(...rets) : null, worst: rets.length ? Math.min(...rets) : null,
    days: C.length ? C.reduce((s, x) => s + x.days, 0) / C.length : null };
}

function seg(name, opts, cur, attr) {
  return `<div class="seg" role="group" aria-label="${esc(name)}">${opts.map(([v, l]) => `<button type="button" ${attr}="${esc(v)}" aria-pressed="${v === cur}">${esc(l)}</button>`).join("")}</div>`;
}
function ctrls(mk, { market = true, side = true } = {}) {
  return (market ? seg("Market", Object.entries(MKS), mk, "data-mk") + (mk === "stocks" && window.ICS?.uniSwitch ? window.ICS.uniSwitch() : "") : "")
    + (side ? seg("Side", [["long", "Long only"], ["all", "Long & short"]], st.side, "data-side") : "")
    + seg("Period", [["auto", "Auto"], ["all", "All years"], ["2021", "Since 2021"], ["2023", "Since 2023"]], st.per, "data-per");
}

// ------------------------------------------------------------------ overview
let ovId = 0;
async function renderAll(mk) {
  const id = ++ovId;
  $("view-all").hidden = false; $("view-one").hidden = true;
  document.title = "Strategy Trades";
  $("ctrls").innerHTML = ctrls(mk);
  $("rules").innerHTML = `
    <div><b>Settings</b>Each strategy's default settings in the Strategy Lab, the same ones its strategy page and the site's backtest use.</div>
    <div><b>Entry</b>At the close of the day the strategy's signal fires, on every one of ${esc(mkName(mk))} it covers.</div>
    <div><b>Exit</b>The strategy's own exit: its opposite signal, its time limit, or its stop or take profit if it has one.</div>
    <div><b>Start year</b>Auto: all the years there are, but from 2021 if a strategy has more than ${LIMIT.toLocaleString("en-US")} trades, and from 2023 if it still has more.${mk === "crypto" ? " Crypto starts in 2021." : ""}</div>
    <div><b>Money</b>€1,000 in every trade, no leverage. No costs or taxes, as in the Lab's defaults: add them there.</div>
    <div><b>Open trades</b>Trades still open on the last day are shown on their own, but not counted in the results.</div>`;
  $("thead").innerHTML = "";
  $("rows").innerHTML = `<tr><td colspan="8" class="msg">Loading ${mk === "stocks" && uni() === "sp500" ? "15 years of S&P 500 prices (a big file, about 30 MB)" : "the price history"}…</td></tr>`;
  let d;
  try { d = await data(mk); } catch { if (id === ovId) $("rows").innerHTML = `<tr><td colspan="8" class="msg">The price history didn't load. Refresh to try again.</td></tr>`; return; }
  if (id !== ovId) return;
  const list = strategies(d, mk), rows = list.map(ind => ({ ind, I: M.INDICATORS[ind], done: false }));
  const sk = st.sort;
  const th = (k, l, n) => `<th class="${n ? "n" : ""}"><button type="button" data-sort="${k}" aria-pressed="${sk === k}">${l}</button></th>`;
  $("thead").innerHTML = `<tr>${th("name", "Strategy")}<th>Start</th>${th("n", "Trades", 1)}${th("win", "Win rate", 1)}${th("avg", "Avg trade", 1)}${th("days", "Avg days", 1)}<th class="n">Best / worst</th>${th("eur", "€1k / trade", 1)}</tr>`;
  const draw = () => {
    const done = rows.filter(r => r.done), wait = rows.filter(r => !r.done);
    done.sort((a, b) => sk === "name" ? a.I.name.localeCompare(b.I.name) : (b[sk] ?? -1e9) - (a[sk] ?? -1e9));
    $("rows").innerHTML = [...done.map(r => `<tr tabindex="0" data-s="${esc(r.ind)}" aria-label="${esc(r.I.name)}: open its trades">
        <td><span class="nm">${esc(r.I.name)}</span>${r.n < 5 ? '<span class="few">few</span>' : ""}<span class="fam">${esc(r.I.group)}${r.I.buysOnly && st.side === "all" ? " · buys only" : ""}</span></td>
        <td><span class="st">${esc(d.dates[r.from].slice(0, 4))}</span></td>
        <td class="n">${r.n.toLocaleString("en-US")}${r.open ? `<span class="sub2">+${r.open} open</span>` : ""}</td>
        <td class="n">${r.win == null ? "–" : r.win.toFixed(1) + "%"}${r.win == null ? "" : `<span class="bar2"><i style="width:${r.win}%"></i></span>`}</td>
        <td class="n">${pct(r.avg)}</td><td class="n">${r.days == null ? "–" : r.days.toFixed(0)}</td>
        <td class="n">${pct(r.best, 0)} / ${pct(r.worst, 0)}</td>
        <td class="n"><b>${eur(r.eur)}</b> <span class="go">›</span></td></tr>`),
      ...wait.map(r => `<tr data-s="${esc(r.ind)}" tabindex="0"><td><span class="nm">${esc(r.I.name)}</span><span class="fam">${esc(r.I.group)}</span></td><td colspan="7" class="wait">Running the backtest…</td></tr>`)].join("");
  };
  draw();
  $("tnote").textContent = `“Few” marks strategies with fewer than 5 trades. Start = the first year counted (Auto picks it from the number of trades; change it above). The Strategy Lab runs the same rules with your own settings, stops, filters and costs.`;
  for (const r of rows) {
    await yieldUI();
    if (id !== ovId) return;
    try {
      const s0 = startOf(d, mk, r.ind, st.side);
      Object.assign(r, stats(tradesOf(d, mk, r.ind, st.side, s0.from)), { from: s0.from, done: true });
    } catch (e) { console.error(r.ind, e); Object.assign(r, { n: 0, from: 0, done: true }); }
    draw();
  }
  $("foot").textContent = `Daily prices ${fmtD(d.dates[0])} to ${fmtD(d.dates[d.dates.length - 1])} from the site's weekly backtest (engine/backtest.py), run on your device with the Strategy Lab's rules (lab.js). ${mk === "stocks" ? `Today's ${uni() === "sp500" ? "S&P 500" : "S&P 100"} members, so stocks that dropped out aren't in it.` : ""} Past results don't predict future ones. For information only. Not investment advice.`;
}

// ------------------------------------------------------------------ one strategy
let oneId = 0, CUR = null;
async function renderOne(mk, ind) {
  const id = ++oneId; ovId++;
  $("view-all").hidden = true; const V = $("view-one"); V.hidden = false;
  const I = M.INDICATORS[ind];
  if (!I) { go(mk); return; }
  document.title = `${I.name} · Strategy Trades`;
  V.innerHTML = `<p class="crumb"><a href="#m=${mk}" data-back>‹ All strategies</a></p><p class="msg">Running the backtest…</p>`;
  let d;
  try { d = await data(mk); } catch { V.innerHTML += `<p class="msg">The price history didn't load. Refresh to try again.</p>`; return; }
  await yieldUI();
  if (id !== oneId) return;
  if (!M.available(d, ind, mk)) { V.innerHTML = `<p class="crumb"><a href="#m=${mk}" data-back>‹ All strategies</a></p><p class="msg">${esc(I.name)} doesn't run on ${esc(MKS[mk].toLowerCase())}.</p>`; return; }
  const s0 = startOf(d, mk, ind, st.side), all = tradesOf(d, mk, ind, st.side, s0.from), S = stats(all), cfg = cfgOf(ind);
  const q = st.q.trim().toUpperCase();
  let L = all.filter(x => (!q || label(mk, x.t).toUpperCase().startsWith(q) || x.t.toUpperCase().startsWith(q))
    && (!st.res || (st.res === "win" ? !x.open && x.ret > 0 : st.res === "loss" ? !x.open && x.ret <= 0 : st.res === "open" ? x.open : x.why === st.res)));
  L = [...L].sort((a, b) => st.order === "old" ? a.g0 - b.g0 : st.order === "best" ? b.ret - a.ret : st.order === "worst" ? a.ret - b.ret : b.g0 - a.g0 || b.g1 - a.g1);
  const show = L.slice(0, st.shown);
  CUR = { mk, ind, d, cfg, list: show, name: I.name };

  // running profit or loss, €1,000 per trade, in the order the trades closed
  const C = all.filter(x => !x.open).sort((a, b) => a.g1 - b.g1);
  let run = 0; const pts = [[0, 0], ...C.map((x, i) => [i + 1, run += x.ret * 1000])];
  const lo = Math.min(0, ...pts.map(p => p[1])), hi = Math.max(0, ...pts.map(p => p[1])), W = 1000, H = 120;
  const X = i => i / Math.max(1, pts.length - 1) * W, Y = v => H - 8 - (v - lo) / ((hi - lo) || 1) * (H - 16);
  const step = Math.max(1, Math.floor(pts.length / 1500));      // thousands of trades: thin the line
  const line = pts.filter((p, j) => j % step === 0 || j === pts.length - 1).map(([i, v], j) => `${j ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("");
  const col = run >= 0 ? "var(--up)" : "var(--down)";
  const whys = ["tp", "sl", "trail", "time"].filter(w => all.some(x => x.why === w));
  const settings = I.params.map(p => `${p.label} ${cfg.p[p.k]}${p.unit ? " " + p.unit : ""}`).join(" · ");
  const sellWord = I.buysOnly ? "its sell signal" : "the opposite signal", noSell = ind === "dip" || ind === "avg_down";
  const exitTxt = I.rule || (cfg.exit === "hold"
    ? `Out ${cfg.tp ? `at a ${cfg.tp}% take profit, or ` : ""}after ${cfg.hold} trading days${noSell ? "" : `, or earlier on ${sellWord}`}.`
    : noSell ? `Out at a ${cfg.tp}% take profit${ind === "avg_down" ? " above the average cost" : ""}, with no time limit.` : `Out on ${sellWord}${cfg.tp ? ` or at a ${cfg.tp}% take profit` : ""}.`);
  const pack = btoa(unescape(encodeURIComponent(JSON.stringify({ m: mk, cfg, side: I.buysOnly ? "long" : st.side })))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const yr = d.dates[s0.from].slice(0, 4), longOnly = I.buysOnly || st.side === "long";

  V.innerHTML = `<p class="crumb"><a href="#m=${mk}" data-back>‹ All strategies</a></p>
    <div class="ph"><div><h1>${esc(I.name)}</h1>
      <div class="sub"><span class="pill ${longOnly ? "L" : "S"}" style="${longOnly ? "" : "background:rgba(56,189,248,.14);color:var(--accent)"}">${longOnly ? "▲ Long only" : "▲▼ Long & short"}</span><span>${esc(I.group)} · ${esc(mkName(mk))} · daily candles · since ${fmtD(d.dates[s0.from])}${s0.why ? ` (${esc(s0.why)})` : ""}</span></div></div>
      <div class="ctrls" style="margin:0">${ctrls(mk, { market: false, side: !I.buysOnly })}</div></div>
    <div class="tiles">
      <div class="panel tile"><small>Trades</small><b>${S.n.toLocaleString("en-US")}</b><span>${whys.length ? whys.map(w => `${all.filter(x => x.why === w).length} ${WHY[w].replace(" hit", "").toLowerCase()}`).join(" · ") : `${all.filter(x => x.why === "rule").length} closed by the strategy's exit`}${S.open ? ` · ${S.open} open` : ""}</span></div>
      <div class="panel tile"><small>Win rate</small><b>${S.win == null ? "–" : S.win.toFixed(1) + "%"}</b><span>Average trade ${pct(S.avg)} · held ${S.days == null ? "–" : S.days.toFixed(0)} days</span></div>
      <div class="panel tile"><small>Profit or loss</small><b>${eur(S.eur)}</b><span>€1,000 in every trade since ${yr}, no costs</span></div>
      <div class="panel tile"><small>Best / worst</small><b>${pct(S.best, 1)} <span style="font-size:18px;color:var(--faint)">/</span> ${pct(S.worst, 1)}</b><span>Single trades</span></div>
    </div>
    <div class="panel rules" style="margin-top:12px">
      <div><b>What it does</b>${esc(I.about)}</div>
      <div><b>Entry and exit</b>${I.own ? "" : "In at the close of the signal day. "}${esc(exitTxt)}</div>
      <div><b>Settings</b>${esc(settings || "Fixed rules, no settings.")}</div>
      <div><b>On the charts</b>Arrow = the entry, square = the exit, the shaded box runs from the entry to the exit price${ovl(ind, cfg).length ? ", and the lines are the strategy's own indicator" : ""}.</div>
    </div>
    <div class="links"><a class="btn ghost" href="index.html#strategylab?c=${pack}">Change the settings in the Strategy Lab ›</a></div>
    ${C.length > 1 ? `<div class="panel eq"><small>Running profit or loss, €1,000 per trade, in the order the trades closed</small>
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Running P/L ends at ${Math.round(run)} euros">
        <line x1="0" x2="${W}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--hair)" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>
        <path d="${line}L${W},${Y(0)}L0,${Y(0)}Z" fill="${col}" opacity=".08"/>
        <path d="${line}" fill="none" stroke="${col}" stroke-width="2" vector-effect="non-scaling-stroke"/>
      </svg></div>` : ""}
    <div class="gridbar"><h2 class="sec">Every trade <span style="font-size:16px;-webkit-text-fill-color:var(--muted);font-weight:400">${L.length.toLocaleString("en-US")} shown · tap one for the full chart</span></h2>
      <div class="ctrls" style="margin:0">
        <input class="find" id="find" type="search" placeholder="${mk === "crypto" ? "Coin, e.g. BTC" : mk === "commodities" ? "e.g. GC=F" : "Ticker, e.g. AAPL"}" value="${esc(st.q)}" aria-label="Filter by ticker" autocomplete="off" autocapitalize="characters" spellcheck="false">
        ${seg("Result", [["", "All"], ["win", "Winners"], ["loss", "Losers"], ...whys.map(w => [w, WHY[w].replace(" hit", "")]), ...(S.open ? [["open", "Open"]] : [])], st.res, "data-res")}
        ${seg("Order", [["new", "Newest"], ["old", "Oldest"], ["best", "Best"], ["worst", "Worst"]], st.order, "data-order")}
      </div></div>
    <div class="minis" id="minis">${show.map((x, k) => card(x, k)).join("") || '<p class="msg">No trades for these settings.</p>'}</div>
    ${L.length > show.length ? `<p class="more"><button type="button" class="btn" data-more>Show ${Math.min(24, L.length - show.length)} more (${(L.length - show.length).toLocaleString("en-US")} left)</button></p>` : ""}`;
  const f = $("find");
  if (f && st.focusFind) { f.focus(); f.setSelectionRange(f.value.length, f.value.length); st.focusFind = false; }
  drawMinis();
}

function chartHref(x) {
  const { mk, ind, d, name } = CUR;
  const p = new URLSearchParams({ st: ind, n: name, m: mk, t: x.t, side: String(x.side), d0: d.dates[x.g0], p0: String(+x.p0.toPrecision(8)), p1: String(+x.p1.toPrecision(8)), why: x.why, r: (x.ret * 100).toFixed(2) });
  if (!x.open) p.set("d1", d.dates[x.g1]);
  if (mk === "stocks" && uni() === "sp500") p.set("u", "sp500");
  return `chart.html?${p}#${encodeURIComponent(x.t)}/D`;
}
function card(x, k) {
  const { mk, d } = CUR, d0 = d.dates[x.g0], ov = ovl(CUR.ind, CUR.cfg);
  return `<a class="mini" href="${esc(chartHref(x))}" data-k="${k}" aria-label="${esc(label(mk, x.t))} ${x.side > 0 ? "long" : "short"} on ${esc(d0)}: ${x.ret > 0 ? "+" : ""}${(x.ret * 100).toFixed(2)}%. Open the full chart">
    <div class="mh"><span><b style="color:var(--fg)">${esc(label(mk, x.t))}</b> · ${x.side > 0 ? '<span class="up">▲</span>' : '<span class="down">▼</span>'} ${esc(d0)}</span><b>${pct(x.ret * 100)}</b></div>
    <canvas width="520" height="340" aria-hidden="true"></canvas>
    ${ov.length ? `<div class="ov">${ov.map(o => `<span><i style="background:${o.col}"></i>${esc(o.name)}</span>`).join("")}</div>` : ""}
    <div class="mf"><div>Entry<b>${money(x.p0)}</b></div><div class="${x.ret > 0 ? "t" : "s"}">${x.open ? "Now" : "Exit"}<b>${money(x.p1)}</b></div><div>Held<b>${x.days} day${x.days === 1 ? "" : "s"}</b></div></div>
    <div class="why">${x.open ? `Still open, since ${esc(fmtD(d0))}` : `${WHY[x.why] || "Closed"} on ${esc(fmtD(d.dates[x.g1]))}`}</div></a>`;
}

// ------------------------------------------------------------------ mini charts
// the strategy's own lines, drawn on its mini charts: [{ name, col, f: closes/highs/lows -> values }]
function ovl(ind, cfg) {
  const p = cfg.p, A = "#f5b942", B = "#a78bfa", Cc = "#38bdf8";
  if (ind === "ma") return [{ name: `${p.n}-day average`, col: A, f: T => sma(T.c, p.n) }];
  if (ind === "cross") return [{ name: `${p.fast}-day`, col: Cc, f: T => sma(T.c, p.fast) }, { name: `${p.slow}-day`, col: A, f: T => sma(T.c, p.slow) }];
  if (ind === "pullback") return [...(p.ma ? [{ name: `${p.ma}-day average`, col: A, f: T => sma(T.c, p.ma) }] : []), { name: `${p.xma}-day average`, col: Cc, f: T => sma(T.c, p.xma) }];
  if (ind === "bollinger") return [{ name: `Bollinger ${p.n}, ${p.k}`, col: B, band: true, f: T => boll(T.c, p.n, p.k) }];
  if (ind === "donchian" || ind === "volume_breakout") return [{ name: `${p.n}-day high / low`, col: B, band: true, f: T => chan(T.h, T.l, p.n) }];
  if (ind === "breakout_bot") return [{ name: "200-day average", col: A, f: T => sma(T.c, 200) }];
  if (ind.startsWith("btc50")) return [{ name: "50-day average", col: A, f: T => sma(T.c, 50) }];
  if (ind === "btc_trend_50_200") return [{ name: "50-day", col: Cc, f: T => sma(T.c, 50) }, { name: "200-day", col: A, f: T => sma(T.c, 200) }];
  if (ind === "deck_ma50") return [{ name: "50-day average", col: A, f: T => sma(T.c, 50) }];
  return [];
}
function sma(x, n) { const o = new Float64Array(x.length).fill(NaN); let s = 0; for (let i = 0; i < x.length; i++) { s += x[i]; if (i >= n) s -= x[i - n]; if (i >= n - 1) o[i] = s / n; } return o; }
function boll(x, n, k) {
  const m = sma(x, n), up = new Float64Array(x.length).fill(NaN), dn = new Float64Array(x.length).fill(NaN);
  for (let i = n - 1; i < x.length; i++) { let v = 0; for (let j = i - n + 1; j <= i; j++) v += (x[j] - m[i]) ** 2; const sd = Math.sqrt(v / n); up[i] = m[i] + k * sd; dn[i] = m[i] - k * sd; }
  return [up, dn, m];
}
function chan(h, l, n) {     // the channel the close is compared with: the N days before today
  const up = new Float64Array(h.length).fill(NaN), dn = new Float64Array(h.length).fill(NaN);
  for (let i = n; i < h.length; i++) { let a = -Infinity, b = Infinity; for (let j = i - n; j < i; j++) { if (h[j] > a) a = h[j]; if (l[j] < b) b = l[j]; } up[i] = a; dn[i] = b; }
  return [up, dn];
}
const OVC = new Map();       // ticker|strategy -> computed lines (per page view)
const posOf = (T, g) => { let lo = 0, hi = T.idx.length - 1; while (lo < hi) { const m = (lo + hi) >> 1; if (T.idx[m] < g) lo = m + 1; else hi = m; } return lo; };

let io;
function drawMinis() {
  io?.disconnect();
  io = new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return;
    io.unobserve(e.target);
    const x = CUR?.list[+e.target.dataset.k], cv = e.target.querySelector("canvas");
    if (x && cv) { try { draw(cv, x); } catch (err) { console.error(err); drawMsg(cv, "Chart unavailable"); } }
  }), { rootMargin: "400px" });
  document.querySelectorAll(".mini").forEach(m => io.observe(m));
}
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function prep(cv) {
  const dpr = Math.min(2, window.devicePixelRatio || 1), w = cv.clientWidth || 260, h = cv.clientHeight || 170;
  cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.fillStyle = css("--c-bg"); g.fillRect(0, 0, w, h);
  return [g, w, h];
}
function drawMsg(cv, t) {
  const [g, w, h] = prep(cv);
  g.fillStyle = css("--faint"); g.font = "12px " + css("--font"); g.textAlign = "center"; g.fillText(t, w / 2, h / 2);
}
function draw(cv, x) {
  const { d, ind, cfg } = CUR, T = d.byT[x.t];
  if (!T) return drawMsg(cv, "No prices");
  const i0 = posOf(T, x.g0), i1 = posOf(T, x.g1), len = i1 - i0;
  const pad = Math.max(10, Math.round(len * 0.35));
  let a = Math.max(0, i0 - pad), b = Math.min(T.c.length - 1, i1 + Math.max(5, Math.round(pad * 0.5)));
  if (b - a > 500) a = Math.max(0, Math.min(i0 - 15, b - 500));
  const [g, w, h] = prep(cv), long = x.side > 0;
  const n = b - a + 1, padL = 4, padR = 4, padT = 10, padB = 10, bw = (w - padL - padR) / n;
  const O = i => T.o ? T.o[i] : i ? T.c[i - 1] : T.c[i];
  const key = `${x.t}|${ind}`;
  if (!OVC.has(key)) OVC.set(key, ovl(ind, cfg).map(o => ({ ...o, v: o.f(T) })));
  const lines = OVC.get(key);
  let lo = Infinity, hi = -Infinity;
  for (let i = a; i <= b; i++) { lo = Math.min(lo, T.l[i]); hi = Math.max(hi, T.h[i]); }
  for (const o of lines) for (const arr of o.band ? o.v.slice(0, 2) : [o.v]) for (let i = a; i <= b; i++) if (arr[i] === arr[i]) { lo = Math.min(lo, arr[i]); hi = Math.max(hi, arr[i]); }
  const m = (hi - lo) * 0.05; lo -= m; hi += m;
  const X = i => padL + (i - a + 0.5) * bw, Y = p => padT + (hi - p) / (hi - lo) * (h - padT - padB);
  const up = css("--c-up"), dn = css("--c-dn"), fg = css("--fg");
  g.strokeStyle = css("--c-grid"); g.lineWidth = 1;
  for (let k = 1; k < 4; k++) { const y = Math.round(padT + k * (h - padT - padB) / 4) + .5; g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); }
  // the trade: a box from the entry to the exit, green for a win, red for a loss
  const win = x.ret > 0, xa = X(i0), xb = Math.max(X(i1), xa + bw);
  g.globalAlpha = .16; g.fillStyle = win ? up : dn;
  g.fillRect(xa, Math.min(Y(x.p0), Y(x.p1)), xb - xa, Math.max(1, Math.abs(Y(x.p1) - Y(x.p0))));
  g.globalAlpha = 1;
  g.strokeStyle = fg; g.setLineDash([3, 3]); g.beginPath(); g.moveTo(xa, Math.round(Y(x.p0)) + .5); g.lineTo(xb, Math.round(Y(x.p0)) + .5); g.stroke(); g.setLineDash([]);
  // the strategy's lines
  for (const o of lines) {
    g.strokeStyle = o.col; g.lineWidth = 1.3;
    for (const [k, arr] of (o.band ? o.v.slice(0, 2) : [o.v]).entries()) {
      g.globalAlpha = o.band ? .8 : .95; g.beginPath(); let on = false;
      for (let i = a; i <= b; i++) { const v = arr[i]; if (v !== v) { on = false; continue; } on ? g.lineTo(X(i), Y(v)) : g.moveTo(X(i), Y(v)); on = true; }
      g.stroke(); void k;
    }
    g.globalAlpha = 1;
  }
  // candles
  const cw = Math.max(1, Math.min(9, bw * 0.7));
  for (let i = a; i <= b; i++) {
    const o = O(i), c = T.c[i], cx = X(i), col = c >= o ? up : dn;
    g.strokeStyle = col; g.lineWidth = 1; g.beginPath(); g.moveTo(Math.round(cx) + .5, Y(T.h[i])); g.lineTo(Math.round(cx) + .5, Y(T.l[i])); g.stroke();
    g.fillStyle = col; const y1 = Y(Math.max(o, c)), y2 = Y(Math.min(o, c));
    g.fillRect(cx - cw / 2, y1, cw, Math.max(1, y2 - y1));
  }
  // entry arrow under (long) or over (short) the candle, exit square
  const ex = X(i0), ey = long ? Y(T.l[i0]) + 3 : Y(T.h[i0]) - 3;
  g.fillStyle = long ? up : dn; g.beginPath();
  if (long) { g.moveTo(ex, ey); g.lineTo(ex - 5, ey + 8); g.lineTo(ex + 5, ey + 8); } else { g.moveTo(ex, ey); g.lineTo(ex - 5, ey - 8); g.lineTo(ex + 5, ey - 8); }
  g.fill();
  if (!x.open) { g.fillStyle = win ? up : dn; g.strokeStyle = css("--c-bg"); g.lineWidth = 1.5; g.beginPath(); g.rect(X(i1) - 4, Y(x.p1) - 4, 8, 8); g.fill(); g.stroke(); }
}

// ------------------------------------------------------------------ wiring
function render() {
  const { mk, s } = route();
  store.mk = mk; save();
  if (s) renderOne(mk, s); else renderAll(mk);
}
document.addEventListener("click", e => {
  const b = e.target.closest("button, tr[data-s], [data-back]");
  if (!b || b.dataset.icsUni) return;
  const { mk, s } = route();
  if (b.dataset.mk) { st.shown = 24; go(b.dataset.mk, ""); return; }
  if (b.dataset.side) { st.side = store.side = b.dataset.side; save(); st.shown = 24; render(); return; }
  if (b.dataset.per) { st.per = store.per = b.dataset.per; save(); st.shown = 24; render(); return; }
  if (b.dataset.sort) { st.sort = store.sort = b.dataset.sort; save(); render(); return; }
  if (b.dataset.res != null) { st.res = b.dataset.res; st.shown = 24; render(); return; }
  if (b.dataset.order) { st.order = b.dataset.order; st.shown = 24; render(); return; }
  if (b.hasAttribute("data-more")) { st.shown += 24; render(); return; }
  if (b.dataset.s) { st.res = ""; st.order = "new"; st.q = ""; st.shown = 24; go(mk, b.dataset.s); scrollTo(0, 0); return; }
  if (b.hasAttribute("data-back")) { e.preventDefault(); go(mk); void s; return; }
});
let findT;
document.addEventListener("input", e => {
  if (e.target.id !== "find") return;
  clearTimeout(findT);
  findT = setTimeout(() => { st.q = e.target.value; st.shown = 24; st.focusFind = true; render(); }, 250);
});
document.addEventListener("keydown", e => { const r = e.target.closest?.("tr[data-s]"); if (r && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); r.click(); } });
addEventListener("hashchange", () => { const { s } = route(); if (!s) st.shown = 24; render(); if (!s) requestAnimationFrame(() => scrollTo(0, 0)); });
let rz; addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => document.querySelectorAll(".mini").forEach(el => { const x = CUR?.list[+el.dataset.k]; const cv = el.querySelector("canvas"); if (x && cv && cv.dataset.drawn !== "0") { try { draw(cv, x); } catch {} } }), 200); });

render();
