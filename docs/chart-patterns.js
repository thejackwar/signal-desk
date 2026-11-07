// Chart patterns that are forming now, and major support/resistance across timeframes.
// Used by chart.js (the Chart page and the Terminal's chart). Tests: tests/chart/test_chart_patterns.mjs
//
// Kept quiet on purpose: swings smaller than ~1.5 average candles are ignored, a pattern has to be
// big (several ATRs tall), recent (its last swing near the right edge) and still alive (not already
// invalidated, or broken out only in the last few candles), and only the best-scoring ones are kept.

import { findPivots, srLevels, analyze, TFS, ORDER } from "./trendlines.js?v=3";

// ------------------------------------------------------------------ helpers
function atrOf(c, from) {
  let s = 0, k = 0;
  for (let i = Math.max(1, from); i < c.length; i++, k++)
    s += Math.max(c[i].high - c[i].low, Math.abs(c[i].high - c[i - 1].close), Math.abs(c[i].low - c[i - 1].close));
  return k ? s / k : 0;
}

// Alternating swing points (high, low, high ...) with small wiggles removed: two swings in a row of
// the same kind keep the more extreme one, and a swing less than `minMove` from the previous one is dropped.
export function zigzag(c, swing, minMove, from = 0) {
  const pts = [...findPivots(c, swing, "high").map(p => ({ ...p, hi: true })), ...findPivots(c, swing, "low").map(p => ({ ...p, hi: false }))]
    .filter(p => p.index >= from).sort((a, b) => a.index - b.index || (a.hi ? -1 : 1));
  const out = [];
  for (const p of pts) {
    const prev = out[out.length - 1];
    if (!prev) { out.push(p); continue; }
    if (prev.hi === p.hi) { if (p.hi ? p.price >= prev.price : p.price <= prev.price) out[out.length - 1] = p; continue; }
    if (Math.abs(p.price - prev.price) < minMove) {
      // too small: keep going, but let a more extreme point of the earlier kind replace it later
      continue;
    }
    out.push(p);
  }
  return out;
}

const lineAt = (a, b) => i => a.price + (b.price - a.price) / (b.index - a.index) * (i - a.index);
// first close beyond a level after index `from` (dir +1 = above, -1 = below)
function firstClose(c, from, level, dir) {
  for (let i = from + 1; i < c.length; i++) if (dir > 0 ? c[i].close > level(i) : c[i].close < level(i)) return i;
  return null;
}

// ------------------------------------------------------------------ patterns
// Each pattern: {kind, name, bull (true/false/null), points [{index, price}], neck [{index, price} x2] | null,
//   upper/lower (triangles), trigger (price to watch now), target, invalid, status "forming"|"breakout"|"breakdown",
//   brokeAt, score, start, end}
export function chartPatterns(c, { swing = 4, lookback = 250, recent = 0.2, fresh = 6, max = 2 } = {}) {
  const n = c.length, last = n - 1;
  if (n < 40) return [];
  const s0 = Math.max(0, n - lookback);
  const atr = atrOf(c, s0);
  if (!atr) return [];
  const Z = zigzag(c, swing, atr * 1.5, s0);
  const close = c[last].close;
  const maxAge = Math.max(15, Math.round(Math.min(lookback, n) * recent));   // the pattern's last swing must be this recent
  const out = [];
  // p is the newest swing, or only an unfinished swing (still inside the last `swing` candles) came after it
  const isLatest = p => { const j = Z.indexOf(p); return j === Z.length - 1 || (j === Z.length - 2 && Z[j + 1].index > last - swing); };

  const alive = (p) => {
    // invalidated patterns are dropped; a breakout older than `fresh` candles is old news
    if (p.dead) return false;
    if (p.brokeAt != null && last - p.brokeAt > fresh) return false;
    return last - p.end <= maxAge;
  };

  // --- double top / bottom: H L H (or L H L), tops within ~0.6 ATR, at least 2.5 ATR tall
  for (let k = Z.length - 3; k >= 0 && k >= Z.length - 6; k--) {
    const [a, m, b] = Z.slice(k, k + 3);
    if (a.hi !== b.hi || m.hi === a.hi) continue;
    const top = a.hi, tol = Math.max(0.6 * atr, 0.004 * a.price);
    if (Math.abs(a.price - b.price) > tol) continue;
    const height = Math.abs((a.price + b.price) / 2 - m.price);
    if (height < 2.5 * atr || b.index - a.index < 8) continue;
    const dir = top ? -1 : 1, ext = top ? Math.max(a.price, b.price) : Math.min(a.price, b.price);
    const neck = () => m.price;
    const brokeAt = firstClose(c, b.index, neck, dir);
    const invAt = firstClose(c, b.index, () => ext - dir * 0.3 * atr, -dir);   // closed past the tops/bottoms = no pattern
    const dead = invAt != null;   // before or after a break: back past the tops/bottoms means it failed
    out.push({ kind: top ? "dtop" : "dbot", name: top ? "Double top" : "Double bottom", bull: !top,
      points: [a, m, b], neck: [{ index: a.index, price: m.price }, { index: last, price: m.price }],
      trigger: m.price, target: m.price + dir * height, invalid: ext, start: a.index, end: b.index,
      status: brokeAt == null ? "forming" : top ? "breakdown" : "breakout", brokeAt, dead,
      score: height / atr + 2 - Math.abs(a.price - b.price) / tol + (isLatest(b) ? 3 : 0) });   // still on the second top: beats a triangle on the same swings
  }

  // --- head & shoulders (and inverse): S1 n1 H n2 S2, head beyond both shoulders, shoulders level-ish
  for (let k = Z.length - 5; k >= 0 && k >= Z.length - 8; k--) {
    const [s1, n1, h, n2, s2] = Z.slice(k, k + 5);
    if (s1.hi === h.hi && s2.hi === h.hi && n1.hi !== h.hi && n2.hi !== h.hi) {
      const top = h.hi, sgn = top ? 1 : -1;
      const headOver = top ? h.price - Math.max(s1.price, s2.price) : Math.min(s1.price, s2.price) - h.price;
      if (headOver < 0.5 * atr) continue;
      if (Math.abs(s1.price - s2.price) > 1.2 * atr + 0.01 * h.price) continue;
      const nl = lineAt(n1, n2), height = sgn * (h.price - nl(h.index));
      if (height < 3 * atr) continue;
      // shoulders should sit roughly as far from the head in time (one at most 3x the other)
      const l = h.index - s1.index, r = s2.index - h.index;
      if (Math.max(l, r) > 3 * Math.min(l, r)) continue;
      const dir = -sgn;
      const brokeAt = firstClose(c, s2.index, nl, dir);
      const invAt = firstClose(c, s2.index, () => h.price, sgn);
      const dead = invAt != null;   // before or after a break: back past the tops/bottoms means it failed
      out.push({ kind: top ? "hs" : "ihs", name: top ? "Head & shoulders" : "Inverse head & shoulders", bull: !top,
        points: [s1, n1, h, n2, s2], neck: [n1, { index: last, price: nl(last) }],
        trigger: nl(last), target: nl(last) + dir * height, invalid: h.price, start: s1.index, end: s2.index,
        status: brokeAt == null ? "forming" : top ? "breakdown" : "breakout", brokeAt, dead,
        score: height / atr + 3 - Math.abs(s1.price - s2.price) / atr + (isLatest(s2) ? 3 : 0) });
    }
  }

  // --- triangles and wedges: last 2 swing highs and last 2 swing lows, interleaved, lines converging
  // confirmed swings only: the unfinished one at the right edge is just where price is now
  const Zc = Z.filter(p => p.index <= last - swing);
  const H = Zc.filter(p => p.hi).slice(-2), Lw = Zc.filter(p => !p.hi).slice(-2);
  if (H.length === 2 && Lw.length === 2) {
    const [h1, h2] = H, [l1, l2] = Lw;
    const start = Math.min(h1.index, l1.index), end = Math.max(h2.index, l2.index);
    const interleaved = Math.max(h1.index, l1.index) < Math.min(h2.index, l2.index);
    const up = lineAt(h1, h2), lo = lineAt(l1, l2);
    const su = (h2.price - h1.price) / (h2.index - h1.index) / atr, sl = (l2.price - l1.price) / (l2.index - l1.index) / atr;   // ATRs per candle
    const w0 = up(start) - lo(start), w1 = up(last) - lo(last);
    const flat = 0.02;
    const spread = Math.min(h2.index - h1.index, l2.index - l1.index) >= 0.35 * (end - start);   // both lines really span the pattern
    if (interleaved && spread && end - start >= 12 && w0 >= 3 * atr && w1 > 0.3 * atr && w1 < 0.75 * w0) {
      let kind = null, name, bull = null;
      if (Math.abs(su) <= flat && sl > flat) { kind = "atri"; name = "Ascending triangle"; bull = true; }
      else if (Math.abs(sl) <= flat && su < -flat) { kind = "dtri"; name = "Descending triangle"; bull = false; }
      else if (su < -flat && sl > flat) { kind = "stri"; name = "Symmetrical triangle"; bull = null; }
      else if (su > flat && sl > flat && sl > su) { kind = "rwedge"; name = "Rising wedge"; bull = false; }
      else if (su < -flat && sl < -flat && su < sl) { kind = "fwedge"; name = "Falling wedge"; bull = true; }
      if (kind) {
        const upAt = firstClose(c, end, up, 1), dnAt = firstClose(c, end, lo, -1);
        const brokeAt = upAt == null ? dnAt : dnAt == null ? upAt : Math.min(upAt, dnAt);
        const brokeUp = brokeAt != null && brokeAt === upAt;
        // apex (where the lines meet) already passed = it petered out
        const apex = (su - sl) !== 0 ? start + (w0 / atr) / (sl - su) : Infinity;
        // petered out (apex passed with no break), or broke against its lean (a rising wedge breaking up is just an uptrend)
        const failed = brokeAt != null && firstClose(c, brokeAt, brokeUp ? lo : up, brokeUp ? -1 : 1) != null;   // came back through the other line
        const dead = (brokeAt == null && apex < last) || (brokeAt != null && bull !== null && brokeUp !== bull) || failed;
        // a break against the pattern's lean still counts (it's what happened), the target follows the break
        const dir = brokeAt != null ? (brokeUp ? 1 : -1) : bull === false ? -1 : 1;
        out.push({ kind, name, bull, points: [h1, h2, l1, l2], neck: null,
          upper: [h1, { index: last, price: up(last) }], lower: [l1, { index: last, price: lo(last) }],
          trigger: bull === false ? lo(last) : up(last), triggerLo: lo(last), triggerHi: up(last),
          target: (dir > 0 ? up(last) : lo(last)) + dir * w0, invalid: bull === false ? up(last) : bull ? lo(last) : null,
          apex, start, end, status: brokeAt == null ? "forming" : brokeUp ? "breakout" : "breakdown", brokeAt, dead,
          score: 0.7 * w0 / atr + (end - start) / 30 });
      }
    }
  }

  // --- flags: a sharp pole, then a short tight channel drifting against it
  for (const f of [flag(c, atr, false, fresh), flag(c, atr, true, fresh)]) if (f) out.push(f);

  // best first; drop overlapping weaker ones (one pattern per stretch of chart)
  const keep = [];
  for (const p of out.filter(alive).sort((a, b) => b.score - a.score)) {
    if (keep.some(q => p.start < q.end && q.start < p.end)) continue;
    keep.push(p);
    if (keep.length >= max) break;
  }
  return keep;
}

// Bull flag (or bear flag with bear = true, found by mirroring the candles upside down).
// Pole: at least 4 ATR in at most 15 candles, steep (>= 0.5 ATR a candle), ending at the flag's top.
// Flag: 3 to 25 candles after it, giving back at most half the pole, flat or drifting against the pole,
// held between two parallel lines (regression of the flag's highs and lows, pushed out to the extremes).
// Breakout = a close past the flag's outer line; it fails if price gives back more than 62% of the pole.
function flag(c0, atr, bear, fresh = 6) {
  const mir = v => bear ? -v : v;
  const c = bear ? c0.map(b => ({ high: -b.low, low: -b.high, close: -b.close })) : c0;
  const n = c.length, last = n - 1;
  let best = null;
  for (let p1 = last - 3; p1 >= Math.max(16, last - 40); p1--) {
    // p1 = the pole's top: the highest high from 15 candles before it up to the flag's end
    let p0 = p1;
    for (let i = p1 - 1; i >= p1 - 15; i--) if (c[i].low < c[p0].low) p0 = i;
    if (p0 === p1) continue;
    // the pole starts where the climb starts: the latest candle still near the bottom
    const floor = c[p0].low + 0.1 * (c[p1].high - c[p0].low);
    for (let i = p0 + 1; i < p1; i++) if (c[i].low <= floor) p0 = i;
    let topOk = true;
    for (let i = p1 - 15; i < p1 && topOk; i++) if (c[i].high > c[p1].high) topOk = false;
    if (!topOk) continue;
    const pole = c[p1].high - c[p0].low, len = p1 - p0;
    if (pole < 4 * atr || pole / len < 0.5 * atr) continue;
    // the flag: candles after the top until a close above its top line (or the latest candle)
    const fit = (a, b, get) => {         // least squares through get(i), i = a..b
      const m = b - a + 1; let sx = 0, sy = 0, sxx = 0, sxy = 0;
      for (let i = a; i <= b; i++) { const x = i - a, y = get(i); sx += x; sy += y; sxx += x * x; sxy += x * y; }
      const slope = (m * sxy - sx * sy) / (m * sxx - sx * sx || 1);
      return { slope, icpt: (sy - slope * sx) / m };
    };
    let e = last, brokeAt = null;
    // grow the flag one candle at a time; stop when a close breaks above the channel built so far
    for (let i = p1 + 4; i <= last; i++) {
      const f = fit(p1 + 1, i - 1, k => (c[k].high + c[k].low) / 2);
      let up = -Infinity;
      for (let k = p1; k < i; k++) up = Math.max(up, c[k].high - (f.icpt + f.slope * (k - p1 - 1)));
      if (c[i].close > f.icpt + Math.min(0, f.slope) * (i - p1 - 1) + up + 0.2 * atr) { brokeAt = i; e = i - 1; break; }   // a clear close above the top line
    }
    if (brokeAt != null && last - brokeAt > fresh) continue;   // an old breakout: not this flag
    const fl = e - p1;
    if (fl < 3 || fl > Math.max(25, 3 * len)) continue;
    let lo = Infinity, hi = -Infinity;
    for (let i = p1 + 1; i <= e; i++) { lo = Math.min(lo, c[i].low); hi = Math.max(hi, c[i].high); }
    if (c[p1].high - lo > 0.5 * pole) continue;                 // gave back too much: not a flag
    if (hi > c[p1].high) continue;
    const f = fit(p1 + 1, e, k => (c[k].high + c[k].low) / 2);
    if (f.slope > 0.15 * atr) continue;                         // a flag drifts flat or against the pole
    let upOff = -Infinity, loOff = Infinity;
    for (let i = p1; i <= e; i++) { const m = f.icpt + f.slope * (i - p1 - 1); upOff = Math.max(upOff, c[i].high - m); if (i > p1) loOff = Math.min(loOff, c[i].low - m); }
    const L = i => f.icpt + f.slope * (i - p1 - 1);
    const width = upOff - loOff;
    if (width > 0.6 * pole) continue;
    // failed: closed back down more than 62% into the pole
    let dead = false;
    for (let i = p1 + 1; i <= last; i++) if (c[i].close < c[p1].high - 0.62 * pole) { dead = true; break; }
    const score = pole / atr + 2 - width / pole * 3 + (brokeAt == null ? 1 : 0);
    if (best && best.score >= score) continue;
    const pt = (i, v) => ({ index: i, price: mir(v) });
    const upEnd = L(last) + upOff, loEnd = L(last) + loOff;
    best = { kind: bear ? "sflag" : "bflag", name: bear ? "Bear flag" : "Bull flag", bull: !bear,
      points: [pt(p0, c[p0].low), pt(p1, c[p1].high)], neck: null, pole: [pt(p0, c[p0].low), pt(p1, c[p1].high)],
      upper: bear ? [pt(p1, L(p1) + loOff), pt(last, loEnd)] : [pt(p1, L(p1) + upOff), pt(last, upEnd)],
      lower: bear ? [pt(p1, L(p1) + upOff), pt(last, upEnd)] : [pt(p1, L(p1) + loOff), pt(last, loEnd)],
      trigger: mir(upEnd), triggerLo: Math.min(mir(upEnd), mir(loEnd)), triggerHi: Math.max(mir(upEnd), mir(loEnd)),
      target: mir((brokeAt != null ? L(brokeAt) + upOff : upEnd) + pole), invalid: mir(c[p1].high - 0.62 * pole),
      apex: Infinity, start: p0, end: e, status: brokeAt == null ? "forming" : bear ? "breakdown" : "breakout", brokeAt, dead, score };
  }
  return best;
}

// ------------------------------------------------------------------ major support / resistance
// Levels from the timeframe on screen AND the bigger ones above it (a weekly level outranks an hourly
// one). Nearby levels from different timeframes merge into one; each gets a strength score from its
// touches, its timeframes and how recently it was respected. Only the strongest `k` each side are kept.
const TF_W = { W: 4, D: 3, "4H": 2, "1H": 1.5, "30m": 1, "15m": 1, "5m": 1, "1m": 1 };
export function majorLevels(frames, tf, { k = 2, swing = 5, range = 0.25, tfs: only = null } = {}) {
  const c = frames[tf];
  const res = { support: [], resistance: [] };
  if (!c?.length) return res;
  const close = c[c.length - 1].close;
  const at = ORDER.indexOf(tf);
  const tfs = only ? only.filter(t => frames[t]?.length > 30)      // a chosen set (a trade's levels use every bigger timeframe)
    : (at < 0 ? ["D", "W"] : ORDER.slice(0, at + 1)).filter(t => frames[t]?.length > 30).slice(-3);   // this one + the next two bigger ones
  if (!tfs.includes(tf) && c.length > 30) tfs.push(tf);
  const cand = [];
  for (const t of tfs) {
    const cc = frames[t], lb = TFS[t]?.lookback || 300;
    const r = srLevels(cc, lb, swing, { minTouches: 2 });
    const age = cc.length - 1;
    for (const l of [...r.support, ...r.resistance]) {
      const rec = 1 - Math.min(1, (age - l.last) / lb);   // 1 = touched on the latest candle
      cand.push({ price: l.price, touches: l.touches, tfs: [t], w: TF_W[t] || 1, score: l.touches * (TF_W[t] || 1) * (0.6 + 0.4 * rec) });
    }
  }
  // merge across timeframes: within a zone of ~1 ATR of the timeframe on screen (at least 0.4% of price)
  const zone = Math.max(atrOf(c, Math.max(0, c.length - 200)) * 1.0, close * 0.004);
  cand.sort((a, b) => a.price - b.price);
  const merged = [];
  for (const x of cand) {
    const m = merged[merged.length - 1];
    if (m && x.price - m.hi <= zone) {
      m.items.push(x); m.hi = x.price;
    } else merged.push({ items: [x], hi: x.price });
  }
  const levels = merged.map(({ items }) => {
    const wsum = items.reduce((s, x) => s + x.w, 0);
    const price = items.reduce((s, x) => s + x.price * x.w, 0) / wsum;
    const tfSet = [...new Set(items.flatMap(x => x.tfs))].sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b));
    return { price, touches: items.reduce((s, x) => s + x.touches, 0), tfs: tfSet,
      score: items.reduce((s, x) => s + x.score, 0) * (1 + 0.3 * (tfSet.length - 1)), lo: items[0].price, hi: items[items.length - 1].price };
  }).filter(l => Math.abs(l.price / close - 1) <= range);
  for (const l of levels) (l.price > close ? res.resistance : res.support).push(l);
  const pick = list => list.sort((a, b) => b.score - a.score).slice(0, k).sort((a, b) => Math.abs(a.price - close) - Math.abs(b.price - close));
  res.support = pick(res.support); res.resistance = pick(res.resistance);
  return res;
}

// ------------------------------------------------------------------ the levels around one trade
// What a trader could see when the trade opened: only candles that had CLOSED before the entry are used
// (no hindsight). Returns the levels that sit in the trade's path (between a little past the stop and a
// little past the target), so they're the ones that could stop it, slow it or cap it:
//   major: horizontal levels from the trade's timeframe and every bigger one (weekly, daily ...), merged
//   local: horizontal levels of the trade's own timeframe from the last ~150 candles, not already a major one
//   lines: the up and down trendlines (same rules as the Trendlines page) still intact at the entry, on the
//          trade's timeframe (local) and the next bigger one (major), as {t1, p1, slope per second, tf}
// Each level: {price, touches, tfs, kind: "major"|"local", side: "support"|"resistance" (vs the entry)}.
const STEP_S = t => TFS[t]?.step || 60;
export function tradeLevels(frames, tf, tEntry, { entry, stop, target, swing = 5, k = 3 } = {}) {
  const out = { major: [], local: [], lines: [] };
  const at = ORDER.indexOf(tf);
  if (at < 0 || !frames[tf]?.length || !entry) return out;
  const upTo = t => { const c = frames[t] || [], st = STEP_S(t); let j = c.length; while (j > 0 && c[j - 1].time + st > tEntry) j--; return c.slice(0, j); };
  const tfs = ORDER.slice(0, at + 1), cut = {};
  for (const t of tfs) cut[t] = upTo(t);
  const c = cut[tf];
  if (c.length < 40) return out;
  const atr = atrOf(c, Math.max(0, c.length - 200));
  const lo0 = Math.min(stop ?? entry, target ?? entry, entry), hi0 = Math.max(stop ?? entry, target ?? entry, entry);
  const pad = Math.max((hi0 - lo0) * 0.25, 1.5 * atr);
  const lo = lo0 - pad, hi = hi0 + pad, inPath = p => p >= lo && p <= hi;
  const side = p => p > entry ? "resistance" : "support";

  const range = Math.max(Math.abs(lo / entry - 1), Math.abs(hi / entry - 1)) + 0.01;
  const M = majorLevels(cut, tf, { k, swing, range, tfs });
  out.major = [...M.support, ...M.resistance].filter(l => inPath(l.price)).map(l => ({ ...l, kind: "major", side: side(l.price) }));

  const zone = Math.max(atr, entry * 0.004);
  const L = srLevels(c, 150, Math.max(2, swing - 2));
  const near = (list, n) => list.filter(l => inPath(l.price) && !out.major.some(m => Math.abs(m.price - l.price) <= zone)).slice(0, n);
  out.local = [...near(L.support, 2), ...near(L.resistance, 2)].map(l => ({ price: l.price, touches: l.touches, tfs: [tf], kind: "local", side: side(l.price) }));

  // trendlines intact at the entry: the trade's timeframe and the next bigger one
  for (const [t, kind] of [[tf, "local"], [ORDER[at - 1], "major"]]) {
    const cc = t && cut[t];
    if (!cc || cc.length < 40) continue;
    const r = analyze(cc, TFS[t].lookback, swing, "close"), st = STEP_S(t);
    for (const dir of ["up", "down"]) {
      const ch = r[dir], v = ch.vertices || [];
      if (ch.broken || ch.level == null || v.length < 2) continue;
      const A = v[v.length - 2], B = v[v.length - 1];
      const t1 = cc[A.index].time, slope = (B.price - A.price) / ((cc[B.index].time - t1) || st);   // price per second
      const now = A.price + slope * (tEntry - t1);
      if (!inPath(now)) continue;
      out.lines.push({ t1, p1: A.price, slope, tf: t, kind, dir, side: side(now), price: now });
    }
  }
  return out;
}
