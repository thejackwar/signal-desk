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
