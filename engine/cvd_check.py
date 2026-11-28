"""Temporary diagnostic of the engine path (branch cvd-check only)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import vwap_cvd as V

def corr(a, b):
    n = len(a); ma = sum(a)/n; mb = sum(b)/n
    c = sum((x-ma)*(y-mb) for x, y in zip(a, b))
    return c / ((sum((x-ma)**2 for x in a) * sum((y-mb)**2 for y in b)) ** .5)

for sym, d in (("AAPL", "2026-11-20"), ("NVDA", "2026-11-20"), ("TSLA", "2026-10-15"), ("MSFT", "2026-11-12"), ("META", "2026-09-10")):
    bars = V.day_bars([sym], d)[sym]
    dl = V.day_cvd(sym, d)
    B = [b for b in V.five_min(bars, dl) if b["end"] <= 720]
    dp = [B[i]["c"] - B[i-1]["c"] for i in range(1, len(B))]
    dc = [B[i]["cvd"] - B[i-1]["cvd"] for i in range(1, len(B))]
    s3p = [B[i]["c"] - B[i-3]["c"] for i in range(3, len(B))]
    s3c = [B[i]["cvd"] - B[i-3]["cvd"] for i in range(3, len(B))]
    print(sym, d, "5m corr", round(corr(dp, dc), 2), "3-bar corr", round(corr(s3p, s3c), 2),
          "3-bar cvd up share", round(sum(x > 0 for x in s3c) / len(s3c), 2))
    for b in B[:8]:
        print("   ", b["end"], round(b["c"], 2), round(b["vwap"], 2), int(b["cvd"]))
