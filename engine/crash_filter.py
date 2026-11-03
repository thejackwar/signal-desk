"""
Smaller Crashes: hold the S&P 500 while it's above its 200-day average, otherwise T-bills.

The one idea the research engine (research/quant/) found useful: it doesn't beat holding, but it
cut the worst falls by about a third. The rule, fixed in advance:

  - every Friday close (the week's last session), compare SPY with its 200-day average
  - above: hold SPY (dividends included) from the next session
  - below: hold 3-month T-bills (^IRX) from the next session
  - each switch costs 10 bp

Runs after the daily scan and writes docs/data/crashfilter.json: today's reading, the current
position, every switch since 1994, weekly account values for the rule and for holding, the big
crashes side by side, and a version after Italian tax (26% on each sale's gain, ETF losses can't
offset ETF gains; 12.5% on T-bill interest; holding is taxed once, when sold at the end).
Sends a phone notification (ntfy topic <NTFY_PREFIX>-crash-filter) when the position changes.

  python engine/crash_filter.py           # real prices
  python engine/crash_filter.py --demo    # random prices, offline
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "crashfilter.json"
N, COST, TAX, TAX_BILLS, START = 200, 0.001, 0.26, 0.125, 10_000
CRASHES = [("Dot-com bust", "2000-03-24", "2002-10-09"), ("Financial crisis", "2007-10-09", "2009-03-09"),
           ("Covid crash", "2020-02-19", "2020-03-23"), ("2022 bear market", "2022-01-03", "2022-10-12")]


def fetch(demo: bool):
    if demo:
        rng = np.random.default_rng(3)
        idx = pd.bdate_range("1994-01-03", "2026-10-30")
        c = pd.Series(50 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, len(idx)))), index=idx)
        return c, pd.Series(3.0, index=idx)
    import yfinance as yf
    raw = yf.download(["SPY", "^IRX"], start="1993-01-01", interval="1d", auto_adjust=True, progress=False)["Close"]
    raw.index = pd.DatetimeIndex(raw.index).tz_localize(None)
    spy = raw["SPY"].dropna()
    return spy, raw["^IRX"].reindex(spy.index).ffill()


def simulate(c: pd.Series, irx: pd.Series):
    sma = c.rolling(N).mean()
    above = c > sma
    last_of_week = c.index.to_series().groupby(c.index.to_period("W-FRI")).transform("max") == c.index.to_series()
    # decided at each week's last close, held from the next session
    decided = above.where(last_of_week.values).ffill()
    pos = decided.shift(1)
    first = sma.first_valid_index()
    c, irx, pos, sma, above = (x[x.index > first] for x in (c, irx, pos, sma, above))
    pos = pos.fillna(True).astype(bool)
    r = c.pct_change().fillna(0)
    bill = (irx.fillna(0) / 100 / 252).clip(lower=0)
    switch = pos != pos.shift(1).fillna(pos.iloc[0])
    rule = np.where(pos.values, r.values, bill.values) - switch.values * COST

    # after Italian tax: a running account with a cost basis
    v, basis, interest, after = START, START, 0.0, []
    for i, d in enumerate(c.index):
        if switch.iloc[i]:
            if not pos.iloc[i]:                    # sold SPY: tax the gain since the last buy
                v -= max(0.0, v - basis) * TAX
            else:                                  # left T-bills: tax the interest earned
                v -= max(0.0, interest) * TAX_BILLS
                interest, basis = 0.0, v
        g = v * rule[i]
        if not pos.iloc[i]:
            interest += g
        v += g
        after.append(v)
    # sold at the end too, like holding: tax what's still untaxed
    after[-1] = v - (max(0.0, v - basis) * TAX if pos.iloc[-1] else max(0.0, interest) * TAX_BILLS)
    after = pd.Series(after, index=c.index)
    hold = START * (1 + r).cumprod()
    hold_after = START + (hold - START) * (1 - TAX)   # as if sold that day
    return c, sma, above, pos, switch, pd.Series(rule, index=c.index), r, after, hold_after


def stats(ret: pd.Series) -> dict:
    eq = (1 + ret).cumprod()
    yrs = len(ret) / 252
    sd = ret.std()
    return {"cagr": round(float(eq.iloc[-1] ** (1 / yrs) - 1) * 100, 2),
            "vol": round(float(sd * np.sqrt(252)) * 100, 1),
            "sharpe": round(float(ret.mean() / sd * np.sqrt(252)), 2),
            "maxdd": round(float((eq / eq.cummax() - 1).min()) * 100, 1)}


def window_dd(ret: pd.Series, a: str, b: str):
    s = ret[(ret.index >= a) & (ret.index <= b)]
    if len(s) < 5:
        return None
    eq = (1 + s).cumprod()
    return round(float((eq / eq.cummax() - 1).min()) * 100, 1)


def notify(prefix: str, pos_now: bool, close: float, sma: float):
    title = "Smaller Crashes: back in the S&P 500" if pos_now else "Smaller Crashes: moving to T-bills"
    msg = (f"SPY {close:.2f} closed the week {'above' if pos_now else 'below'} its 200-day average ({sma:.2f}). "
           f"The rule {'buys back the S&P 500' if pos_now else 'sells the S&P 500 and holds T-bills'} at the next session.")
    req = urllib.request.Request(f"https://ntfy.sh/{prefix}-crash-filter", data=msg.encode(), method="POST",
                                 headers={"Title": title, "Tags": "shield"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:  # noqa: BLE001
        print(f"  ! notification failed: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    spy, irx = fetch(args.demo)
    if len(spy) < N + 50:
        raise SystemExit("Not enough SPY data; leaving the previous file untouched.")
    c, sma, above, pos, switch, rule, r, after, hold_after = simulate(spy, irx)

    sw = [{"date": f"{d:%Y-%m-%d}", "to": "spy" if pos.loc[d] else "bills", "close": round(float(c.loc[d]), 2)}
          for d in c.index[switch.values] if d != c.index[0]]
    back_soon = sum(1 for a, b in zip(sw, sw[1:]) if a["to"] == "bills" and
                    (pd.Timestamp(b["date"]) - pd.Timestamp(a["date"])).days <= 35)
    eq_rule, eq_hold = START * (1 + rule).cumprod(), START * (1 + r).cumprod()
    wk = eq_rule.resample("W-FRI").last().dropna().index
    yrs = len(c) / 252

    # next session's position: decided at this week's last close; mid-week it can't change yet
    today, last = c.index[-1], c.index[-1]
    is_week_end = today.weekday() == 4
    next_pos = bool(above.iloc[-1]) if is_week_end else bool(pos.iloc[-1])
    prev = json.loads(OUT.read_text()) if OUT.exists() else None
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "demo": args.demo,
        "rule": "Every Friday close: SPY above its 200-day average → hold the S&P 500 from the next session; below → 3-month T-bills. 10 bp per switch.",
        "start": f"{c.index[0]:%Y-%m-%d}",
        "now": {"date": f"{last:%Y-%m-%d}", "close": round(float(c.iloc[-1]), 2), "sma200": round(float(sma.iloc[-1]), 2),
                "gap": round(float(c.iloc[-1] / sma.iloc[-1] - 1) * 100, 2), "above": bool(above.iloc[-1]),
                "position": "spy" if next_pos else "bills", "week_end": is_week_end,
                "bill_rate": round(float(irx.iloc[-1]), 2) if pd.notna(irx.iloc[-1]) else None,
                "since": sw[-1]["date"] if sw else f"{c.index[0]:%Y-%m-%d}"},
        "stats": {"rule": stats(rule), "hold": stats(r),
                  "invested": round(float(pos.mean()) * 100, 1),
                  "switches_per_year": round(len(sw) / yrs, 1), "whipsaws": back_soon, "switches": len(sw),
                  "after_tax": {"rule": round(float((after.iloc[-1] / START) ** (1 / yrs) - 1) * 100, 2),
                                "hold": round(float((hold_after.iloc[-1] / START) ** (1 / yrs) - 1) * 100, 2)}},
        "crashes": [{"name": n, "from": a, "to": b, "rule": window_dd(rule, a, b), "hold": window_dd(r, a, b)} for n, a, b in CRASHES],
        "years": [{"y": int(y), "rule": round(float((1 + rule[rule.index.year == y]).prod() - 1) * 100, 1),
                   "hold": round(float((1 + r[r.index.year == y]).prod() - 1) * 100, 1)} for y in sorted(set(c.index.year))],
        "switches": sw,
        "weekly": {"dates": [f"{d:%Y-%m-%d}" for d in wk],
                   "rule": [round(float(x)) for x in eq_rule.reindex(wk)],
                   "hold": [round(float(x)) for x in eq_hold.reindex(wk)],
                   "in": [1 if pos.reindex(wk).iloc[i] else 0 for i in range(len(wk))]},
    }
    OUT.write_text(json.dumps(doc, separators=(",", ":")))
    s = doc["stats"]
    print(f"Rule {s['rule']['cagr']}%/yr (worst {s['rule']['maxdd']}%) vs hold {s['hold']['cagr']}%/yr (worst {s['hold']['maxdd']}%); "
          f"now {'S&P 500' if next_pos else 'T-bills'}, SPY {doc['now']['gap']:+.1f}% vs 200-day -> {OUT.relative_to(ROOT)}")

    prefix = os.environ.get("NTFY_PREFIX", "").strip()
    if prefix and prev and not args.demo and prev.get("now", {}).get("position") != doc["now"]["position"]:
        notify(prefix, next_pos, doc["now"]["close"], doc["now"]["sma200"])


if __name__ == "__main__":
    main()
