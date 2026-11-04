"""
Improvement tests for the two strategies the screen flagged (screen.py --set improve).

Ten variants, fixed BEFORE seeing any result, each changing one thing that has a known reason
to help (not a search over settings):

  Residual momentum (base: mom_residual_12_1, top 10 monthly; failed 2018-2020)
    mrv_top20        20 stocks instead of 10: less riding on a few names
    mrv_buffer       top 10, but a holding stays until it drops out of the top 25: fewer trades
    mrv_mkt_trend    only while SPY is above its 200-day average, else T-bills: momentum crashes in bear markets
    mrv_vol_scaled   exposure cut when its own volatility is above 15% a year (Barroso & Santa-Clara 2015)
    mrv_stock_trend  only stocks above their own 200-day average
  High-volume premium (base: high_volume_premium, top 10 monthly; confirmed 2018-2020)
    hvp_top20        20 stocks instead of 10
    hvp_stock_trend  only stocks above their own 200-day average
    hvp_up_volume    volume jump on a flat-to-rising week only (5-day return 0% to +5%)
    hvp_two_month    each month's picks held for two months (half the money in each month's picks):
                     the premium is reported to last more than one month (Gervais, Kaniel & Mingelgrin 2001)
    hvp_mkt_trend    only while SPY is above its 200-day average, else T-bills

How the winner is picked (also fixed now): per strategy, the variant (or the base) with the best
Sharpe minus holding's on 2018-now, years already seen. It is then judged ONCE on 2011-2017,
which no test has looked at yet. Holds up = beats holding in >= 70% of bootstraps there, with a
higher Sharpe. The deflated Sharpe counts all 97 strategies tried so far.
"""
from __future__ import annotations

import pandas as pd

import screen_strategies as S
from screen_strategies import _mom10, _own, _row_mask, regime, slots, topn  # noqa: F401

REG: dict[str, tuple] = {}
BASE = {"Residual momentum": "mom_residual_12_1", "High-volume premium": "high_volume_premium"}


def var(sid, group):
    def deco(fn):
        REG[sid] = (group, fn)
        return fn
    return deco


def _spy_up(x):
    return x.spy > x.spy.rolling(200).mean()


RM, HV = "Residual momentum", "High-volume premium"


@var("mom_residual_12_1", RM)
def _rm_base(x):
    return S.REG["mom_residual_12_1"][1](x)


@var("mrv_top20", RM)
def mrv_top20(x):
    return topn(x, S.resid_mom_score(x), 20, x.me)


@var("mrv_buffer", RM)
def mrv_buffer(x):
    sc = S.resid_mom_score(x)
    rank = sc.where(x.ok).rank(axis=1, ascending=False)
    return slots(x, sc, 10, x.me, None, x.me, rank <= 25)


@var("mrv_mkt_trend", RM)
def mrv_mkt_trend(x):
    return regime(x, S.REG["mom_residual_12_1"][1](x), _spy_up(x), x.me)


@var("mrv_vol_scaled", RM)
def mrv_vol_scaled(x):
    w = S.REG["mom_residual_12_1"][1](x)
    v = _own(x, w).rolling(126, min_periods=63).std() * S.SQ
    return regime(x, w, (0.15 / v).clip(upper=1).fillna(1), x.me)


@var("mrv_stock_trend", RM)
def mrv_stock_trend(x):
    return topn(x, S.resid_mom_score(x), 10, x.me, eligible=x.C > x.sma(200))


@var("high_volume_premium", HV)
def _hv_base(x):
    return S.REG["high_volume_premium"][1](x)


def _flat(x):
    return x.ret(5).abs() <= 0.05


@var("hvp_top20", HV)
def hvp_top20(x):
    return topn(x, S.abnormal_volume(x), 20, x.me, eligible=_flat(x))


@var("hvp_stock_trend", HV)
def hvp_stock_trend(x):
    return topn(x, S.abnormal_volume(x), 10, x.me, eligible=_flat(x) & (x.C > x.sma(200)))


@var("hvp_up_volume", HV)
def hvp_up_volume(x):
    r5 = x.ret(5)
    return topn(x, S.abnormal_volume(x), 10, x.me, eligible=(r5 >= 0) & (r5 <= 0.05))


@var("hvp_two_month", HV)
def hvp_two_month(x):
    w = S.REG["high_volume_premium"][1](x)
    me = w[_row_mask(x, x.me)]
    prev = me.shift(1).reindex(x.idx).ffill().fillna(0)
    return 0.5 * w + 0.5 * prev


@var("hvp_mkt_trend", HV)
def hvp_mkt_trend(x):
    return regime(x, S.REG["high_volume_premium"][1](x), _spy_up(x), x.me)
