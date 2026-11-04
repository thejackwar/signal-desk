// Volume profile maths for docs/volumeprofile.html (no page code here, so tests can import it).
// A bar is [t, open, high, low, close, volume]. Each bar's volume is spread evenly over its
// high-low range, so with 1-5 minute bars the result is close to a profile built from every trade.

// Profile of `bars` on `rows` equal price rows. Options: va (value-area share, default 0.7),
// lo/hi (fix the price grid, e.g. to share one grid between several profiles).
export function buildProfile(bars, { rows = 60, va = 0.7, lo = null, hi = null } = {}) {
  if (!bars.length) return null;
  if (lo == null) lo = Math.min(...bars.map(b => b[3]));
  if (hi == null) hi = Math.max(...bars.map(b => b[2]));
  if (!(hi > lo)) hi = lo + Math.max(Math.abs(lo) * 1e-4, 1e-9);   // flat price: one thin band
  const step = (hi - lo) / rows;
  const vol = new Float64Array(rows);
  const row = p => Math.min(rows - 1, Math.max(0, Math.floor((p - lo) / step)));
  let total = 0;
  for (const [, , h, l, c, v] of bars) {
    if (!(v > 0)) continue;
    total += v;
    if (!(h > l)) { vol[row(c)] += v; continue; }
    const a = row(l), b = row(h), per = v / (h - l);
    if (a === b) { vol[a] += v; continue; }
    for (let i = a; i <= b; i++) {                              // the share of the bar's range in each row
      const top = Math.min(h, lo + (i + 1) * step), bot = Math.max(l, lo + i * step);
      if (top > bot) vol[i] += (top - bot) * per;
    }
  }
  if (!(total > 0)) return null;

  let poc = 0;
  for (let i = 1; i < rows; i++) if (vol[i] > vol[poc]) poc = i;
  // value area: start at the point of control and add the busier neighbouring row until `va` of the volume is in
  let a = poc, b = poc, inside = vol[poc];
  while (inside < va * total && (a > 0 || b < rows - 1)) {
    const up = b < rows - 1 ? vol[b + 1] : -1, dn = a > 0 ? vol[a - 1] : -1;
    if (up >= dn) inside += vol[++b]; else inside += vol[--a];
  }
  const mid = i => lo + (i + 0.5) * step;
  return {
    lo, hi, step, rows, vol: Array.from(vol), total,
    poc: mid(poc), pocRow: poc, vah: lo + (b + 1) * step, val: lo + a * step, vaRows: [a, b], vaShare: inside / total,
    mid,
  };
}

// High-volume nodes (busy price areas, often support/resistance) and low-volume nodes
// (thin areas price tends to move through). Rows are smoothed over 3 first, so single-row noise is ignored.
export function findNodes(p) {
  if (!p) return { hvn: [], lvn: [] };
  const v = p.vol, n = v.length;
  const s = v.map((_, i) => (v[Math.max(0, i - 1)] + v[i] + v[Math.min(n - 1, i + 1)]) / 3);
  const max = Math.max(...s);
  const peaks = [];
  for (let i = 0; i < n; i++) {
    const l = i === 0 ? -1 : s[i - 1], r = i === n - 1 ? -1 : s[i + 1];
    if (s[i] >= l && s[i] > r && s[i] >= 0.35 * max) peaks.push(i);
  }
  // keep peaks that stand out: between two kept peaks there must be a dip to 80% of the smaller one
  const kept = [];
  for (const i of peaks) {
    const j = kept[kept.length - 1];
    if (j == null) { kept.push(i); continue; }
    const dip = Math.min(...s.slice(j, i + 1));
    if (dip <= 0.8 * Math.min(s[i], s[j])) kept.push(i);
    else if (s[i] > s[j]) kept[kept.length - 1] = i;
  }
  const lvn = [];
  for (let k = 1; k < kept.length; k++) {
    const a = kept[k - 1], b = kept[k];
    let m = a;
    for (let i = a; i <= b; i++) if (s[i] < s[m]) m = i;
    if (s[m] <= 0.5 * Math.min(s[a], s[b])) lvn.push(m);
  }
  const node = i => ({ price: p.mid(i), row: i, share: s[i] / max });
  return { hvn: kept.filter(i => i !== p.pocRow).map(node), lvn: lvn.map(node) };
}

// Split bars into sessions (by New York date for stocks, UTC date for crypto: t is already "wall" time).
export function bySession(bars) {
  const out = new Map();
  for (const b of bars) {
    const d = new Date(b[0] * 1000).toISOString().slice(0, 10);
    if (!out.has(d)) out.set(d, []);
    out.get(d).push(b);
  }
  return [...out].map(([date, bs]) => ({ date, bars: bs }));
}

// Each session's own profile on a shared row height, plus whether its point of control is "naked":
// not traded through by any later bar. Naked POCs are levels many traders watch.
export function sessionProfiles(bars, { rows = 60, va = 0.7 } = {}) {
  const ss = bySession(bars);
  if (!ss.length) return [];
  const lo = Math.min(...bars.map(b => b[3])), hi = Math.max(...bars.map(b => b[2]));
  const step = (hi - lo) / rows || 1;
  const out = ss.map(s => {
    const sl = Math.min(...s.bars.map(b => b[3])), sh = Math.max(...s.bars.map(b => b[2]));
    const a = lo + Math.floor((sl - lo) / step) * step;
    const n = Math.max(1, Math.ceil((sh - a) / step - 1e-9));
    return { date: s.date, from: s.bars[0][0], to: s.bars[s.bars.length - 1][0], bars: s.bars,
      p: buildProfile(s.bars, { rows: n, va, lo: a, hi: a + n * step }) };
  }).filter(s => s.p);
  out.forEach((s, k) => {
    const later = out.slice(k + 1).flatMap(x => x.bars);
    s.naked = k < out.length - 1 && !later.some(b => b[3] <= s.p.poc && b[2] >= s.p.poc);
  });
  return out;
}

// Candles for the chart: bars grouped into `minutes`-long candles ("D" = one per session).
export function candles(bars, minutes) {
  const out = [];
  let cur = null;
  for (const [t, o, h, l, c, v] of bars) {
    const k = minutes === "D" ? Math.floor(t / 86400) * 86400 : Math.floor(t / (minutes * 60)) * minutes * 60;
    if (cur && cur.time === k) {
      cur.high = Math.max(cur.high, h); cur.low = Math.min(cur.low, l); cur.close = c; cur.volume += v;
    } else {
      cur = { time: k, open: o, high: h, low: l, close: c, volume: v };
      out.push(cur);
    }
  }
  return out;
}

// Where the last price sits against the value area.
export function where(price, p) {
  if (!p) return null;
  if (price > p.vah) return "above";
  if (price < p.val) return "below";
  return "inside";
}
