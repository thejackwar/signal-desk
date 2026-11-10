"""Premarket Momentum Short: if a stock is falling in the pre-market, short it when the New York market opens.

The exact mirror of engine/premarket_momentum.py (the long strategy), asked for by Giacomo (Nov 2026):
"if the pre-market is negative, does it short it?" - this tests that, with every rule flipped.

Exact rules tested here (every one is a switch, see VARIANTS):
  Universe    S&P 500 (default) or S&P 100, current members; 20-day average dollar volume >= $10M.
  Pre-market  drop = last pre-market price (before 09:30) vs yesterday's close. Must be down at least
              0.5%, 1% (default), 2%, 3% or 5%.
  Cap         "none" (default) or "6": skip drops bigger than 6% (huge gaps are mostly earnings / big news).
  Falling     how long the pre-market has been going down:
                "any"    no condition
                "30m"    last pre-market price below the price 30 minutes earlier (09:00) (default)
                "60m"    ... below the price 60 minutes earlier (08:30) and below the pre-market VWAP
                "120m"   ... below the price 2 hours earlier (07:30) and below the pre-market VWAP
                "steady" every 15-minute checkpoint from 08:30 to 09:30 lower than the one before
              (no trade before a checkpoint = yesterday's close is used)
  PM volume   pre-market dollar volume: "any", at least $1M (default) or at least $5M.
  Daily trend as of yesterday's close: "any", "20" close < SMA20 (default), "50" close < SMA50,
              "50_200" close < SMA50 < SMA200, "up5" (here: DOWN over 5 days) close below the close 5 days earlier.
  Entry       "open": short at the 09:30 open (default); "5m": only if the first 5-minute candle closes red
              (below its open) - short at its close, 09:35.
  Stop        1%, 2% (default), 3% above the entry, or "none".
  Target      1%, 2%, 3%, 5% (default) below the entry, or "none".
  Time exit   whatever is still open is covered at 10:30 (default), 12:00 or the 15:55 close.
  Side        "short" (the strategy). "long" is the same signal traded the other way (buy the pre-market
              losers at the open, betting on a bounce), shown only as a check.
  Exit rules  stop first if a bar hits both (cautious); a gap through a level fills at the bar's open.
  Costs       0.03% a side (no borrow fee: every trade is closed the same morning).
  Account     each day up to 5 trades (the biggest pre-market drops first), 20% of the account each,
              no leverage - comparable with holding SPY.

Data: Alpaca SIP daily bars (filters) and 5-minute bars incl. pre-market, split/dividend adjusted, from 2021.
Current index members only (survivorship bias: stocks that crashed out of the index are missing, which
makes any SHORT strategy look a little worse than it was). Only days whose official open was at least 0.4%
below yesterday's close are downloaded at 5 minutes.

The backtest itself (sweeps, grids, walk-forward, trend check, costs, leverage) is premarket_momentum.main,
run with the mirrored candidates and rules below, so both sides are measured exactly the same way.

    python engine/premarket_short.py           # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/premarket_short.py --demo    # synthetic data, writes /tmp/premarket-demo.json
Output: docs/data/premarket/short/backtest.json
"""
from __future__ import annotations

import sys
from array import array
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import premarket_momentum as pm  # noqa: E402
from open_breakout import NY, fetch, ny  # noqa: E402

OPEN_MIN = pm.OPEN_MIN

# the mirror of the long default and of its first version
DEFAULT = {**pm.DEFAULT, "side": "short"}
PLAIN = {**pm.PLAIN, "side": "short"}
VARIANTS = {**{k: o for k, o in pm.VARIANTS.items() if k != "market"}, "side": ["short", "long"]}  # market filter: long side only


def build_candidates(tab: dict, daily: dict, sp100: set[str], demo=None) -> list[dict]:
    closes = {s: {b["t"][:10]: b["c"] for b in bars} for s, bars in daily.items()}
    dates = {s: [b["t"][:10] for b in bars] for s, bars in daily.items()}
    by_day: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for s, rows in tab.items():
        idx = {d: i for i, d in enumerate(dates[s])}
        for r in rows:
            if r["d"] >= pm.FROM and r["usd"] >= 10e6 and r["o"] * OPEN_MIN <= r["pc"]:
                i = idx[r["d"]]
                r["c5"] = closes[s][dates[s][i - 6]] if i >= 6 else None
                by_day[r["d"]].append((s, r))
    days = sorted(by_day)
    print(f"{sum(map(len, by_day.values()))} stock-days to check at 5 minutes over {len(days)} sessions", flush=True)
    cands = []
    for k, d in enumerate(days):
        items = by_day[d]
        if demo:
            intr = demo(d, items)
        else:
            start = datetime.fromisoformat(d + "T04:00").replace(tzinfo=NY).astimezone(timezone.utc)
            end = datetime.fromisoformat(d + "T16:00").replace(tzinfo=NY).astimezone(timezone.utc)
            syms, intr = [s for s, _ in items], {}
            for j in range(0, len(syms), 100):
                intr.update(fetch(syms[j:j + 100], "5Min", start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                  end.strftime("%Y-%m-%dT%H:%M:%SZ")))
        for s, r in items:
            c = facts(s, d, r, intr.get(s) or [])
            if c:
                c["sp100"] = s in sp100
                cands.append(c)
        if k % 100 == 0:
            print(f"  {d}: {k + 1}/{len(days)} sessions, {len(cands)} candidates", flush=True)
    return cands


def facts(s: str, d: str, r: dict, bars: list[dict]) -> dict | None:
    """Same as premarket_momentum.facts, mirrored. 'gain' holds the size of the DROP (positive), so the
    shared rules (minimum %, cap, biggest first) work unchanged."""
    pmb, today = [], []
    for b in bars:
        t = ny(b["t"])
        if t.date().isoformat() != d:
            continue
        m = t.hour * 60 + t.minute
        if 240 <= m < 570:
            pmb.append((m, b))
        elif 570 <= m < 960:
            today.append((m, b))
    if not pmb or not today or today[0][0] != 570:
        return None
    pc = r["pc"]
    last = pmb[-1][1]["c"]
    drop = 1 - last / pc
    if drop < 0.005:
        return None

    def at(minute: int) -> float:
        px = pc
        for m, b in pmb:
            if m + 5 <= minute:
                px = b["c"]
            else:
                break
        return px

    usd = sum(b["v"] * b["c"] for _, b in pmb)
    vol = sum(b["v"] for _, b in pmb)
    vwap = sum(b["v"] * (b["h"] + b["l"] + b["c"]) / 3 for _, b in pmb) / vol if vol else last
    chk = [at(m) for m in (510, 525, 540, 555, 570)]
    climb = {"any": True, "30m": last < at(540), "60m": last < at(510) and last <= vwap,
             "120m": last < at(450) and last <= vwap, "steady": all(b < a for a, b in zip(chk, chk[1:]))}
    trend = {"any": True, "20": pc < r["s20"], "50": pc < r["s50"], "50_200": pc < r["s50"] < r["s200"],
             "up5": r["c5"] is not None and pc < r["c5"]}
    ohlc = array("f")
    for m, b in today:
        if m > 955:
            break
        ohlc.extend((b["o"], b["h"], b["l"], b["c"], m))
    return {"s": s, "d": d, "pc": pc, "gain": drop, "usd": usd, "climb": climb, "trend": trend,
            "pm_high": max(b["h"] for _, b in pmb), "pm_low": min(b["l"] for _, b in pmb), "pm_last": last,
            "bars": ohlc}


def passes(c: dict, v: dict) -> bool:
    if v["univ"] == "sp100" and not c["sp100"]:
        return False
    if c["gain"] < float(v["pm"]) / 100:
        return False
    if v["cap"] != "none" and c["gain"] > float(v["cap"]) / 100:
        return False
    if not c["climb"][v["climb"]] or not c["trend"][v["trend"]]:
        return False
    if v["pmvol"] == "1m" and c["usd"] < 1e6 or v["pmvol"] == "5m" and c["usd"] < 5e6:
        return False
    if v["entry"] == "5m":
        b = c["bars"]
        if b[3] >= b[0]:                    # first candle must close red
            return False
    return True


def main() -> None:
    # run the long backtest's main() with the mirrored pieces swapped in
    pm.__doc__ = __doc__
    pm.DEFAULT, pm.PLAIN, pm.VARIANTS = DEFAULT, PLAIN, VARIANTS
    pm.build_candidates, pm.facts, pm.passes = build_candidates, facts, passes
    pm.OUT_DIR = pm.ROOT / "docs" / "data" / "premarket" / "short"
    pm.main()


if __name__ == "__main__":
    main()
