// Chart Patterns page (patterns.html): every pattern the site detects, backtested on BTC since 2021.
// Data: data/crypto/btc_patterns.json (rules, settings) + btc_patterns_<D|4H|1H>.json (the trades), from engine/btc_patterns.py.
//   patterns.html#tf=D                                      overview: tiles, rules, one row per pattern
//   patterns.html#tf=4H&p=deck-candle|Bullish engulfing|LONG one pattern: its stats, then every trade as a mini chart
// A mini chart opens chart.html?pat=<trade id>#BTC-USD/<tf>, the full chart with the pattern and the trade drawn.
// Mini-chart candles come straight from Binance (public API), fetched as each card scrolls into view.

const $ = id => document.getElementById(id);
const esc = t => String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const TFN = { D: "daily", "4H": "4-hour", "1H": "1-hour" };
const STEP = { D: 86400, "4H": 14400, "1H": 3600 };
const IV = { D: "1d", "4H": "4h", "1H": "1h" };
const FAM = { chart: "Chart finder", "deck-multi": "Deck · chart", "deck-short": "Deck · short-term", "deck-candle": "Deck · candlestick" };
const WHY = { stop: "Stop loss hit", target: "Take profit hit", time: "Time exit", open: "Still open" };
const LS = "ics.patterns";
const store = (() => { try { return JSON.parse(localStorage.getItem(LS)) || {}; } catch { return {}; } })();
const save = () => { try { localStorage.setItem(LS, JSON.stringify(store)); } catch {} };

const st = { per: store.per || "all", side: store.side || "", sort: store.sort || "eur", res: "", order: "new", shown: 24 };
let META = null;
const TR = {};             // tf -> trades (promise while loading)
const BH = {};             // start time -> BTC close (buy & hold)

const money = v => v >= 1000 ? Math.round(v).toLocaleString("en-US") : v.toLocaleString("en-US", { maximumFractionDigits: 2 });
const pct = (v, d = 2) => v == null ? "–" : `<span class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}%</span>`;
const eur = v => v == null ? "–" : `<span class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "+" : v < 0 ? "−" : ""}€${Math.abs(Math.round(v)).toLocaleString("en-US")}</span>`;
const when = (t, tf) => new Date(t * 1000).toISOString().slice(0, tf === "D" ? 10 : 16).replace("T", " ");
const keyOf = x => `${x.fam}|${x.name}|${x.dir}`;
const cut = () => st.per === "3y" ? Date.now() / 1000 - 3 * 365.25 * 86400 : 0;
const inPer = x => x.ts >= cut();

function route() {
  const q = new URLSearchParams(location.hash.slice(1));
  return { tf: TFN[q.get("tf")] ? q.get("tf") : (store.tf || "D"), p: q.get("p") || "" };
}
const go = (tf, p) => { location.hash = new URLSearchParams(p ? { tf, p } : { tf }).toString(); };

function stats(L) {
  const C = L.filter(x => x.why !== "open"), sum = C.reduce((s, x) => s + x.ret, 0);
  const rets = C.map(x => x.ret);
  return { n: C.length, win: C.length ? C.filter(x => x.ret > 0).length / C.length * 100 : null, avg: C.length ? sum / C.length : null, eur: sum * 10,
    best: rets.length ? Math.max(...rets) : null, worst: rets.length ? Math.min(...rets) : null, open: L.length - C.length };
}
const med = a => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

async function getJson(u) { const r = await fetch(u, { cache: "no-cache" }); if (!r.ok) throw new Error(r.status); return r.json(); }
function trades(tf) { return (TR[tf] ||= getJson(`data/crypto/btc_patterns_${tf}.json`).then(d => d.trades)); }
async function btcAt(t) {     // daily close on/after t (buy & hold), and the latest daily close
  if (BH[t] != null) return BH[t];
  const q = t ? `startTime=${t * 1000}&limit=1` : "limit=1";
  const js = await getJson(`https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1d&${q}`).catch(() => null);
  return (BH[t] = js?.[0] ? +js[0][t ? 1 : 4] : null);
}

function seg(name, opts, cur, attr) {
  return `<div class="seg" role="group" aria-label="${esc(name)}">${opts.map(([v, l]) => `<button type="button" ${attr}="${esc(v)}" aria-pressed="${v === cur}">${esc(l)}</button>`).join("")}</div>`;
}
function ctrls(tf, withSide = true) {
  return seg("Timeframe", [["D", "Daily"], ["4H", "4-hour"], ["1H", "1-hour"]], tf, "data-tf")
    + seg("Period", [["all", "Since 2021"], ["3y", "Last 3 years"]], st.per, "data-per")
    + (withSide ? seg("Side", [["", "Long & short"], ["LONG", "Long"], ["SHORT", "Short"]], st.side, "data-side") : "");
}

// ------------------------------------------------------------------ overview
async function renderAll(tf) {
  $("view-all").hidden = false; $("view-one").hidden = true;
  document.title = "Chart Patterns on Bitcoin";
  $("ctrls").innerHTML = ctrls(tf);
  $("rules").innerHTML = `
    <div><b>Entry</b>The pattern's breakout, confirmed on the candle close. In at the next candle's open.</div>
    <div><b>Stop loss</b>The pattern's own stop, listed for each pattern below. If the next candle opens past it, there's no trade.</div>
    <div><b>Take profit</b>The pattern's measured move. Patterns with no target (candlesticks, NR4, inside bars, gaps) use 2× the risk.</div>
    <div><b>Time exit</b>Neither hit after ${META.hold.D} daily, ${META.hold["4H"]} four-hour or ${META.hold["1H"]} one-hour candles: closed at that candle's close.</div>
    <div><b>Same candle</b>If one candle touches both the stop and the target, it counts as the stop.</div>
    <div><b>Costs</b>${META.fee_pct}% fee on the way in and out. No leverage. Long and short signals both traded, €1,000 per trade.</div>`;
  $("rows").innerHTML = `<tr><td colspan="9" class="msg">Loading the ${TFN[tf]} trades…</td></tr>`;
  let T;
  try { T = await trades(tf); } catch { $("rows").innerHTML = `<tr><td colspan="9" class="msg">The backtest file didn't load. Refresh to try again.</td></tr>`; return; }
  if (route().tf !== tf || route().p) return;
  const L = T.filter(x => inPer(x) && (!st.side || x.dir === st.side)), S = stats(L);
  const t0 = st.per === "3y" ? Math.floor(cut() / 86400) * 86400 : Date.parse(META.start) / 1000;
  const longs = stats(L.filter(x => x.dir === "LONG")), shorts = stats(L.filter(x => x.dir === "SHORT"));
  const tiles = bh => [
    ["Trades", S.n.toLocaleString("en-US"), `${L.filter(x => x.why === "target").length} take profits · ${L.filter(x => x.why === "stop").length} stop losses`],
    ["Win rate", S.win == null ? "–" : S.win.toFixed(1) + "%", `Long ${longs.win == null ? "–" : longs.win.toFixed(0) + "%"} · short ${shorts.win == null ? "–" : shorts.win.toFixed(0) + "%"}`],
    ["Profit or loss", eur(S.eur), `€1,000 in every trade · long ${eur(longs.eur)} · short ${eur(shorts.eur)}`],
    ["Buy & hold BTC", bh == null ? "…" : pct(bh, 0), bh == null ? "Loading the price…" : `€1,000 held over the same period: ${eur(bh * 10)}`],
  ].map(([k, v, s]) => `<div class="panel tile"><small>${k}</small><b>${v}</b><span>${s}</span></div>`).join("");
  $("tiles").innerHTML = tiles(null);
  Promise.all([btcAt(t0), btcAt(0)]).then(([a, b]) => { if (route().tf === tf && !route().p) $("tiles").innerHTML = tiles(a && b ? (b / a - 1) * 100 : null); });

  const G = {};
  L.forEach(x => (G[keyOf(x)] ||= []).push(x));
  const rows = Object.entries(G).map(([k, xs]) => {
    const [fam, name, dir] = k.split("|"), s = stats(xs);
    return { k, fam, name, dir, ...s, risk: med(xs.map(x => Math.abs(x.entry / x.stop - 1) * 100)), tp: med(xs.map(x => Math.abs(x.target / x.entry - 1) * 100)) };
  }).filter(r => r.n);
  const sk = st.sort;
  rows.sort((a, b) => sk === "name" ? a.name.localeCompare(b.name) || a.dir.localeCompare(b.dir) : (b[sk] ?? -1e9) - (a[sk] ?? -1e9));
  const th = (k, l, n) => `<th class="${n ? "n" : ""}"><button type="button" data-sort="${k}" aria-pressed="${sk === k}">${l}</button></th>`;
  $("thead").innerHTML = `<tr>${th("name", "Pattern")}<th>Side</th><th>Stop loss</th><th>Take profit</th>${th("n", "Trades", 1)}${th("win", "Win rate", 1)}${th("avg", "Avg trade", 1)}<th class="n">Typical SL / TP</th>${th("eur", "€1k / trade", 1)}</tr>`;
  $("rows").innerHTML = rows.length ? rows.map(r => {
    const R = META.rules[`${r.fam}|${r.name}`] || {};
    return `<tr tabindex="0" data-p="${esc(r.k)}" aria-label="${esc(r.name)} ${r.dir === "LONG" ? "long" : "short"}: open its trades">
      <td><span class="nm">${esc(r.name)}</span>${r.n < 5 ? '<span class="few">few</span>' : ""}<span class="fam">${esc(FAM[r.fam])}</span></td>
      <td><span class="pill ${r.dir[0]}">${r.dir === "LONG" ? "▲ Long" : "▼ Short"}</span></td>
      <td class="rule">${esc(R.stop || "")}</td><td class="rule">${esc(R.target || "")}</td>
      <td class="n">${r.n}</td><td class="n">${r.win.toFixed(1)}%<span class="bar2"><i style="width:${r.win}%"></i></span></td>
      <td class="n">${pct(r.avg)}</td><td class="n">${r.risk.toFixed(1)}% / ${r.tp.toFixed(1)}%</td>
      <td class="n"><b>${eur(r.eur)}</b> <span class="go">›</span></td></tr>`;
  }).join("") : `<tr><td colspan="9" class="msg">No patterns for these settings.</td></tr>`;
  $("foot").textContent = `Bitcoin candles: ${META.source}, ${TFN[tf]}, from ${new Date(META.start).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}. Trades still open aren't counted. Backtest built ${new Date(META.as_of).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })} (engine/btc_patterns.py, rebuilt every Sunday). Past results don't predict future ones. For information only. Not investment advice.`;
}

// ------------------------------------------------------------------ one pattern
async function renderOne(tf, k) {
  $("view-all").hidden = true; const V = $("view-one"); V.hidden = false;
  const [fam, name, dir] = k.split("|"), R = META.rules[`${fam}|${name}`] || {}, long = dir === "LONG";
  document.title = `${name} on Bitcoin · Chart Patterns`;
  V.innerHTML = `<p class="crumb"><a href="#tf=${tf}" data-back>‹ All patterns</a></p><p class="msg">Loading the trades…</p>`;
  let T;
  try { T = await trades(tf); } catch { V.innerHTML += `<p class="msg">The backtest file didn't load. Refresh to try again.</p>`; return; }
  const r = route(); if (r.tf !== tf || r.p !== k) return;
  const all = T.filter(x => keyOf(x) === k && inPer(x)), S = stats(all);
  let L = all.filter(x => !st.res || (st.res === "win" ? x.ret > 0 && x.why !== "open" : st.res === "loss" ? x.ret <= 0 && x.why !== "open" : x.why === st.res));
  L = [...L].sort((a, b) => st.order === "old" ? a.ts - b.ts : st.order === "best" ? b.ret - a.ret : st.order === "worst" ? a.ret - b.ret : b.ts - a.ts);
  const show = L.slice(0, st.shown);

  // equity curve: €1,000 per trade, oldest first
  const C = all.filter(x => x.why !== "open").sort((a, b) => a.tx - b.tx);
  let run = 0; const pts = [[0, 0], ...C.map((x, i) => [i + 1, run += x.ret * 10])];
  const lo = Math.min(0, ...pts.map(p => p[1])), hi = Math.max(0, ...pts.map(p => p[1])), W = 1000, H = 120;
  const X = i => i / Math.max(1, pts.length - 1) * W, Y = v => H - 8 - (v - lo) / ((hi - lo) || 1) * (H - 16);
  const line = pts.map(([i, v], j) => `${j ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join("");
  const endUp = run >= 0, col = endUp ? "var(--up)" : "var(--down)";

  V.innerHTML = `<p class="crumb"><a href="#tf=${tf}" data-back>‹ All patterns</a></p>
    <div class="ph"><div><h1>${esc(name)}</h1>
      <div class="sub"><span class="pill ${dir[0]}">${long ? "▲ Long" : "▼ Short"}</span><span>${esc(FAM[fam])} · Bitcoin · ${TFN[tf]} candles · ${st.per === "3y" ? "last 3 years" : "since " + META.start.slice(0, 4)}</span></div></div>
      <div class="ctrls" style="margin:0">${ctrls(tf, false)}</div></div>
    <div class="tiles">
      <div class="panel tile"><small>Trades</small><b>${S.n}</b><span>${all.filter(x => x.why === "target").length} take profits · ${all.filter(x => x.why === "stop").length} stop losses${S.open ? ` · ${S.open} open` : ""}</span></div>
      <div class="panel tile"><small>Win rate</small><b>${S.win == null ? "–" : S.win.toFixed(1) + "%"}</b><span>Average trade ${pct(S.avg)}</span></div>
      <div class="panel tile"><small>Profit or loss</small><b>${eur(S.eur)}</b><span>€1,000 in every trade, after fees</span></div>
      <div class="panel tile"><small>Best / worst</small><b>${pct(S.best, 1)} <span style="font-size:18px;color:var(--faint)">/</span> ${pct(S.worst, 1)}</b><span>Single trades</span></div>
    </div>
    <div class="panel rules" style="margin-top:12px">
      <div><b>Entry</b>${esc(META.trigger[fam] || "")}. In at the next candle's open.</div>
      <div><b>Stop loss</b>${esc(R.stop || "")}</div>
      <div><b>Take profit</b>${esc(R.target || "")}</div>
      <div><b>Time exit</b>After ${META.hold[tf]} ${TFN[tf]} candles. ${META.fee_pct}% fees each way.</div>
    </div>
    ${C.length > 1 ? `<div class="panel eq"><small>Running profit or loss, €1,000 per trade, in the order the trades closed</small>
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Running P/L ends at ${Math.round(run)} euros">
        <line x1="0" x2="${W}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--hair)" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>
        <path d="${line}L${W},${Y(0)}L0,${Y(0)}Z" fill="${col}" opacity=".08"/>
        <path d="${line}" fill="none" stroke="${col}" stroke-width="2" vector-effect="non-scaling-stroke"/>
      </svg></div>` : ""}
    <div class="gridbar"><h2 class="sec">Every trade <span style="font-size:16px;-webkit-text-fill-color:var(--muted);font-weight:400">${L.length} shown · tap one for the full chart</span></h2>
      <div class="ctrls" style="margin:0">
        ${seg("Result", [["", "All"], ["win", "Winners"], ["loss", "Losers"], ["target", "Take profit"], ["stop", "Stop loss"]], st.res, "data-res")}
        ${seg("Order", [["new", "Newest"], ["old", "Oldest"], ["best", "Best"], ["worst", "Worst"]], st.order, "data-order")}
      </div></div>
    <div class="minis" id="minis">${show.map(card).join("") || '<p class="msg">No trades for these settings.</p>'}</div>
    ${L.length > show.length ? `<p class="more"><button type="button" class="btn" data-more>Show ${Math.min(24, L.length - show.length)} more (${L.length - show.length} left)</button></p>` : ""}`;
  watch();
}

function card(x) {
  const href = `chart.html?pat=${encodeURIComponent(x.id)}#BTC-USD/${x.tf}`;
  return `<a class="mini" href="${esc(href)}" data-id="${esc(x.id)}" aria-label="${esc(x.name)} on ${esc(when(x.ts, x.tf))}: ${x.ret > 0 ? "+" : ""}${x.ret.toFixed(2)}%. Open the full chart">
    <div class="mh"><span>${esc(when(x.ts, x.tf))}${x.tf === "D" ? "" : " UTC"}</span><b>${pct(x.ret)}</b></div>
    <canvas width="520" height="340" aria-hidden="true"></canvas>
    <div class="mf"><div>Entry<b>${money(x.entry)}</b></div><div class="s">Stop loss<b>${money(x.stop)}</b></div><div class="t">Take profit<b>${money(x.target)}${x.tr === "2R" ? " ·2R" : ""}</b></div></div>
    <div class="why">${WHY[x.why]} at ${money(x.exit)} after ${x.bars} candle${x.bars === 1 ? "" : "s"}</div></a>`;
}

// ------------------------------------------------------------------ mini charts
const CANDLES = new Map();
let queue = [], busy = 0;
function pump() {
  while (busy < 4 && queue.length) {
    const [x, cv] = queue.shift(); busy++;
    candlesFor(x).then(c => draw(cv, x, c)).catch(() => drawMsg(cv, "Chart unavailable")).finally(() => { busy--; pump(); });
  }
}
let io;
function watch() {
  io?.disconnect();
  const byId = {};
  trades(route().tf).then(T => {
    T.forEach(t => { byId[t.id] = t; });
    io = new IntersectionObserver(es => es.forEach(e => {
      if (!e.isIntersecting) return;
      io.unobserve(e.target);
      const x = byId[e.target.dataset.id], cv = e.target.querySelector("canvas");
      if (x && cv) { drawMsg(cv, "Loading…"); queue.push([x, cv]); pump(); }
    }), { rootMargin: "300px" });
    document.querySelectorAll(".mini").forEach(m => io.observe(m));
  });
}
function span(x) {
  const sh = x.shape || {}, ts = [x.ts, ...(sh.pts || []).map(p => p[0]), ...(sh.lines || []).flatMap(l => [l[0], l[2]]), ...(sh.box ? [sh.box[0]] : [])];
  const s = STEP[x.tf], t0 = Math.min(...ts), t1 = Math.max(x.tx, x.te);
  const pad = Math.max(6, Math.round((t1 - t0) / s * 0.15));
  let a = t0 - pad * s, b = t1 + Math.max(4, Math.round(pad * 0.6)) * s;
  if ((b - a) / s > 600) a = b - 600 * s;
  return [a, b];
}
async function candlesFor(x) {
  if (CANDLES.has(x.id)) return CANDLES.get(x.id);
  const [a, b] = span(x);
  const q = new URLSearchParams({ symbol: "BTCUSDT", interval: IV[x.tf], startTime: String(a * 1000), endTime: String(b * 1000), limit: "1000" });
  let js;
  for (let k = 0; k < 2; k++) { try { js = await getJson(`https://data-api.binance.vision/api/v3/klines?${q}`); break; } catch (e) { if (k) throw e; await new Promise(r => setTimeout(r, 800)); } }
  const c = js.map(k => ({ t: Math.floor(k[0] / 1000), o: +k[1], h: +k[2], l: +k[3], c: +k[4] }));
  CANDLES.set(x.id, c);
  return c;
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
function draw(cv, x, c) {
  if (!c.length) return drawMsg(cv, "No candles");
  const [g, w, h] = prep(cv), sh = x.shape || {}, s = STEP[x.tf], long = x.dir === "LONG";
  const padL = 4, padR = 4, padT = 10, padB = 10, n = c.length, bw = (w - padL - padR) / n;
  let lo = Math.min(...c.map(b => b.l), x.stop, x.target), hi = Math.max(...c.map(b => b.h), x.stop, x.target);
  const m = (hi - lo) * 0.04; lo -= m; hi += m;
  const t0 = c[0].t;
  const X = t => padL + ((t - t0) / s + 0.5) * bw, Y = p => padT + (hi - p) / (hi - lo) * (h - padT - padB);
  const up = css("--c-up"), dn = css("--c-dn"), amber = css("--amber"), fg = css("--fg");
  // grid
  g.strokeStyle = css("--c-grid"); g.lineWidth = 1;
  for (let k = 1; k < 4; k++) { const y = Math.round(padT + k * (h - padT - padB) / 4) + .5; g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); }
  // trade zones: entry to target (green) and entry to stop (red), from the entry candle to the exit
  const xa = X(x.te) - bw / 2, xb = Math.max(X(x.tx) + bw / 2, xa + 2 * bw);
  g.globalAlpha = .16;
  g.fillStyle = up; g.fillRect(xa, Math.min(Y(x.entry), Y(x.target)), xb - xa, Math.abs(Y(x.target) - Y(x.entry)));
  g.fillStyle = dn; g.fillRect(xa, Math.min(Y(x.entry), Y(x.stop)), xb - xa, Math.abs(Y(x.stop) - Y(x.entry)));
  g.globalAlpha = 1;
  const hline = (p, col, dash) => { g.strokeStyle = col; g.setLineDash(dash); g.lineWidth = 1; g.beginPath(); g.moveTo(xa, Math.round(Y(p)) + .5); g.lineTo(xb, Math.round(Y(p)) + .5); g.stroke(); g.setLineDash([]); };
  hline(x.target, up, []); hline(x.stop, dn, []); hline(x.entry, fg, [3, 3]);
  // candles
  const cw = Math.max(1, Math.min(9, bw * 0.7));
  for (const b of c) {
    const cx = X(b.t), col = b.c >= b.o ? up : dn;
    g.strokeStyle = col; g.lineWidth = 1; g.beginPath(); g.moveTo(Math.round(cx) + .5, Y(b.h)); g.lineTo(Math.round(cx) + .5, Y(b.l)); g.stroke();
    g.fillStyle = col; const y1 = Y(Math.max(b.o, b.c)), y2 = Y(Math.min(b.o, b.c));
    g.fillRect(cx - cw / 2, y1, cw, Math.max(1, y2 - y1));
  }
  // the pattern: box, boundary lines (dashed), swing points joined in amber
  g.lineWidth = 1.5;
  if (sh.box) { g.strokeStyle = amber; g.strokeRect(X(sh.box[0]) - bw / 2, Y(sh.box[3]), X(sh.box[1]) - X(sh.box[0]) + bw, Y(sh.box[2]) - Y(sh.box[3])); }
  g.strokeStyle = css("--muted"); g.setLineDash([4, 3]);
  for (const [a, pa, b, pb] of sh.lines || []) { g.beginPath(); g.moveTo(X(a), Y(pa)); g.lineTo(X(b), Y(pb)); g.stroke(); }
  g.setLineDash([]);
  const P = (sh.pts || []).slice().sort((a, b) => a[0] - b[0]);
  if (P.length > 1) { g.strokeStyle = amber; g.lineWidth = 2; g.beginPath(); P.forEach(([t, p], i) => i ? g.lineTo(X(t), Y(p)) : g.moveTo(X(t), Y(p))); g.stroke(); }
  g.font = "600 10px " + css("--font"); g.textAlign = "center";
  for (const [t, p, lb] of P) {
    g.fillStyle = amber; g.beginPath(); g.arc(X(t), Y(p), 2.5, 0, 7); g.fill();
    if (lb) { const above = p >= (hi + lo) / 2; g.fillText(String(lb).slice(0, 6), X(t), Y(p) + (above ? -6 : 13)); }
  }
  // entry arrow and exit square
  const ex = X(x.te), ey = Y(x.entry);
  g.fillStyle = long ? up : dn; g.beginPath();
  if (long) { g.moveTo(ex, ey + 3); g.lineTo(ex - 5, ey + 11); g.lineTo(ex + 5, ey + 11); } else { g.moveTo(ex, ey - 3); g.lineTo(ex - 5, ey - 11); g.lineTo(ex + 5, ey - 11); }
  g.fill();
  if (x.why !== "open") { g.fillStyle = x.ret >= 0 ? up : dn; g.strokeStyle = css("--c-bg"); g.lineWidth = 1.5; g.beginPath(); g.rect(X(x.tx) - 4, Y(x.exit) - 4, 8, 8); g.fill(); g.stroke(); }
}

// ------------------------------------------------------------------ wiring
function render() {
  const { tf, p } = route();
  store.tf = tf; save();
  if (p) renderOne(tf, p); else renderAll(tf);
}
document.addEventListener("click", e => {
  const b = e.target.closest("button, tr[data-p], [data-back]");
  if (!b) return;
  const { tf, p } = route();
  if (b.dataset.tf) { st.shown = 24; go(b.dataset.tf, p); return; }
  if (b.dataset.per != null) { st.per = store.per = b.dataset.per; save(); st.shown = 24; render(); return; }
  if (b.dataset.side != null) { st.side = store.side = b.dataset.side; save(); render(); return; }
  if (b.dataset.sort) { st.sort = store.sort = b.dataset.sort; save(); render(); return; }
  if (b.dataset.res != null) { st.res = b.dataset.res; st.shown = 24; render(); return; }
  if (b.dataset.order) { st.order = b.dataset.order; st.shown = 24; render(); return; }
  if (b.hasAttribute("data-more")) { st.shown += 24; render(); return; }
  if (b.dataset.p) { st.res = ""; st.order = "new"; st.shown = 24; go(tf, b.dataset.p); scrollTo(0, 0); return; }
  if (b.hasAttribute("data-back")) { e.preventDefault(); go(tf); return; }
});
document.addEventListener("keydown", e => { const r = e.target.closest?.("tr[data-p]"); if (r && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); r.click(); } });
addEventListener("hashchange", () => { const { p } = route(); if (!p) st.shown = 24; render(); if (!p) requestAnimationFrame(() => scrollTo(0, 0)); });
let rz; addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => document.querySelectorAll(".mini").forEach(m => { const x = CANDLES.get(m.dataset.id); if (x) trades(route().tf).then(T => { const t = T.find(y => y.id === m.dataset.id); if (t) draw(m.querySelector("canvas"), t, x); }); }), 200); });

getJson("data/crypto/btc_patterns.json").then(m => { META = m; render(); })
  .catch(() => { $("rows").innerHTML = `<tr><td colspan="9" class="msg">The backtest isn't built yet. It runs every Sunday with the crypto scan.</td></tr>`; });
