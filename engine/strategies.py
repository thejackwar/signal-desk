"""
Strategy engine.

Every strategy is a function that takes a daily OHLC DataFrame
(columns: open, high, low, close, volume; DatetimeIndex) and returns a
StrategyResult:

  signals  -> pd.Series aligned to df.index: +1 = bullish alert, -1 = bearish alert, 0 = nothing
  notes    -> dict {bar_index: "human readable reason"} for bars with a signal
  overlays -> lines to draw on the price chart   [{name, values(list), style}]
  panel    -> optional indicator pane under the chart (RSI / MACD)
  shapes   -> pattern drawings [{name, points: [[bar_index, price], ...]}]

The same functions power both the live scanner (scan.py) and the
worked examples shown in the app (build_examples.py), so what you read
in the explanation is exactly what triggers the alerts.

Informational only. Nothing here is investment advice.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class StrategyResult:
    signals: pd.Series
    notes: dict = field(default_factory=dict)
    overlays: list = field(default_factory=list)
    panel: dict | None = None
    shapes: list = field(default_factory=list)


def P(v) -> str:
    """Price for alert text: 2 decimals, or 5 significant digits for cheap coins."""
    v = float(v)
    return f"{v:,.2f}" if abs(v) >= 1 else f"{v:.5g}"


# ---------------------------------------------------------------- indicators

def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def crossed_above(a: pd.Series, b) -> pd.Series:
    b = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossed_below(a: pd.Series, b) -> pd.Series:
    b = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return (a < b) & (a.shift(1) >= b.shift(1))


def _vals(s: pd.Series) -> list:
    return [None if pd.isna(v) else round(float(v), 4) for v in s]


def _signals(df, buy: pd.Series, sell: pd.Series) -> pd.Series:
    sig = pd.Series(0, index=df.index, dtype=int)
    sig[buy.fillna(False)] = 1
    sig[sell.fillna(False)] = -1
    return sig


# ------------------------------------------------------- indicator strategies

def ma200(df: pd.DataFrame) -> StrategyResult:
    c = df["close"]
    m = sma(c, 200)
    sig = _signals(df, crossed_above(c, m), crossed_below(c, m))
    notes = {}
    for i in np.flatnonzero(sig.values):
        side = "above" if sig.iloc[i] > 0 else "below"
        notes[int(i)] = f"Closed {side} the 200-day average ({P(m.iloc[i])})"
    return StrategyResult(sig, notes, [{"name": "SMA 200", "values": _vals(m), "style": "accent"}])


def bollinger(df: pd.DataFrame, n: int = 20, k: float = 2.0) -> StrategyResult:
    c = df["close"]
    mid = sma(c, n)
    sd = c.rolling(n, min_periods=n).std(ddof=0)
    up, lo = mid + k * sd, mid - k * sd
    # mean reversion: price was outside the band and closes back inside
    buy = (c > lo) & (c.shift(1) < lo.shift(1))
    sell = (c < up) & (c.shift(1) > up.shift(1))
    sig = _signals(df, buy, sell)
    notes = {}
    for i in np.flatnonzero(sig.values):
        notes[int(i)] = ("Closed back inside the lower band after a drop below it"
                         if sig.iloc[i] > 0 else
                         "Closed back inside the upper band after a spike above it")
    return StrategyResult(sig, notes, [
        {"name": "Upper band", "values": _vals(up), "style": "band"},
        {"name": "Middle (SMA 20)", "values": _vals(mid), "style": "muted"},
        {"name": "Lower band", "values": _vals(lo), "style": "band"},
    ])


def donchian(df: pd.DataFrame, n: int = 20) -> StrategyResult:
    hi = df["high"].rolling(n, min_periods=n).max().shift(1)
    lo = df["low"].rolling(n, min_periods=n).min().shift(1)
    c = df["close"]
    buy = (c > hi) & (c.shift(1) <= hi.shift(1))
    sell = (c < lo) & (c.shift(1) >= lo.shift(1))
    sig = _signals(df, buy, sell)
    notes = {}
    for i in np.flatnonzero(sig.values):
        notes[int(i)] = (f"New {n}-day high: closed above {P(hi.iloc[i])}" if sig.iloc[i] > 0
                         else f"New {n}-day low: closed below {P(lo.iloc[i])}")
    return StrategyResult(sig, notes, [
        {"name": f"{n}-day high", "values": _vals(hi), "style": "band"},
        {"name": f"{n}-day low", "values": _vals(lo), "style": "band"},
    ])


def rsi_strategy(df: pd.DataFrame, n: int = 14, low: float = 30, high: float = 70) -> StrategyResult:
    r = rsi(df["close"], n)
    sig = _signals(df, crossed_above(r, low), crossed_below(r, high))
    notes = {}
    for i in np.flatnonzero(sig.values):
        notes[int(i)] = (f"RSI climbed back above {low:.0f} (now {r.iloc[i]:.0f}): leaving oversold"
                         if sig.iloc[i] > 0 else
                         f"RSI fell back below {high:.0f} (now {r.iloc[i]:.0f}): leaving overbought")
    panel = {"name": "RSI 14", "lines": [{"name": "RSI", "values": _vals(r), "style": "accent"}],
             "levels": [low, high], "min": 0, "max": 100}
    return StrategyResult(sig, notes, [], panel)


def macd_strategy(df: pd.DataFrame) -> StrategyResult:
    c = df["close"]
    line = ema(c, 12) - ema(c, 26)
    signal = ema(line, 9)
    valid = pd.Series(np.arange(len(df)) >= 35, index=df.index)
    sig = _signals(df, crossed_above(line, signal) & valid, crossed_below(line, signal) & valid)
    notes = {}
    for i in np.flatnonzero(sig.values):
        where = "below zero" if line.iloc[i] < 0 else "above zero"
        notes[int(i)] = (f"MACD crossed above its signal line ({where})" if sig.iloc[i] > 0
                         else f"MACD crossed below its signal line ({where})")
    panel = {"name": "MACD 12/26/9", "lines": [
        {"name": "MACD", "values": _vals(line.where(valid)), "style": "accent"},
        {"name": "Signal", "values": _vals(signal.where(valid)), "style": "muted"}],
        "hist": _vals((line - signal).where(valid)), "levels": [0]}
    return StrategyResult(sig, notes, [], panel)


def golden_cross(df: pd.DataFrame) -> StrategyResult:
    c = df["close"]
    f, s = sma(c, 50), sma(c, 200)
    sig = _signals(df, crossed_above(f, s), crossed_below(f, s))
    notes = {int(i): ("Golden cross: 50-day average crossed above the 200-day"
                      if sig.iloc[i] > 0 else "Death cross: 50-day average crossed below the 200-day")
             for i in np.flatnonzero(sig.values)}
    return StrategyResult(sig, notes, [
        {"name": "SMA 50", "values": _vals(f), "style": "accent2"},
        {"name": "SMA 200", "values": _vals(s), "style": "accent"},
    ])


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average true range, Wilder's smoothing (the first bar's true range is just high - low)."""
    pc = df["close"].shift(1)
    tr = np.fmax(df["high"] - df["low"], np.fmax((df["high"] - pc).abs(), (df["low"] - pc).abs()))
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> StrategyResult:
    """A line `mult` ATRs below the price in an uptrend (above it in a downtrend) that only moves
    in the trend's direction. Closing through it flips the trend: that flip is the alert."""
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    a = atr(df, n).values
    mid = (h + l) / 2
    N = len(df)
    up, lo = np.full(N, np.nan), np.full(N, np.nan)
    trend = np.zeros(N, dtype=int)
    sig = pd.Series(0, index=df.index, dtype=int)
    notes = {}
    for i in range(N):
        if not np.isfinite(a[i]):
            continue
        bu, bl = mid[i] + mult * a[i], mid[i] - mult * a[i]
        if i == 0 or not np.isfinite(up[i - 1]):
            up[i], lo[i] = bu, bl
            trend[i] = 1 if c[i] > bu else -1 if c[i] < bl else 0
            continue
        up[i] = bu if bu < up[i - 1] or c[i - 1] > up[i - 1] else up[i - 1]
        lo[i] = bl if bl > lo[i - 1] or c[i - 1] < lo[i - 1] else lo[i - 1]
        t = trend[i - 1]
        if t >= 0 and c[i] < lo[i]:
            trend[i] = -1
        elif t <= 0 and c[i] > up[i]:
            trend[i] = 1
        else:
            trend[i] = t
        if t != 0 and trend[i] != t:               # a real flip, not the first reading
            sig.iloc[i] = trend[i]
            notes[i] = (f"Supertrend turned up: closed above {P(up[i])}" if trend[i] > 0
                        else f"Supertrend turned down: closed below {P(lo[i])}")
    line_up = np.where(trend > 0, lo, np.nan)      # the line that matters: support in an uptrend
    line_dn = np.where(trend < 0, up, np.nan)      # resistance in a downtrend
    return StrategyResult(sig, notes, [
        {"name": "Supertrend (uptrend)", "values": _vals(pd.Series(line_up)), "style": "accent2"},
        {"name": "Supertrend (downtrend)", "values": _vals(pd.Series(line_dn)), "style": "accent"},
    ])


def volume_breakout(df: pd.DataFrame, n: int = 20, vol_mult: float = 1.5) -> StrategyResult:
    """A Donchian breakout that only counts when volume is at least `vol_mult` x its n-day average."""
    hi = df["high"].rolling(n, min_periods=n).max().shift(1)
    lo = df["low"].rolling(n, min_periods=n).min().shift(1)
    c = df["close"]
    v = df["volume"].astype(float).where(df["volume"] > 0)
    avg = v.rolling(n, min_periods=n).mean().shift(1)
    heavy = v >= vol_mult * avg
    buy = (c > hi) & (c.shift(1) <= hi.shift(1)) & heavy
    sell = (c < lo) & (c.shift(1) >= lo.shift(1)) & heavy
    sig = _signals(df, buy, sell)
    notes = {}
    for i in np.flatnonzero(sig.values):
        x = v.iloc[i] / avg.iloc[i]
        notes[int(i)] = (f"New {n}-day high on {x:.1f}x normal volume: closed above {P(hi.iloc[i])}" if sig.iloc[i] > 0
                         else f"New {n}-day low on {x:.1f}x normal volume: closed below {P(lo.iloc[i])}")
    return StrategyResult(sig, notes, [
        {"name": f"{n}-day high", "values": _vals(hi), "style": "band"},
        {"name": f"{n}-day low", "values": _vals(lo), "style": "band"},
    ], {"name": f"Volume vs {n}-day average", "lines": [
        {"name": "Volume ÷ average", "values": _vals(v / avg), "style": "accent"}], "levels": [1, vol_mult]})


# What relative strength is measured against: set by the scanner / backtest before running
# (SPY for stocks, bitcoin for crypto, the average of all 19 for commodities).
BENCHMARK = {"close": None, "name": "the market"}


def set_benchmark(close: pd.Series | None, name: str):
    BENCHMARK["close"], BENCHMARK["name"] = close, name


def relative_strength(df: pd.DataFrame, n: int = 63) -> StrategyResult:
    """RS line = price / benchmark. Bullish when it closes at a new n-day high (the stock starts
    leading the market), bearish at a new n-day low (lagging). Only switches alert: after a bullish
    alert the next one is bearish, so a leader that keeps making new highs alerts once."""
    b = BENCHMARK["close"]
    c = df["close"]
    if b is None:
        return StrategyResult(pd.Series(0, index=df.index, dtype=int))
    b = b.copy()
    if b.index.tz is not None:
        b.index = b.index.tz_localize(None)
    idx = df.index.tz_localize(None) if df.index.tz is not None else df.index
    b = b.reindex(b.index.union(idx)).ffill().reindex(idx)
    rs = pd.Series(c.values / b.values * 100, index=df.index)
    hi = rs.rolling(n, min_periods=n).max().shift(1)
    lo = rs.rolling(n, min_periods=n).min().shift(1)
    # alert only on a switch: the first 3-month high since the last 3-month low, and vice versa
    raw = _signals(df, rs > hi, rs < lo).values
    sig = pd.Series(0, index=df.index, dtype=int)
    state = 0
    for i in np.flatnonzero(raw):
        if raw[i] != state:
            state = raw[i]
            sig.iloc[i] = state
    notes = {}
    name = BENCHMARK["name"]
    for i in np.flatnonzero(sig.values):
        j = max(0, i - n)
        diff = ((c.iloc[i] / c.iloc[j]) / (b.iloc[i] / b.iloc[j]) - 1) * 100 if np.isfinite(b.iloc[j]) else np.nan
        by = f" ({diff:+.1f}% vs {name} over 3 months)" if np.isfinite(diff) else ""
        notes[int(i)] = (f"Leading {name}: relative strength at a 3-month high{by}" if sig.iloc[i] > 0
                         else f"Lagging {name}: relative strength at a 3-month low{by}")
    panel = {"name": f"Relative strength vs {name}", "lines": [
        {"name": f"Price ÷ {name}", "values": _vals(rs), "style": "accent"},
        {"name": "Its 3-month high", "values": _vals(hi), "style": "muted"}]}
    return StrategyResult(sig, notes, [], panel)


# ----------------------------------------------------------- chart patterns

def pivots(df: pd.DataFrame, k: int = 5):
    """Swing highs/lows. A pivot at bar i is only *known* at bar i + k."""
    h, l = df["high"].values, df["low"].values
    highs, lows = [], []
    for i in range(k, len(df) - k):
        if h[i] == h[i - k:i + k + 1].max():
            highs.append(i)
        if l[i] == l[i - k:i + k + 1].min():
            lows.append(i)
    return highs, lows


def _known(piv, t, k):
    return [p for p in piv if p + k <= t]


def double_top_bottom(df: pd.DataFrame, k: int = 5, tol: float = 0.03,
                      min_gap: int = 10, max_gap: int = 80, depth: float = 0.04) -> StrategyResult:
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    ph, pl = pivots(df, k)
    sig = pd.Series(0, index=df.index, dtype=int)
    notes, shapes, used = {}, [], set()
    for t in range(1, len(df)):
        kh, kl = _known(ph, t, k), _known(pl, t, k)
        # double bottom: last two swing lows ~equal, swing high between = neckline
        if len(kl) >= 2:
            a, b = kl[-2], kl[-1]
            mids = [p for p in kh if a < p < b]
            if (min_gap <= b - a <= max_gap and mids and t - b <= 30 and ("DB", a, b) not in used
                    and abs(l[a] - l[b]) / l[a] <= tol):
                m = max(mids, key=lambda p: h[p])
                neck = h[m]
                if neck >= max(l[a], l[b]) * (1 + depth) and c[t] > neck >= c[t - 1]:
                    used.add(("DB", a, b))
                    sig.iloc[t] = 1
                    notes[t] = f"Double bottom: broke above the neckline at {P(neck)}"
                    shapes.append({"at": t, "name": "Double bottom", "points": [[a, l[a]], [m, neck], [b, l[b]], [t, c[t]]]})
                    shapes.append({"at": t, "name": "Neckline", "points": [[m, neck], [t, neck]], "dash": True})
                    continue
        if len(kh) >= 2:
            a, b = kh[-2], kh[-1]
            mids = [p for p in kl if a < p < b]
            if (min_gap <= b - a <= max_gap and mids and t - b <= 30 and ("DT", a, b) not in used
                    and abs(h[a] - h[b]) / h[a] <= tol):
                m = min(mids, key=lambda p: l[p])
                neck = l[m]
                if neck <= min(h[a], h[b]) * (1 - depth) and c[t] < neck <= c[t - 1]:
                    used.add(("DT", a, b))
                    sig.iloc[t] = -1
                    notes[t] = f"Double top: broke below the neckline at {P(neck)}"
                    shapes.append({"at": t, "name": "Double top", "points": [[a, h[a]], [m, neck], [b, h[b]], [t, c[t]]]})
                    shapes.append({"at": t, "name": "Neckline", "points": [[m, neck], [t, neck]], "dash": True})
    return StrategyResult(sig, notes, [], None, shapes)


def head_shoulders(df: pd.DataFrame, k: int = 5, tol: float = 0.05, max_span: int = 120) -> StrategyResult:
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    ph, pl = pivots(df, k)
    sig = pd.Series(0, index=df.index, dtype=int)
    notes, shapes, used = {}, [], set()

    def neck_at(p1, v1, p2, v2, x):
        return v1 + (v2 - v1) * (x - p1) / (p2 - p1)

    for t in range(1, len(df)):
        kh, kl = _known(ph, t, k), _known(pl, t, k)
        # classic (bearish): high - higher high - high, neckline through the two troughs
        if len(kh) >= 3:
            ls, hd, rs = kh[-3], kh[-2], kh[-1]
            tr1 = [p for p in kl if ls < p < hd]
            tr2 = [p for p in kl if hd < p < rs]
            if (tr1 and tr2 and rs - ls <= max_span and t - rs <= 25 and ("HS", ls) not in used
                    and h[hd] > max(h[ls], h[rs]) * 1.02 and abs(h[ls] - h[rs]) / h[ls] <= tol):
                t1, t2 = min(tr1, key=lambda p: l[p]), min(tr2, key=lambda p: l[p])
                n_now, n_prev = neck_at(t1, l[t1], t2, l[t2], t), neck_at(t1, l[t1], t2, l[t2], t - 1)
                if c[t] < n_now and c[t - 1] >= n_prev:
                    used.add(("HS", ls))
                    sig.iloc[t] = -1
                    notes[t] = f"Head & shoulders: closed below the neckline (~{P(n_now)})"
                    shapes.append({"at": t, "name": "Head & shoulders",
                                   "points": [[ls, h[ls]], [t1, l[t1]], [hd, h[hd]], [t2, l[t2]], [rs, h[rs]]]})
                    shapes.append({"at": t, "name": "Neckline", "points": [[t1, l[t1]], [t, n_now]], "dash": True})
                    continue
        # inverse (bullish)
        if len(kl) >= 3:
            ls, hd, rs = kl[-3], kl[-2], kl[-1]
            pk1 = [p for p in kh if ls < p < hd]
            pk2 = [p for p in kh if hd < p < rs]
            if (pk1 and pk2 and rs - ls <= max_span and t - rs <= 25 and ("IHS", ls) not in used
                    and l[hd] < min(l[ls], l[rs]) * 0.98 and abs(l[ls] - l[rs]) / l[ls] <= tol):
                p1, p2 = max(pk1, key=lambda p: h[p]), max(pk2, key=lambda p: h[p])
                n_now, n_prev = neck_at(p1, h[p1], p2, h[p2], t), neck_at(p1, h[p1], p2, h[p2], t - 1)
                if c[t] > n_now and c[t - 1] <= n_prev:
                    used.add(("IHS", ls))
                    sig.iloc[t] = 1
                    notes[t] = f"Inverse head & shoulders: closed above the neckline (~{P(n_now)})"
                    shapes.append({"at": t, "name": "Inverse head & shoulders",
                                   "points": [[ls, l[ls]], [p1, h[p1]], [hd, l[hd]], [p2, h[p2]], [rs, l[rs]]]})
                    shapes.append({"at": t, "name": "Neckline", "points": [[p1, h[p1]], [t, n_now]], "dash": True})
    return StrategyResult(sig, notes, [], None, shapes)


def triangle(df: pd.DataFrame, k: int = 4, lookback: int = 70) -> StrategyResult:
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    ph, pl = pivots(df, k)
    sig = pd.Series(0, index=df.index, dtype=int)
    notes, shapes, used = {}, [], set()
    for t in range(1, len(df)):
        kh = [p for p in _known(ph, t, k) if p >= t - lookback]
        kl = [p for p in _known(pl, t, k) if p >= t - lookback]
        if len(kh) < 2 or len(kl) < 2:
            continue
        hx, lx = kh[-3:], kl[-3:]
        sh, ih = np.polyfit(hx, h[hx], 1)
        sl, il = np.polyfit(lx, l[lx], 1)
        ref = c[t]
        flat = 0.0006 * ref  # per-bar slope that counts as "flat"
        # converging: upper not rising, lower not falling, at least one clearly sloped
        if not (sh <= flat and sl >= -flat and (sh < -flat or sl > flat)):
            continue
        start = min(hx[0], lx[0])
        if t - start < 15 or ("T", hx[0], lx[0]) in used:
            continue
        # every touch must sit close to its line (a real, tidy pattern)
        if (np.max(np.abs(h[hx] - (sh * np.array(hx) + ih))) > 0.02 * ref or
                np.max(np.abs(l[lx] - (sl * np.array(lx) + il))) > 0.02 * ref):
            continue
        up_t, lo_t = sh * t + ih, sl * t + il
        up_p, lo_p = sh * (t - 1) + ih, sl * (t - 1) + il
        if up_t <= lo_t:
            continue
        kind = ("Ascending triangle" if abs(sh) <= flat else
                "Descending triangle" if abs(sl) <= flat else "Symmetrical triangle")
        hit = 0
        if c[t] > up_t and c[t - 1] <= up_p:
            hit = 1
        elif c[t] < lo_t and c[t - 1] >= lo_p:
            hit = -1
        if hit:
            used.add(("T", hx[0], lx[0]))
            sig.iloc[t] = hit
            notes[t] = f"{kind}: broke {'out above' if hit > 0 else 'down below'} the pattern"
            shapes.append({"at": t, "name": "Upper line", "points": [[start, sh * start + ih], [t, up_t]]})
            shapes.append({"at": t, "name": "Lower line", "points": [[start, sl * start + il], [t, lo_t]]})
    return StrategyResult(sig, notes, [], None, shapes)


REGISTRY = {
    "ma200": ma200,
    "bollinger": bollinger,
    "donchian": donchian,
    "rsi": rsi_strategy,
    "macd": macd_strategy,
    "golden_cross": golden_cross,
    "supertrend": supertrend,
    "volume_breakout": volume_breakout,
    "relative_strength": relative_strength,
    "double_top_bottom": double_top_bottom,
    "head_shoulders": head_shoulders,
    "triangle": triangle,
}
