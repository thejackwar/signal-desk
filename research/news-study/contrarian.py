"""News study, step 3: the contrarian S&P 500 rule as a strategy, on SPY itself.

  python research/news-study/contrarian.py          (needs internet for SPY prices)

Runs engine/bot_strategies.news_contrarian on SPY's daily prices with the GDELT market tone in
docs/data/news_market.json: buy at the next open after a gloomy news day (counted from the next
session's close), sell at the next open 5 sessions after the last one. Compares with holding SPY,
after a 0.03% cost per side, over 2019-06-2023 and 2024-now, and against random days in the market
(the same number of trades, the same lengths). Writes research/news-study/contrarian.md and .json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[1] / "engine"))
import strategies  # noqa: E402,F401  (loads bot_strategies in the right order)
import bot_strategies as B  # noqa: E402

COST = 0.0003
LO, SPLIT = "2019-06-01", "2024-01-01"


def spy() -> pd.DataFrame:
    import yfinance as yf
    d = yf.download("SPY", start="2018-01-01", auto_adjust=True, progress=False)
    d.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in d.columns]
    d.index = pd.to_datetime(d.index).tz_localize(None)
    return d[["open", "high", "low", "close", "volume"]].dropna()


def daily(df: pd.DataFrame, trades: list) -> tuple[pd.Series, pd.Series]:
    """Open-to-open returns while held (cost at each buy and sell), and the held flag."""
    o = df["open"].values
    r, held = np.zeros(len(df)), np.zeros(len(df), bool)
    for t in trades:
        if t["in"] is None:
            continue
        a, b = t["in"], t["out"] if t["out"] is not None else len(df) - 1
        for k in range(a, b):
            r[k] = o[k + 1] / o[k] - 1
            held[k] = True
        r[a] -= COST
        if t["out"] is not None:
            r[b - 1] -= COST
    return pd.Series(r, df.index), pd.Series(held, df.index)


def stats(r: pd.Series) -> dict:
    yrs = len(r) / 252
    eq = (1 + r).cumprod()
    return {"cagr": round(100 * (eq.iloc[-1] ** (1 / yrs) - 1), 2),
            "sharpe": round(float(r.mean() / r.std() * np.sqrt(252)), 2) if r.std() > 0 else 0,
            "maxdd": round(100 * float((eq / eq.cummax() - 1).min()), 1)}


def main():
    df = spy()
    res = B.news_contrarian(df)
    r, held = daily(df, res.trades)
    bh = (df["open"].shift(-1) / df["open"] - 1).fillna(0)
    out = {"trades": sum(1 for t in res.trades if t["in"] is not None and df.index[t["in"]] >= pd.Timestamp(LO))}
    for name, a, b in (("all", LO, None), ("2019-23", LO, SPLIT), ("2024-now", SPLIT, None)):
        m = (df.index >= a) & ((df.index < b) if b else True)
        rr, bb, hh = r[m], bh[m], held[m]
        out[name] = {"strategy": stats(rr), "hold": stats(bb), "invested_pct": round(100 * hh.mean(), 1),
                     # the fair question for a part-time strategy: are its days in the market better than average?
                     "avg_day_in_pct": round(100 * float(bh[m][hh].mean()), 3), "avg_day_all_pct": round(100 * float(bb.mean()), 3)}
    # random days in the market: shuffle where the trades start, keep how long each one lasts
    rng = np.random.default_rng(0)
    m = df.index >= LO
    lens = [(t["out"] or len(df) - 1) - t["in"] for t in res.trades if t["in"] is not None and df.index[t["in"]] >= pd.Timestamp(LO)]
    n, base = int(m.sum()), np.flatnonzero(m)[0]
    real = float(bh[m][held[m]].mean())
    sims = []
    for _ in range(2000):
        h = np.zeros(n, bool)
        for L in lens:
            s0 = rng.integers(0, max(1, n - L))
            h[s0:s0 + L] = True
        sims.append(float(bh.values[base:base + n][h].mean()))
    out["random_days"] = {"share_as_good": round(float((np.array(sims) >= real).mean()), 3),
                          "real_avg_day_pct": round(100 * real, 3), "random_median_pct": round(100 * float(np.median(sims)), 3)}
    (HERE / "contrarian.json").write_text(json.dumps(out, indent=1))
    L = ["# News Contrarian on SPY", "",
         f"Buy SPY at the next open after a gloomy day of stock-market news (GDELT tone in the lowest fifth of the past "
         f"year, counted from the next session's close), sell at the next open 5 sessions after the last one. "
         f"{out['trades']} trades since June 2019, 0.03% cost per side.", "",
         "| Period | Strategy a year | Holding SPY | Sharpe (vs hold) | Worst fall (vs hold) | Time invested | Avg day invested vs any day |",
         "|---|---|---|---|---|---|---|"]
    for k in ("all", "2019-23", "2024-now"):
        x = out[k]
        L.append(f"| {k} | {x['strategy']['cagr']:+.1f}% | {x['hold']['cagr']:+.1f}% | {x['strategy']['sharpe']:.2f} vs {x['hold']['sharpe']:.2f} | "
                 f"{x['strategy']['maxdd']:.0f}% vs {x['hold']['maxdd']:.0f}% | {x['invested_pct']:.0f}% | {x['avg_day_in_pct']:+.3f}% vs {x['avg_day_all_pct']:+.3f}% |")
    rd = out["random_days"]
    L += ["", f"Random days in the market (same number and length of trades, 2,000 tries): its days averaged "
          f"{rd['real_avg_day_pct']:+.3f}% against a random median of {rd['random_median_pct']:+.3f}%; "
          f"{100 * rd['share_as_good']:.1f}% of the random tries did as well."]
    (HERE / "contrarian.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
