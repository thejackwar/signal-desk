// Dark Pools page: block prints (data/darkpool/blocks.json), daily pressure (daily.json),
// dark pool venues (weekly.json), backtest (backtest.json). All built by engine/darkpool.py.
const $ = s => document.querySelector(s);
// S&P 100 / S&P 500 (the app's stock-list choice, docs/sections.js): this list's data folder and name
const UD = p => globalThis.ICS ? ICS.udata(p) : "data/" + p;
const UL = (globalThis.ICS?.uniName?.() || "S&P 100").replace("&", "&amp;");
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const VIEWS = ["prints", "pressure", "venues", "backtest", "learn"];
let B, D, W, T;
const st = { q: "", big: false, sort: "time", sig: "all", bt: "out" };

const money = v => v == null ? "–" : v >= 1e9 ? `$${(v / 1e9).toFixed(2)}B` : v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : `$${Math.round(v / 1e3)}K`;
const num = v => v == null ? "–" : v >= 1e9 ? `${(v / 1e9).toFixed(2)}B` : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(0)}K` : String(v);
const pct = (v, d = 1) => v == null ? "–" : `${v.toFixed(d)}%`;
const sgn = (v, d = 2) => v == null ? "–" : `${v > 0 ? "+" : ""}${v.toFixed(d)}`;
const cls = v => v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "";
const fmtDate = s => s ? new Date(s + (s.length === 10 ? "T12:00:00Z" : "")).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "–";
const nyTime = s => new Date(s).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "America/New_York" });
const localTime = s => new Date(s).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
const stockLink = t => `index.html#stock-${encodeURIComponent(t)}`;

function spark(vals, { w = 110, h = 26, color = "var(--accent)", zero = null } = {}) {
  const v = vals.map(x => x == null ? NaN : x), ok = v.filter(Number.isFinite);
  if (ok.length < 2) return "";
  const lo = Math.min(...ok, zero ?? Infinity), hi = Math.max(...ok, zero ?? -Infinity), sp = hi - lo || 1;
  const X = i => (i / (v.length - 1)) * (w - 2) + 1, Y = x => h - 2 - ((x - lo) / sp) * (h - 4);
  let d = "", pen = false;
  v.forEach((x, i) => { if (!Number.isFinite(x)) { pen = false; return; } d += `${pen ? "L" : "M"}${X(i).toFixed(1)},${Y(x).toFixed(1)}`; pen = true; });
  const z = zero != null ? `<line x1="0" x2="${w}" y1="${Y(zero)}" y2="${Y(zero)}" stroke="var(--hair)" stroke-dasharray="2 2"/>` : "";
  const last = ok.length ? `<circle cx="${X(v.length - 1)}" cy="${Y(v[v.length - 1])}" r="2.2" fill="${color}"/>` : "";
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${z}<path d="${d}" fill="none" stroke="${color}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>${last}</svg>`;
}

// line chart with several series; each {name, v, color, dash, axis: "l"|"r"}
function lines(dates, series, { h = 260, fmtL = x => x, fmtR = x => x } = {}) {
  const W0 = Math.min(780, Math.max(320, ($("main")?.clientWidth || 760) - 60)), H = h, pl = 46, pr = series.some(s => s.axis === "r") ? 46 : 10, pt = 10, pb = 26;
  const ax = a => { const all = series.filter(s => (s.axis || "l") === a).flatMap(s => s.v).filter(Number.isFinite); return all.length ? [Math.min(...all), Math.max(...all)] : [0, 1]; };
  const rng = { l: ax("l"), r: ax("r") };
  const X = i => pl + (i / Math.max(1, dates.length - 1)) * (W0 - pl - pr);
  const Y = (x, a) => { const [lo, hi] = rng[a]; return pt + (1 - (x - lo) / ((hi - lo) || 1)) * (H - pt - pb); };
  let g = "";
  for (let k = 0; k <= 4; k++) {
    const y = pt + k * (H - pt - pb) / 4, [lo, hi] = rng.l, val = hi - k * (hi - lo) / 4;
    g += `<line x1="${pl}" x2="${W0 - pr}" y1="${y}" y2="${y}" stroke="var(--c-grid)"/><text x="${pl - 6}" y="${y + 4}" text-anchor="end" font-size="11" fill="var(--c-txt)">${fmtL(val)}</text>`;
    if (pr > 10) { const [l2, h2] = rng.r, v2 = h2 - k * (h2 - l2) / 4; g += `<text x="${W0 - pr + 6}" y="${y + 4}" font-size="11" fill="var(--c-txt)">${fmtR(v2)}</text>`; }
  }
  const ticks = 5;
  for (let k = 0; k <= ticks; k++) {
    const i = Math.round(k * (dates.length - 1) / ticks);
    g += `<text x="${X(i)}" y="${H - 6}" text-anchor="${k === 0 ? "start" : k === ticks ? "end" : "middle"}" font-size="11" fill="var(--c-txt)">${new Date(dates[i] + "T12:00:00Z").toLocaleDateString("en-GB", { month: "short", year: "2-digit" })}</text>`;
  }
  for (const s of series) {
    if (s.candles) {   // a price as candles: every `s.candles` closes make one (open = the close before)
      const m = s.candles, sw = (X(1) - X(0)) * m, bw = Math.max(1, Math.min(9, sw * .62)), a = s.axis || "l";
      for (let a0 = 0; a0 < s.v.length; a0 += m) {
        const grp = s.v.slice(a0, a0 + m).filter(Number.isFinite); if (!grp.length) continue;
        const op = a0 && Number.isFinite(s.v[a0 - 1]) ? s.v[a0 - 1] : grp[0], cl = grp[grp.length - 1], hh = Math.max(op, ...grp), ll = Math.min(op, ...grp);
        const cx = X(a0 + (Math.min(m, s.v.length - a0) - 1) / 2), col = cl >= op ? "var(--up-line)" : "var(--down-line)";
        g += `<line x1="${cx.toFixed(1)}" x2="${cx.toFixed(1)}" y1="${Y(hh, a).toFixed(1)}" y2="${Y(ll, a).toFixed(1)}" stroke="${col}" stroke-opacity=".7"/><rect x="${(cx - bw / 2).toFixed(1)}" y="${Y(Math.max(op, cl), a).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, Math.abs(Y(op, a) - Y(cl, a))).toFixed(1)}" fill="${col}" fill-opacity=".7"/>`;
      }
      continue;
    }
    let d = "", pen = false;
    s.v.forEach((x, i) => { if (!Number.isFinite(x)) { pen = false; return; } d += `${pen ? "L" : "M"}${X(i).toFixed(1)},${Y(x, s.axis || "l").toFixed(1)}`; pen = true; });
    g += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.wd || 2}" ${s.dash ? 'stroke-dasharray="5 4"' : ""}/>`;
  }
  const leg = `<div class="legend">${series.map(s => `<span><i class="${s.dash ? "dash" : ""}" style="border-color:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>`;
  return `<div class="chartbox"><svg viewBox="0 0 ${W0} ${H}">${g}</svg></div>${leg}`;
}

function show(v) {
  if (!VIEWS.includes(v)) v = "prints";
  for (const k of VIEWS) $(`#v-${k}`).hidden = k !== v;
  for (const b of document.querySelectorAll("#tabs button")) b.setAttribute("aria-pressed", String(b.dataset.v === v));
  if (location.hash !== `#${v}`) history.replaceState(null, "", `#${v}`);
}

const waiting = what => `<div class="msg"><strong>Data on its way</strong>${what}</div>`;
const nameOf = t => (D?.stocks || []).find(s => s.t === t)?.n || "";

// ------------------------------------------------------------------ block prints
function renderPrints() {
  const el = $("#v-prints");
  if (!B) { el.innerHTML = waiting("Block prints are read every 30 minutes while the US market is open (15:30–22:00 Italian time)."); return; }
  const prints = B.prints || [], S = B.stocks || {};
  const tot = prints.reduce((a, p) => a + p.v, 0), up = prints.filter(p => p.lean === 1).reduce((a, p) => a + p.v, 0), dn = prints.filter(p => p.lean === -1).reduce((a, p) => a + p.v, 0);
  const allTot = Object.values(S).reduce((a, s) => a + s.tot, 0), allDark = Object.values(S).reduce((a, s) => a + s.dark, 0);
  const live = !B.final;
  const until = B.until ? nyTime(B.until) : "–";
  const lead = Object.entries(S).filter(([, s]) => s.bv > 0).sort((a, b) => b[1].bv - a[1].bv).slice(0, 12);
  const sub = B.ntfy_prefix && B.ntfy_prefix !== "demo" ? `<a href="https://ntfy.sh/${encodeURIComponent(B.ntfy_prefix)}-darkpool" target="_blank" rel="noopener">Get phone alerts for big blocks</a> (free ntfy app, topic <code>${esc(B.ntfy_prefix)}-darkpool</code>).` : "";
  el.innerHTML = `
    <dl class="kpis">
      <div><dt>${live ? "Today so far" : "Session"}</dt><dd>${fmtDate(B.date)}</dd><small>${live ? `up to ${until} New York` : "full day"}</small></div>
      <div><dt>Block prints</dt><dd>${prints.length.toLocaleString("en-GB")}</dd><small>$${(B.min || 1e6) / 1e6}M or more each</small></div>
      <div><dt>Block value</dt><dd>${money(tot)}</dd><small>${tot ? `<span class="pos">${Math.round(100 * up / tot)}% above</span> · <span class="neg">${Math.round(100 * dn / tot)}% below</span> lit` : "&nbsp;"}</small></div>
      <div><dt>Off-exchange share</dt><dd>${allTot ? pct(100 * allDark / allTot, 0) : "–"}</dd><small>of all ${UL} volume</small></div>
    </dl>
    <div class="panel"><h2>Biggest dark money today</h2>
      <p class="cardnote">Stocks with the most block value. Green = printed above the last exchange trade (buyer paid up), red = below (seller gave way), grey = at the price.</p>
      ${lead.length ? `<div class="tbl"><table><thead><tr><th>Stock</th><th>Blocks</th><th>Value</th><th>Lean</th><th class="hm">Off-exchange</th></tr></thead><tbody>
      ${lead.map(([t, s]) => { const a = Math.max(0, s.bv - s.up - s.dn); return `<tr class="click" data-t="${esc(t)}"><td class="tk"><b>${esc(t)}</b><small>${esc(nameOf(t))}</small></td><td>${s.bn}</td><td>${money(s.bv)}</td><td><span class="split2"><i class="u" style="width:${100 * s.up / s.bv}%"></i><i class="a" style="width:${100 * a / s.bv}%"></i><i class="d" style="width:${100 * s.dn / s.bv}%"></i></span></td><td class="hm">${s.tot ? pct(100 * s.dark / s.tot, 0) : "–"}</td></tr>`; }).join("")}
      </tbody></table></div>` : `<p class="note">No blocks yet today.</p>`}
    </div>
    <div class="panel"><h2>Every block</h2>
      <div class="toolbar">
        <input type="search" id="q" placeholder="Ticker" value="${esc(st.q)}" autocomplete="off" autocapitalize="characters">
        <div class="seg" id="bigsel"><button type="button" data-b="0" aria-pressed="${!st.big}">All</button><button type="button" data-b="1" aria-pressed="${st.big}">Big only</button></div>
        <div class="seg" id="sortsel"><button type="button" data-s="time" aria-pressed="${st.sort === "time"}">Latest</button><button type="button" data-s="size" aria-pressed="${st.sort === "size"}">Largest</button></div>
      </div>
      <div id="plist"></div>
      <p class="note">“Big” = $10M or more and at least 1% of what the stock usually trades in a day: those are the phone alerts. ${sub}</p>
    </div>`;
  drawList();
  $("#q").addEventListener("input", e => { st.q = e.target.value.trim().toUpperCase(); drawList(); });
  el.querySelectorAll("#bigsel button").forEach(b => b.onclick = () => { st.big = b.dataset.b === "1"; renderPrints(); });
  el.querySelectorAll("#sortsel button").forEach(b => b.onclick = () => { st.sort = b.dataset.s; renderPrints(); });
  el.querySelectorAll("tr.click").forEach(r => r.onclick = () => { st.q = r.dataset.t; renderPrints(); $("#plist").scrollIntoView({ behavior: "smooth", block: "start" }); });
}

function drawList() {
  let p = (B.prints || []).filter(x => (!st.q || x.t.startsWith(st.q)) && (!st.big || x.big));
  p = st.sort === "size" ? p.slice().sort((a, b) => b.v - a.v) : p.slice().sort((a, b) => b.ts.localeCompare(a.ts));
  const shown = p.slice(0, 200);
  const lean = l => l === 1 ? `<span class="lean up">Above</span>` : l === -1 ? `<span class="lean dn">Below</span>` : l === 0 ? `<span class="lean">At</span>` : `<span class="dim">–</span>`;
  $("#plist").innerHTML = shown.length ? `<div class="tbl"><table><thead><tr><th>Time (NY)</th><th>Stock</th><th>Value</th><th class="hm">Shares</th><th>Price</th><th class="hm2">Lit price</th><th>vs lit</th></tr></thead><tbody>
    ${shown.map(x => `<tr class="${x.big ? "big" : ""}"><td title="${localTime(x.ts)} your time">${nyTime(x.ts)}</td><td><a href="${stockLink(x.t)}"><b>${esc(x.t)}</b></a></td><td><b>${money(x.v)}</b></td><td class="hm">${x.sz.toLocaleString("en-GB")}</td><td>${x.px}</td><td class="dim hm2">${x.lit ?? "–"}</td><td>${lean(x.lean)}</td></tr>`).join("")}
    </tbody></table></div>${p.length > shown.length ? `<p class="note">Showing ${shown.length} of ${p.length}.</p>` : ""}` : `<div class="empty">No blocks match.</div>`;
}

// ------------------------------------------------------------------ daily pressure
function renderPressure() {
  const el = $("#v-pressure");
  if (!D) { el.innerHTML = waiting("The daily score is computed every evening after FINRA publishes the day's data (around midnight Italian time)."); return; }
  const I = D.index;
  const read = I.z == null ? "" : I.z >= D.z_hot ? "unusually high: heavy off-exchange buying, on the theory" : I.z <= -D.z_hot ? "unusually low: heavy off-exchange selling, on the theory" : "in its normal range";
  const spyN = I.spy ? (() => { const f = I.spy.find(Number.isFinite); return I.spy.map(x => x == null ? NaN : 100 * x / f); })() : null;
  const list = D.stocks.filter(s => st.sig === "all" || s.sig === st.sig);
  const nb = D.stocks.filter(s => s.sig === "buying").length, ns = D.stocks.filter(s => s.sig === "selling").length;
  const zdot = z => { if (z == null) return ""; const p = Math.max(0, Math.min(100, 50 + z * 12.5)); return `<span class="ztrack"><i class="${z >= D.z_hot ? "u" : z <= -D.z_hot ? "d" : ""}" style="left:${p}%"></i></span>`; };
  el.innerHTML = `
    <div class="panel"><h2>${UL} dark index</h2>
      <p class="lead">The share of off-exchange volume that was short sales, for all the stocks together, weighted by dollars traded. On ${fmtDate(D.date)} it was ${pct(I.value)}, ${read}.</p>
      <dl class="stats"><div><dt>Dark index</dt><dd>${pct(I.value)}</dd></div><div><dt>vs last ${D.z_win} days</dt><dd class="${cls(I.z)}">${sgn(I.z)} σ</dd></div><div><dt>Higher than</dt><dd>${I.pct_1y == null ? "–" : I.pct_1y + "% of the year"}</dd></div></dl>
      ${lines(I.dates, [...(spyN ? [{ name: "SPY weekly candles (right axis)", v: spyN, candles: 5, color: "var(--up-line)", axis: "r" }] : []), { name: "Dark index (%)", v: I.v.map(x => x ?? NaN), color: "#38bdf8" }], { fmtL: x => x.toFixed(1), fmtR: x => x.toFixed(0) })}
    </div>
    <div class="panel"><h2>Stock by stock</h2>
      <p class="cardnote">Short ratio = short volume ÷ off-exchange volume. Compared with each stock's own last ${D.z_win} days: past ±${D.z_hot}σ is unusual. High = <span class="pos">buying</span> (market makers sell short to fill buyers), low = <span class="neg">selling</span>, on the popular reading the backtest checks.</p>
      <div class="toolbar"><div class="seg" id="sigsel">${[["all", `All ${D.stocks.length}`], ["buying", `Buying ${nb}`], ["selling", `Selling ${ns}`]].map(([k, l]) => `<button type="button" data-k="${k}" aria-pressed="${st.sig === k}">${l}</button>`).join("")}</div></div>
      ${list.length ? `<div class="tbl"><table><thead><tr><th>Stock</th><th>Short ratio</th><th class="hm">Usual</th><th>vs usual</th><th class="hm">Off-exch. share</th><th class="hm">Volume</th><th class="hm2">Last 60 days</th></tr></thead><tbody>
      ${list.map(s => `<tr><td class="tk"><a href="${stockLink(s.t)}"><b>${esc(s.t)}</b></a><small>${esc(s.n)}</small></td><td>${pct(s.ratio)}${s.sig ? ` <span class="sig ${s.sig}">${s.sig === "buying" ? "Buying" : "Selling"}</span>` : ""}</td><td class="hm dim">${pct(s.avg)}</td><td class="${s.sig ? cls(s.z) : ""}">${sgn(s.z)}σ${zdot(s.z)}</td><td class="hm">${pct(s.offex, 0)}</td><td class="hm">${num(s.vol)}${s.volx ? ` <span class="dim">${s.volx.toFixed(1)}×</span>` : ""}</td><td class="hm2">${spark(s.s)}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty">No stocks with that signal today.</div>`}
      <p class="note">Off-exch. share = off-exchange volume ÷ all volume that day. Volume = off-exchange shares, with × vs usual.</p>
    </div>`;
  el.querySelectorAll("#sigsel button").forEach(b => b.onclick = () => { st.sig = b.dataset.k; renderPressure(); });
}

// ------------------------------------------------------------------ weekly venues
function renderVenues() {
  const el = $("#v-venues");
  if (!W || !W.weeks?.length) { el.innerHTML = waiting(W?.error ? `FINRA's ATS data couldn't be read yet (${esc(W.error)}). It's retried every evening.` : "FINRA's weekly dark pool data is read every evening."); return; }
  const top = W.venues[0]?.pct || 1;
  const wkEnd = d => { const x = new Date(d + "T12:00:00Z"); x.setUTCDate(x.getUTCDate() + 4); return x.toISOString().slice(0, 10); };
  el.innerHTML = `
    <div class="panel"><h2>Week of ${fmtDate(W.week)}</h2>
      <p class="lead">The latest week FINRA has published (about two weeks late for these stocks): shares traded inside each named dark pool, ${fmtDate(W.week)} to ${fmtDate(wkEnd(W.week))}.</p>
      <dl class="stats"><div><dt>Dark pool shares, ${UL}</dt><dd>${num(W.total.at(-1))}</dd></div><div><dt>vs previous week</dt><dd class="${cls(W.total.at(-1) - W.total.at(-2))}">${W.total.length > 1 ? sgn(100 * (W.total.at(-1) / W.total.at(-2) - 1), 1) + "%" : "–"}</dd></div><div><dt>Weeks shown</dt><dd>${W.weeks.length}</dd></div></dl>
      <h4>Biggest dark pools</h4>
      ${W.venues.map(v => `<div class="vbar"><span title="${esc(v.mpid)}">${esc(v.name)}</span><span class="tr"><i style="width:${100 * v.pct / top}%"></i></span><b>${pct(v.pct)}</b></div>`).join("")}
    </div>
    <div class="panel"><h2>Stock by stock</h2>
      <p class="cardnote">Dark pool share = shares traded in dark pools ÷ all shares traded that week. A jump vs its usual level means more of the stock changed hands out of sight.</p>
      <div class="tbl"><table><thead><tr><th>Stock</th><th>Dark pool shares</th><th>Share of volume</th><th class="hm">Usual</th><th class="hm">Top dark pools</th><th class="hm2">${W.weeks.length} weeks</th></tr></thead><tbody>
      ${W.stocks.map(s => { const d = s.pct != null && s.avg ? s.pct / s.avg - 1 : null; return `<tr><td class="tk"><a href="${stockLink(s.t)}"><b>${esc(s.t)}</b></a><small>${esc(s.n)}</small></td><td>${num(s.sh)}</td><td>${pct(s.pct)}${d != null && Math.abs(d) >= .25 ? ` <span class="sig ${d > 0 ? "buying" : "selling"}">${d > 0 ? "+" : ""}${Math.round(100 * d)}%</span>` : ""}</td><td class="hm dim">${pct(s.avg)}</td><td class="hm dim" style="white-space:normal;min-width:220px">${s.top.slice(0, 3).map(([n, , p]) => `${esc(n)} ${p}%`).join(" · ")}</td><td class="hm2">${spark(s.trend)}</td></tr>`; }).join("")}
      </tbody></table></div>
    </div>`;
}

// ------------------------------------------------------------------ backtest
const NAMES = { dark_buys: "Dark buying stocks", dark_index_spy: "SPY with dark index filter", spy: "Buy & hold SPY", equal_weight: `All ${UL.replace("&amp;", "&")}, equal weight` };
function renderBacktest() {
  const el = $("#v-backtest");
  if (!T) { el.innerHTML = waiting("The backtest runs after the FINRA history has been downloaded (the first evening), then every Friday."); return; }
  const ev = T.events.filter(e => e.period === st.bt);
  const res = T.results[st.bt] || {};
  const per = { all: `${fmtDate(T.start)} – ${fmtDate(T.end)}`, in: `${fmtDate(T.start)} – ${fmtDate(T.split)}`, out: `${fmtDate(T.split)} – ${fmtDate(T.end)}` }[st.bt];
  const C = T.curves;
  el.innerHTML = `
    <div class="panel"><h2>Does it predict anything?</h2>
      <p class="lead">${esc(T.verdict)} Settings fixed in advance (±${T.z_hot}σ vs the last ${T.z_win} days), so the later period is a fair out-of-sample test.</p>
      <div class="seg" id="btsel">${[["out", "Out of sample"], ["in", "In sample"], ["all", "All years"]].map(([k, l]) => `<button type="button" data-k="${k}" aria-pressed="${st.bt === k}">${l}</button>`).join("")}</div>
      <p class="note">${per}</p>
      <h4>After a signal: buy at the next open, sell at the close a few days later</h4>
      <div class="tbl"><table><thead><tr><th>Signal</th><th>Hold</th><th>Times</th><th>Average</th><th class="hm">Won</th><th>Any day</th><th>Edge</th></tr></thead><tbody>
      ${ev.map(e => `<tr><td><span class="sig ${e.sig}">${e.sig === "buying" ? "Dark buying" : "Dark selling"}</span></td><td>${e.h} days</td><td>${e.n.toLocaleString("en-GB")}</td><td class="${cls(e.avg)}">${sgn(e.avg)}%</td><td class="hm">${pct(e.win, 0)}</td><td class="dim">${sgn(e.base)}%</td><td class="${cls(e.edge)}"><b>${sgn(e.edge)}%</b></td></tr>`).join("")}
      </tbody></table></div>
      <p class="note">Edge = the signal's average minus the average of the same stocks on every day. For “dark selling” a negative edge would mean the signal works (the stock did worse).</p>
    </div>
    <div class="panel"><h2>As strategies, $100,000</h2>
      <p class="cardnote"><b>Dark buying stocks</b>: hold, in equal parts, every stock that had a dark buying signal in the last 10 days, cash otherwise. <b>Dark index filter</b>: hold SPY when the 5-day dark index is above its 1-year median, cash otherwise. 0.10% per trade, no tax, cash earns nothing. Uses today's S&amp;P 100 members for every year, which flatters all the stock lines a little (yesterday's losers that left the index are missing).</p>
      ${lines(C.dates, Object.keys(NAMES).filter(k => C[k]).map((k, i) => ({ name: NAMES[k], v: C[k], color: ["#38bdf8", "#f59e0b", "var(--faint)", "#a78bfa"][i], dash: k === "spy" })), { fmtL: x => "$" + Math.round(x / 1000) + "K" })}
      <div class="tbl"><table><thead><tr><th>${st.bt === "all" ? "All years" : st.bt === "in" ? "In sample" : "Out of sample"}</th><th>Yearly</th><th>Total</th><th>Worst fall</th><th class="hm">Sharpe</th></tr></thead><tbody>
      ${Object.keys(NAMES).filter(k => res[k]).map(k => `<tr class="${k === "spy" ? "bench" : ""}"><td>${NAMES[k]}</td><td class="${cls(res[k].cagr)}">${sgn(res[k].cagr, 1)}%</td><td>${sgn(res[k].total, 1)}%</td><td class="neg">${res[k].dd}%</td><td class="hm">${res[k].sharpe ?? "–"}</td></tr>`).join("")}
      </tbody></table></div>
      <p class="note">Updated ${fmtDate(T.generated_at.slice(0, 10))}. Past results don't predict future ones.</p>
    </div>`;
  el.querySelectorAll("#btsel button").forEach(b => b.onclick = () => { st.bt = b.dataset.k; renderBacktest(); });
}

// ------------------------------------------------------------------ learn
function renderLearn() {
  $("#v-learn").innerHTML = `
    <div class="panel"><h2>What a dark pool is</h2>
      <p class="lead">A private trading venue, usually run by a bank or broker, where orders aren't shown to anyone before they trade.</p>
      <p>A fund selling 2 million shares on a normal exchange would show its hand, and other traders would push the price down before it finished. In a dark pool the order waits unseen and gets matched, usually at the midpoint of the exchange's bid and ask. The trade is still reported, so it appears on the public tape seconds later, but nobody saw it coming.</p>
      <div class="steps">
        <div><span class="n">1</span><b>Block prints</b><p>Every off-exchange trade is reported to a FINRA facility (code “D” in the trade feed). We read them 15+ minutes late and keep the $1M+ ones.</p></div>
        <div><span class="n">2</span><b>Daily pressure</b><p>FINRA publishes, every evening, how much of each stock's off-exchange volume was short sales. Unusual levels are the signal.</p></div>
        <div><span class="n">3</span><b>Dark pools</b><p>FINRA's weekly data names the dark pools themselves, UBS, Goldman, JPMorgan and others, and how much of each stock they traded.</p></div>
      </div>
    </div>
    <div class="panel"><h2>How to read it</h2>
      <h4>Above or below the lit price</h4><p>A block printed above the last exchange trade suggests a buyer who paid up; below, a seller who gave way. Most dark pool trades print at the midpoint, so many show “At”, and the lean is only a hint.</p>
      <h4>Why short volume can mean buying</h4><p>When someone buys off-exchange, the market maker on the other side often doesn't own the shares and sells them short, then covers later. So a lot of short volume off-exchange can mean a lot of buyers. That's the idea behind SqueezeMetrics' DIX index, which our dark index copies for the S&amp;P 100. It's an interpretation, not a fact: the Backtest tab checks whether it has worked.</p>
      <h4>Things the data can't tell you</h4>
      <ul class="find">
        <li>Who traded, and whether a block was a buy or a sell: the tape has no side.</li>
        <li>Off-exchange isn't all dark pools: a large share is retail orders filled by wholesalers such as Citadel Securities and Virtu. Big blocks are more likely institutional, small prints mostly retail.</li>
        <li>One fund's block can be one half of a trade it's hedging elsewhere, or a client trade the bank is offsetting.</li>
        <li>Short volume isn't short interest: most of it is covered the same day and says nothing about bets against the stock.</li>
      </ul>
    </div>`;
}

// ------------------------------------------------------------------ boot
async function getJSON(u) {
  try { const r = await fetch(u, { cache: "no-cache" }); return r.ok ? r.json() : null; } catch { return null; }
}

async function boot() {
  [B, D, W, T] = await Promise.all(["blocks", "daily", "weekly", "backtest"].map(k => getJSON(UD(`darkpool/${k}.json`))));
  renderPrints(); renderPressure(); renderVenues(); renderBacktest(); renderLearn();
  document.querySelectorAll("#tabs button").forEach(b => b.addEventListener("click", () => show(b.dataset.v)));
  addEventListener("hashchange", () => show(location.hash.slice(1)));
  show(location.hash.slice(1));
  let lastW = innerWidth;
  addEventListener("resize", () => { if (Math.abs(innerWidth - lastW) < 60) return; lastW = innerWidth; renderPressure(); renderBacktest(); });
}

boot();

// the S&P 100 / S&P 500 switch in the page's header (picking one reloads with that list)
if (typeof document !== "undefined") { const el = document.getElementById("uniSw"); if (el && globalThis.ICS) el.innerHTML = ICS.uniSwitch(); }
