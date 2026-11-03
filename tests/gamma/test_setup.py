"""Checks for engine/gamma_setup.py: a crafted rejection opens a paper trade, later bars close it.

    python tests/gamma/test_setup.py
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine"))
import gamma_setup as G  # noqa: E402

NY = G.NY


def day(d, n, px=100.0, v=1000):
    out = []
    for k in range(n):
        t = datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY) + timedelta(minutes=5 * k)
        out.append({"t": t.isoformat(timespec="minutes"), "o": px, "h": px + .05, "l": px - .05, "c": px, "v": v})
    return out


def run():
    d0, d1 = datetime(2026, 11, 3).date(), datetime(2026, 11, 4).date()
    prev = day(d0, 78)
    prev[40]["h"] = 101.0                                   # prior high 101.00
    today = day(d1, 30)
    today[-1].update(o=100.6, h=101.02, l=100.4, c=100.5, v=5000)   # pokes 101, closes low, 5x volume
    data = {"SPY": prev + today, "QQQ": [], "IWM": []}
    last_end = datetime.fromisoformat(today[-1]["t"]) + timedelta(minutes=5)
    out = Path("/tmp/gamma-test/live.json")
    out.unlink(missing_ok=True)

    G.fetch_bars = lambda *a, **k: data
    G.latest_prices = lambda s: {"SPY": 100.5}
    G.gex_today = lambda d: {"SPY": {"flip": 102.0, "call_wall": 105.0, "put_wall": 99.0, "magnets": [],
                                     "call_premium": 1.0, "put_premium": 2.0}}
    G.DATA_DIR = out.parent
    G.START = "2026-01-01"
    G.notify = lambda t: None

    G.live((last_end + timedelta(minutes=1)).astimezone(timezone.utc), demo=False, force=True)
    b = json.loads(out.read_text())
    assert len(b["trades"]) == 1, b
    t = b["trades"][0]
    assert t["side"] == "short" and t["level_name"] == "prior high", t
    assert t["filters"] == {"gamma": True, "premium": True}, t["filters"]
    assert t["target"] == 99.0 and t["target_name"] == "put wall", t   # risk ~0.55 -> 99 pays 2.7R
    print("opened:", t["side"], t["entry"], "stop", t["stop"], "target", t["target"], t["rr"], "R")

    # price falls to the put wall on the next bars -> closed at target
    nxt = day(d1, 33)[30:]
    for k, b2 in enumerate(nxt):
        b2.update(o=100.4 - k * .6, h=100.45 - k * .6, l=99.9 - k * .6, c=100.0 - k * .6)
    data["SPY"] = prev + today + nxt
    G.live((datetime.fromisoformat(nxt[-1]["t"]) + timedelta(minutes=6)).astimezone(timezone.utc), demo=False, force=True)
    t = json.loads(out.read_text())["trades"][0]
    assert t["exit"]["reason"] == "target" and t["r"] > 2, t
    print("closed:", t["exit"], t["r"], "R")

    # a late run logs it as missed instead of trading it
    out.unlink()
    data["SPY"] = prev + today
    G.live((last_end + timedelta(minutes=30)).astimezone(timezone.utc), demo=False, force=True)
    b = json.loads(out.read_text())
    assert not b["trades"] and b["missed"][0]["why"].startswith("found late"), b
    print("late run -> missed: ok")


if __name__ == "__main__":
    run()
    print("all good")
