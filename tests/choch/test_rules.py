"""Checks of the 4H CHoCH rules on a hand-made chart: python tests/choch/test_rules.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))
import choch as C


def bars(closes):
    b = {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        b["t"].append(f"2024-01-{1 + i // 6:02d}T{(i % 6) * 4:02d}:00")
        b["o"].append(o); b["c"].append(c)
        b["h"].append(max(o, c) + 0.2); b["l"].append(min(o, c) - 0.2); b["v"].append(100 + i)
        prev = c
    return b


# down: lower highs / lower lows, then a strong rally through the last lower high, a pullback, then up
path = [100, 101, 102, 101, 99, 97, 96, 97, 99, 98, 95, 92, 91, 92, 94, 93, 90, 87, 86, 87, 89, 88, 86,
        84, 83, 85, 88, 92, 96, 99, 97, 95, 94, 96, 99, 102, 104, 106, 108, 110, 109, 108]
b = bars(path)
sh, sl = C.pivots(b, 2)
evs = C.events(b, 2, sh, sl)
bull = [e for e in evs if e["side"] > 0]
assert bull, "the rally should be a bullish CHoCH"
e = bull[0]
assert b["c"][e["k"]] > e["level"], "CHoCH candle closes above the broken lower high"
assert e["k"] - 2 >= e["piv"], "the broken swing was known before the break (no look-ahead)"
assert e["ref"] == min(b["l"][e["piv"]:e["k"] + 1]), "stop reference is the leg's low"
lo, hi = e["zone"]
assert e["ref"] <= lo < hi <= b["c"][e["k"]], "zone inside the leg"
t = C.simulate(b, e, "close", "2r", sh, sl, 2, 0.0)
assert t and t["entry"] == b["c"][e["k"]] and t["stop0"] < e["ref"]
if "out" in t:
    assert t["why"] in ("target", "stop", "time")
    if t["why"] == "target":
        assert abs(t["r"] - 2) < 1e-9, t["r"]
# no future candle changes past events (look-ahead check)
for cut in range(e["k"] + 1, len(path)):
    sh2, sl2 = C.pivots({k: v[:cut] for k, v in b.items()}, 2)
    evs2 = C.events({k: v[:cut] for k, v in b.items()}, 2, sh2, sl2)
    assert any(x["k"] == e["k"] and x["side"] == 1 for x in evs2)
# 4H candles from 30-minute bars: 09:30 and 13:30 New York
rows = [(f"2024-03-01T{h:02d}:{m:02d}-05:00", 1, 2, 0.5, 1.5, 10) for h in range(9, 16) for m in (0, 30) if (9 * 60 + 30) <= h * 60 + m < 960]
four = C.to_4h(rows)
assert four["t"] == ["2024-03-01T09:30", "2024-03-01T13:30"] and four["v"] == [80, 50], four
print("choch rules: ok")
