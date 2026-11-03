// Pairs Trading page (docs/pairs.html). Data: docs/data/pairs/ (engine/pairs.py).
// simulate() and zscore() mirror engine/pairs.py; tests/pairs/test_parity.py checks they agree.

const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pct = (x, d = 1) => (x == null || !isFinite(x)) ? "–" : `${x > 0 ? "+" : ""}${(x * 100).toFixed(d)}%`;
const eur = x => `${x < 0 ? "−" : ""}€${Math.abs(x).toLocaleString("en-GB", { maximumFractionDigits: 0 })}`;
const eur2 = x => `${x < 0 ? "−" : x > 0 ? "+" : ""}€${Math.abs(x).toFixed(2)}`;
const cls = x => x > 0 ? "pos" : x < 0 ? "neg" : "";
const fmtDate = d => new Date(d + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

const SIGNAL = {
  long_a: (a, b) => `Buy ${a}, short ${b}`,
  short_a: (a, b) => `Short ${a}, buy ${b}`,
  near: () => "Getting close",
  stretched: () => "Too stretched",
  none: () => "No trade",
};

// ------------------------------------------------------------------ maths (mirrors engine/pairs.py)
export function zscore(spread, w) {
  const out = new Array(spread.length).fill(NaN);
  for (let i = w - 1; i < spread.length; i++) {
    let m = 0;
    for (let k = i - w + 1; k <= i; k++) m += spread[k];
    m /= w;
    let v = 0;
    for (let k = i - w + 1; k <= i; k++) v += (spread[k] - m) ** 2;
    const sd = Math.sqrt(v / (w - 1));       // sample std, like pandas
    out[i] = sd > 0 ? (spread[i] - m) / sd : NaN;
  }
  return out;
}

export function simulate(z, ra, rb, beta, rules, dates, forceCloseAtEnd = true) {
  const wa = 1 / (1 + Math.abs(beta)), wb = Math.abs(beta) / (1 + Math.abs(beta));
  const cost = rules.cost_bps / 1e4;
  const n = z.length, pnl = new Array(n).fill(0), trades = [];
  let state = 0, held = 0, entry = -1, acc = 0;
  for (let i = 1; i < n; i++) {
    if (state) {
      const day = state * (wa * ra[i] - wb * rb[i]);
      pnl[i] += day;
      acc = (1 + acc) * (1 + day) - 1;
      held++;
    }
    const zp = z[i - 1], last = i === n - 1;
    if (state) {
      let why = null;
      if (Math.abs(z[i]) < rules.exit_z) why = "back to normal";
      else if (Math.abs(z[i]) > rules.stop_z) why = "stop";
      else if (held >= rules.max_days) why = "time";
      else if (last && forceCloseAtEnd) why = "period end";
      if (why) {
        pnl[i] -= 2 * cost; acc -= 2 * cost;
        trades.push({ open: dates[entry], close: dates[i], side: state > 0 ? 1 : -1, days: held, ret: acc, why });
        state = 0;
      }
    } else if (!isNaN(zp) && !last) {
      if (zp > rules.entry_z && zp < rules.stop_z) state = -1;
      else if (zp < -rules.entry_z && zp > -rules.stop_z) state = 1;
      if (state) { pnl[i] -= 2 * cost; held = 0; entry = i; acc = -2 * cost; }
    }
  }
  return { pnl, trades };
}

function stats(pnl) {
  let eq = 1, peak = 1, dd = 0;
  for (const r of pnl) { eq *= 1 + r; peak = Math.max(peak, eq); dd = Math.min(dd, eq / peak - 1); }
  const m = pnl.reduce((a, b) => a + b, 0) / pnl.length;
  const sd = Math.sqrt(pnl.reduce((a, b) => a + (b - m) ** 2, 0) / (pnl.length - 1));
  return { total: eq - 1, sharpe: sd > 0 ? m / sd * Math.sqrt(252) : 0, max_dd: dd };
}

// ------------------------------------------------------------------ chart (SVG, themed by CSS variables)
function lineChart(el, { x, series, refs = [], height = 220, fmt = v => v.toFixed(2), xfmt = d => d, zeroBase = false }) {
  const W = Math.max(300, Math.min(760, el.clientWidth || 760)), H = height, L = 46, R = 10, T = 12, B = 22;
  let lo = Infinity, hi = -Infinity;
  for (const s of series) for (const v of s.y) if (isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  for (const r of refs) { lo = Math.min(lo, r.y); hi = Math.max(hi, r.y); }
  if (zeroBase) lo = Math.min(lo, 0);
  const pad = (hi - lo) * 0.06 || 1; lo -= pad; hi += pad;
  const n = x.length;
  const px = i => L + (W - L - R) * i / Math.max(n - 1, 1);
  const py = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  const ticks = 4, grid = [];
  for (let k = 0; k <= ticks; k++) {
    const v = lo + (hi - lo) * k / ticks;
    grid.push(`<line x1="${L}" x2="${W - R}" y1="${py(v)}" y2="${py(v)}" stroke="var(--c-grid)"/><text x="${L - 6}" y="${py(v) + 4}" text-anchor="end" font-size="11" fill="var(--c-txt)">${esc(fmt(v))}</text>`);
  }
  const xl = [0, Math.floor(n / 2), n - 1].map(i => `<text x="${px(i)}" y="${H - 4}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}" font-size="11" fill="var(--c-txt)">${esc(xfmt(x[i]))}</text>`);
  const refl = refs.map(r => `<line x1="${L}" x2="${W - R}" y1="${py(r.y)}" y2="${py(r.y)}" stroke="${r.color}" stroke-width="1.2" ${r.dash ? 'stroke-dasharray="4 4"' : ""}/>`);
  const paths = series.map(s => {
    let d = "", pen = false;
    s.y.forEach((v, i) => { if (!isFinite(v)) { pen = false; return; } d += `${pen ? "L" : "M"}${px(i).toFixed(1)},${py(v).toFixed(1)}`; pen = true; });
    return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2}" ${s.dash ? 'stroke-dasharray="5 4"' : ""} stroke-linejoin="round"/>`;
  });
  const marks = series.flatMap(s => (s.marks || []).map(m => `<circle cx="${px(m.i)}" cy="${py(s.y[m.i])}" r="4" fill="${m.color}" stroke="var(--c-bg)" stroke-width="1.5"/>`));
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img">${grid.join("")}${refl.join("")}${paths.join("")}${marks.join("")}${xl.join("")}<line class="cur" y1="${T}" y2="${H - B}" stroke="var(--hair)" visibility="hidden"/></svg><div class="tip" hidden></div>`;
  const svg = el.querySelector("svg"), tip = el.querySelector(".tip"), cur = svg.querySelector(".cur");
  const move = e => {
    const r = svg.getBoundingClientRect();
    const i = Math.max(0, Math.min(n - 1, Math.round((((e.clientX - r.left) / r.width) * W - L) / (W - L - R) * (n - 1))));
    cur.setAttribute("x1", px(i)); cur.setAttribute("x2", px(i)); cur.setAttribute("visibility", "visible");
    tip.hidden = false;
    tip.innerHTML = `${esc(xfmt(x[i]))} · ` + series.filter(s => s.name).map(s => `<span style="color:${s.color}">${esc(s.name)} ${esc(isFinite(s.y[i]) ? fmt(s.y[i]) : "–")}</span>`).join(" · ");
  };
  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerleave", () => { tip.hidden = true; cur.setAttribute("visibility", "hidden"); });
}

// ------------------------------------------------------------------ state
let DATA = null, LIVE = null, GRID = null, sel = 0, rules = null;
const views = ["today", "learn", "backtest", "live"];

function show(v) {
  if (!views.includes(v)) v = "today";
  for (const k of views) $(`#v-${k}`).hidden = k !== v;
  // charts size to the visible width, so draw them once their tab is showing
  if (v === "backtest") renderBacktest();
  if (v === "today" && DATA.pairs.length) renderPair();
  for (const b of document.querySelectorAll("#tabs button")) b.setAttribute("aria-pressed", String(b.dataset.v === v));
  if (location.hash !== `#${v}`) history.replaceState(null, "", `#${v}`);
}

// ------------------------------------------------------------------ Today
function renderToday() {
  const el = $("#v-today");
  const P = DATA.pairs;
  if (!P.length) {
    el.innerHTML = `<div class="msg"><strong>No pairs today</strong>No same-sector pair passed the cointegration test over the last two years.</div>`;
    return;
  }
  const active = P.filter(p => p.signal === "long_a" || p.signal === "short_a").length;
  const rows = P.map((p, i) => {
    const pos = Math.max(0, Math.min(100, 50 + p.z / (DATA.rules.entry_z * 2) * 50));
    return `<tr class="click${i === sel ? " sel" : ""}" data-i="${i}">
      <td><b>${esc(p.a)}</b> / <b>${esc(p.b)}</b><br><span class="dim">${esc(p.sector)}</span></td>
      <td>${p.z > 0 ? "+" : ""}${p.z.toFixed(2)}<span class="zbar hm"><i class="${Math.abs(p.z) > DATA.rules.entry_z ? "hot" : ""}" style="left:${pos}%"></i></span></td>
      <td><span class="sig ${p.signal}">${esc(SIGNAL[p.signal](p.a, p.b))}</span></td>
      <td class="hm">${p.half_life} d</td><td class="hm">${p.beta.toFixed(2)}</td></tr>`;
  }).join("");
  el.innerHTML = `
    <h2 class="sec">Today's pairs <small>${active ? `${active} to trade` : "nothing to trade today"} · prices of ${fmtDate(DATA.last_day)}</small></h2>
    <div class="panel">
      <div class="tbl"><table><thead><tr><th>Pair</th><th>Gap (z-score)</th><th>Signal</th><th class="hm">Half-life</th><th class="hm">Hedge β</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="note">The gap is how far today's spread is from its normal level, in standard deviations over the last ${DATA.rules.z_window} sessions. Beyond ±${DATA.rules.entry_z} a trade opens; past ±${DATA.rules.stop_z} it's too stretched and the link may be breaking. Half-life: how many sessions the gap usually takes to close halfway. Tap a pair for its chart.</p>
    </div>
    <div id="pairdetail"></div>`;
  el.querySelectorAll("tr.click").forEach(tr => tr.addEventListener("click", () => {
    sel = +tr.dataset.i;
    el.querySelectorAll("tr.click").forEach(r => r.classList.toggle("sel", r === tr));
    renderPair();
    $("#pairdetail").scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  renderPair();
}

function renderPair() {
  const p = DATA.pairs[sel];
  const box = $("#pairdetail");
  const R = rules;
  const spread = p.pa.map((x, i) => Math.log(x) - p.beta * Math.log(p.pb[i]));
  const z = zscore(spread, R.z_window);
  const ra = p.pa.map((x, i) => i ? x / p.pa[i - 1] - 1 : 0);
  const rb = p.pb.map((x, i) => i ? x / p.pb[i - 1] - 1 : 0);
  const { pnl, trades } = simulate(z, ra, rb, p.beta, R, p.dates, true);
  const st = stats(pnl);
  const wins = trades.filter(t => t.ret > 0).length;
  const sideTxt = s => s > 0 ? `Buy ${p.a}, short ${p.b}` : `Short ${p.a}, buy ${p.b}`;
  const changed = ["entry_z", "exit_z", "stop_z", "max_days", "z_window"].some(k => R[k] !== DATA.rules[k]);

  box.innerHTML = `
    <div class="panel">
      <h2>${esc(p.a)} / ${esc(p.b)}</h2>
      <p class="lead">${esc(p.sector)} · for every €1 of ${esc(p.a)}, the hedge is €${p.beta.toFixed(2)} of ${esc(p.b)} · cointegration p-value ${p.p.toFixed(3)}</p>
      <h4>Both prices, rebased to 100</h4>
      <div class="chartbox" id="c-px"></div>
      <div class="legend"><span><i style="border-color:var(--accent)"></i>${esc(p.a)}</span><span><i style="border-color:var(--res)"></i>${esc(p.b)}</span></div>
      <h4>The gap (z-score)</h4>
      <div class="chartbox" id="c-z"></div>
      <div class="legend"><span><i class="dash" style="border-color:var(--up-line)"></i>open a trade (±${R.entry_z})</span><span><i class="dash" style="border-color:var(--faint)"></i>close (±${R.exit_z})</span><span><i class="dash" style="border-color:var(--down-line)"></i>stop (±${R.stop_z})</span></div>

      <h4>Try other settings on this pair</h4>
      <div class="sliders">
        ${slider("entry_z", "Open when the gap passes", 1, 3.5, 0.25, R.entry_z, "σ")}
        ${slider("exit_z", "Close when it's back within", 0, 1.5, 0.25, R.exit_z, "σ")}
        ${slider("stop_z", "Stop when it passes", 2.5, 6, 0.5, R.stop_z, "σ")}
        ${slider("max_days", "Close after at most", 5, 90, 5, R.max_days, " sessions")}
        ${slider("z_window", "Normal level measured over", 20, 120, 10, R.z_window, " sessions")}
      </div>
      ${changed ? `<div class="btnrow"><button class="btn ghost" id="reset">Back to the default settings</button></div>` : ""}
      <dl class="stats">
        <div><dt>Return, last 2 years</dt><dd class="${cls(st.total)}">${pct(st.total)}</dd></div>
        <div><dt>Trades</dt><dd>${trades.length}</dd></div>
        <div><dt>Winners</dt><dd>${trades.length ? Math.round(wins / trades.length * 100) + "%" : "–"}</dd></div>
        <div><dt>Sharpe</dt><dd>${st.sharpe.toFixed(2)}</dd></div>
        <div><dt>Worst drop</dt><dd class="neg">${pct(st.max_dd)}</dd></div>
      </dl>
      <p class="note">Careful: this pair was picked <em>because</em> it behaved well over these same two years, so this result flatters it. The <a href="#backtest">Backtest</a> tab picks pairs without seeing the future, which is the fair test.</p>
      ${trades.length ? `<div class="tbl"><table><thead><tr><th>Trade</th><th>Opened</th><th class="hm">Closed</th><th class="hm">Days</th><th class="hm">Why closed</th><th>Result</th></tr></thead><tbody>
        ${trades.slice().reverse().map(t => `<tr><td>${esc(sideTxt(t.side))}</td><td>${fmtDate(t.open)}</td><td class="hm">${fmtDate(t.close)}</td><td class="hm">${t.days}</td><td class="dim hm">${esc(t.why)}</td><td class="${cls(t.ret)}">${pct(t.ret, 2)}</td></tr>`).join("")}
      </tbody></table></div>` : ""}
    </div>`;

  const base = (arr) => arr.map(v => v / arr[0] * 100);
  const short = d => new Date(d + "T12:00:00").toLocaleDateString("en-GB", { month: "short", year: "2-digit" });
  lineChart($("#c-px"), { x: p.dates, xfmt: short, fmt: v => v.toFixed(0), height: 200,
    series: [{ name: p.a, y: base(p.pa), color: "var(--accent)" }, { name: p.b, y: base(p.pb), color: "var(--res)" }] });
  const idx = Object.fromEntries(p.dates.map((d, i) => [d, i]));
  lineChart($("#c-z"), { x: p.dates, xfmt: short, fmt: v => v.toFixed(1), height: 200,
    refs: [R.entry_z, -R.entry_z].map(y => ({ y, color: "var(--up-line)", dash: true }))
      .concat([R.exit_z, -R.exit_z].map(y => ({ y, color: "var(--faint)", dash: true })))
      .concat([R.stop_z, -R.stop_z].map(y => ({ y, color: "var(--down-line)", dash: true }))),
    series: [{ name: "z", y: z, color: "var(--fg)", width: 1.6,
      marks: trades.flatMap(t => [{ i: idx[t.open], color: "var(--up-line)" }, { i: idx[t.close], color: t.ret > 0 ? "var(--accent)" : "var(--down-line)" }]) }] });

  box.querySelectorAll("input[type=range]").forEach(inp => inp.addEventListener("input", () => {
    rules = { ...rules, [inp.name]: +inp.value };
    if (rules.exit_z >= rules.entry_z) rules.exit_z = Math.max(0, rules.entry_z - 0.25);
    if (rules.stop_z <= rules.entry_z) rules.stop_z = rules.entry_z + 0.5;
    renderPair();
  }));
  $("#reset")?.addEventListener("click", () => { rules = { ...DATA.rules }; renderPair(); });
}

function slider(name, label, min, max, step, val, unit) {
  return `<label>${esc(label)} <span><b>${val}${unit}</b></span><input type="range" name="${name}" min="${min}" max="${max}" step="${step}" value="${val}"></label>`;
}

// ------------------------------------------------------------------ How it works
function renderLearn() {
  const R = DATA.rules;
  $("#v-learn").innerHTML = `
    <div class="panel">
      <h2>Arbitrage, and the version you can actually trade</h2>
      <p class="lead">Pure arbitrage is buying something in one place and selling the same thing for more somewhere else at the same moment: a locked-in, riskless profit. Those gaps last milliseconds and belong to high-frequency firms.</p>
      <p>Pairs trading is <b>statistical</b> arbitrage. It isn't riskless: it bets that two prices which have been tied together for years will stay tied, so when they drift apart they'll come back. Think of Coca-Cola and PepsiCo: same business, same customers, same costs. If one jumps 8% and the other doesn't, the gap tends to close.</p>
      <div class="steps">
        <div><span class="n">1</span><b>Find pairs</b><p>Every pair of stocks in the same sector is tested over the last two years. Only pairs that pass the Engle-Granger cointegration test (p &lt; ${R.p_max}) and close half their gap in ${R.hl_min}–${R.hl_max} sessions are kept, at most ${R.max_pairs}.</p></div>
        <div><span class="n">2</span><b>Size the hedge</b><p>A regression of one price on the other gives β: how many euros of B balance one euro of A. The spread is ln(A) − β·ln(B).</p></div>
        <div><span class="n">3</span><b>Measure the gap</b><p>The z-score says how far today's spread is from its average of the last ${R.z_window} sessions, in standard deviations.</p></div>
        <div><span class="n">4</span><b>Trade it</b><p>Gap above +${R.entry_z}: short A, buy B. Below −${R.entry_z}: buy A, short B. Close when it's back within ±${R.exit_z}, stop beyond ±${R.stop_z}, or after ${R.max_days} sessions.</p></div>
      </div>
      <h4>Correlated is not the same as cointegrated</h4>
      <p>Two stocks can rise on the same days (correlated) and still drift apart for good. Cointegrated means the <em>gap</em> between them keeps returning to a level. That's the property this strategy needs.</p>
      <h4>What goes wrong</h4>
      <ol>
        <li><b>The link breaks.</b> A takeover, a scandal or a new business line can separate two stocks for good. The stop and the time limit are there for this.</li>
        <li><b>Costs eat thin edges.</b> Four orders per trade (two legs, in and out), plus borrowing fees on the short.</li>
        <li><b>Too many pairs, some lucky.</b> Test hundreds of pairs and some will look great by chance. That's why the backtest re-picks pairs every six months using only past data.</li>
        <li><b>It's crowded.</b> Pairs trading has been public since the 1980s and the easy gaps are traded away fast.</li>
      </ol>
      <h4>Can I do this from Italy?</h4>
      <p>You need to short the leg that ran ahead. Interactive Brokers and a few others allow shorting US stocks; many retail apps don't. CFDs allow it but come with leverage and overnight fees. Without a short, holding only the cheap leg is just a mean-reversion bet with full market risk.</p>
    </div>`;
}

// ------------------------------------------------------------------ Backtest
function renderBacktest() {
  const W = DATA.walk_forward, s = W.strategy, b = W.spy;
  const beat = s.cagr > b.cagr, good = s.sharpe > 0.5 && s.cagr > 0;
  const verdict = beat
    ? `Out of sample it earned ${pct(s.cagr)} a year against ${pct(b.cagr)} for holding SPY.`
    : good
      ? `It made money (${pct(s.cagr)} a year) with low ties to the market, but less than holding SPY (${pct(b.cagr)} a year).`
      : `It didn't work: ${pct(s.cagr)} a year after costs, against ${pct(b.cagr)} for holding SPY. Over these years the gaps between S&P 100 pairs haven't closed reliably enough to pay four trading costs per trade.`;
  const short = d => new Date(d + "T12:00:00").toLocaleDateString("en-GB", { month: "short", year: "numeric" });
  const exits = Object.entries(W.exits || {}).map(([k, v]) => `${esc(k)} ${Math.round(v / W.trades * 100)}%`).join(" · ");
  $("#v-backtest").innerHTML = `
    <div class="panel">
      <h2>€100,000, ${short(W.from)} to ${short(W.to)}</h2>
      <p class="lead">Walk-forward, out of sample: every ${Math.round(DATA.rules.trading_days / 21)} months the pairs are picked using only the ${Math.round(DATA.rules.formation_days / 252)} years before, then traded for the next ${Math.round(DATA.rules.trading_days / 21)} months. Money is split equally across the pairs; money not in a trade earns nothing.</p>
      <div class="chartbox" id="c-eq"></div>
      <div class="legend"><span><i style="border-color:var(--accent)"></i>Pairs trading</span><span><i class="dash" style="border-color:var(--faint)"></i>Holding SPY</span></div>
      <div class="verdict ${good ? "good" : ""}"><p>${verdict}</p></div>
      <div class="tbl"><table><thead><tr><th></th><th>Per year</th><th>Total</th><th>Sharpe</th><th>Worst drop</th><th class="hm">Volatility</th></tr></thead><tbody>
        <tr><td><b>Pairs trading</b></td><td class="${cls(s.cagr)}">${pct(s.cagr)}</td><td class="${cls(s.total)}">${pct(s.total, 0)}</td><td>${s.sharpe}</td><td class="neg">${pct(s.max_dd)}</td><td class="hm">${pct(s.vol).replace("+", "")}</td></tr>
        <tr class="bench"><td>Holding SPY</td><td>${pct(b.cagr)}</td><td>${pct(b.total, 0)}</td><td>${b.sharpe}</td><td>${pct(b.max_dd)}</td><td class="hm">${pct(b.vol).replace("+", "")}</td></tr>
      </tbody></table></div>
      <dl class="stats">
        <div><dt>Trades</dt><dd>${W.trades.toLocaleString("en-GB")}</dd></div>
        <div><dt>Winners</dt><dd>${Math.round(W.win_rate * 100)}%</dd></div>
        <div><dt>Average trade</dt><dd class="${cls(W.avg_trade)}">${pct(W.avg_trade, 2)}</dd></div>
        <div><dt>Average hold</dt><dd>${W.avg_days} sessions</dd></div>
        <div><dt>Correlation with SPY</dt><dd>${W.corr_spy}</dd></div>
      </dl>
      <p class="note">How trades ended: ${exits}. Costs included: 0.10% per leg on every open and close. Not included: borrowing fees on the short leg, Italian tax.</p>
    </div>
    ${GRID ? renderGrid() : ""}
    <div class="panel">
      <h2>Each six months</h2>
      <p class="lead">The pairs that were picked, and what they made in the months that followed.</p>
      <div class="tbl"><table><thead><tr><th>Period</th><th>Pairs traded</th><th>Return</th></tr></thead><tbody>
        ${W.periods.slice().reverse().map(p => `<tr><td>${short(p.from)} – ${short(p.to)}</td><td style="text-align:left;white-space:normal" class="dim">${p.pairs.length ? esc(p.pairs.join(", ")) : "none passed the test"}</td><td class="${cls(p.ret)}">${pct(p.ret)}</td></tr>`).join("")}
      </tbody></table></div>
    </div>
    <div class="panel">
      <h2>Latest trades in the backtest</h2>
      <div class="tbl"><table><thead><tr><th>Pair</th><th class="hm">Trade</th><th>Opened</th><th class="hm">Days</th><th class="hm">Why closed</th><th>Result</th></tr></thead><tbody>
        ${W.recent_trades.map(t => `<tr><td><b>${esc(t.a)}/${esc(t.b)}</b></td><td class="dim hm">${esc(t.side)}</td><td>${fmtDate(t.open)}</td><td class="hm">${t.days}</td><td class="dim hm">${esc(t.why)}</td><td class="${cls(t.ret)}">${pct(t.ret, 2)}</td></tr>`).join("")}
      </tbody></table></div>
    </div>`;
  const toMap = c => Object.fromEntries(c);
  const sm = toMap(W.spy_curve);
  lineChart($("#c-eq"), {
    x: W.curve.map(c => c[0]), xfmt: d => new Date(d + "T12:00:00").getFullYear(), fmt: v => `€${Math.round(v / 1000)}k`, height: 240,
    refs: [{ y: 100000, color: "var(--hair)" }],
    series: [{ name: "Pairs", y: W.curve.map(c => c[1]), color: "var(--accent)" },
             { name: "SPY", y: W.curve.map(c => sm[c[0]] ?? NaN), color: "var(--faint)", dash: true }],
  });
}

function renderGrid() {
  const rows = GRID.results.slice().sort((a, b) => b.sharpe - a.sharpe);
  const pos = rows.filter(r => r.cagr > 0).length;
  return `<div class="panel">
    <h2>Other settings, same fair test</h2>
    <p class="lead">${rows.length} combinations of how long to look back, how long to trade and how wide a gap to wait for. ${pos} of ${rows.length} made money after costs. Don't just pick the best row: with this many tries, one will look good by luck.</p>
    <div class="tbl"><table><thead><tr><th>Look back</th><th>Trade for</th><th>Open at</th><th>Per year</th><th>Sharpe</th><th class="hm">Worst drop</th><th class="hm">Trades</th></tr></thead><tbody>
      ${rows.map(r => `<tr${r.formation === DATA.rules.formation_days && r.trading === DATA.rules.trading_days && r.entry === DATA.rules.entry_z ? ' class="sel"' : ""}><td>${r.formation / 252} yr</td><td>${Math.round(r.trading / 21)} mo</td><td>±${r.entry}</td><td class="${cls(r.cagr)}">${pct(r.cagr)}</td><td>${r.sharpe}</td><td class="neg hm">${pct(r.max_dd)}</td><td class="hm">${r.trades}</td></tr>`).join("")}
    </tbody></table></div>
    <p class="note">Highlighted: the settings this page uses. Recalculated every week.</p>
  </div>`;
}

// ------------------------------------------------------------------ Live
function renderLive() {
  const el = $("#v-live");
  if (!LIVE) {
    el.innerHTML = `<div class="msg"><strong>Starting soon</strong>Paper trades begin after the next daily scan.</div>`;
    return;
  }
  const S = LIVE.summary || { open: 0, closed: 0, pnl_closed: 0, pnl_open: 0 };
  const side = t => t.dir > 0 ? `Buy ${t.a}, short ${t.b}` : `Short ${t.a}, buy ${t.b}`;
  el.innerHTML = `
    <div class="panel">
      <h2>Paper trades since ${fmtDate(LIVE.since)}</h2>
      <p class="lead">Every signal from the Today tab, traded with €${LIVE.trade_eur.toLocaleString("en-GB")} of pretend money at the daily close, with the default settings. Closed trades are never changed or removed.</p>
      <dl class="stats">
        <div><dt>Closed trades</dt><dd>${S.closed}</dd></div>
        <div><dt>Profit, closed</dt><dd class="${cls(S.pnl_closed)}">${eur2(S.pnl_closed)}</dd></div>
        <div><dt>Open trades</dt><dd>${S.open}</dd></div>
        <div><dt>Profit, open</dt><dd class="${cls(S.pnl_open)}">${eur2(S.pnl_open)}</dd></div>
        <div><dt>Winners</dt><dd>${S.win_rate == null ? "–" : Math.round(S.win_rate * 100) + "%"}</dd></div>
      </dl>
    </div>
    <div class="panel"><h2>Open</h2>
      ${LIVE.open.length ? `<div class="tbl"><table><thead><tr><th>Trade</th><th class="hm">Opened</th><th class="hm">Gap then</th><th>Gap now</th><th class="hm">Days</th><th>Profit</th></tr></thead><tbody>
        ${LIVE.open.map(t => `<tr><td><b>${esc(side(t))}</b></td><td class="hm">${fmtDate(t.opened)}</td><td class="hm">${t.z0.toFixed(2)}</td><td>${t.z.toFixed(2)}</td><td class="hm">${t.days}</td><td class="${cls(t.pnl)}">${eur2(t.pnl)}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="note">No open trades: no pair's gap is past ±${DATA.rules.entry_z} right now.</p>`}
    </div>
    <div class="panel"><h2>Closed</h2>
      ${LIVE.closed.length ? `<div class="tbl"><table><thead><tr><th>Trade</th><th class="hm">Opened</th><th>Closed</th><th class="hm">Why</th><th>Profit</th></tr></thead><tbody>
        ${LIVE.closed.slice().reverse().map(t => `<tr><td><b>${esc(side(t))}</b></td><td class="hm">${fmtDate(t.opened)}</td><td>${fmtDate(t.closed)}</td><td class="dim hm">${esc(t.why)}</td><td class="${cls(t.pnl)}">${eur2(t.pnl)}</td></tr>`).join("")}
      </tbody></table></div>` : `<p class="note">None yet.</p>`}
    </div>`;
}

// ------------------------------------------------------------------ boot
async function getJSON(u) {
  try { const r = await fetch(u, { cache: "no-cache" }); return r.ok ? r.json() : null; } catch { return null; }
}

async function boot() {
  [DATA, LIVE, GRID] = await Promise.all([getJSON("data/pairs/pairs.json"), getJSON("data/pairs/live.json"), getJSON("data/pairs/grid.json")]);
  if (!DATA) {
    $("#v-today").hidden = false;
    $("#v-today").innerHTML = `<div class="msg"><strong>No data yet</strong>The pairs scan runs after each daily scan.</div>`;
    return;
  }
  rules = { ...DATA.rules };
  renderToday(); renderLearn(); renderBacktest(); renderLive();
  document.querySelectorAll("#tabs button").forEach(b => b.addEventListener("click", () => show(b.dataset.v)));
  addEventListener("hashchange", () => show(location.hash.slice(1)));
  show(location.hash.slice(1));
  let lastW = innerWidth;
  addEventListener("resize", () => {
    if (Math.abs(innerWidth - lastW) < 60) return;
    lastW = innerWidth; renderPair(); renderBacktest();
  });
}

if (typeof document !== "undefined") boot();
