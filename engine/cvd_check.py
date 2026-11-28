"""Temporary diagnostic: is tick-rule CVD sane? (branch cvd-check only)"""
import sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import vwap_cvd as V

def key(t):
    a, _, f = t["t"].rstrip("Z").partition(".")
    return a + "." + f.ljust(9, "0")

def run(raw, keep, label):
    last, side, dm, px = None, 0, collections.defaultdict(float), {}
    big = []
    for t in raw:
        if V.SKIP_COND & set(t.get("c") or []) or not keep(t):
            continue
        p, s = t["p"], t["s"]
        if last is not None:
            if p > last: side = 1
            elif p < last: side = -1
        last = p
        m = V.minute(t["t"])[1]
        if side: dm[m] += side * s
        px.setdefault(m, [p, p])[1] = p
        big.append((s, side, t["t"][11:23], p, t.get("x"), t.get("c")))
    ms = sorted(px)
    chg = [px[m][1] - px[m][0] for m in ms]; dl = [dm[m] for m in ms]
    n = len(ms); mc = sum(chg)/n; md = sum(dl)/n
    cov = sum((a-mc)*(b-md) for a, b in zip(chg, dl))
    sa = sum((a-mc)**2 for a in chg) ** .5; sb = sum((b-md)**2 for b in dl) ** .5
    print(f" {label:10s} delta {sum(dl):>12.0f} corr {cov/(sa*sb):.2f}")
    return big

for sym, d in (("AAPL", "2026-11-20"), ("NVDA", "2026-11-20"), ("TSLA", "2026-10-15"), ("MSFT", "2026-11-12")):
    raw = V.paged("trades", "trades", {"symbols": sym, "start": V.utc(d, "09:30"), "end": V.utc(d, "10:30"),
                                       "limit": "10000", "feed": "sip"})[sym]
    raw.sort(key=key)
    print(sym, d, len(raw))
    big = run(raw, lambda t: True, "all")
    run(raw, lambda t: t.get("x") != "D", "lit")
    run(raw, lambda t: "I" not in (t.get("c") or []), "no-odd")
    run(raw, lambda t: t.get("x") != "D" and "I" not in (t.get("c") or []), "lit-round")
    run(raw, lambda t: t["s"] < 5000, "no-blocks")
    for b in sorted(big, reverse=True)[:6]:
        print("   big", b)
