"""Temporary diagnostic: is tick-rule CVD sane? (branch cvd-check only)"""
import sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import vwap_cvd as V

for sym, d in (("AAPL", "2026-11-20"), ("NVDA", "2026-11-20"), ("TSLA", "2026-10-15")):
    raw = V.paged("trades", "trades", {"symbols": sym, "start": V.utc(d, "09:30"), "end": V.utc(d, "10:30"),
                                       "limit": "10000", "feed": "sip"})[sym]
    conds = collections.Counter(c for t in raw for c in (t.get("c") or []))
    print(sym, d, "trades", len(raw), "conds", conds.most_common(12))
    print(" first", [(t["t"][11:23], t["p"], t["s"], t.get("c"), t.get("x")) for t in raw[:8]])
    ts = [t["t"] for t in raw]
    print(" sorted?", ts == sorted(ts))
    delta = V.day_cvd.__wrapped__(sym, d) if hasattr(V.day_cvd, "__wrapped__") else None
    # recompute here with and without condition filter, plus price per minute
    for skip in (True, False):
        last, side, dm, px = None, 0, collections.defaultdict(float), {}
        zero = up = dn = 0
        for t in raw:
            if skip and V.SKIP_COND & set(t.get("c") or []):
                continue
            p, s = t["p"], t["s"]
            if last is not None:
                if p > last: side = 1; up += 1
                elif p < last: side = -1; dn += 1
                else: zero += 1
            last = p
            m = V.minute(t["t"])[1]
            if side: dm[m] += side * s
            px.setdefault(m, [p, p])[1] = p
        ms = sorted(px)
        chg = [px[m][1] - px[m][0] for m in ms]
        dl = [dm[m] for m in ms]
        n = len(ms); mc = sum(chg)/n; md = sum(dl)/n
        cov = sum((a-mc)*(b-md) for a, b in zip(chg, dl))
        sa = sum((a-mc)**2 for a in chg) ** .5; sb = sum((b-md)**2 for b in dl) ** .5
        print(f" skip={skip} upticks {up} downticks {dn} zero {zero} total delta {sum(dl):.0f} "
              f"price {px[ms[0]][0]}->{px[ms[-1]][1]} corr(min delta, min price chg) {cov/(sa*sb):.2f}")
