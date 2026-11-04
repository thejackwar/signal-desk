// Implied Correlation page (docs/correlation.html). Data: docs/data/corr/latest.json (engine/implied_corr.py).

const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const p0 = x => (x == null || !isFinite(x)) ? "–" : `${(x * 100).toFixed(0)}%`;
const p1 = x => (x == null || !isFinite(x)) ? "–" : `${(x * 100).toFixed(1)}%`;
const sp = x => (x == null || !isFinite(x)) ? "–" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x * 100).toFixed(0)} pts`;
const fmtDate = d => new Date(d + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const TENOR_NAME = { 30: "1 month", 60: "2 months", 91: "3 months", 182: "6 months" };
const TENOR_SHORT = { 30: "1M", 60: "2M", 91: "3M", 182: "6M" };
const TCOL = ["var(--t1)", "var(--t2)", "var(--t3)", "var(--t4)"];   // fixed order: 1M, 2M, 3M, 6M
const DSHORT = ["10Δ put", "25Δ put", "ATM", "25Δ call", "10Δ call"];

let D = null;

// rough reading bands for the S&P 500's average correlation
function level(r) {
  if (r == null) return ["no reading", ""];
  if (r < 0.2) return ["low", "stocks are expected to move mostly on their own news: a stock-picker's market"];
  if (r < 0.35) return ["normal", "a usual mix of market-wide and stock-specific moves"];
  if (r < 0.55) return ["high", "the market expects stocks to move together more than usual: macro is in charge"];
  return ["very high", "stress-level: the market expects stocks to fall or rise as one block"];
}

// ------------------------------------------------------------------ charts
// A small SVG line chart. series: [{name, color, dash, pts: [[x, y]], dots}], x numeric or date strings.
function lineChart(el, series, { xs = null, xLabels = null, yMin = null, yMax = null, yFmt = p0, dates = false, hoverFmt = null, H = 300, W: Wmax = 760 } = {}) {
  const W = Math.max(340, Math.min(Wmax, el.clientWidth || Wmax));
  const L = 46, R = 14, T = 14, B = 34;
  const all = series.flatMap(s => s.pts.filter(p => p[1] != null));
  if (!all.length) { el.innerHTML = `<p class="note">No data yet.</p>`; return; }
  const xv = p => dates ? new Date(p[0] + "T12:00:00").getTime() : p[0];
  const xsAll = xs || all.map(xv);
  const x0 = Math.min(...xsAll), x1 = Math.max(...xsAll);
  let y0 = yMin ?? Math.min(...all.map(p => p[1])), y1 = yMax ?? Math.max(...all.map(p => p[1]));
  const span = Math.max(y1 - y0, 0.04);
  const step = [0.01, 0.02, 0.05, 0.1, 0.2, 0.25].find(s => span / s <= 5) || 0.5;
  if (yMin == null) y0 = Math.floor((y0 - span * 0.1) / step) * step;
  if (yMax == null) y1 = Math.ceil((y1 + span * 0.1) / step) * step;
  if (y1 <= y0) y1 = y0 + step;
  const X = v => L + (x1 === x0 ? (W - L - R) / 2 : (v - x0) / (x1 - x0) * (W - L - R));
  const Y = v => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
  const g = [];
  const nT = Math.round((y1 - y0) / step);
  for (let k = 0; k <= nT; k++) {
    const v = y0 + k * step;
    g.push(`<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--c-grid)"/><text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end" font-size="12" fill="var(--c-txt)">${yFmt(v)}</text>`);
  }
  if (xLabels) for (const [v, t] of xLabels) g.push(`<text x="${X(v)}" y="${H - 10}" text-anchor="${v === x0 ? "start" : v === x1 ? "end" : "middle"}" font-size="12" fill="var(--c-txt)">${esc(t)}</text>`);
  else if (dates) {
    const n = x1 === x0 ? 0 : W < 520 ? 3 : 5;
    for (let k = 0; k <= n; k++) {
      const v = n ? x0 + (x1 - x0) * k / n : x0;
      const t = new Date(v).toLocaleDateString("en-GB", { month: "short", year: "2-digit" });
      g.push(`<text x="${X(v)}" y="${H - 10}" text-anchor="${n === 0 ? "middle" : k === 0 ? "start" : k === n ? "end" : "middle"}" font-size="12" fill="var(--c-txt)">${t}</text>`);
    }
  }
  const paths = [], marks = [];
  for (const s of series) {
    const pts = s.pts.filter(p => p[1] != null);
    if (!pts.length) continue;
    if (pts.length > 1 && !s.dotsOnly) {
      const d = pts.map((p, i) => `${i ? "L" : "M"}${X(xv(p)).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
      paths.push(`<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"${s.dash ? ' stroke-dasharray="5 4"' : ""}/>`);
    }
    if (s.dots || pts.length === 1 || s.dotsOnly)
      for (const p of pts) marks.push(`<circle cx="${X(xv(p))}" cy="${Y(p[1])}" r="4.5" fill="${s.color}" stroke="var(--card)" stroke-width="2"/>`);
  }
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img">${g.join("")}${paths.join("")}${marks.join("")}<line class="cur" y1="${T}" y2="${H - B}" stroke="var(--hair)" visibility="hidden"/></svg><div class="tip" hidden></div>`;
  // hover: nearest x across series
  const svg = el.querySelector("svg"), tip = el.querySelector(".tip"), cur = el.querySelector(".cur");
  const xsU = [...new Set(series.flatMap(s => s.pts.filter(p => p[1] != null).map(xv)))].sort((a, b) => a - b);
  const move = ev => {
    const r = svg.getBoundingClientRect();
    const px = (ev.clientX - r.left) / r.width * W;
    if (px < L || px > W - R) { tip.hidden = true; cur.setAttribute("visibility", "hidden"); return; }
    const v = x0 + (px - L) / (W - L - R) * (x1 - x0);
    const near = xsU.reduce((a, b) => Math.abs(b - v) < Math.abs(a - v) ? b : a);
    const parts = series.map(s => { const p = s.pts.find(q => q[1] != null && xv(q) === near); return p ? `<span style="color:${s.color}">●</span> ${esc(s.name)} ${yFmt(p[1])}` : null; }).filter(Boolean);
    if (!parts.length) return;
    const head = hoverFmt ? hoverFmt(near) : dates ? new Date(near).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : near;
    tip.innerHTML = `${esc(head)} · ${parts.join(" · ")}`;
    tip.hidden = false;
    cur.setAttribute("x1", X(near)); cur.setAttribute("x2", X(near)); cur.setAttribute("visibility", "visible");
  };
  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerleave", () => { tip.hidden = true; cur.setAttribute("visibility", "hidden"); });
}

const legend = items => `<div class="legend">${items.map(([n, c, dash]) => `<span><i${dash ? ' class="dash"' : ""} style="border-color:${c}"></i>${esc(n)}</span>`).join("")}</div>`;

// heatmap colour: one hue, stronger = higher correlation, fixed scale 0–80% so days compare
function heatBg(v) {
  const f = Math.max(0, Math.min(1, v / 0.8));
  return `color-mix(in srgb, var(--heat) ${Math.round(8 + f * 82)}%, var(--card))`;
}
const heatFg = v => (v / 0.8 > 0.55 ? "#04121a" : "var(--fg)");

// ------------------------------------------------------------------ views
function viewToday() {
  const h = D.head, ATM = 2;
  const [lv, lvTxt] = level(h.rho_1m);
  const prem = h.premium_1m, skew = h.skew_1m;
  const premTxt = prem == null ? "" : prem > 0.03
    ? `Options price in <b>more</b> co-movement than stocks have actually shown over the last month (${sp(prem)}): the usual premium for index protection.`
    : prem < -0.03
      ? `Stocks have actually been moving together <b>more</b> than options now price in (${sp(prem)}): unusual, often after a shock that the market expects to fade.`
      : `Options price in about the same co-movement stocks have shown over the last month.`;
  const skewTxt = skew == null ? "" : skew > 0.02
    ? `Correlation is ${sp(skew)} higher on the downside (25Δ puts vs 25Δ calls): the market expects stocks to fall together more than they'd rise together.`
    : `Little difference between downside and upside correlation (${sp(skew)}).`;

  const rows = D.tenors.map((t, i) => `<tr><th class="rh" scope="row">${TENOR_NAME[t]}</th>${D.surface[i].map((v, j) =>
    v == null ? `<td class="empty" data-i="${i}" data-j="${j}">–</td>`
      : `<td data-i="${i}" data-j="${j}" style="background:${heatBg(v)};color:${heatFg(v)}" tabindex="0">${p0(v)}</td>`).join("")}</tr>`).join("");

  $("#v-today").innerHTML = `
  <div class="kpis">
    <div class="kpi"><dt>1-month, at the money</dt><dd>${p0(h.rho_1m)}</dd><small>${lv}</small></div>
    <div class="kpi"><dt>3-month, at the money</dt><dd>${p0(h.rho_3m)}</dd><small>Cboe's COR3M measures this</small></div>
    <div class="kpi"><dt>Realized, last month</dt><dd>${p0(h.realized_1m)}</dd><small>what stocks actually did</small></div>
    <div class="kpi"><dt>Implied minus realized</dt><dd class="${prem > 0 ? "" : "neg"}">${sp(prem)}</dd><small>1 month</small></div>
    <div class="kpi"><dt>Downside skew</dt><dd>${sp(skew)}</dd><small>25Δ put minus 25Δ call, 1 month</small></div>
  </div>

  <div class="panel">
    <h2>What it says today</h2>
    <p class="read">The options market prices an average correlation of <b>${p0(h.rho_1m)}</b> between the 50 biggest S&amp;P 500 stocks over the next month. That's <b>${lv}</b>: ${lvTxt}.</p>
    <p class="note" style="font-size:15px;color:var(--fg)">${premTxt} ${skewTxt}</p>
    <p class="note">SPY 1-month implied vol ${p1(h.spy_iv_1m)} vs ${p1(h.basket_iv_1m)} average for the 50 stocks. The index is calmer than its stocks because they don't all move together; the gap is what the correlation measures. Data as of ${esc(D.asof)}.</p>
  </div>

  <div class="panel">
    <h2>The surface</h2>
    <p class="lead">Average implied correlation by maturity (rows) and strike (columns, from the downside on the left to the upside on the right). Tap a cell.</p>
    <div class="tbl" style="margin:0"><table class="heat">
      <colgroup><col class="rh">${D.tenors.length ? D.delta_labels.map(() => "<col>").join("") : ""}</colgroup><thead><tr><th></th>${D.delta_labels.map((l, j) => `<th scope="col">${esc(window.innerWidth < 560 ? DSHORT[j] : l)}</th>`).join("")}</tr></thead>
      <tbody>${rows}</tbody></table></div>
    <div class="scale"><span>0%</span><i></i><span>80%+</span></div>
    <div class="cell" id="cell">Each cell compares SPY options and the stocks' own options at the same delta and maturity.</div>
  </div>

  <div class="split">
    <div class="panel">
      <h2 style="font-size:22px">Skew</h2>
      <p class="lead" style="font-size:15px">Correlation across strikes, one line per maturity.</p>
      <div class="chartbox" id="ch-skew"></div>
      ${legend(D.tenors.map((t, i) => [TENOR_NAME[t], TCOL[i]]))}
    </div>
    <div class="panel">
      <h2 style="font-size:22px">Term structure</h2>
      <p class="lead" style="font-size:15px">Correlation across maturities.</p>
      <div class="chartbox" id="ch-term"></div>
      ${legend([["At the money", "var(--t1)"], ["25Δ put", "var(--t2)"], ["25Δ call", "var(--t3)"]])}
    </div>
  </div>`;

  // cell details
  const cellBox = $("#cell");
  const pick = td => {
    const i = +td.dataset.i, j = +td.dataset.j;
    document.querySelectorAll(".heat td.on").forEach(x => x.classList.remove("on"));
    td.classList.add("on");
    const v = D.surface[i][j], cov = D.coverage[i][j];
    if (v == null) { cellBox.innerHTML = `<b>${TENOR_NAME[D.tenors[i]]}, ${esc(D.delta_labels[j])}</b>: not enough quotes (${p0(cov)} of the basket weight had one; 80% needed).`; return; }
    cellBox.innerHTML = `<b>${TENOR_NAME[D.tenors[i]]}, ${esc(D.delta_labels[j])}: ${p0(v)}</b> · SPY implied vol ${p1(D.index_iv[i][j])}, the 50 stocks' weighted average ${p1(D.basket_iv[i][j])} · ${p0(cov)} of the basket weight quoted.`;
  };
  document.querySelectorAll(".heat td:not(.empty)").forEach(td => {
    td.addEventListener("click", () => pick(td));
    td.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(td); } });
  });

  // skew chart: x = delta position 0..4
  lineChart($("#ch-skew"), D.tenors.map((t, i) => ({ name: TENOR_SHORT[t], color: TCOL[i], dots: true, pts: D.surface[i].map((v, j) => [j, v]) })),
    { xLabels: DSHORT.map((l, j) => [j, l]), hoverFmt: j => DSHORT[j], H: 300, W: 480 });
  // term chart: x = days
  const col = j => D.tenors.map((t, i) => [t, D.surface[i][j]]);
  lineChart($("#ch-term"), [
    { name: "ATM", color: "var(--t1)", dots: true, pts: col(ATM) },
    { name: "25Δ put", color: "var(--t2)", dots: true, pts: col(1) },
    { name: "25Δ call", color: "var(--t3)", dots: true, pts: col(3) },
  ], { xLabels: D.tenors.map(t => [t, TENOR_SHORT[t]]), hoverFmt: t => TENOR_NAME[t], H: 300, W: 480 });
}

function viewHistory() {
  const hist = D.history || [];
  const imp = hist.filter(r => r.rho_1m != null).map(r => [r.date, r.rho_1m]);
  const real = D.realized_series || [];
  const cboe = (D.cboe || {}).COR1M || [];
  const start = real.length ? real[0][0] : null;
  const cb = start ? cboe.filter(p => p[0] >= start) : cboe;
  const series = [{ name: "Realized, 1 month", color: "var(--faint)", pts: real }];
  if (cb.length) series.push({ name: "Cboe COR1M", color: "var(--t2)", dash: true, pts: cb });
  series.push({ name: "Implied, 1 month (ours)", color: "var(--t1)", dots: imp.length < 40, pts: imp });
  const leg = [["Realized, 1 month", "var(--faint)"]];
  if (cb.length) leg.push(["Cboe COR1M (official index)", "var(--t2)", true]);
  leg.push(["Implied, 1 month (ours)", "var(--t1)"]);

  const skewRows = hist.filter(r => r.rho_1m_p25 != null && r.rho_1m_c25 != null);
  $("#v-history").innerHTML = `
  <div class="panel">
    <h2>Implied vs realized</h2>
    <p class="lead">What options priced in vs what the 50 stocks actually did, over the past year.</p>
    <div class="chartbox" id="ch-hist"></div>
    ${legend(leg)}
    <p class="note">Free history of implied vols doesn't exist, so our implied line starts on ${imp.length ? fmtDate(imp[0][0]) : "the first update"} and grows by one point every weekday. ${cb.length ? "Cboe's index fills in the past: it uses SPX options and the 50 biggest S&amp;P 500 stocks, so it runs close to ours but not identical." : ""} The realized line uses the same formula on daily returns over a rolling month.</p>
  </div>
  <div class="panel">
    <h2 style="font-size:22px">Downside skew over time</h2>
    <p class="lead" style="font-size:15px">1-month correlation at 25Δ puts minus 25Δ calls. Rising = the market increasingly expects stocks to fall together.</p>
    <div class="chartbox" id="ch-skewh"></div>
  </div>
  <div class="panel">
    <h2 style="font-size:22px">Every update</h2>
    <div class="tbl"><table><thead><tr><th>Date</th><th>1M</th><th>3M</th><th class="hm">6M</th><th class="hm">1M 25Δ put</th><th class="hm">1M 25Δ call</th><th>Realized 1M</th><th class="hm">SPY IV 1M</th></tr></thead>
    <tbody>${hist.slice().reverse().slice(0, 60).map(r => `<tr><td>${fmtDate(r.date)}</td><td>${p0(r.rho_1m)}</td><td>${p0(r.rho_3m)}</td><td class="hm">${p0(r.rho_6m)}</td><td class="hm">${p0(r.rho_1m_p25)}</td><td class="hm">${p0(r.rho_1m_c25)}</td><td>${p0(r.realized_1m)}</td><td class="hm">${p1(r.spy_iv_1m)}</td></tr>`).join("")}</tbody></table></div>
    <p class="note"><a href="data/corr/history.csv" download>Download the full history (CSV)</a></p>
  </div>`;
  lineChart($("#ch-hist"), series, { dates: true, yMin: 0 });
  lineChart($("#ch-skewh"), [{ name: "Downside skew", color: "var(--t1)", dots: skewRows.length < 40, pts: skewRows.map(r => [r.date, r.rho_1m_p25 - r.rho_1m_c25]) }],
    { dates: true, yFmt: v => `${(v * 100).toFixed(0)} pts`, H: 220 });
}

function viewBasket() {
  const st = D.stocks.slice().sort((a, b) => b.w - a.w);
  $("#v-basket").innerHTML = `
  <div class="panel">
    <h2>The 50 stocks</h2>
    <p class="lead">The basket behind the numbers: the 50 biggest S&amp;P 100 stocks by market cap, with weights rescaled to 100%, and their 1-month implied vols.</p>
    <div class="tbl"><table><thead><tr><th>Stock</th><th>Weight</th><th>IV, at the money</th><th>IV, 25Δ put</th><th class="hm">Put premium</th></tr></thead>
    <tbody>${st.map(s => `<tr><td><b>${esc(s.t)}</b></td><td>${p1(s.w)}</td><td>${p1(s.iv)}</td><td>${p1(s.put25)}</td><td class="hm">${s.iv && s.put25 ? sp(s.put25 - s.iv) : "–"}</td></tr>`).join("")}</tbody></table></div>
    <p class="note">The biggest names matter most: a few mega-caps carry a large share of the weight, so their own vols move the result a lot. A stock with no usable quote is left out of that day's calculation and the weights rescaled.</p>
  </div>`;
}

function viewLearn() {
  $("#v-learn").innerHTML = `
  <div class="panel">
    <h2>What is implied correlation?</h2>
    <p class="lead">An index is a basket. How much the basket swings depends on how much each stock swings and on how much they move together.</p>
    <p>If every stock moved in lockstep, the index would be as volatile as its stocks. If they moved independently, their moves would cancel out and the index would be far calmer. Options on the index and options on each stock each tell you how volatile the market expects them to be. Put them side by side and you can back out the one number that makes them consistent: the <b>average correlation</b> the options market is pricing in.</p>
    <div class="formula">ρ = (σ<sub>index</sub>² − Σ wᵢ²σᵢ²) ÷ ((Σ wᵢσᵢ)² − Σ wᵢ²σᵢ²)</div>
    <p class="note">σ<sub>index</sub>: SPY's implied vol. σᵢ: each stock's implied vol. wᵢ: its weight in the basket. The bottom is the index's variance if correlation were 100% minus what it would be at 0%; the top is how far above 0% the index really sits.</p>
  </div>
  <div class="panel">
    <h2>Why a surface?</h2>
    <div class="steps">
      <div><span class="n">1</span><b>Strikes (skew)</b><p>Using puts far below the price gives a different answer from calls above it. Correlation is almost always higher on the downside: in a sell-off everything falls together, so index puts are priced richer than the stocks' own puts would suggest.</p></div>
      <div><span class="n">2</span><b>Maturities (term)</b><p>Short-dated correlation jumps in a panic and drops fast after. Longer maturities move slowly. When 1-month sits above 6-month, the market sees the stress as temporary.</p></div>
      <div><span class="n">3</span><b>Same delta</b><p>Each cell compares the index and the stocks at the same delta (how far out of the money, scaled by each one's own volatility), the usual way to build a correlation skew.</p></div>
    </div>
  </div>
  <div class="panel">
    <h2>How traders use it</h2>
    <ol class="find">
      <li><b>Risk gauge.</b> Rising implied correlation means the market is thinking about the macro picture (rates, recession, a shock), not individual companies.</li>
      <li><b>Stock-picking climate.</b> Low correlation = stocks move on their own news, so picking stocks and pairs trading have more room to work.</li>
      <li><b>Dispersion trades.</b> Selling index options and buying options on the stocks is a bet that correlation turns out lower than priced. Implied usually sits above realized (the "correlation premium"), which is what that trade collects. It loses badly when everything suddenly moves together.</li>
      <li><b>Hedging cost.</b> A steep downside skew means index puts are expensive relative to single-stock puts.</li>
    </ol>
  </div>
  <div class="panel">
    <h2>Limits</h2>
    <ul class="find">
      <li>It's an <b>average</b>: one number stands for every pair of stocks. Some pairs are far more correlated than others.</li>
      <li>We use SPY (the ETF) for the index and the 50 biggest S&amp;P 100 stocks; Cboe uses SPX index options and the 50 biggest S&amp;P 500 stocks. Close, not identical.</li>
      <li>The free option feed is indicative (not every exchange's quotes), so wing cells (10Δ) are noisier than at-the-money ones.</li>
      <li>Correlation above 100% or below 0% can show up when quotes are thin: it means the inputs don't fit together, not that the market expects it.</li>
    </ul>
  </div>`;
}

// ------------------------------------------------------------------ boot
const VIEWS = { today: viewToday, history: viewHistory, basket: viewBasket, learn: viewLearn };
const done = new Set();
function show(v) {
  if (!VIEWS[v]) v = "today";
  for (const k of Object.keys(VIEWS)) $(`#v-${k}`).hidden = k !== v;
  for (const b of document.querySelectorAll("#tabs button")) b.setAttribute("aria-pressed", String(b.dataset.v === v));
  if (location.hash !== `#${v}`) history.replaceState(null, "", `#${v}`);
  if (!D && v !== "learn") return;
  if (!done.has(v)) { VIEWS[v](); done.add(v); }
}

async function boot() {
  document.querySelectorAll("#tabs button").forEach(b => b.addEventListener("click", () => show(b.dataset.v)));
  addEventListener("hashchange", () => show(location.hash.slice(1)));
  try {
    const r = await fetch("data/corr/latest.json", { cache: "no-cache" });
    D = r.ok ? await r.json() : null;
  } catch { D = null; }
  if (!D) {
    for (const k of ["today", "history", "basket"]) $(`#v-${k}`).innerHTML = `<p class="msg small"><strong>No data yet</strong>The first update runs on the next weekday near the US close.</p>`;
    done.add("today"); done.add("history"); done.add("basket");
  } else if (D.demo) $("#demo").hidden = false;
  show(location.hash.slice(1));
}
boot();
