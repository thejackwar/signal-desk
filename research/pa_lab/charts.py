"""Higher charts from the 1-minute bars, and the detectors behind every setup.

Every chart keeps, per bar, the index of its first and last 1-minute bar. A bar is usable only from its close:
anything decided on chart bar i happens at 1-minute index end[i] (its close), never earlier.
ATR is Wilder's 14-bar ATR using only bars before the current one (atr[i] is known when bar i opens).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numba as nb
import numpy as np
import pandas as pd

from . import config as C


@dataclass
class Chart:
    name: str
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    start: np.ndarray        # first 1-minute index in the bar
    end: np.ndarray          # last 1-minute index in the bar (the bar is usable from end[i])
    sess: np.ndarray         # session number
    atr: np.ndarray          # Wilder ATR over bars before this one
    bar_of_1m: np.ndarray    # chart bar index of each 1-minute bar
    cache: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.c)


@dataclass
class Market:
    symbol: str
    m1: pd.DataFrame
    sessions: np.ndarray     # session date strings, by session number
    charts: dict
    news: np.ndarray         # 0/1 per session number
    minute: np.ndarray       # minute of day per 1-minute bar
    sess1m: np.ndarray       # session number per 1-minute bar


@nb.njit(cache=True)
def wilder_atr(h, l, c, n):
    out = np.full(len(c), np.nan)
    if len(c) < 2:
        return out
    tr = np.empty(len(c))
    tr[0] = h[0] - l[0]
    for i in range(1, len(c)):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    a = np.nan
    for i in range(len(c)):
        # atr[i] uses tr[0..i-1] only
        if i == n:
            a = tr[:n].mean()
        elif i > n:
            a = (a * (n - 1) + tr[i - 1]) / n
        out[i] = a
    return out


def _group_keys(minute: np.ndarray, sess: np.ndarray, chart: str) -> np.ndarray:
    m = minute.astype(np.int64) - C.OPEN_MIN
    if chart == "1d":
        b = np.zeros_like(m)
    elif chart == "4h":
        b = (m >= 240).astype(np.int64)
    else:
        b = m // C.CHART_MIN[chart]
    return sess.astype(np.int64) * 1000 + b


def build_chart(name: str, o, h, l, c, minute, sess) -> Chart:
    n = len(c)
    if name == "1m":
        idx = np.arange(n)
        return Chart(name, o, h, l, c, idx, idx, sess, wilder_atr(h, l, c, C.ATR_N), idx)
    key = _group_keys(minute, sess, name)
    change = np.r_[True, key[1:] != key[:-1]]
    starts = np.flatnonzero(change)
    ends = np.r_[starts[1:] - 1, n - 1]
    bo = o[starts]
    bh = np.maximum.reduceat(h, starts)
    bl = np.minimum.reduceat(l, starts)
    bc = c[ends]
    bar_of = np.cumsum(change) - 1
    return Chart(name, bo, bh, bl, bc, starts, ends, sess[starts], wilder_atr(bh, bl, bc, C.ATR_N), bar_of)


def load_news() -> dict[str, str]:
    df = pd.read_csv(C.ROOT / "news_days.csv", comment="#")
    return dict(zip(df["date"], df["event"]))


def build_market(symbol: str, m1: pd.DataFrame) -> Market:
    m1 = m1.reset_index(drop=True)
    sess_codes, sessions = pd.factorize(m1["session"], sort=True)
    sess = sess_codes.astype(np.int64)
    o, h, l, c = (m1[k].to_numpy(np.float64) for k in ("open", "high", "low", "close"))
    minute = m1["minute"].to_numpy(np.int64)
    charts = {name: build_chart(name, o, h, l, c, minute, sess) for name in C.CHARTS}
    news_map = load_news()
    news = np.array([1 if s in news_map else 0 for s in sessions], dtype=np.int8)
    return Market(symbol, m1, np.asarray(sessions), charts, news, minute, sess)


# ======================================================================= detectors
@nb.njit(cache=True)
def swings(h, l, k):
    """Swing highs/lows of strength k. A swing at bar i is confirmed at bar i+k (its close).
    Returns arrays (pivot_bar, confirm_bar, price, kind) with kind +1 high, -1 low, sorted by confirm bar."""
    n = len(h)
    pb = np.empty(2 * n, np.int64); cb = np.empty(2 * n, np.int64); pr = np.empty(2 * n); kd = np.empty(2 * n, np.int64)
    m = 0
    for i in range(k, n - k):
        hi = True
        lo = True
        for j in range(i - k, i):
            if h[j] >= h[i]:
                hi = False
            if l[j] <= l[i]:
                lo = False
        for j in range(i + 1, i + k + 1):
            if h[j] > h[i]:
                hi = False
            if l[j] < l[i]:
                lo = False
        if hi:
            pb[m] = i; cb[m] = i + k; pr[m] = h[i]; kd[m] = 1; m += 1
        if lo:
            pb[m] = i; cb[m] = i + k; pr[m] = l[i]; kd[m] = -1; m += 1
    return pb[:m], cb[:m], pr[:m], kd[:m]


@nb.njit(cache=True)
def structure(c, n_bars, sw_cb, sw_pr, sw_kd):
    """Per bar (as of its close): trend from the last two swing highs and lows (+1 HH+HL, -1 LH+LL, else 0),
    last confirmed swing high/low, and break events: the first close past the last confirmed swing.
    A break with the trend (or from no trend) is a break of structure (kind 1); against it a change of character (kind 2).
    Returns trend, last_hi, last_lo, ev_bar, ev_dir, ev_kind, ev_level."""
    trend = np.zeros(n_bars, np.int64)
    last_hi = np.full(n_bars, np.nan)
    last_lo = np.full(n_bars, np.nan)
    ev_bar = np.empty(n_bars, np.int64); ev_dir = np.empty(n_bars, np.int64); ev_kind = np.empty(n_bars, np.int64); ev_lvl = np.empty(n_bars)
    ne = 0
    h1 = np.nan; h2 = np.nan; l1 = np.nan; l2 = np.nan   # latest and previous swing highs / lows
    hi_live = False; lo_live = False
    t = 0
    j = 0
    ns = len(sw_cb)
    for i in range(n_bars):
        while j < ns and sw_cb[j] <= i:
            if sw_kd[j] == 1:
                h2 = h1; h1 = sw_pr[j]; hi_live = True
            else:
                l2 = l1; l1 = sw_pr[j]; lo_live = True
            j += 1
        if not np.isnan(h2) and not np.isnan(l2):
            if h1 > h2 and l1 > l2:
                t = 1
            elif h1 < h2 and l1 < l2:
                t = -1
        # breaks are judged against the trend as it stood before this bar's break
        if hi_live and c[i] > h1:
            ev_bar[ne] = i; ev_dir[ne] = 1; ev_kind[ne] = 2 if t == -1 else 1; ev_lvl[ne] = h1; ne += 1
            hi_live = False
            t = 1 if t == -1 else t
        if lo_live and c[i] < l1:
            ev_bar[ne] = i; ev_dir[ne] = -1; ev_kind[ne] = 2 if t == 1 else 1; ev_lvl[ne] = l1; ne += 1
            lo_live = False
            t = -1 if t == 1 else t
        trend[i] = t
        last_hi[i] = h1
        last_lo[i] = l1
    return trend, last_hi, last_lo, ev_bar[:ne], ev_dir[:ne], ev_kind[:ne], ev_lvl[:ne]


def get_structure(ch: Chart, k: int):
    key = ("st", k)
    if key not in ch.cache:
        sw = swings(ch.h, ch.l, k)
        ch.cache[("sw", k)] = sw
        ch.cache[key] = structure(ch.c, len(ch), sw[1], sw[2], sw[3])
    return ch.cache[key]


def get_swings(ch: Chart, k: int):
    get_structure(ch, k)
    return ch.cache[("sw", k)]


@nb.njit(cache=True)
def levels_at(i, atr_i, sw_cb, sw_pr, look, tol):
    """Clusters of confirmed swings (confirmed before bar i, within the last `look` swings) whose prices lie
    within tol*ATR of each other. Returns arrays (lo, hi, mid, touches) per cluster."""
    j = np.searchsorted(sw_cb, i, side="left")   # swings confirmed strictly before bar i
    a = max(0, j - look)
    p = np.sort(sw_pr[a:j])
    m = len(p)
    lo = np.empty(m); hi = np.empty(m); mid = np.empty(m); cnt = np.empty(m, np.int64)
    nc = 0
    s = 0
    while s < m:
        e = s
        while e + 1 < m and p[e + 1] - p[s] <= tol * atr_i:
            e += 1
        lo[nc] = p[s]; hi[nc] = p[e]; mid[nc] = p[s:e + 1].mean(); cnt[nc] = e - s + 1; nc += 1
        s = e + 1
    return lo[:nc], hi[:nc], mid[:nc], cnt[:nc]


def fvgs(ch: Chart):
    """Three-candle fair value gaps, known at the close of the third candle (bar i).
    Bullish: low[i] > high[i-2] (gap high[i-2]..low[i]); bearish: high[i] < low[i-2]."""
    if "fvg" in ch.cache:
        return ch.cache["fvg"]
    h, l = ch.h, ch.l
    i = np.arange(2, len(ch))
    bull = i[l[i] > h[i - 2]]
    bear = i[h[i] < l[i - 2]]
    out = []
    for b in bull:
        out.append((b, 1, h[b - 2], l[b], l[b - 1]))       # bar, dir, gap low, gap high, middle candle low
    for b in bear:
        out.append((b, -1, h[b], l[b - 2], h[b - 1]))      # middle candle high
    out.sort()
    arr = np.array(out, dtype=np.float64).reshape(-1, 5)
    ch.cache["fvg"] = arr
    return arr


def zones(ch: Chart, run_min: int = 2, run_max: int = 5, cover_lo: float = 1.5, cover_hi: float = 3.0):
    """Supply/demand zones: the base candle before a run of 2-5 same-colour candles that together cover
    1.5-3 ATR. Demand (dir +1) after an up run: zone = base candle low..high. Known at the run's last close."""
    key = ("zone", run_min, run_max, cover_lo, cover_hi)
    if key in ch.cache:
        return ch.cache[key]
    o, h, l, c, atr = ch.o, ch.h, ch.l, ch.c, ch.atr
    col = np.sign(c - o)
    out = []
    n = len(ch)
    i = 1
    while i < n:
        d = col[i]
        if d == 0:
            i += 1
            continue
        base = i - 1
        a = atr[base]
        j = i
        # walk the run bar by bar; the zone is known at the first bar where the run qualifies (no peeking
        # at whether the run goes on)
        while True:
            L = j - i + 1
            if L >= run_min and not np.isnan(a) and a > 0:
                cover = (h[i:j + 1].max() - l[base]) if d > 0 else (h[base] - l[i:j + 1].min())
                if cover_lo * a <= cover <= cover_hi * a:
                    out.append((j, int(d), l[base], h[base], base))
                    break
                if cover > cover_hi * a:
                    break
            if j + 1 < n and col[j + 1] == d and L < run_max:
                j += 1
            else:
                break
        while j + 1 < n and col[j + 1] == d:
            j += 1
        i = j + 1
    arr = np.array(out, dtype=np.float64).reshape(-1, 5)
    ch.cache[key] = arr
    return arr
