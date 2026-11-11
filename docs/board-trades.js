// Strategy Board: the trades behind one tile (a day, or a week in the "since 2021" view).
// The board's Lab rows are Lab.run(data, defaults(ind), fi).long, the same as engine/day_board.mjs.
// Lab.traceRun() is that run plus every position the account held, with the daily returns that
// make its curve, so the positions listed here are exactly the ones behind the tile and their
// shares of the account add up to it. Test: tests/board/test_trades.mjs
//
//   tileTrades(Lab, data, cfg, fi, gA, gB[, R]) -> { trades, acct, sum }
//   gA..gB: first and last session of the tile (indexes into data.dates); R: a cached traceRun()

export function tileTrades(Lab, data, cfg, fi, gA, gB, R = null) {
  R = R || Lab.traceRun(data, cfg, fi);
  const mine = R.long, W = R.weights || {}, eq = mine.eq, D = data.dates.length;
  const byT = new Map(data.tickers.map(T => [T.t, T]));
  const price = (t, g) => { const T = byT.get(t); if (!T) return null; const i = T.idx.indexOf(g); return i < 0 ? null : T.c[i]; };
  const out = [];
  for (const P of R.trace || []) {
    const last = P.g.length ? P.g[P.g.length - 1] : P.g0;
    const isOpen = !(P.gE < Infinity);
    if (P.g0 > gB || (P.g.length ? last < gA : P.g0 < gA)) continue;
    if (P.g0 < fi && last <= fi) continue;
    let share = 0, move = 1, counted = false, total = 1;
    // fixed-money positions (Average down with a limit): the position's value the day before ÷ the account's
    let val = P.size || 0;
    for (let m = 0; m < P.g.length; m++) {
      const g = P.g[m], r = P.r[m];
      if (g > fi) total *= 1 + r;
      if (g >= gA && g <= gB && g > fi && eq[g - 1] > 0) {
        const w = W.avg ? val / eq[g - 1] : W.risk ? P.w : 1 / (W.slots || mine.held[g] || 1);
        share += r * w; move *= 1 + r; counted = true;
      }
      val *= 1 + r;
    }
    const bought = P.g0 >= gA && P.g0 <= gB, sold = !isOpen && P.gE >= gA && P.gE <= gB;
    if (!counted && !bought) continue;
    const sellAt = isOpen ? null : P.gE < D ? P.gE : last;
    out.push({
      t: P.t, d0: data.dates[P.g0], d1: sellAt == null ? null : data.dates[sellAt],
      p0: P.p0 ?? price(P.t, P.g0), p1: isOpen ? price(P.t, D - 1) : (P.p1 ?? price(P.t, sellAt)),
      ret: total - 1, open: isOpen, bought, sold, before: P.g0 < fi,
      move: counted ? move - 1 : null, share: counted ? share : null,
    });
  }
  let acct = 1;
  for (let g = Math.max(gA, fi + 1); g <= gB; g++) if (eq[g - 1] > 0) acct *= eq[g] / eq[g - 1];
  out.sort((a, b) => (b.share ?? -1e9) - (a.share ?? -1e9) || a.t.localeCompare(b.t));
  return { trades: out, acct: acct - 1, sum: out.reduce((s, x) => s + (x.share || 0), 0) };
}

// ------------------------------------------------------------ the trades bought on a tile's day(s)
// The board's tiles show each trade on the day it was bought, coloured by how the whole trade did
// (closed: its result; still open: its result so far), not by the days it was only being held.
// The same functions build the tiles (engine/day_board.mjs) and the list behind a tile (board.html),
// so the two always agree. Test: tests/board/test_buys.mjs
//
//   tradeOf(data, P)              -> one trade { t, d0, d1, p0, p1, ret, open, n } from a traceRun() position
//   buysIn(data, R, gA, gB)       -> the trades bought from session gA to gB
//   freshIndex(data, R)           -> a lookup of a newer run's trades by ticker and buy date
//   freshen(tr, recent, idx)      -> a trade still open in the history, followed on to its exit (or today)
//                                    in the newer prices (lab_recent.json), when the newer run has it too
//   allBuys(hist, Rh, fi, recent, Rr) -> every trade bought after session fi, fresh, oldest first

function priceAt(T, g) {      // the ticker's close on session g, or its last close before it
  let lo = 0, hi = T.idx.length - 1, k = -1;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (T.idx[m] <= g) { k = m; lo = m + 1; } else hi = m - 1; }
  return k < 0 ? null : { p: T.c[k], g: T.idx[k] };
}
const tickerMap = data => data._byT ||= new Map(data.tickers.map(T => [T.t, T]));

export function tradeOf(data, P) {
  const D = data.dates.length, T = tickerMap(data).get(P.t);
  let ret = 1; for (const r of P.r) ret *= 1 + r;
  const lastG = P.g.length ? P.g[P.g.length - 1] : P.g0;
  const endPx = T ? priceAt(T, D - 1) : null;
  // still open: no exit yet and the stock still trades (one that left the index is closed at its last price)
  const open = !(P.gE < Infinity) && !!endPx && endPx.g === D - 1;
  const gOut = open ? D - 1 : P.gE < Infinity ? Math.min(P.gE, D - 1) : lastG;
  const p1 = open ? endPx.p : (P.p1 ?? (T ? priceAt(T, gOut)?.p : null));
  return { t: P.t, d0: data.dates[P.g0], d1: open ? null : data.dates[gOut], p0: P.p0 ?? (T ? priceAt(T, P.g0)?.p : null), p1,
    ret: ret - 1, open, n: gOut - P.g0 };
}

export function buysIn(data, R, gA, gB) {
  return (R.trace || []).filter(P => P.g0 >= gA && P.g0 <= gB).map(P => tradeOf(data, P));
}

export function freshIndex(data, R) {
  const m = new Map();
  for (const P of R?.trace || []) m.set(P.t + "|" + data.dates[P.g0], P);
  return m;
}

export function freshen(tr, recent, idx) {
  if (!tr.open || !recent || !idx) return tr;
  const Q = idx.get(tr.t + "|" + tr.d0);
  return Q ? tradeOf(recent, Q) : tr;
}

export function allBuys(hist, Rh, fi, recent = null, Rr = null) {
  const last = hist.dates[hist.dates.length - 1], idx = recent && Rr ? freshIndex(recent, Rr) : null;
  const out = [];
  for (const P of Rh.trace || []) if (P.g0 > fi) out.push(freshen(tradeOf(hist, P), recent, idx));
  if (idx) for (const P of Rr.trace || []) if (recent.dates[P.g0] > last) out.push(tradeOf(recent, P));
  return out.sort((a, b) => a.d0.localeCompare(b.d0) || a.t.localeCompare(b.t));
}
