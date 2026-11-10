"""Index Open Study: does the pre-market direction of SPY / QQQ tell you the first 30-60 minutes?

A research check, not a strategy page. For every regular session since 2020, on 5-minute bars
including the pre-market (Alpaca SIP, split/dividend adjusted):

  Gap         last pre-market price (close of the 09:25 bar, the last one before 09:30) vs
              yesterday's regular close (close of the 15:55 bar). Its sign is the "pre-market direction".
  Late trend  last pre-market price vs the 09:00 price: is the pre-market still going that way at the end?
  Open        open of the 09:30 bar.
  Windows     09:30 -> 10:00 (close of the 09:55 bar) and 09:30 -> 10:30 (close of the 10:25 bar).

Questions answered (each split by gap size, by year, 2020-2023 vs 2024 onwards, and by filter):
  1. Direction match: how often the window moves the same way as the gap.
  2. Follow trade: enter at the open in the gap's direction, exit at the window's end,
     0.01% a side (index ETFs are cheap to trade). Win rate, average, profit factor.
  3. Small move first: from the open, does price reach +X% in the gap's direction before -X% against
     it, inside the window (X = 0.10 / 0.20 / 0.30%)? A bar that touches both counts as a loss
     (cautious). This is the "I only need a small move, then I close" idea.
  Filters: none; SPY above its 20-day average (yesterday's close) and gap in the same direction
  ("with market trend"); late trend agrees with the gap; both.

    python engine/index_open_study.py            # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/index_open_study.py --demo     # synthetic bars, writes to /tmp/index-open-demo
Output: docs/data/index_open/study.json and research/index-open/REPORT.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_momentum import NY, by_day, demo_bars, fetch_bars, minute_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SYMBOLS = ["SPY", "QQQ"]
FROM = "2020-01-01"
SPLIT = "2024-01-01"                 # 2020-2023 vs 2024 onwards
COST = 0.0001                        # 0.01% a side
WINDOWS = {"30m": 9 * 60 + 55, "60m": 10 * 60 + 25}   # start minute of the last bar in each window
TOUCH = [0.001, 0.002, 0.003]
GAP_BUCKETS = [(0, 0.0025, "under 0.25%"), (0.0025, 0.005, "0.25-0.5%"),
               (0.005, 0.01, "0.5-1%"), (0.01, 9, "over 1%")]
SMA_N = 20


def sign(x: float) -> int:
    return (x > 0) - (x < 0)


def session_rows(bars: list[dict]) -> list[dict]:
    """One row per regular session: gap, late trend, moves to each window, small-move-first results."""
    days = by_day(bars)
    rows, closes, prev_close = [], [], None
    for day in sorted(days):
        ds = days[day]
        at = {minute_of(b["t"]): b for b in ds}
        last = at.get(15 * 60 + 55) or at.get(12 * 60 + 55)        # regular close (or a half day's)
        if 9 * 60 + 30 in at and prev_close is not None and day >= FROM:
            pm = [b for b in ds if 4 * 60 <= minute_of(b["t"]) < 9 * 60 + 30]
            if pm and all(m in at for m in WINDOWS.values()):
                pm_last = pm[-1]["c"]
                p0900 = next((b["c"] for b in reversed(pm) if minute_of(b["t"]) <= 9 * 60), prev_close)
                gap = pm_last / prev_close - 1
                o = at[9 * 60 + 30]["o"]
                sma = sum(closes[-SMA_N:]) / SMA_N if len(closes) >= SMA_N else None
                r = {"d": day, "gap": gap, "dir": sign(gap), "late": sign(pm_last - p0900),
                     "above_sma": None if sma is None else prev_close > sma}
                for w, end in WINDOWS.items():
                    r["mv_" + w] = at[end]["c"] / o - 1
                    win = [b for b in ds if 9 * 60 + 30 <= minute_of(b["t"]) <= end]
                    for x in TOUCH:
                        r[f"t{x}_{w}"] = first_touch(win, o, r["dir"], x)
                rows.append(r)
        if last:
            prev_close = last["c"]
            closes.append(last["c"])
    return rows


def first_touch(win: list[dict], o: float, d: int, x: float) -> int | None:
    """+1 if price reaches x in direction d before -x against it, -1 if the other way, None if neither."""
    if d == 0:
        return None
    good, bad = o * (1 + d * x), o * (1 - d * x)
    for b in win:
        hit_good = b["h"] >= good if d > 0 else b["l"] <= good
        hit_bad = b["l"] <= bad if d > 0 else b["h"] >= bad
        if hit_bad:
            return -1                      # both in one bar: cautious, counted as a loss
        if hit_good:
            return 1
    return None


def summarise(rows: list[dict]) -> dict:
    rows = [r for r in rows if r["dir"] != 0]
    out = {"days": len(rows)}
    if not rows:
        return out
    for w in WINDOWS:
        mv = [r["mv_" + w] for r in rows]
        same = sum(sign(m) == r["dir"] for m, r in zip(mv, rows))
        tr = [r["dir"] * m - 2 * COST for m, r in zip(mv, rows)]
        gains, losses = sum(t for t in tr if t > 0), -sum(t for t in tr if t < 0)
        eq = 1.0
        for t in tr:
            eq *= 1 + t
        o = {"match": round(100 * same / len(rows), 1),
             "trade_win": round(100 * sum(t > 0 for t in tr) / len(tr), 1),
             "trade_avg": round(100 * sum(tr) / len(tr), 3),
             "pf": round(gains / losses, 2) if losses else None,
             "total": round(100 * (eq - 1), 1)}
        for x in TOUCH:
            res = [r[f"t{x}_{w}"] for r in rows]
            hit = sum(v == 1 for v in res)
            miss = sum(v == -1 for v in res)
            o[f"first_{x * 100:.1f}"] = {"good_first": round(100 * hit / len(rows), 1),
                                         "bad_first": round(100 * miss / len(rows), 1),
                                         "neither": round(100 * (len(rows) - hit - miss) / len(rows), 1)}
        out[w] = o
    return out


def study(rows: list[dict], spy_sma: dict[str, bool | None]) -> dict:
    filters = {
        "none": lambda r: True,
        "with_market_trend": lambda r: spy_sma.get(r["d"]) is not None
                                       and r["dir"] == (1 if spy_sma[r["d"]] else -1),
        "late_agrees": lambda r: r["late"] == r["dir"],
        "both": lambda r: spy_sma.get(r["d"]) is not None
                          and r["dir"] == (1 if spy_sma[r["d"]] else -1) and r["late"] == r["dir"],
    }
    res = {}
    for fname, f in filters.items():
        sub = [r for r in rows if f(r)]
        res[fname] = {
            "all": summarise(sub),
            "up_gaps": summarise([r for r in sub if r["dir"] > 0]),
            "down_gaps": summarise([r for r in sub if r["dir"] < 0]),
            "dev_2020_2023": summarise([r for r in sub if r["d"] < SPLIT]),
            "test_2024_on": summarise([r for r in sub if r["d"] >= SPLIT]),
            "by_gap": {lab: summarise([r for r in sub if lo <= abs(r["gap"]) < hi])
                       for lo, hi, lab in GAP_BUCKETS},
            "by_year": {y: summarise([r for r in sub if r["d"][:4] == y])
                        for y in sorted({r["d"][:4] for r in sub})},
        }
    return res


def report(out: dict) -> str:
    L = [f"# Index Open Study ({out['generated'][:10]})", "",
         "Does the pre-market direction (last pre-market price vs yesterday's close) tell you which way "
         "SPY / QQQ go in the first 30 or 60 minutes after the 09:30 open? 5-minute SIP bars since "
         f"{FROM}. Follow trade = enter at the open in the gap's direction, exit at 10:00 / 10:30, "
         "0.01% a side. Small move first = from the open, +X% in the gap's direction reached before -X% "
         "against it (a bar touching both counts as a loss). 50% is a coin flip.", ""]
    for sym, res in out["symbols"].items():
        L += [f"## {sym}", ""]
        L += ["| Filter | Days | Match 30m | Match 60m | Follow 60m win / avg / PF | "
              "+0.1% first (60m) | +0.2% first | +0.3% first |", "|---|---|---|---|---|---|---|---|"]
        for f, block in res.items():
            a = block["all"]
            if not a.get("days"):
                continue
            s = a["60m"]
            ft = lambda x: f"{s[x]['good_first']}% vs {s[x]['bad_first']}%"
            L.append(f"| {f} | {a['days']} | {a['30m']['match']}% | {s['match']}% | "
                     f"{s['trade_win']}% / {s['trade_avg']:+.3f}% / {s['pf']} | "
                     f"{ft('first_0.1')} | {ft('first_0.2')} | {ft('first_0.3')} |")
        L += ["", "By gap size, no filter:", "", "| Gap | Days | Match 30m | Match 60m | Follow 60m avg |",
              "|---|---|---|---|---|"]
        for lab, s in res["none"]["by_gap"].items():
            if s.get("days"):
                L.append(f"| {lab} | {s['days']} | {s['30m']['match']}% | {s['60m']['match']}% | "
                         f"{s['60m']['trade_avg']:+.3f}% |")
        L += ["", "Dev vs test, match 60m:", ""]
        for f, block in res.items():
            d, t = block["dev_2020_2023"], block["test_2024_on"]
            if d.get("days") and t.get("days"):
                L.append(f"- {f}: 2020-2023 {d['60m']['match']}% ({d['days']} days), "
                         f"2024 on {t['60m']['match']}% ({t['days']} days)")
        L.append("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    if a.demo:
        bars = {s: demo_bars(1600, seed=i + 3, px=300.0 + 100 * i) for i, s in enumerate(SYMBOLS)}
        out_json = Path("/tmp/index-open-demo/study.json")
        out_md = Path("/tmp/index-open-demo/REPORT.md")
    else:
        start = datetime(2019, 11, 1, tzinfo=NY)
        bars = fetch_bars(SYMBOLS, start, datetime.now(timezone.utc) - timedelta(minutes=16), "sip")  # free SIP: 15+ min old
        out_json = ROOT / "docs" / "data" / "index_open" / "study.json"
        out_md = ROOT / "research" / "index-open" / "REPORT.md"
    rows = {s: session_rows(bars[s]) for s in SYMBOLS}
    spy_sma = {r["d"]: r["above_sma"] for r in rows["SPY"]}
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "from": FROM,
           "to": max(r["d"] for r in rows["SPY"]), "cost_pct_side": COST * 100,
           "symbols": {s: study(rows[s], spy_sma) for s in SYMBOLS}}
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(out, indent=1))
    out_md.write_text(report(out))
    print(report(out))


if __name__ == "__main__":
    main()
