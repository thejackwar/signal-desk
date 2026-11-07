"""
Gap study: regular-session price gaps on S&P 100 and S&P 500 stocks, 2021 -> today.

A gap is the jump between yesterday's regular-session close and today's open
(daily candles = 9:30-16:00 New York, so pre-market/after-hours are ignored).

    gap % = open_today / close_yesterday - 1

For every gap of at least 0.5% it measures:
  * fill: does price trade back to yesterday's close? same day, within 5 / 20 days, or ever
  * fade trade: at the open, bet against the gap; take profit at yesterday's close (gap filled),
    otherwise exit at today's close. Long for gap downs, short for gap ups.
  * follow trade: bet with the gap from open to close (no target)
  * drift: return from the gap day's close over the next 5 / 20 days, in the gap's direction,
    compared with the average stock over the same days
Costs: 0.10% per round trip (both trades).

Universe: today's S&P 100 (engine/tickers.txt) and S&P 500 (engine/tickers500.txt). That is a
hindsight list (survivorship bias): stocks that dropped out of the index since 2021 are missing.

Run:  python research/gap-study/gaps.py           (real prices via research/quant/data.py)
      python research/gap-study/gaps.py --demo    (random prices, for testing)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research" / "quant"))
import data  # noqa: E402

OUT = Path(__file__).parent / "results"
START = "2021-01-01"
COST = 0.0010                       # round trip
MIN_GAP = 0.005
BUCKETS = [(0.005, 0.01), (0.01, 0.02), (0.02, 0.04), (0.04, 0.08), (0.08, 10.0)]
BUCKET_NAMES = ["0.5-1%", "1-2%", "2-4%", "4-8%", "8%+"]


def tickers(name: str) -> list[str]:
    lines = (ROOT / "engine" / name).read_text().splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def gaps_for(t: str, df: pd.DataFrame, mkt: pd.Series) -> pd.DataFrame:
    df = df.sort_index()
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    n = len(df)
    if n < 30:
        return pd.DataFrame()
    pc = np.r_[np.nan, c[:-1]]
    ph = np.r_[np.nan, h[:-1]]
    pl = np.r_[np.nan, l[:-1]]
    gap = o / pc - 1
    idx = df.index
    keep = (np.abs(gap) >= MIN_GAP) & (idx >= pd.Timestamp(START))
    rows = []
    # running min of lows / max of highs from day i onward, for "filled ever"
    for i in np.flatnonzero(keep):
        g = gap[i]
        up = g > 0
        target = pc[i]
        # first day (0 = same day) on which price trades back to yesterday's close
        if up:
            hit = np.flatnonzero(l[i:] <= target)
        else:
            hit = np.flatnonzero(h[i:] >= target)
        days = int(hit[0]) if len(hit) else -1
        full = (o[i] > ph[i]) if up else (o[i] < pl[i])          # opened beyond yesterday's range
        sign = -1.0 if up else 1.0                                  # fade direction
        if days == 0:
            fade = abs(g) / (1 + g)                                 # open -> yesterday's close
        else:
            fade = sign * (c[i] / o[i] - 1)
        follow = -sign * (c[i] / o[i] - 1)
        d5 = d20 = np.nan
        if i + 5 < n:
            d5 = -sign * (c[i + 5] / c[i] - 1 - (mkt.get(idx[i + 5], np.nan) / mkt.get(idx[i], np.nan) - 1))
        if i + 20 < n:
            d20 = -sign * (c[i + 20] / c[i] - 1 - (mkt.get(idx[i + 20], np.nan) / mkt.get(idx[i], np.nan) - 1))
        rows.append((t, idx[i], g, up, bool(full), days, fade - COST, follow - COST, d5, d20, n - 1 - i))
    return pd.DataFrame(rows, columns=["ticker", "date", "gap", "up", "full", "fill_days",
                                       "fade", "follow", "drift5", "drift20", "days_left"])


def market_index(prices: dict[str, pd.DataFrame]) -> pd.Series:
    """Equal-weight average stock (cumulative), used as the drift baseline."""
    rets = pd.DataFrame({t: df["close"].pct_change() for t, df in prices.items()})
    rets = rets[rets.index >= pd.Timestamp("2020-12-01")]
    return (1 + rets.mean(axis=1).fillna(0)).cumprod()


def summarise(g: pd.DataFrame) -> dict:
    if g.empty:
        return {"gaps": 0}
    fd = g["fill_days"]
    out = {
        "gaps": int(len(g)),
        "per_stock_per_year": None,
        "filled_same_day": round(float((fd == 0).mean()) * 100, 1),
        "filled_5d": round(float(((fd >= 0) & (fd <= 5))[g["days_left"] >= 5].mean()) * 100, 1),
        "filled_20d": round(float(((fd >= 0) & (fd <= 20))[g["days_left"] >= 20].mean()) * 100, 1),
        "filled_ever": round(float((fd >= 0).mean()) * 100, 1),
        "fade_avg_pct": round(float(g["fade"].mean()) * 100, 3),
        "fade_win_pct": round(float((g["fade"] > 0).mean()) * 100, 1),
        "follow_avg_pct": round(float(g["follow"].mean()) * 100, 3),
        "follow_win_pct": round(float((g["follow"] > 0).mean()) * 100, 1),
        "drift5_vs_mkt_pct": round(float(g["drift5"].mean()) * 100, 3),
        "drift20_vs_mkt_pct": round(float(g["drift20"].mean()) * 100, 3),
    }
    return out


def tables(g: pd.DataFrame, n_stocks: int, years: float) -> dict:
    res = {"all": summarise(g)}
    res["all"]["per_stock_per_year"] = round(len(g) / max(n_stocks, 1) / years, 1)
    for d, name in ((True, "gap_up"), (False, "gap_down")):
        res[name] = summarise(g[g["up"] == d])
    res["full_gaps"] = summarise(g[g["full"]])
    res["by_size"] = {}
    for (lo, hi), name in zip(BUCKETS, BUCKET_NAMES):
        sub = g[(g["gap"].abs() >= lo) & (g["gap"].abs() < hi)]
        res["by_size"][name] = {"up": summarise(sub[sub["up"]]), "down": summarise(sub[~sub["up"]])}
    res["by_year"] = {str(y): summarise(s) for y, s in g.groupby(g["date"].dt.year)}
    return res


def main(demo: bool = False) -> None:
    sp100, sp500 = tickers("tickers.txt"), tickers("tickers500.txt")
    universe = sorted(set(sp100) | set(sp500))
    prices = data.demo(universe, bars=1500) if demo else data.load(universe, start="2020-06-01")
    print(f"prices for {len(prices)}/{len(universe)} tickers", flush=True)
    mkt = market_index(prices)
    frames = [gaps_for(t, df, mkt) for t, df in prices.items()]
    allg = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    last = max(df.index.max() for df in prices.values())
    years = (last - pd.Timestamp(START)).days / 365.25
    report = {"start": START, "end": str(last.date()), "min_gap_pct": MIN_GAP * 100,
              "cost_round_trip_pct": COST * 100, "demo": demo, "universes": {}}
    for name, tk in (("sp100", sp100), ("sp500", sp500)):
        s = set(t for t in tk if t in prices)
        report["universes"][name] = {"stocks": len(s), **tables(allg[allg["ticker"].isin(s)], len(s), years)}
    OUT.mkdir(exist_ok=True)
    (OUT / "gaps.json").write_text(json.dumps(report, indent=1, default=float))
    allg.assign(date=allg["date"].dt.date).to_csv(OUT / "gaps_all.csv.gz", index=False, float_format="%.5f")
    print(json.dumps({k: v["all"] for k, v in report["universes"].items()}, indent=1))


if __name__ == "__main__":
    main(demo="--demo" in sys.argv)
