"""
Phase 3: portfolios built around a trend filter, sized by risk instead of split equally.

All designed up front (no settings tuned on the results), rebalanced every Friday close, held
from the next session, 10 bp on every change in weight, idle money earns the 3-month T-bill
rate (^IRX). Universe: the point-in-time top 100.

  hold_all            every stock in the universe, equal weight (the benchmark, here with costs + T-bills)
  market_trend        hold_all while SPY closes above its 200-day average, otherwise T-bills
  market_cross        hold_all while SPY's 50-day average is above its 200-day, otherwise T-bills
  stock_trend_ivol    the universe stocks above their own 200-day average, weighted by 1 / volatility,
                      at most 5% each (so with fewer than 20 in a trend the rest waits in T-bills)
  vol_target          hold_all, scaled down when its recent volatility is above 15% a year (never above 100%)
  trend_vol_target    stock_trend_ivol, scaled the same way
"""
from __future__ import annotations

import numpy as np
import pandas as pd

COST_BPS = 10
CAP = 0.05
TARGET_VOL = 0.15
NAMES = ["market_trend", "market_cross", "stock_trend_ivol", "vol_target", "trend_vol_target"]


def _weekly(w: pd.DataFrame) -> pd.DataFrame:
    """Targets set at each week's last close, held from the next session."""
    fri = w.index.to_series().groupby(w.index.to_period("W-FRI")).transform("max") == w.index.to_series()
    return w.where(pd.Series(fri.values, index=w.index), axis=0).ffill().shift(1).fillna(0)


def _run(w: pd.DataFrame, rets: pd.DataFrame, cash: pd.Series) -> pd.DataFrame:
    w = _weekly(w)
    r = rets.reindex(columns=w.columns).fillna(0)
    invested = w.sum(axis=1)
    gross = (w * r).sum(axis=1) + (1 - invested).clip(lower=0) * cash
    traded = (w - w.shift(1).fillna(0)).abs().sum(axis=1)
    return pd.DataFrame({"net": gross - traded * COST_BPS / 1e4, "traded": traded, "invested": invested})


def _scale(w: pd.DataFrame, rets: pd.DataFrame, lookback: int = 63) -> pd.DataFrame:
    """Shrink a weight set so its trailing volatility is at most TARGET_VOL (no leverage)."""
    port = (w.shift(1).fillna(0) * rets.reindex(columns=w.columns).fillna(0)).sum(axis=1)
    vol = port.rolling(lookback, min_periods=21).std() * np.sqrt(252)
    k = (TARGET_VOL / vol).clip(upper=1).fillna(1)
    return w.mul(k, axis=0)


def build(closes: pd.DataFrame, rets: pd.DataFrame, member: pd.DataFrame, spy: pd.Series,
          irx: pd.Series | None) -> dict[str, pd.DataFrame]:
    cols = member.columns
    c, r = closes[cols], rets[cols]
    cash = ((irx.reindex(closes.index).ffill().fillna(0) / 100) / 252) if irx is not None else pd.Series(0.0, index=closes.index)
    ok = member & c.notna()
    ew = ok.div(ok.sum(axis=1).replace(0, np.nan), axis=0).fillna(0).astype(float)

    s200, s50 = spy.rolling(200).mean(), spy.rolling(50).mean()
    up = (spy > s200).astype(float)
    cross = (s50 > s200).astype(float)

    trend = ok & (c > c.rolling(200, min_periods=200).mean())
    ivol = 1 / r.rolling(63, min_periods=42).std()
    raw = ivol.where(trend)
    w_ti = raw.div(raw.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    w_ti = w_ti.clip(upper=CAP)                      # cap after normalising: the rest stays in cash

    weights = {
        "hold_all": ew,
        "market_trend": ew.mul(up, axis=0),
        "market_cross": ew.mul(cross, axis=0),
        "stock_trend_ivol": w_ti,
        "vol_target": _scale(ew, r),
        "trend_vol_target": _scale(w_ti, r),
    }
    return {k: _run(w, r, cash) for k, w in weights.items()}


def crash_stats(net: pd.Series) -> dict:
    """How it did in the two big falls of the period."""
    def dd(a, b):
        s = net[(net.index >= a) & (net.index <= b)]
        eq = (1 + s).cumprod()
        return float((eq / eq.cummax() - 1).min()) if len(s) else None
    y22 = net[net.index.year == 2022]
    return {"covid_2020": dd("2020-02-19", "2020-04-30"), "year_2022": float((1 + y22).prod() - 1) if len(y22) else None}
