"""Checks for engine/session_momentum.py: a crafted day goes long above the EMA, trails the stop, exits on it.

    python tests/session/test_session.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))
import session_momentum as M  # noqa: E402


def bar(d, m, o, h, l, c):
    t = datetime(d.year, d.month, d.day, m // 60, m % 60, tzinfo=M.NY)
    return {"t": t.isoformat(timespec="minutes"), "o": o, "h": h, "l": l, "c": c, "v": 1000}


def run():
    d0, d1 = datetime(2026, 1, 5).date(), datetime(2026, 1, 6).date()
    bars = [bar(d0, m, 100, 100.05, 99.95, 100) for m in range(570, 960, 5)]
    day = [bar(d1, 570, 100, 100.6, 99.9, 100.5)]                  # first candle closes above the EMA (~100)
    px = 100.5
    for k, m in enumerate(range(575, 700, 5)):                     # a steady rise: the stop trails up
        day.append(bar(d1, m, px, px + .25, px - .05, px + .2)); px += .2
    day.append(bar(d1, 700, px, px + .05, px - 2, px - 1.9))       # a drop through the trailed stop
    day += [bar(d1, m, px - 2, px - 1.9, px - 2.1, px - 2) for m in range(705, 960, 5)]
    for b in bars + day:
        b["t"] = b["t"]
    ts = [t for t in M.run_variant(bars + day, "rth", "swing", "both") if t["date"] == str(d1)]
    assert len(ts) == 1 and ts[0]["side"] == 1, ts
    t = ts[0]
    assert t["stop0"] == 99.9 and t["stop"] > 104, t["stop"]
    assert t["exit"]["reason"] == "trail" and t["exit"]["at"].endswith("11:40-05:00"), t["exit"]
    assert t["pct"] > 0.03, t["pct"]
    f = [t for t in M.run_variant(bars + day, "rth", "fixed1r", "both") if t["date"] == str(d1)][0]
    assert f["exit"]["reason"] == "target" and abs(f["exit"]["price"] - 101.1) < 1e-6, f["exit"]
    print("ok", round(t["pct"] * 100, 2), "% trailed vs", round(f["pct"] * 100, 2), "% fixed 1R")


M.BT_FROM = "2026-01-06"
run()
