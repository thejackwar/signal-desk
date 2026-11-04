"""
Strategy screen: 87 new stock strategies (price data only), written as target weights.

Source: the idea list (106 ideas, 87 need only daily prices). Every function gets the context
`x` and returns a date x ticker DataFrame of TARGET weights decided at that day's close
(0..1 per stock, row sum <= 1; whatever isn't invested earns T-bills). The runner (screen.py)
holds them from the next session, so nothing uses a price before it is known.

Two exceptions trade inside the day (open -> close or close -> open); they return a dict with
their own returns and are marked in the report.

Settings are the idea list's single defaults: nothing here was tuned on results.
Universe: the point-in-time top 100 (research/quant/universe.py); new positions only in stocks
that are in it on the decision day.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SQ = np.sqrt(252)


class Ctx:
    """Price matrices (date x ticker) plus cached indicators."""

    def __init__(self, O, H, L, C, V, member, spy, cash):
        self.O, self.H, self.L, self.C, self.V = O, H, L, C, V
        self.R = C.pct_change(fill_method=None)
        self.M = member.reindex(index=C.index, columns=C.columns, fill_value=False).astype(bool)
        self.ok = self.M & C.notna()
        self.spy = spy.reindex(C.index).ffill()
        self.spy_r = self.spy.pct_change().fillna(0)
        self.cash = cash.reindex(C.index).fillna(0)
        self.idx = C.index
        s = self.idx.to_series()
        self.me = (s.groupby(self.idx.to_period("M")).transform("max") == s).values        # month-end
        self.fri = (s.groupby(self.idx.to_period("W-FRI")).transform("max") == s).values   # week-end
        self.daily = np.ones(len(self.idx), bool)
        self._c = {}

    # ---- cached indicators -------------------------------------------------------------
    def _get(self, key, fn):
        if key not in self._c:
            self._c[key] = fn()
        return self._c[key]

    def sma(self, n):
        return self._get(("sma", n), lambda: self.C.rolling(n, min_periods=n).mean())

    def ema(self, n):
        return self._get(("ema", n), lambda: self.C.ewm(span=n, adjust=False, min_periods=n).mean())

    def atr(self, n):
        def f():
            pc = self.C.shift(1)
            tr = np.maximum(self.H - self.L, np.maximum((self.H - pc).abs(), (self.L - pc).abs()))
            return tr.rolling(n, min_periods=n).mean()
        return self._get(("atr", n), f)

    def ret(self, n, skip=0):
        return self._get(("ret", n, skip), lambda: self.C.shift(skip) / self.C.shift(n) - 1)

    def vol(self, n):
        return self._get(("vol", n), lambda: self.R.rolling(n, min_periods=n).std())

    def beta(self, n=252):
        def f():
            m = self.spy_r
            cov = self.R.rolling(n, min_periods=n).cov(m)
            return cov.div(m.rolling(n, min_periods=n).var(), axis=0)
        return self._get(("beta", n), f)

    def resid(self, n=252):
        """Daily return minus beta x SPY (beta from the previous n days)."""
        return self._get(("resid", n), lambda: self.R - self.beta(n).shift(1).mul(self.spy_r, axis=0))

    def hi(self, n):
        return self._get(("hi", n), lambda: self.C.rolling(n, min_periods=n).max())

    def lo(self, n):
        return self._get(("lo", n), lambda: self.C.rolling(n, min_periods=n).min())

    def avgv(self, n=50):
        """Average volume of the previous n days (not including today)."""
        return self._get(("avgv", n), lambda: self.V.shift(1).rolling(n, min_periods=n).mean())

    def breadth(self, n=200):
        def f():
            s = self.sma(n)
            have = self.ok & s.notna()
            return (have & (self.C > s)).sum(axis=1) / have.sum(axis=1).replace(0, np.nan)
        return self._get(("breadth", n), f)

    def m13612(self, C=None):
        C = self.C if C is None else C
        return 12 * (C / C.shift(21) - 1) + 4 * (C / C.shift(63) - 1) + 2 * (C / C.shift(126) - 1) + (C / C.shift(252) - 1)

    def tbill_cum(self, n=252):
        return self._get(("tb", n), lambda: np.expm1(np.log1p(self.cash).rolling(n, min_periods=n).sum()))

    def ew_index(self):
        def f():
            r = self.R.where(self.ok.shift(1, fill_value=False)).mean(axis=1).fillna(0)
            return (1 + r).cumprod()
        return self._get("ewidx", f)


# ---- building blocks ---------------------------------------------------------------------

def _row_mask(x, days):
    return pd.Series(days, index=x.idx)


def rebal(x, target: pd.DataFrame, days) -> pd.DataFrame:
    """Keep the targets set on rebalance days, hold them in between."""
    t = target.fillna(0).astype(float)
    return t.where(_row_mask(x, days), axis=0).ffill().fillna(0)


def eqw(sel: pd.DataFrame) -> pd.DataFrame:
    sel = sel.fillna(False).astype(bool)
    return sel.div(sel.sum(axis=1).replace(0, np.nan), axis=0).fillna(0).astype(float)


def cap_eqw(sel, cap=0.05):
    w = eqw(sel)
    return w.clip(upper=cap)


def univ(x):
    return eqw(x.ok)


def topn(x, score, n, days, eligible=None, low=False):
    """Fixed slots: the n best scores among eligible universe stocks get 1/n each."""
    el = x.ok & score.notna()
    if eligible is not None:
        el &= eligible.fillna(False)
    s = score.where(el)
    rk = s.rank(axis=1, ascending=low, method="first")
    return rebal(x, (rk <= n).astype(float) / n, days)


def regime(x, w, on: pd.Series, days):
    """w while `on` (1/0 or a fraction), decided on rebalance days."""
    flag = on.astype(float).where(_row_mask(x, days)).ffill().fillna(0)
    return w.mul(flag, axis=0)


def hysteresis(cond_on: pd.Series, cond_off: pd.Series, days=None) -> pd.Series:
    s = pd.Series(np.nan, index=cond_on.index)
    s[cond_on.fillna(False)] = 1.0
    s[cond_off.fillna(False)] = 0.0
    if days is not None:
        s = s.where(pd.Series(days, index=s.index))
    return s.ffill().fillna(0)


def hysteresis_df(on: pd.DataFrame, off: pd.DataFrame, days) -> pd.DataFrame:
    s = pd.DataFrame(np.nan, index=on.index, columns=on.columns)
    s[on.fillna(False)] = 1.0
    s[off.fillna(False)] = 0.0
    s = s.where(pd.Series(days, index=s.index), axis=0)
    return s.ffill().fillna(0)


def hold_n(event: pd.Series, n: int) -> pd.Series:
    """Held for n closes starting at the event close (a new event restarts the clock)."""
    return event.fillna(False).astype(float).rolling(n, min_periods=1).max()


def alerts(x, entry, exit=None, max_hold=None, stop=None, tp=None) -> pd.DataFrame:
    """Per-stock trades: buy at the close of an entry alert (stock must be in the universe that
    day), sell at the close when `exit` fires, after max_hold sessions, or when the close falls
    below the stop set on the entry day. Returns 'split' weights (money split equally over open
    positions, like the app's account)."""
    E = entry.fillna(False).values & x.M.values & x.C.notna().values
    X = exit.fillna(False).values if exit is not None else None
    S = stop.values if stop is not None else None
    C = x.C.values
    n, m = C.shape
    held = np.zeros((n, m))
    for j in np.flatnonzero(E.any(axis=0)):
        inpos, cnt, sv = False, 0, np.nan
        e, c = E[:, j], C[:, j]
        xj = X[:, j] if X is not None else None
        for i in range(n):
            if inpos:
                cnt += 1
                out = (xj is not None and xj[i]) or (max_hold and cnt >= max_hold) \
                    or (S is not None and not np.isnan(sv) and c[i] < sv) or np.isnan(c[i])
                if not out:
                    held[i, j] = 1
                    continue
                inpos = False
            if e[i]:
                inpos, cnt = True, 0
                sv = S[i, j] if S is not None else np.nan
                held[i, j] = 1
    H = pd.DataFrame(held, index=x.idx, columns=x.C.columns)
    return eqw(H > 0)


def slots(x, score, n, buy_days, buy_ok=None, sell_days=None, keep_ok=None):
    """Stateful slots: on sell days drop holdings failing keep_ok; on buy days fill empty slots
    with the best-scored stocks passing buy_ok. 1/n each, empty slots in T-bills."""
    cols = x.C.columns
    sc = score.where(x.ok).values
    bo = (buy_ok.reindex(columns=cols).fillna(False).values if buy_ok is not None else np.ones(sc.shape, bool))
    ko = keep_ok.reindex(columns=cols).fillna(False).values if keep_ok is not None else None
    sell_days = buy_days if sell_days is None else sell_days
    out = np.zeros(sc.shape)
    held: set[int] = set()
    for i in range(len(x.idx)):
        if sell_days[i] and ko is not None:
            held = {h for h in held if ko[i, h]}
        if buy_days[i] and len(held) < n:
            s = np.where(bo[i] & ~np.isnan(sc[i]), sc[i], -np.inf)
            for j in np.argsort(-s):
                if len(held) >= n or s[j] == -np.inf:
                    break
                held.add(int(j))
        for h in held:
            out[i, h] = 1.0 / n
    return pd.DataFrame(out, index=x.idx, columns=cols)


# ---- the strategies ----------------------------------------------------------------------
REG: dict[str, tuple] = {}     # id -> (family, function)


def strat(sid, family):
    def deco(fn):
        REG[sid] = (family, fn)
        return fn
    return deco


F_MOM, F_REV, F_LOW, F_VB, F_MR = "Cross-sectional momentum", "Short-term reversal", "Low volatility / defensive", "Volatility breakout", "Mean reversion"
F_GAP, F_SHOCK, F_CAL, F_BR, F_DUAL = "Gaps", "Post-shock drift", "Calendar & seasonality", "Market breadth & regime", "Dual / absolute momentum"
F_TP, F_VOL, F_COR, F_52, F_ENS, F_RISK = "Trend + pullback", "Volume & liquidity", "Correlation & dispersion", "52-week highs/lows & index events", "Ensembles & composites", "Risk overlays"


def _mom10(x):
    return topn(x, x.ret(252, 21), 10, x.me)


@strat("mom_12_1", F_MOM)
def mom_12_1(x):
    return _mom10(x)


@strat("mom_residual_12_1", F_MOM)
def mom_residual_12_1(x):
    # Residuals from a 3-year regression on SPY, summed over months 12 to 2 back (a regression
    # over only those months would make the residuals sum to zero by construction).
    R = x.R.values
    m = x.spy_r.values
    out = pd.DataFrame(np.nan, index=x.idx, columns=x.C.columns)
    for i in np.flatnonzero(x.me):
        if i < 756:
            continue
        y = R[i - 755:i + 1]
        mm = m[i - 755:i + 1]
        good = ~np.isnan(y).any(axis=0) & x.ok.values[i]
        if good.sum() < 10:
            continue
        Y = y[:, good]
        Xd = np.column_stack([np.ones(len(mm)), mm])
        coef, *_ = np.linalg.lstsq(Xd, Y, rcond=None)
        e = (Y - Xd @ coef)[-252:-21]
        sc = e.sum(axis=0) / e.std(axis=0)
        out.iloc[i, np.flatnonzero(good)] = sc
    return topn(x, out, 10, x.me)


@strat("mom_52wk_high", F_MOM)
def mom_52wk_high(x):
    return topn(x, x.C / x.hi(252) + 1e-6 * x.ret(252, 21).fillna(0), 10, x.me)


@strat("mom_frog_in_pan", F_MOM)
def mom_frog_in_pan(x):
    r = x.ret(252, 21)
    pos = (x.R > 0).astype(float).rolling(231).mean().shift(21)
    neg = (x.R < 0).astype(float).rolling(231).mean().shift(21)
    idd = np.sign(r) * (neg - pos)
    top30 = r.where(x.ok).rank(axis=1, ascending=False) <= 30
    return topn(x, idd, 10, x.me, eligible=top30, low=True)


@strat("mom_risk_adjusted", F_MOM)
def mom_risk_adjusted(x):
    sd = x.R.rolling(231, min_periods=200).std().shift(21) * SQ
    return topn(x, x.ret(252, 21) / sd, 10, x.me)


def _own(x, w):
    """Daily returns of a weight set held from the next session (no costs), for overlays."""
    return (w.shift(1).fillna(0) * x.R.fillna(0)).sum(axis=1)


@strat("mom_vol_managed", F_MOM)
def mom_vol_managed(x):
    w = _mom10(x)
    v = _own(x, w).rolling(126, min_periods=63).std() * SQ
    k = (0.15 / v).clip(upper=1).fillna(1)
    return regime(x, w, k, x.me)


@strat("mom_bear_switch", F_MOM)
def mom_bear_switch(x):
    up = (x.spy / x.spy.shift(504) - 1) >= 0
    flag = up.astype(float).where(_row_mask(x, x.me)).ffill().fillna(1)
    u = rebal(x, univ(x), x.me)
    return _mom10(x).mul(flag, axis=0) + u.mul(1 - flag, axis=0)


@strat("mom_time_series_stock", F_DUAL)
def mom_time_series_stock(x):
    sel = x.ok & x.ret(252).gt(x.tbill_cum(252), axis=0)
    return rebal(x, cap_eqw(sel, 0.05), x.me)


@strat("mom_intermediate_12_7", F_MOM)
def mom_intermediate_12_7(x):
    return topn(x, x.C.shift(147) / x.C.shift(252) - 1, 10, x.me)


@strat("mom_acceleration", F_MOM)
def mom_acceleration(x):
    sc = x.ret(126) - (x.C.shift(126) / x.C.shift(252) - 1)
    return topn(x, sc, 10, x.me, eligible=x.ret(252) > 0)


@strat("mom_consistent", F_MOM)
def mom_consistent(x):
    cnt = sum(((x.C.shift(21 * k) / x.C.shift(21 * (k + 1)) - 1) > 0).astype(int) for k in range(1, 12))
    return topn(x, x.ret(252, 21), 10, x.me, eligible=cnt >= 8)


@strat("mom_clenow_regression", F_MOM)
def mom_clenow_regression(x):
    n = 90
    y = np.log(x.C)
    t = pd.Series(np.arange(len(x.idx), dtype=float), index=x.idx)
    st, stt = t.rolling(n).sum(), (t * t).rolling(n).sum()
    sy = y.rolling(n, min_periods=n).sum()
    sty = y.mul(t, axis=0).rolling(n, min_periods=n).sum()
    syy = (y * y).rolling(n, min_periods=n).sum()
    vt = (n * stt - st * st)
    slope = (sty.mul(n) - sy.mul(st, axis=0)).div(vt, axis=0)
    vy = syy * n - sy * sy
    r2 = (slope ** 2).mul(vt, axis=0) / vy
    score = (np.exp(slope * 252) - 1) * r2
    gap = x.R.abs().rolling(n).max() > 0.15
    above = x.C > x.sma(100)
    rank = score.where(x.ok).rank(axis=1, ascending=False)
    mkt = (x.spy > x.spy.rolling(200).mean())
    buy_ok = above & ~gap & pd.DataFrame(np.repeat(mkt.values[:, None], x.C.shape[1], 1), index=x.idx, columns=x.C.columns)
    keep = (rank <= 20) & above & ~gap
    return slots(x, score, 10, x.fri, buy_ok, x.fri, keep)


@strat("rev_1w", F_REV)
def rev_1w(x):
    return topn(x, x.ret(5), 10, x.fri, low=True)


@strat("rev_1m", F_REV)
def rev_1m(x):
    return topn(x, x.ret(21), 10, x.me, low=True)


@strat("rev_residual_1w", F_REV)
def rev_residual_1w(x):
    r5s = x.spy / x.spy.shift(5) - 1
    sc = x.ret(5) - x.beta(252).mul(r5s, axis=0)
    return topn(x, sc, 10, x.fri, low=True)


@strat("rev_overnight_momentum", F_REV)
def rev_overnight_momentum(x):
    return topn(x, np.log(x.O / x.C.shift(1)).rolling(21).sum(), 10, x.me)


@strat("rev_intraday_losers", F_REV)
def rev_intraday_losers(x):
    return topn(x, np.log(x.C / x.O).rolling(21).sum(), 10, x.me, low=True)


@strat("overnight_only_hold", F_REV)
def overnight_only_hold(x):
    w = univ(x)
    r = x.O / x.C.shift(1) - 1                       # earned from the close before to this open
    return {"w": w, "r": r, "traded": 2 * w.sum(axis=1).shift(1).fillna(0), "intraday": "close -> next open"}


@strat("lowvol_252", F_LOW)
def lowvol_252(x):
    return topn(x, x.vol(252), 20, x.me, low=True)


def _bab_beta(x):
    def f():
        r3 = x.C / x.C.shift(3) - 1
        s3 = x.spy / x.spy.shift(3) - 1
        corr = r3.rolling(756, min_periods=500).corr(s3)
        return corr * x.vol(252).div(x.spy_r.rolling(252).std(), axis=0)
    return x._get("babbeta", f)


@strat("lowbeta_bab", F_LOW)
def lowbeta_bab(x):
    return topn(x, _bab_beta(x), 20, x.me, low=True)


@strat("low_idio_vol", F_LOW)
def low_idio_vol(x):
    b = x.beta(63)
    var = x.R.rolling(63).var() - (b ** 2).mul(x.spy_r.rolling(63).var(), axis=0)
    return topn(x, var.clip(lower=0), 20, x.me, low=True)


@strat("lottery_max_avoid", F_LOW)
def lottery_max_avoid(x):
    mx = x.R.rolling(21).max().where(x.ok)
    sel = x.ok & mx.notna() & (mx.rank(axis=1, pct=True) <= 0.8)
    return rebal(x, eqw(sel), x.me)


@strat("low_realized_skew", F_LOW)
def low_realized_skew(x):
    return topn(x, x.R.rolling(63).skew(), 20, x.me, low=True)


def _capped_simplex(v, cap):
    """Projection of v onto {w >= 0, w <= cap, sum w = 1}."""
    lo, hi = v.min() - 1, v.max()
    for _ in range(60):
        mid = (lo + hi) / 2
        if np.clip(v - mid, 0, cap).sum() > 1:
            lo = mid
        else:
            hi = mid
    return np.clip(v - hi, 0, cap)


@strat("min_variance", F_LOW)
def min_variance(x):
    # Covariance shrunk halfway to a constant-correlation target (a fixed 0.5 instead of the
    # Ledoit-Wolf estimate, to keep it simple), max 10% per stock.
    R = x.R.values
    out = pd.DataFrame(0.0, index=x.idx, columns=x.C.columns)
    for i in np.flatnonzero(x.me):
        if i < 252:
            continue
        y = R[i - 251:i + 1]
        good = ~np.isnan(y).any(axis=0) & x.ok.values[i]
        k = good.sum()
        if k < 15:
            continue
        S = np.cov(y[:, good], rowvar=False)
        sd = np.sqrt(np.diag(S))
        cor = S / np.outer(sd, sd)
        rbar = (cor.sum() - k) / (k * (k - 1))
        T = rbar * np.outer(sd, sd)
        np.fill_diagonal(T, sd ** 2)
        S = 0.5 * S + 0.5 * T
        w = np.full(k, 1 / k)
        step = 1 / np.linalg.eigvalsh(S)[-1]
        for _ in range(400):
            w = _capped_simplex(w - step * (S @ w), 0.10)
        out.iloc[i, np.flatnonzero(good)] = w
    return rebal(x, out, x.me)


@strat("nr7_breakout", F_VB)
def nr7_breakout(x):
    rng = x.H - x.L
    nr7 = rng <= rng.rolling(7).min()
    up = x.C > x.sma(200)
    e1 = nr7.shift(1, fill_value=False) & (x.C > x.H.shift(1)) & up
    e2 = nr7.shift(2, fill_value=False) & (x.C > x.H.shift(2)) & up & ~e1
    stop = x.L.shift(1).where(e1, x.L.shift(2))
    return alerts(x, e1 | e2, max_hold=10, stop=stop)


@strat("squeeze_breakout", F_VB)
def squeeze_breakout(x):
    mid, sd = x.sma(20), x.C.rolling(20).std()
    e20, a20 = x.ema(20), x.atr(20)
    kcu, kcl = e20 + 1.5 * a20, e20 - 1.5 * a20
    sq = ((mid + 2 * sd) < kcu) & ((mid - 2 * sd) > kcl)
    c = sq.astype(int).cumsum()
    run = c - c.where(~sq).ffill().fillna(0)
    entry = (run.shift(1) >= 5) & (x.C > kcu) & (x.C.shift(1) <= kcu.shift(1))
    return alerts(x, entry, exit=x.C < e20)


@strat("keltner_trend", F_VB)
def keltner_trend(x):
    band = x.ema(20) + 2 * x.atr(10)
    entry = (x.C > band) & (x.C.shift(1) <= band.shift(1))
    return alerts(x, entry, exit=x.C < x.ema(20))


@strat("wide_range_followthrough", F_VB)
def wide_range_followthrough(x):
    rng = x.H - x.L
    loc = (x.C - x.L) / rng.replace(0, np.nan)
    entry = (rng > 2 * x.atr(20).shift(1)) & (loc >= 0.75) & (x.C > x.O) & (x.C > x.sma(50))
    return alerts(x, entry, max_hold=10, stop=x.L)


@strat("vol_open_breakout", F_VB)
def vol_open_breakout(x):
    lvl = x.O + 0.5 * (x.H.shift(1) - x.L.shift(1))
    ok = (x.C.shift(1) > x.sma(200).shift(1)) & x.M.shift(1, fill_value=False) & (x.H >= lvl)
    w = eqw(ok)
    r = (x.C / lvl - 1).where(ok)
    # w and r are for the same day (decided and closed inside it)
    return {"w_same_day": w, "r": r, "traded": 2 * w.sum(axis=1), "intraday": "buy-stop after the open -> close"}


@strat("ibs_reversion", F_MR)
def ibs_reversion(x):
    ibs = ((x.C - x.L) / (x.H - x.L).replace(0, np.nan)).fillna(0.5)
    return alerts(x, (ibs < 0.2) & (x.C > x.sma(200)), exit=ibs > 0.8, max_hold=5)


@strat("consec_down_days", F_MR)
def consec_down_days(x):
    d = x.C < x.C.shift(1)
    entry = d & d.shift(1, fill_value=False) & d.shift(2, fill_value=False) & (x.C > x.sma(200))
    return alerts(x, entry, exit=x.C > x.C.shift(1), max_hold=5)


@strat("residual_zscore_reversion", F_MR)
def residual_zscore_reversion(x):
    s5 = x.resid(252).rolling(5).sum()
    z = s5 / s5.rolling(252, min_periods=200).std()
    return alerts(x, (z < -2) & (x.C > x.sma(200)), exit=z > 0, max_hold=10)


@strat("variance_ratio_dip", F_MR)
def variance_ratio_dip(x):
    r5 = x.ret(5)
    vr = r5.rolling(252).var() / (5 * x.R.rolling(252).var())
    entry = (vr < 0.9) & (x.C < x.C.shift(1).rolling(10).min()) & (x.C > x.sma(200))
    return alerts(x, entry, exit=x.C > x.sma(10), max_hold=10)


@strat("cs_ibs_daily", F_MR)
def cs_ibs_daily(x):
    ibs = ((x.C - x.L) / (x.H - x.L).replace(0, np.nan)).fillna(0.5)
    return topn(x, ibs, 5, x.daily, eligible=x.C > x.sma(200), low=True)


@strat("drawdown_turnaround", F_MR)
def drawdown_turnaround(x):
    s50 = x.sma(50)
    entry = (x.C <= 0.7 * x.hi(252)) & (x.C > s50) & (x.C.shift(1) <= s50.shift(1)) & (s50 > s50.shift(10))
    return alerts(x, entry, exit=x.C < s50, max_hold=60)


@strat("gap_up_continuation", F_GAP)
def gap_up_continuation(x):
    loc = (x.C - x.L) / (x.H - x.L).replace(0, np.nan)
    entry = (x.O >= 1.04 * x.C.shift(1)) & (x.V >= 2 * x.avgv(50)) & (loc >= 0.5) & (x.C > x.sma(50))
    return alerts(x, entry, max_hold=20, stop=x.L)


@strat("gap_down_reversal", F_GAP)
def gap_down_reversal(x):
    entry = (x.O <= 0.97 * x.C.shift(1)) & (x.C > x.O) & (x.V < 2 * x.avgv(50)) & (x.C > x.sma(200))
    return alerts(x, entry, max_hold=5, stop=x.L)


@strat("gap_fill_intraday", F_GAP)
def gap_fill_intraday(x):
    ok = (x.O <= 0.98 * x.C.shift(1)) & (x.C.shift(1) > x.sma(200).shift(1)) & x.M.shift(1, fill_value=False)
    w = eqw(ok)
    return {"w_same_day": w, "r": (x.C / x.O - 1).where(ok), "traded": 2 * w.sum(axis=1), "intraday": "open -> close"}


@strat("shock_drift_up", F_SHOCK)
def shock_drift_up(x):
    e = x.resid(252)
    entry = (e > 3 * e.rolling(60).std().shift(1)) & (x.V > 2 * x.avgv(50))
    return alerts(x, entry, max_hold=20)


@strat("shock_reversal_no_volume", F_SHOCK)
def shock_reversal_no_volume(x):
    e = x.resid(252)
    entry = (e < -3 * e.rolling(60).std().shift(1)) & (x.V < 1.5 * x.avgv(50))
    return alerts(x, entry, max_hold=10)


@strat("market_crash_rebound", F_SHOCK)
def market_crash_rebound(x):
    return univ(x).mul(hold_n(x.spy_r <= -0.03, 5), axis=0)


def _day_nums(x, freq):
    s = pd.Series(1, index=x.idx)
    g = s.groupby(x.idx.to_period(freq))
    first = g.cumsum()
    last = g.transform("size") - first + 1       # 1 = last trading day of the period
    return first, last


@strat("turn_of_month", F_CAL)
def turn_of_month(x):
    first, last = _day_nums(x, "M")
    on = (last <= 2) | (first <= 2)
    return univ(x).mul(on.astype(float), axis=0)


@strat("halloween", F_CAL)
def halloween(x):
    first, last = _day_nums(x, "M")
    mo = x.idx.month
    on = pd.Series(np.isin(mo, [11, 12, 1, 2, 3, 4]), index=x.idx)
    on |= (mo == 10) & (last == 1)
    on &= ~((mo == 4) & (last == 1))
    return univ(x).mul(on.astype(float), axis=0)


@strat("month_seasonality_cs", F_CAL)
def month_seasonality_cs(x):
    mc = x.C[x.me]
    mr = mc / mc.shift(1) - 1                               # each month's return, at its month-end
    out = pd.DataFrame(np.nan, index=x.idx, columns=x.C.columns)
    per = mr.index.to_period("M")
    for k, d in enumerate(mr.index):
        nxt = (per[k] + 1)
        same = [p for p in range(1, 6)]
        rows = [mr.index[j] for j in range(len(per)) if per[j].month == nxt.month and nxt.year - 5 <= per[j].year <= nxt.year - 1]
        if len(rows) < 5:
            continue
        out.loc[d] = mr.loc[rows].mean(skipna=False)
        del same
    return topn(x, out, 10, x.me)


@strat("dec_tax_loss_rebound", F_CAL)
def dec_tax_loss_rebound(x):
    first, last = _day_nums(x, "M")
    yend = x.C[x.me & (x.idx.month == 12)]
    prev = yend.reindex(x.idx).ffill().groupby(x.idx.year).transform("first")
    # last close of the previous year: shift the year-end table by one year
    ye = yend.copy()
    ye.index = ye.index.year + 1
    base = pd.DataFrame(ye.reindex(x.idx.year).values, index=x.idx, columns=x.C.columns)
    del prev
    ytd = x.C / base - 1
    pick_day = ((x.idx.month == 12) & (first.values == 15))
    rk = ytd.where(x.ok & ytd.notna()).rank(axis=1, method="first")
    picks = ((rk <= 10).astype(float) / 10).where(pd.Series(pick_day, index=x.idx), axis=0)
    w = picks.ffill().fillna(0)
    on = pd.Series(((x.idx.month == 12) & (first.values >= 15)) | ((x.idx.month == 1) & (last.values > 1)), index=x.idx)
    return w.mul(on.astype(float), axis=0)


@strat("santa_rally", F_CAL)
def santa_rally(x):
    first, last = _day_nums(x, "M")
    on = ((x.idx.month == 12) & (last.values <= 6)) | ((x.idx.month == 1) & (first.values <= 1))
    return univ(x).mul(pd.Series(on, index=x.idx).astype(float), axis=0)


@strat("opex_week", F_CAL)
def opex_week(x):
    on = np.zeros(len(x.idx), bool)
    for p in sorted(set(x.idx.to_period("M"))):
        d1 = p.start_time
        fri = d1 + pd.Timedelta(days=(4 - d1.weekday()) % 7 + 14)
        on |= (x.idx >= fri - pd.Timedelta(days=7)) & (x.idx < fri)
    return univ(x).mul(pd.Series(on, index=x.idx).astype(float), axis=0)


@strat("weekday_skip_monday", F_CAL)
def weekday_skip_monday(x):
    return univ(x).mul(pd.Series(~x.fri, index=x.idx).astype(float), axis=0)


@strat("quarter_end_winners", F_CAL)
def quarter_end_winners(x):
    first, last = _day_nums(x, "Q")
    q = x.idx.to_period("Q")
    qstart = x.C.groupby(q).transform(lambda c: c.shift(1)).groupby(q).transform("first")
    prev_q_close = x.C.shift(1).where(pd.Series(first.values == 1, index=x.idx), axis=0).ffill()
    del qstart
    qtd = x.C / prev_q_close - 1
    pick_day = pd.Series(last.values == 6, index=x.idx)
    rk = qtd.where(x.ok & qtd.notna()).rank(axis=1, ascending=False, method="first")
    w = ((rk <= 10).astype(float) / 10).where(pick_day, axis=0).ffill().fillna(0)
    on = pd.Series((last.values >= 2) & (last.values <= 6), index=x.idx)
    return w.mul(on.astype(float), axis=0)


@strat("beta_rotation_breadth", F_BR)
def beta_rotation_breadth(x):
    b = x.breadth()
    st = hysteresis(b > 0.6, b < 0.4, x.fri)      # 1 = high beta, 0 = low beta
    hi_b = topn(x, _bab_beta(x), 20, x.fri)
    lo_b = topn(x, _bab_beta(x), 20, x.fri, low=True)
    return hi_b.mul(st, axis=0) + lo_b.mul(1 - st, axis=0)


@strat("breadth_200d_regime", F_BR)
def breadth_200d_regime(x):
    b = x.breadth()
    return univ(x).mul(hysteresis(b > 0.5, b < 0.4, x.fri), axis=0)


@strat("nh_nl_regime", F_BR)
def nh_nl_regime(x):
    nh = (x.ok & (x.C >= x.hi(252))).sum(axis=1)
    nl = (x.ok & (x.C <= x.lo(252))).sum(axis=1)
    s = (nh - nl).rolling(10).mean()
    return univ(x).mul(hysteresis(s > 0, s < 0, x.fri), axis=0)


@strat("breadth_thrust", F_BR)
def breadth_thrust(x):
    have = x.ok & x.R.notna()
    a = ((x.R > 0) & have).sum(axis=1) / have.sum(axis=1).replace(0, np.nan)
    t = a.ewm(span=10, adjust=False).mean()
    sig = (t > 0.615) & (t.rolling(10).min() < 0.40)
    return univ(x).mul(hold_n(sig, 63), axis=0)


@strat("breadth_washout_buy", F_BR)
def breadth_washout_buy(x):
    return univ(x).mul(hold_n(x.breadth(20) < 0.10, 20), axis=0)


@strat("ad_line_divergence", F_BR)
def ad_line_divergence(x):
    have = x.ok & x.R.notna()
    ad = (((x.R > 0) & have).sum(axis=1) - ((x.R < 0) & have).sum(axis=1)).cumsum()
    adh, adl = ad.rolling(63).max(), ad.rolling(63).min()
    div = (x.spy >= 0.98 * x.spy.rolling(63).max()) & (ad < adh - 0.05 * (adh - adl))
    return univ(x).mul(hysteresis(~div, div, x.fri), axis=0)


@strat("market_absorption_ratio", F_BR)
def market_absorption_ratio(x):
    R = x.R.values
    okv = x.ok.values
    ar = np.full(len(x.idx), np.nan)
    for i in range(252, len(x.idx)):
        y = R[i - 251:i + 1]
        good = ~np.isnan(y).any(axis=0) & okv[i]
        if good.sum() < 20:
            continue
        ev = np.linalg.eigvalsh(np.cov(y[:, good], rowvar=False))
        ar[i] = ev[-10:].sum() / ev.sum()
    ar = pd.Series(ar, index=x.idx)
    shift = (ar.rolling(15).mean() - ar.rolling(252).mean()) / ar.rolling(252).std()
    return univ(x).mul(hysteresis(shift <= 1, shift > 1, x.fri), axis=0)


@strat("market_turbulence", F_BR)
def market_turbulence(x):
    # No sector data here: 20 fixed stocks with prices over the whole period (picked by a fixed
    # random seed from those ever in the universe), as the idea list allows.
    full = [c for c in x.C.columns if x.C[c].notna().all() and x.M[c].any()]
    rng = np.random.default_rng(42)
    pick = list(rng.choice(sorted(full), size=min(20, len(full)), replace=False))
    R = x.R[pick].fillna(0).values
    d = np.full(len(x.idx), np.nan)
    for i in range(757, len(x.idx)):
        h = R[i - 756:i]
        mu = h.mean(axis=0)
        S = np.cov(h, rowvar=False)
        v = R[i] - mu
        d[i] = v @ np.linalg.pinv(S) @ v
    d = pd.Series(d, index=x.idx).rolling(20).mean()
    lim = d.shift(1).rolling(756, min_periods=500).quantile(0.8)
    return univ(x).mul(hysteresis(d < lim, d >= lim, x.fri), axis=0)


@strat("dual_momentum_top10", F_DUAL)
def dual_momentum_top10(x):
    r = x.ret(252)
    rk = r.where(x.ok).rank(axis=1, ascending=False, method="first")
    sel = (rk <= 10) & r.gt(x.tbill_cum(252), axis=0)
    return rebal(x, sel.astype(float) / 10, x.me)


@strat("protective_breadth_mom", F_DUAL)
def protective_breadth_mom(x):
    m = x.m13612()
    have = x.ok & m.notna()
    nbad = (have & (m <= 0)).sum(axis=1)
    cf = (2 * nbad / have.sum(axis=1).replace(0, np.nan)).clip(upper=1).fillna(1)
    w = topn(x, m, 10, x.me)
    return regime(x, w, 1 - cf, x.me)


@strat("canary_universe", F_DUAL)
def canary_universe(x):
    sm = x.m13612(x.spy.to_frame("s"))["s"]
    em = x.m13612(x.ew_index().to_frame("e"))["e"]
    return regime(x, topn(x, x.m13612(), 10, x.me), (sm > 0) & (em > 0), x.me)


@strat("winners_on_dips", F_TP)
def winners_on_dips(x):
    mom = x.ret(252, 21).where(x.ok)
    rk = mom.rank(axis=1, ascending=False).values
    r5 = x.ret(5).values
    out = np.zeros(x.C.shape)
    cohorts = []                                    # [start, set(cols)]
    n = len(x.idx)
    for i in range(n):
        if x.fri[i]:
            for c in cohorts:
                c[1] = {j for j in c[1] if rk[i, j] <= 30}
            cand = np.flatnonzero(rk[i] <= 20)
            if len(cand):
                pick = cand[np.argsort(r5[i, cand])][:5]
                pick = [j for j in pick if not np.isnan(r5[i, j])]
                cohorts.append([i, set(pick)])
        cohorts = [c for c in cohorts if i - c[0] < 10]
        for c in cohorts:
            for j in c[1]:
                out[i, j] += 0.10
    return pd.DataFrame(out, index=x.idx, columns=x.C.columns)


@strat("trend_pullback_ema", F_TP)
def trend_pullback_ema(x):
    s50, s200, e20 = x.sma(50), x.sma(200), x.ema(20)
    setup = (x.C > s200) & (s50 > s200) & (s50 > s50.shift(20)) & (x.L <= e20).astype(float).rolling(3).max().astype(bool)
    return alerts(x, setup & (x.C > x.H.shift(1)), exit=x.C < s50, max_hold=20)


@strat("high_near_52wh_pullback", F_TP)
def high_near_52wh_pullback(x):
    h = x.hi(252)
    entry = (x.C.shift(3) >= 0.95 * h.shift(3)) & (x.ret(3) <= -0.03)
    return alerts(x, entry, exit=x.C >= h, max_hold=10)


@strat("ma_slope_trend_strength", F_TP)
def ma_slope_trend_strength(x):
    er = (x.C - x.C.shift(20)).abs() / x.C.diff().abs().rolling(20).sum()
    s50 = x.sma(50)
    return alerts(x, (er > 0.5) & (x.C > x.C.shift(20)) & (x.C > s50), exit=(er < 0.2) | (x.C < s50))


@strat("mom_top_trend_exit", F_TP)
def mom_top_trend_exit(x):
    above = x.C > x.sma(100)
    return slots(x, x.ret(252, 21), 10, x.me, above, x.fri, above)


@strat("amihud_illiquid", F_VOL)
def amihud_illiquid(x):
    il = (x.R.abs() / (x.C * x.V).replace(0, np.nan)).rolling(252, min_periods=200).mean()
    return topn(x, il, 20, x.me)


@strat("high_volume_premium", F_VOL)
def high_volume_premium(x):
    av = x.V.rolling(5).mean() / x.V.shift(5).rolling(50).mean()
    r5 = x.ret(5)
    return topn(x, av, 10, x.me, eligible=r5.abs() <= 0.05)


@strat("obv_accumulation", F_VOL)
def obv_accumulation(x):
    obv = (np.sign(x.C.diff()).fillna(0) * x.V.fillna(0)).cumsum()
    s50 = x.sma(50)
    entry = (obv >= obv.rolling(20).max()) & (x.C < x.hi(20)) & (x.C > s50)
    return alerts(x, entry, exit=x.C < s50, max_hold=20)


@strat("chaikin_money_flow", F_VOL)
def chaikin_money_flow(x):
    mfm = ((x.C - x.L) - (x.H - x.C)) / (x.H - x.L).replace(0, np.nan)
    cmf = (mfm.fillna(0) * x.V).rolling(20).sum() / x.V.rolling(20).sum()
    s200 = x.sma(200)
    entry = (cmf > 0.10) & (cmf.shift(1) <= 0.10) & (x.C > s200)
    ex = ((cmf < -0.05) & (cmf.shift(1) >= -0.05)) | (x.C < s200)
    return alerts(x, entry, exit=ex)


@strat("volume_dryup_pullback", F_VOL)
def volume_dryup_pullback(x):
    av = x.avgv(50)
    dry = (x.C < x.C.shift(1)) & (x.V < av)
    setup = (x.C > x.sma(50)) & (x.sma(50) > x.sma(200)) & dry & dry.shift(1, fill_value=False) & dry.shift(2, fill_value=False)
    entry = setup.shift(1, fill_value=False) & (x.C > x.C.shift(1)) & (x.V > av)
    stop = x.L.shift(1).rolling(3).min()
    return alerts(x, entry, max_hold=20, stop=stop)


@strat("corr_spike_filter", F_COR)
def corr_spike_filter(x):
    have = x.ok & x.R.notna()
    rr = x.R.where(have)
    ew = rr.mean(axis=1)
    vp = ew.rolling(21).var()
    s = rr.rolling(21, min_periods=15).std().where(have)
    n = s.notna().sum(axis=1).replace(0, np.nan)
    ss, ss2 = s.sum(axis=1), (s ** 2).sum(axis=1)
    corr = (vp - ss2 / n ** 2) / ((ss / n) ** 2 - ss2 / n ** 2)
    hi = corr > corr.shift(1).rolling(756, min_periods=500).quantile(0.8)
    out = hi & ((x.spy / x.spy.shift(21) - 1) < 0)
    return univ(x).mul(hysteresis(~out, out, x.fri), axis=0)


@strat("dispersion_mom_switch", F_COR)
def dispersion_mom_switch(x):
    disp = x.ret(21).where(x.ok).std(axis=1)
    flag = (disp > disp.rolling(252).median()).astype(float).where(_row_mask(x, x.me)).ffill().fillna(0)
    u = rebal(x, univ(x), x.me)
    return _mom10(x).mul(flag, axis=0) + u.mul(1 - flag, axis=0)


@strat("low_corr_stocks", F_COR)
def low_corr_stocks(x):
    return topn(x, x.R.rolling(252, min_periods=200).corr(x.spy_r), 20, x.me, low=True)


@strat("first_new_high_after_base", F_52)
def first_new_high_after_base(x):
    nh = x.C >= x.hi(252)
    entry = nh & (nh.astype(float).shift(1).rolling(63).max() == 0)
    return alerts(x, entry, exit=x.C < x.sma(50))


@strat("universe_entry", F_52)
def universe_entry(x):
    new = x.M & ~x.M.shift(1, fill_value=False)
    new.iloc[:300] = False                       # the universe's first quarters aren't "entries"
    held = new.astype(float).rolling(63, min_periods=1).max().astype(bool) & x.M
    return eqw(held & x.C.notna())


@strat("near_52wk_low_avoid", F_52)
def near_52wk_low_avoid(x):
    return rebal(x, eqw(x.ok & (x.C > 1.05 * x.lo(252))), x.me)


def _composite_rank(x):
    def f():
        el = x.ok & x.ret(252, 21).notna() & x.vol(252).notna()
        a = x.ret(252, 21).where(el).rank(axis=1, ascending=False)
        b = x.vol(252).where(el).rank(axis=1, ascending=True)
        c = (x.C / x.hi(252)).where(el).rank(axis=1, ascending=False)
        return ((a + b + c) / 3).rank(axis=1, method="first")
    return x._get("comp", f)


@strat("composite_mom_lowvol_52wh", F_ENS)
def composite_mom_lowvol_52wh(x):
    r = _composite_rank(x)
    return slots(x, -r, 15, x.me, None, x.me, r <= 20)


@strat("trend_lowvol", F_ENS)
def trend_lowvol(x):
    return topn(x, x.vol(252), 20, x.me, eligible=x.C > x.sma(200), low=True)


@strat("trend_vote_5", F_ENS)
def trend_vote_5(x):
    rs = x.ret(63).sub(x.spy / x.spy.shift(63) - 1, axis=0)
    score = ((x.C > x.sma(200)).astype(int) + (x.sma(50) > x.sma(200)).astype(int) + (x.ret(252, 21) > 0).astype(int)
             + (x.C >= 0.9 * x.hi(252)).astype(int) + (rs > 0).astype(int))
    st = hysteresis_df((score >= 4) & x.ok, (score <= 2) | ~x.C.notna(), x.fri)
    return cap_eqw(st > 0, 0.05)


@strat("site_lineup_vote", F_ENS)
def site_lineup_vote(x):
    held = x._c.get("lineup_held")
    if held is None:
        raise RuntimeError("line-up positions not loaded")
    votes = sum(h.reindex(index=x.idx, columns=x.C.columns).fillna(0) for h in held.values())
    st = hysteresis_df(votes >= 3, votes <= 1, x.daily)
    st = st.where(~((st > 0) & (st.shift(1) == 0) & ~x.M), 0)    # new buys only inside the universe
    return eqw(st > 0)


@strat("mom_breadth_filter", F_ENS)
def mom_breadth_filter(x):
    return regime(x, _mom10(x), x.breadth() > 0.5, x.me)


@strat("factor_rotation_mom", F_ENS)
def factor_rotation_mom(x):
    subs = {k: REG[k][1](x) for k in ("mom_12_1", "lowvol_252", "mom_52wk_high")}
    perf = pd.DataFrame({k: (1 + _own(x, w)).rolling(126).apply(np.prod, raw=True) for k, w in subs.items()})
    best = perf.idxmax(axis=1).where(_row_mask(x, x.me)).ffill()
    out = sum(subs[k].mul((best == k).astype(float), axis=0) for k in subs)
    return out


@strat("multi_horizon_trend_score", F_ENS)
def multi_horizon_trend_score(x):
    score = sum(np.sign(x.ret(n)).fillna(0) for n in (21, 63, 126, 252))
    st = hysteresis_df((score >= 4) & x.ok, (score <= 2) | ~x.C.notna(), x.fri)
    return cap_eqw(st > 0, 0.05)


@strat("inv_vol_universe", F_RISK)
def inv_vol_universe(x):
    iv = (1 / x.vol(63)).where(x.ok)
    w = iv.div(iv.sum(axis=1), axis=0).fillna(0).values
    for _ in range(20):                                # cap 5%, spread the excess
        over = np.clip(w - 0.05, 0, None).sum(axis=1)
        w = np.minimum(w, 0.05)
        room = np.where(w < 0.05, w, 0)
        rs = room.sum(axis=1)
        w = w + room / np.where(rs > 0, rs, 1)[:, None] * over[:, None]
        if over.max() < 1e-9:
            break
    return rebal(x, pd.DataFrame(w, index=x.idx, columns=x.C.columns), x.me)


@strat("vol_target_mom", F_RISK)
def vol_target_mom(x):
    w = REG["composite_mom_lowvol_52wh"][1](x)
    v = _own(x, w).rolling(63, min_periods=42).std() * SQ
    return regime(x, w, (0.12 / v).clip(upper=1).fillna(1), x.fri)


@strat("drawdown_brake", F_RISK)
def drawdown_brake(x):
    u = univ(x)
    ur = _own(x, u).values
    cash = x.cash.values
    idx = x.ew_index()
    newhi = (idx >= idx.rolling(63).max()).values
    k = np.ones(len(ur))
    eq, hist, state = 1.0, [], 1.0
    for i in range(len(ur)):
        if i:
            eq *= 1 + k[i - 1] * ur[i] + (1 - k[i - 1]) * cash[i]
        hist.append(eq)
        if x.fri[i]:
            peak = max(hist[-252:])
            if state == 1.0 and eq / peak - 1 < -0.10:
                state = 0.5
            elif state == 0.5 and newhi[i]:
                state = 1.0
        k[i] = state
    return u.mul(pd.Series(k, index=x.idx), axis=0)


@strat("breadth_scaled_exposure", F_RISK)
def breadth_scaled_exposure(x):
    e = ((x.breadth() - 0.30) / 0.40).clip(0, 1)
    return regime(x, univ(x), e, x.fri)
