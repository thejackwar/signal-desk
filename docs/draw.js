// Drawing tools for the chart page (docs/chart.html), TradingView style: an SVG layer on top of the
// Lightweight Charts price pane. Every point is stored as {t: unix seconds, p: price}, so a drawing
// stays in the same place on every timeframe. The page keeps the list per market on the device.
//
// Fixed range volume profile: pick two candles; the volume traded in between is spread over price rows
// (maths shared with the Volume Profile page, vp-lib.js), with the point of control and the value area.
//
//   const layer = createDrawLayer({ chart, series, host, bars, step, store, getTool, setTool, ... })
//   layer.redraw(); layer.destroy();
//
// Pure helpers (time <-> candle index, fib levels, position maths) are exported for the tests:
// node tests/chart/test_draw.mjs
import { buildProfile } from "./vp-lib.js?v=1";

// ------------------------------------------------------------------ the tools
// [id, name, points needed, group, svg path for the button]
export const TOOLS = [
  ["cursor", "Select and move", 0, "Pointer", '<path d="M5 3l14 8-6 1.6L10 19z"/>'],
  ["trend", "Trend line", 2, "Lines", '<path d="M4 19 20 5"/><circle cx="4" cy="19" r="1.8"/><circle cx="20" cy="5" r="1.8"/>'],
  ["ray", "Ray", 2, "Lines", '<path d="M4 19 22 3"/><circle cx="4" cy="19" r="1.8"/><circle cx="12" cy="12" r="1.8"/>'],
  ["ext", "Extended line", 2, "Lines", '<path d="M2 21 22 3"/><circle cx="8" cy="15.6" r="1.8"/><circle cx="16" cy="8.4" r="1.8"/>'],
  ["arrow", "Arrow", 2, "Lines", '<path d="M4 20 19 5"/><path d="M11 5h8v8"/>'],
  ["hline", "Horizontal line", 1, "Levels", '<path d="M2 12h20"/><circle cx="12" cy="12" r="1.8"/>'],
  ["hray", "Horizontal ray", 1, "Levels", '<path d="M6 12h16"/><circle cx="6" cy="12" r="1.8"/>'],
  ["vline", "Vertical line", 1, "Levels", '<path d="M12 2v20"/><circle cx="12" cy="12" r="1.8"/>'],
  ["channel", "Parallel channel", 3, "Shapes", '<path d="M3 15 17 5"/><path d="M7 20 21 10"/>'],
  ["rect", "Rectangle", 2, "Shapes", '<rect x="4" y="6" width="16" height="12" rx="1"/>'],
  ["fib", "Fib retracement", 2, "Fibonacci", '<path d="M3 5h18M3 9h18M3 13h18M3 19h18"/><path d="M5 19 19 5" stroke-dasharray="2 2"/>'],
  ["long", "Long position", 2, "Trades", '<rect x="4" y="4" width="16" height="9" rx="1"/><path d="M4 13h16v7H4z" opacity=".55"/>'],
  ["short", "Short position", 2, "Trades", '<path d="M4 4h16v7H4z" opacity=".55"/><rect x="4" y="11" width="16" height="9" rx="1"/>'],
  ["measure", "Measure", 2, "Trades", '<path d="M4 20 20 4"/><path d="M4 14v6h6M14 4h6v6"/>'],
  ["frvp", "Fixed range volume profile", 2, "Volume", '<path d="M4 3v18"/><path d="M4 6h7M4 9h11M4 12h16M4 15h9M4 18h5"/>'],
  ["text", "Text note", 1, "Notes", '<path d="M5 6V4h14v2M12 4v16M9 20h6"/>'],
];
export const TOOL = Object.fromEntries(TOOLS.map(t => [t[0], { id: t[0], name: t[1], n: t[2], grp: t[3], icon: t[4] }]));
export const COLORS = ["#38bdf8", "#4ade80", "#f87171", "#facc15", "#c084fc", "#fb923c", "#f472b6", "#e6edf3"];
export const FIB = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618];
const FIB_COL = ["#8b98a5", "#f87171", "#fb923c", "#4ade80", "#2dd4bf", "#38bdf8", "#8b98a5", "#c084fc"];

// ------------------------------------------------------------------ maths (pure)
// candle times (oldest first) -> fractional candle index for any time, and back; past the last candle
// (or before the first) the bars go on at one step each, so a line can reach into the future.
export function makeTimeMap(times, step) {
  const n = times.length, last = n - 1, t0 = times[0], tl = times[last];
  const tToL = t => {
    if (t >= tl) return last + (t - tl) / step;
    if (t <= t0) return (t - t0) / step;
    let lo = 0, hi = last;
    while (lo < hi) { const m = (lo + hi) >> 1; if (times[m] < t) lo = m + 1; else hi = m; }
    const a = times[lo - 1], b = times[lo];
    return lo - 1 + (t - a) / (b - a);
  };
  const lToT = l => {
    if (l >= last) return tl + (l - last) * step;
    if (l <= 0) return t0 + l * step;
    const i = Math.floor(l), a = times[i], b = times[i + 1];
    return a + (l - i) * (b - a);
  };
  return { tToL, lToT };
}

// Fib retracement drawn from a (start of the move) to b (end): level 0 sits at b, 1 at a.
export const fibPrice = (a, b, lvl) => b + (a - b) * lvl;

// Long/short position: entry, target, stop -> risk/reward numbers.
export function positionStats(side, entry, target, stop) {
  const dir = side === "short" ? -1 : 1;
  const reward = (target - entry) * dir, risk = (entry - stop) * dir;
  return { reward, risk, rr: risk > 0 ? reward / risk : null, tpPct: (target / entry - 1) * 100 * dir, slPct: (stop / entry - 1) * 100 * dir };
}

// Fixed range volume profile over candles [from..to] (inclusive, any order): buildProfile plus the
// buying share of each row (volume of candles that closed up). null when the range has no volume.
export function rangeProfile(bars, from, to, rows = 24, va = 0.7) {
  const a = Math.max(0, Math.ceil(Math.min(from, to))), b = Math.min(bars.length - 1, Math.floor(Math.max(from, to)));
  if (b < a) return null;
  const rowsOf = sel => bars.slice(a, b + 1).filter(sel).map(x => [x.time, x.open, x.high, x.low, x.close, x.volume]);
  const all = rowsOf(() => true);
  const p = buildProfile(all, { rows, va });
  if (!p) return null;
  const up = buildProfile(rowsOf(x => x.close >= x.open), { rows, va, lo: p.lo, hi: p.hi });
  return { ...p, buy: up ? up.vol : new Array(rows).fill(0), from: a, to: b };
}

// old drawings ({type:"h", p} and {type:"l", a, b}) -> the current format
export function upgrade(list) {
  return (Array.isArray(list) ? list : []).map((d, k) => {
    if (!d || typeof d !== "object") return null;
    if (d.type === "h") return { id: "o" + k, type: "hline", pts: [{ t: 0, p: d.p }], color: COLORS[0], w: 1.5, dash: 0 };
    if (d.type === "l") return { id: "o" + k, type: "trend", pts: [d.a, d.b], color: COLORS[0], w: 2, dash: 0 };
    return TOOL[d.type] && Array.isArray(d.pts) ? d : null;
  }).filter(Boolean);
}

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const dp = p => Math.abs(p) >= 1 ? 2 : Math.abs(p) >= 0.01 ? 4 : 6;
const fmt = p => p == null || !isFinite(p) ? "–" : (+p).toLocaleString("en-US", { minimumFractionDigits: dp(p), maximumFractionDigits: dp(p) });
const pct = x => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(2)}%`;
const uid = () => Math.random().toString(36).slice(2, 9);
const DASH = ["", "6 4", "2 3"];

// ------------------------------------------------------------------ the layer
export function createDrawLayer(o) {
  const { chart, series, host, bars, step, store } = o;
  const ts = chart.timeScale();
  const times = bars.map(b => b.time), last = bars.length - 1;
  const { tToL, lToT } = makeTimeMap(times, step);
  const X = t => ts.logicalToCoordinate(tToL(t));
  const Y = p => series.priceToCoordinate(p);
  const ns = "http://www.w3.org/2000/svg";

  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "dlayer");
  host.appendChild(svg);
  const bar = document.createElement("div");   // edit bar for the selected drawing
  bar.className = "dsel"; bar.hidden = true;
  host.appendChild(bar);

  let list = upgrade(store.get());
  let sel = null, build = null, drag = null, sig = "", raf = 0, dead = false;
  const vpCache = new WeakMap();   // drawing -> its profile, rebuilt only when its range or rows change
  const undo = [];
  const save = () => { store.set(list); o.onChange?.(list); };
  const snapshot = () => { undo.push(JSON.stringify(list)); if (undo.length > 60) undo.shift(); };
  const tool = () => o.getTool?.() || "cursor";
  const box = () => {
    const w = host.clientWidth - (chart.priceScale("right").width() || 0), h = host.clientHeight - (ts.height() || 0);
    return { w: Math.max(0, w), h: Math.max(0, h) };
  };

  // pointer -> {t, p}, pulled to the nearest open/high/low/close when the magnet is on
  function ptAt(ev) {
    const r = host.getBoundingClientRect(), x = ev.clientX - r.left, y = ev.clientY - r.top;
    let l = ts.coordinateToLogical(x), p = series.coordinateToPrice(y);
    if (l == null || p == null) return null;
    if (o.magnet?.()) {
      const i = Math.round(l);
      if (i >= 0 && i <= last) {
        l = i;
        const b = bars[i];
        let best = null, bd = 16;
        for (const v of [b.open, b.high, b.low, b.close]) { const d = Math.abs(Y(v) - y); if (d < bd) { bd = d; best = v; } }
        if (best != null) p = best;
      }
    }
    return { t: Math.round(lToT(l)), p: +(+p).toPrecision(8), x, y };
  }

  // -------------------------------------------------------------- render
  function lineEnds(a, b, mode, W) {
    let [x1, y1, x2, y2] = [a.x, a.y, b.x, b.y];
    const dx = x2 - x1, dy = y2 - y1;
    if (!dx && !dy) return [x1, y1, x2, y2];
    const k = (W * 6 + 2000) / Math.hypot(dx, dy);
    if (mode === "ray" || mode === "ext") { x2 = x1 + dx * k; y2 = y1 + dy * k; }
    if (mode === "ext") { x1 = a.x - dx * k; y1 = a.y - dy * k; }
    return [x1, y1, x2, y2];
  }
  const tag = (x, y, text, col, anchor = "start", dark = false) => {
    const w = text.length * 6.4 + 10, bx = anchor === "end" ? x - w : anchor === "middle" ? x - w / 2 : x;
    return `<g class="tag"><rect x="${bx.toFixed(1)}" y="${(y - 9).toFixed(1)}" width="${w.toFixed(1)}" height="18" rx="4" fill="${dark ? "#0b0f14" : col}" ${dark ? `stroke="${col}"` : ""} opacity="${dark ? .9 : 1}"/><text x="${(bx + w / 2).toFixed(1)}" y="${(y + 4).toFixed(1)}" text-anchor="middle" fill="${dark ? col : "#04121a"}">${esc(text)}</text></g>`;
  };

  function shape(d, W, H, isSel, sys) {
    const c = d.color || COLORS[0], w = d.w || 2, da = DASH[d.dash || 0];
    const P = d.pts.map(q => ({ x: X(q.t), y: Y(q.p), t: q.t, p: q.p }));
    if (P.some(q => q.y == null || (d.type !== "hline" && q.x == null))) return "";
    const st = `stroke="${c}" stroke-width="${w}" ${da ? `stroke-dasharray="${da}"` : ""} fill="none" stroke-linecap="round"`;
    const hit = (path) => sys ? "" : `<path class="hit" d="${path}"/>`;
    let g = "", handles = [];
    const a = P[0], b = P[1];
    switch (d.type) {
      case "trend": case "ray": case "ext": case "arrow": {
        const [x1, y1, x2, y2] = lineEnds(a, b, d.type, W);
        const path = `M${x1.toFixed(1)} ${y1.toFixed(1)}L${x2.toFixed(1)} ${y2.toFixed(1)}`;
        g += `<path d="${path}" ${st}/>` + hit(path);
        if (d.type === "arrow") {
          const ang = Math.atan2(b.y - a.y, b.x - a.x), L0 = 12 + w * 2;
          const p1 = [b.x - L0 * Math.cos(ang - .45), b.y - L0 * Math.sin(ang - .45)], p2 = [b.x - L0 * Math.cos(ang + .45), b.y - L0 * Math.sin(ang + .45)];
          g += `<path d="M${p1[0].toFixed(1)} ${p1[1].toFixed(1)}L${b.x.toFixed(1)} ${b.y.toFixed(1)}L${p2[0].toFixed(1)} ${p2[1].toFixed(1)}" ${st.replace(/stroke-dasharray="[^"]*"/, "")} stroke-linejoin="round"/>`;
        }
        if (d.label) g += tag((a.x + b.x) / 2, (a.y + b.y) / 2 - 14, d.label, c, "middle", true);
        handles = [0, 1];
        break;
      }
      case "hline": {
        const path = `M0 ${a.y.toFixed(1)}H${W}`;
        g += `<path d="${path}" ${st}/>` + hit(path) + tag(W - 4, a.y, (d.label ? d.label + " " : "") + fmt(a.p), c, "end");
        handles = [];
        break;
      }
      case "hray": {
        const path = `M${a.x.toFixed(1)} ${a.y.toFixed(1)}H${W}`;
        g += `<path d="${path}" ${st}/>` + hit(path) + tag(W - 4, a.y, (d.label ? d.label + " " : "") + fmt(a.p), c, "end");
        handles = [0];
        break;
      }
      case "vline": {
        const path = `M${a.x.toFixed(1)} 0V${H}`;
        g += `<path d="${path}" ${st}/>` + hit(path);
        if (d.label) g += tag(a.x + 6, 14, d.label, c, "start", true);
        handles = [];
        break;
      }
      case "rect": {
        const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y), rw = Math.abs(b.x - a.x), rh = Math.abs(b.y - a.y);
        g += `<rect class="${sys ? "" : "hitf"}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${rw.toFixed(1)}" height="${rh.toFixed(1)}" fill="${c}" fill-opacity=".14" ${st.replace('fill="none"', "")}/>`;
        if (d.label) g += tag(x + 4, y + 12, d.label, c, "start", true);
        handles = [0, 1];
        break;
      }
      case "channel": {
        if (P.length < 3) { const [x1, y1, x2, y2] = [a.x, a.y, b.x, b.y]; g += `<path d="M${x1} ${y1}L${x2} ${y2}" ${st}/>`; handles = [0, 1]; break; }
        const la = tToL(a.t), lb = tToL(b.t), lc = tToL(P[2].t);
        const at = l => lb === la ? a.p : a.p + (b.p - a.p) * (l - la) / (lb - la);
        const off = P[2].p - at(lc);
        const a2 = { x: a.x, y: Y(a.p + off) }, b2 = { x: b.x, y: Y(b.p + off) }, am = { x: a.x, y: Y(a.p + off / 2) }, bm = { x: b.x, y: Y(b.p + off / 2) };
        if (a2.y == null || b2.y == null) return "";
        const poly = `M${a.x} ${a.y}L${b.x} ${b.y}L${b2.x} ${b2.y}L${a2.x} ${a2.y}Z`;
        g += `<path class="${sys ? "" : "hitf"}" d="${poly}" fill="${c}" fill-opacity=".1" stroke="none"/><path d="M${a.x} ${a.y}L${b.x} ${b.y}M${a2.x} ${a2.y}L${b2.x} ${b2.y}" ${st}/><path d="M${am.x} ${am.y}L${bm.x} ${bm.y}" stroke="${c}" stroke-width="1" stroke-dasharray="4 4" opacity=".7"/>` + hit(`M${a.x} ${a.y}L${b.x} ${b.y}M${a2.x} ${a2.y}L${b2.x} ${b2.y}`);
        handles = [0, 1, 2];
        break;
      }
      case "fib": {
        const x1 = Math.min(a.x, b.x), x2 = Math.max(a.x, b.x, x1 + 40);
        let prev = null;
        FIB.forEach((lv, k) => {
          const pr = fibPrice(a.p, b.p, lv), y = Y(pr);
          if (y == null) return;
          if (prev != null && lv <= 1) g += `<rect x="${x1.toFixed(1)}" y="${Math.min(prev, y).toFixed(1)}" width="${(x2 - x1).toFixed(1)}" height="${Math.abs(y - prev).toFixed(1)}" fill="${FIB_COL[k]}" fill-opacity=".07"/>`;
          g += `<path d="M${x1.toFixed(1)} ${y.toFixed(1)}H${x2.toFixed(1)}" stroke="${FIB_COL[k]}" stroke-width="1.2" ${lv === 1.618 ? 'stroke-dasharray="4 3"' : ""}/>`;
          g += `<text class="fl" x="${(x1 + 4).toFixed(1)}" y="${(y - 3).toFixed(1)}" fill="${FIB_COL[k]}">${lv} (${fmt(pr)})</text>`;
          prev = y;
        });
        g += `<path d="M${a.x} ${a.y}L${b.x} ${b.y}" stroke="${c}" stroke-width="1" stroke-dasharray="3 3"/>` + hit(`M${x1} ${a.y}H${x2}M${x1} ${b.y}H${x2}M${a.x} ${a.y}L${b.x} ${b.y}`);
        handles = [0, 1];
        break;
      }
      case "long": case "short": {
        const stop = P[2] || { p: d.type === "long" ? a.p - (b.p - a.p) / 2 : a.p + (a.p - b.p) / 2 };
        const ys = Y(stop.p), x1 = Math.min(a.x, b.x), x2 = Math.max(a.x, b.x, x1 + 30);
        if (ys == null) return "";
        const S0 = positionStats(d.type, a.p, b.p, stop.p);
        const gr = "#4ade80", rd = "#f87171", bw = x2 - x1;
        g += `<rect class="${sys ? "" : "hitf"}" x="${x1.toFixed(1)}" y="${Math.min(a.y, b.y).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.abs(b.y - a.y).toFixed(1)}" fill="${gr}" fill-opacity=".16" stroke="${gr}" stroke-opacity=".6"/>`;
        g += `<rect class="${sys ? "" : "hitf"}" x="${x1.toFixed(1)}" y="${Math.min(a.y, ys).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.abs(ys - a.y).toFixed(1)}" fill="${rd}" fill-opacity=".16" stroke="${rd}" stroke-opacity=".6"/>`;
        g += `<path d="M${x1.toFixed(1)} ${a.y.toFixed(1)}H${x2.toFixed(1)}" stroke="#e6edf3" stroke-width="1.4"/>`;
        // the result so far (signal view): a filled band from the entry to the latest/exit price
        if (d.now) {
          const yn = Y(d.now.p), xn = X(d.now.t);
          if (yn != null && xn != null) {
            const good = (d.now.p - a.p) * (d.type === "short" ? -1 : 1) >= 0;
            g += `<rect x="${x1.toFixed(1)}" y="${Math.min(a.y, yn).toFixed(1)}" width="${Math.max(2, Math.min(x2, xn) - x1).toFixed(1)}" height="${Math.abs(yn - a.y).toFixed(1)}" fill="${good ? gr : rd}" fill-opacity=".28"/>`;
            g += `<path d="M${a.x.toFixed(1)} ${a.y.toFixed(1)}L${xn.toFixed(1)} ${yn.toFixed(1)}" stroke="#e6edf3" stroke-width="1" stroke-dasharray="3 3"/>`;
          }
        }
        const mid = (x1 + x2) / 2;
        g += tag(mid, b.y + (b.y < a.y ? -12 : 12), `${d.tpLabel || "Target"} ${fmt(b.p)} · ${pct(S0.tpPct)}`, gr, "middle");
        g += tag(mid, ys + (ys > a.y ? 12 : -12), `${d.slLabel || "Stop"} ${fmt(stop.p)} · ${pct(S0.slPct)}`, rd, "middle");
        g += tag(x2 + 4, a.y, `${d.type === "long" ? "Long" : "Short"} ${fmt(a.p)}${S0.rr != null ? ` · R:R ${S0.rr.toFixed(2)}` : ""}${d.nowLabel ? " · " + d.nowLabel : ""}`, "#e6edf3", "start", true);
        handles = [0, 1, 2];
        P[2] = { x: x2, y: ys };
        P[1] = { x: x2, y: b.y };
        break;
      }
      case "measure": {
        const up = b.p >= a.p, col = up ? "#4ade80" : "#f87171";
        const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y), rw = Math.abs(b.x - a.x), rh = Math.abs(b.y - a.y);
        const nb = Math.round(tToL(b.t) - tToL(a.t)), days = Math.abs(b.t - a.t) / 86400;
        g += `<rect class="${sys ? "" : "hitf"}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${rw.toFixed(1)}" height="${rh.toFixed(1)}" fill="${col}" fill-opacity=".15" stroke="${col}" stroke-opacity=".7"/>`;
        g += `<path d="M${a.x} ${a.y}L${b.x} ${b.y}" stroke="${col}" stroke-width="1.4" stroke-dasharray="4 3"/>`;
        g += tag((a.x + b.x) / 2, Math.max(y + rh + 14, 14), `${fmt(b.p - a.p)} (${pct((b.p / a.p - 1) * 100)}) · ${Math.abs(nb)} bars · ${days >= 1 ? Math.round(days) + "d" : Math.round(days * 24) + "h"}`, col, "middle");
        handles = [0, 1];
        break;
      }
      case "frvp": {
        const la = tToL(a.t), lb = tToL(b.t), x1 = Math.min(a.x, b.x), x2 = Math.max(a.x, b.x);
        const key = `${la.toFixed(3)}|${lb.toFixed(3)}|${d.rows || 24}`;
        let pr = vpCache.get(d);
        if (!pr || pr.key !== key) { pr = { key, p: rangeProfile(bars, la, lb, d.rows || 24) }; vpCache.set(d, pr); }
        const p = pr.p, rw = Math.max(1, x2 - x1);
        if (!p) {
          g += `<rect class="${sys ? "" : "hitf"}" x="${x1.toFixed(1)}" y="0" width="${rw.toFixed(1)}" height="${H}" fill="#8b98a5" fill-opacity=".06" stroke="#8b98a5" stroke-dasharray="4 4"/>` + tag((x1 + x2) / 2, 30, "No volume data on this chart", "#8b98a5", "middle", true);
          handles = [0, 1];
          break;
        }
        const yTop = Y(p.hi), yBot = Y(p.lo);
        if (yTop == null || yBot == null) return "";
        const maxV = Math.max(...p.vol) || 1, maxW = Math.max(40, Math.min(rw, Math.max(rw * 0.45, 70)));
        g += `<rect class="${sys ? "" : "hitf"}" x="${x1.toFixed(1)}" y="${yTop.toFixed(1)}" width="${rw.toFixed(1)}" height="${(yBot - yTop).toFixed(1)}" fill="#38bdf8" fill-opacity=".04" stroke="#38bdf8" stroke-opacity=".25"/>`;
        const yVah = Y(p.vah), yVal = Y(p.val);
        if (yVah != null && yVal != null) g += `<rect x="${x1.toFixed(1)}" y="${yVah.toFixed(1)}" width="${rw.toFixed(1)}" height="${(yVal - yVah).toFixed(1)}" fill="#38bdf8" fill-opacity=".07"/>`;
        for (let i = 0; i < p.rows; i++) {
          if (!p.vol[i]) continue;
          const yt = Y(p.lo + (i + 1) * p.step), yb = Y(p.lo + i * p.step);
          if (yt == null || yb == null) continue;
          const h = Math.max(1, yb - yt - 1), w0 = p.vol[i] / maxV * maxW, wb = w0 * Math.min(1, (p.buy[i] || 0) / p.vol[i]);
          const inVa = i >= p.vaRows[0] && i <= p.vaRows[1], op = inVa ? .75 : .35;
          g += `<rect x="${x1.toFixed(1)}" y="${(yt + .5).toFixed(1)}" width="${wb.toFixed(1)}" height="${h.toFixed(1)}" fill="#2dd4bf" fill-opacity="${op}"/>`;
          g += `<rect x="${(x1 + wb).toFixed(1)}" y="${(yt + .5).toFixed(1)}" width="${(w0 - wb).toFixed(1)}" height="${h.toFixed(1)}" fill="#fb923c" fill-opacity="${op}"/>`;
        }
        const yPoc = Y(p.poc);
        if (yPoc != null) g += `<path d="M${x1.toFixed(1)} ${yPoc.toFixed(1)}H${x2.toFixed(1)}" stroke="#f87171" stroke-width="2"/>` + tag(x2 + 4, yPoc, `POC ${fmt(p.poc)}`, "#f87171");
        if (yVah != null) g += `<path d="M${x1.toFixed(1)} ${yVah.toFixed(1)}H${x2.toFixed(1)}" stroke="#38bdf8" stroke-width="1" stroke-dasharray="5 4"/>` + tag(x2 + 4, yVah - (Math.abs(yVah - (yPoc ?? -99)) < 18 ? 10 : 0), `VAH ${fmt(p.vah)}`, "#38bdf8", "start", true);
        if (yVal != null) g += `<path d="M${x1.toFixed(1)} ${yVal.toFixed(1)}H${x2.toFixed(1)}" stroke="#38bdf8" stroke-width="1" stroke-dasharray="5 4"/>` + tag(x2 + 4, yVal + (Math.abs(yVal - (yPoc ?? -99)) < 18 ? 10 : 0), `VAL ${fmt(p.val)}`, "#38bdf8", "start", true);
        const buyShare = p.buy.reduce((s0, v) => s0 + v, 0) / p.total;
        g += tag((x1 + x2) / 2, Math.max(12, yTop - 12), `${p.to - p.from + 1} bars · ${Math.round(buyShare * 100)}% on up candles`, "#8b98a5", "middle", true);
        handles = [0, 1];
        break;
      }
      case "text": {
        const t = d.text || "Note", tw = t.length * 7.2 + 14;
        g += `<rect class="${sys ? "" : "hitf"}" x="${a.x.toFixed(1)}" y="${(a.y - 12).toFixed(1)}" width="${tw.toFixed(1)}" height="22" rx="5" fill="#0b0f14" fill-opacity=".85" stroke="${c}"/><text class="tx" x="${(a.x + 7).toFixed(1)}" y="${(a.y + 3).toFixed(1)}" fill="${c}">${esc(t)}</text>`;
        handles = [0];
        break;
      }
    }
    if (isSel && !sys) g += handles.map(k => P[k] && P[k].x != null ? `<circle class="hd" data-h="${k}" cx="${P[k].x.toFixed(1)}" cy="${P[k].y.toFixed(1)}" r="5.5"/>` : "").join("");
    return `<g class="dr${isSel ? " sel" : ""}${sys ? " sys" : ""}" ${sys ? "" : `data-id="${d.id}"`}>${g}</g>`;
  }

  function draw() {
    if (dead) return;
    const { w: W, h: H } = box();
    svg.setAttribute("width", host.clientWidth); svg.setAttribute("height", host.clientHeight);
    const sysList = o.extra?.() || [];
    const hidden = o.hidden?.();
    const items = hidden ? [] : list.slice();
    if (build) items.push(build.d);
    svg.innerHTML = `<defs><clipPath id="dclip"><rect x="0" y="0" width="${W}" height="${H}"/></clipPath></defs><g clip-path="url(#dclip)">` +
      sysList.map(d => shape(d, W, H, false, true)).join("") + items.map(d => shape(d, W, H, d.id === sel, false)).join("") + `</g>`;
    svg.classList.toggle("drawing", tool() !== "cursor");
    editBar();
  }
  // redraw whenever the view moves: scrolled, zoomed, the price axis dragged or the box resized
  function loop() {
    if (dead) return;
    const s = [host.clientWidth, host.clientHeight, ts.logicalToCoordinate(0), ts.logicalToCoordinate(10), Y(bars[last].close), Y(bars[last].close * 1.01)].join();
    if (s !== sig) { sig = s; draw(); }
    raf = requestAnimationFrame(loop);
  }

  // -------------------------------------------------------------- edit bar
  function editBar() {
    const d = list.find(x => x.id === sel);
    if (!d || build) { bar.hidden = true; return; }
    bar.hidden = false;
    const isPos = d.type === "long" || d.type === "short" || d.type === "measure" || d.type === "fib" || d.type === "frvp";
    bar.innerHTML = `<b>${esc(TOOL[d.type].name)}</b>` +
      (isPos ? "" : COLORS.map(c => `<button type="button" class="sw" data-col="${c}" style="--c:${c}" aria-pressed="${d.color === c}" aria-label="Colour"></button>`).join("") +
        `<span class="sep"></span>` + [1, 2, 3].map(w => `<button type="button" data-w="${w}" aria-pressed="${Math.round(d.w || 2) === w}" title="Thickness ${w}"><i style="height:${w}px"></i></button>`).join("") +
        `<button type="button" data-dash title="Line style">${["Solid", "Dashed", "Dotted"][d.dash || 0]}</button>`) +
      (d.type === "frvp" ? `<span class="sep"></span><b>Rows</b>${[12, 24, 48, 96].map(r => `<button type="button" data-rows="${r}" aria-pressed="${(d.rows || 24) === r}">${r}</button>`).join("")}` : "") +
      (d.type === "text" ? `<button type="button" data-edit>Edit text</button>` : d.type === "long" || d.type === "short" ? `<button type="button" data-flip>Flip to ${d.type === "long" ? "short" : "long"}</button>` : "") +
      `<button type="button" data-clone title="Copy">Copy</button><button type="button" class="del" data-del title="Delete (Del key)">Delete</button>`;
  }
  bar.addEventListener("pointerdown", e => e.stopPropagation());
  bar.addEventListener("click", e => {
    const d = list.find(x => x.id === sel), b = e.target.closest("button");
    if (!d || !b) return;
    snapshot();
    if (b.dataset.col) d.color = b.dataset.col;
    else if (b.dataset.w) d.w = +b.dataset.w;
    else if (b.dataset.rows) d.rows = +b.dataset.rows;
    else if (b.dataset.dash !== undefined) d.dash = ((d.dash || 0) + 1) % 3;
    else if (b.dataset.edit !== undefined) { const t = prompt("Text", d.text || ""); if (t !== null) d.text = t.slice(0, 80); }
    else if (b.dataset.flip !== undefined) {
      const a = d.pts[0], tp = d.pts[1], sl = d.pts[2];
      d.type = d.type === "long" ? "short" : "long";
      d.pts = [a, { t: tp.t, p: 2 * a.p - tp.p }, { t: sl.t, p: 2 * a.p - sl.p }];
    }
    else if (b.dataset.clone !== undefined) {
      const off = (series.coordinateToPrice(20) ?? 0) - (series.coordinateToPrice(0) ?? 0);
      const c = { ...JSON.parse(JSON.stringify(d)), id: uid() };
      c.pts = c.pts.map(q => ({ t: q.t, p: q.p + off }));
      list.push(c); sel = c.id;
    }
    else if (b.dataset.del !== undefined) { list = list.filter(x => x !== d); sel = null; }
    save(); draw();
  });

  // -------------------------------------------------------------- pointer
  const finish = () => {
    const d = build.d;
    if (d.type === "text") { const t = prompt("Text for the note", ""); if (!t) { build = null; draw(); return; } d.text = t.slice(0, 80); }
    if ((d.type === "long" || d.type === "short") && !d.pts[2]) {
      const a = d.pts[0], b = d.pts[1];
      // if the second click is on the wrong side, mirror it: the target is always in the trade's direction
      if ((d.type === "long" && b.p < a.p) || (d.type === "short" && b.p > a.p)) b.p = 2 * a.p - b.p;
      if (b.p === a.p) b.p = a.p * (d.type === "long" ? 1.03 : 0.97);
      if (tToL(b.t) - tToL(a.t) < 5) b.t = Math.round(lToT(tToL(a.t) + 20));
      d.pts[2] = { t: b.t, p: d.type === "long" ? a.p - (b.p - a.p) / 2 : a.p + (a.p - b.p) / 2 };
    }
    snapshot();
    list.push(d); sel = d.id; build = null;
    save();
    if (!o.keep?.()) o.setTool?.("cursor");
    draw();
  };

  svg.addEventListener("pointerdown", e => {
    if (e.button > 0) return;
    const T = tool();
    if (T !== "cursor") {
      const pt = ptAt(e); if (!pt) return;
      e.preventDefault(); e.stopPropagation();
      svg.setPointerCapture?.(e.pointerId);
      const n = TOOL[T].n;
      if (!build) {
        const d = { id: uid(), type: T, pts: [pt], color: T === "hline" || T === "hray" ? COLORS[3] : COLORS[0], w: T === "hline" || T === "vline" || T === "hray" ? 1.5 : 2, dash: 0 };
        build = { d, step: 1, x: e.clientX, y: e.clientY };
        if (n === 1) { finish(); return; }
        d.pts.push({ ...pt });
      } else {
        build.d.pts[build.step] = pt; build.step++;
        if (build.step >= n) { finish(); return; }
        build.d.pts.push({ ...pt }); build.x = e.clientX; build.y = e.clientY;
      }
      draw();
      return;
    }
    const h = e.target.closest("[data-h]"), g = e.target.closest("[data-id]");
    if (!g) return;
    e.preventDefault(); e.stopPropagation();
    svg.setPointerCapture?.(e.pointerId);
    sel = g.dataset.id;
    const d = list.find(x => x.id === sel), pt = ptAt(e);
    if (!d || !pt) { draw(); return; }
    drag = { d, h: h ? +h.dataset.h : -1, from: pt, orig: JSON.parse(JSON.stringify(d.pts)), moved: false };
    draw();
  });
  svg.addEventListener("pointermove", e => {
    if (build) {
      const pt = ptAt(e); if (!pt) return;
      build.d.pts[build.step] = pt; draw(); return;
    }
    if (!drag) return;
    const pt = ptAt(e); if (!pt) return;
    if (!drag.moved) { snapshot(); drag.moved = true; }
    const { d, h, from, orig } = drag;
    if (h < 0) {
      const dl = tToL(pt.t) - tToL(from.t), dpv = pt.p - from.p;
      d.pts = orig.map(q => ({ t: d.type === "hline" ? q.t : Math.round(lToT(tToL(q.t) + dl)), p: q.p + dpv }));
    } else if ((d.type === "long" || d.type === "short") && h > 0) {
      d.pts[h] = { t: h === 1 ? pt.t : d.pts[h].t, p: pt.p };
      if (h === 1) d.pts[2] = { ...d.pts[2], t: pt.t };
    } else d.pts[h] = { t: pt.t, p: pt.p };
    draw();
  });
  const up = e => {
    if (build && build.step === 1 && Math.hypot(e.clientX - build.x, e.clientY - build.y) > 6) {
      const pt = ptAt(e);
      if (pt) { build.d.pts[1] = pt; build.step = 2; if (build.step >= TOOL[build.d.type].n) finish(); else { build.d.pts.push({ ...pt }); draw(); } }
      return;
    }
    if (drag) { if (drag.moved) save(); drag = null; }
  };
  svg.addEventListener("pointerup", up);
  svg.addEventListener("pointercancel", () => { drag = null; });
  svg.addEventListener("dblclick", e => {
    const g = e.target.closest("[data-id]"); if (!g) return;
    const d = list.find(x => x.id === g.dataset.id);
    if (d?.type === "text") { const t = prompt("Text", d.text || ""); if (t !== null) { snapshot(); d.text = t.slice(0, 80); save(); draw(); } }
  });
  const onChartClick = () => { if (tool() === "cursor" && sel && !drag) { sel = null; draw(); } };
  chart.subscribeClick(onChartClick);
  const onKey = e => {
    if (e.target.closest?.("input, textarea, select, [contenteditable]")) return;
    if ((e.key === "Delete" || e.key === "Backspace") && sel) { e.preventDefault(); snapshot(); list = list.filter(x => x.id !== sel); sel = null; save(); draw(); }
    else if (e.key === "Escape") { if (build) build = null; else if (sel) sel = null; else o.setTool?.("cursor"); draw(); }
    else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") { e.preventDefault(); api.undo(); }
  };
  document.addEventListener("keydown", onKey);
  raf = requestAnimationFrame(loop);

  const api = {
    redraw: () => { sig = ""; draw(); },
    cancel: () => { build = null; draw(); },
    list: () => list,
    undo() { if (!undo.length) return; list = JSON.parse(undo.pop()); sel = null; save(); draw(); },
    clear() { if (!list.length) return; snapshot(); list = []; sel = null; save(); draw(); },
    add(d) { snapshot(); list.push({ id: uid(), color: COLORS[0], w: 2, dash: 0, ...d }); save(); draw(); },
    destroy() { dead = true; cancelAnimationFrame(raf); document.removeEventListener("keydown", onKey); chart.unsubscribeClick(onChartClick); svg.remove(); bar.remove(); },
  };
  return api;
}
