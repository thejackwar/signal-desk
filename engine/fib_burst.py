"""Fibonacci on a burst: Bitcoin makes a sharp move, draw the levels on it, trade the pullback.

The idea (Giacomo, 11 Nov 2026, from a BTCUSD 1-minute chart): BTC jumped ~1.4% in ~13 minutes on a volume
spike (73,312 -> 74,377); levels drawn from that low to that high; price pulled back to the 50% level twice,
held, and went back up. Here the burst is found by a fixed rule, so there is no hindsight in where the levels go.

Exact rules tested here (every one is a switch, see VARIANTS):
  Data        Binance BTCUSDT spot, 1-minute candles, from 2021-01-01. Trades 24/7.
  Burst       price moves at least X% (0.5, 1 (default), 1.5, 2) from the lowest low to the current high within
              the last N minutes (5, 15 (default), 30), and the high is being made right now. Volume filter:
              "any", or the burst's minutes averaged at least 2x (default) / 3x the average minute of the previous
              24 hours. A sell-off burst is the mirror (we then short the bounce).
  One candle  a 1-minute candle can hold the whole move (its own low AND high are the window's extremes), and
              1-minute data cannot say which came first. "keep" (default, the original behaviour): such a candle
              counts as an up burst whenever it qualifies as one; "skip": ignore bursts whose low (up) / high (down)
              is in the trigger candle itself; "close": a candle that makes a new high and a new low at once is read
              by its close - closed down (red) = the spike came first, a sell-off; closed up = a burst up.
              (Found on 10 Nov 2026 14:30: spike to 74,770, crash to 73,525 in one candle, traded as a long.)
  Levels      from the burst's low (100%) to its high (0%). While price keeps making new highs the levels are
              redrawn up to the new high (as you would on the chart); the low stays where it is.
  Entry       a limit order at the 38.2%, 50% (default) or 61.8% pullback - filled the first minute that reaches
              it (at the minute's open if it gaps through) - "touch" (default); or "confirm": a minute that
              reaches the level and closes back above it, enter at that close; or "bounce": like "confirm" but the
              minute must also be a green candle (closes above its open; red for a short) - a real sign the level
              is holding, not a red candle that happens to close a few dollars above it; or "zone": enter the first minute
              price trades between 38.2% and 50% (at the 38.2% level, or at the minute's open if it opens inside
              the zone; a minute that opens past 50% does not count).
  Slowing     "off" (default) or "on": only enter when the pullback has paused - at least one of the last 2
              CLOSED candles (before the entry minute) closed against the pullback (green or flat for a long, red or
              flat for a short), and the entry minute does not open more than 10% of the burst past the level.
              Never buy into 2+ red candles in a row (sell into 2+ green); the setup keeps waiting while alive.
              (First version also looked at the entry minute's own close - look-ahead, fixed 11 Nov 2026.)
  Filters     (each "off" by default; tested on top of slowing + 23.6% target):
              trend  "on": longs only when the burst's close is above the 1-hour 50-EMA (last completed hour),
                     shorts only below it.
              pvol   "on": the pullback's average minute volume must be under 60% of the burst's.
              clean  "on": the burst is built over 3+ candles and its trigger candle closes in the top 30% of it.
              vwap   "on": longs only when the burst closes above the day's VWAP (from 00:00 UTC), shorts below.
              hours  "all" / "day": entries 08:00-22:00 Rome time / "day_wk": the same, Monday to Friday.
              cap    "off" / "1day": at most one trade a day (Rome) / "cool": no new trade for 4 hours after a loss.
              (From the 4 Nov 04:23 and 6 Nov 16:13 shorts: price ran straight up through 50%.)
  Wait        if the level is not reached within 60 minutes (default) / 180 minutes of the last new high, the
              setup is dropped.
  Stop        78.6% (default), 61.8% (Giacomo: just beyond it), 70% (between 61.8% and 78.6%: below that the
              move has failed) or 100% (the burst's low), plus a small tick.
  Target      "swing" (default): back to the burst's high; "1.272": the 127.2% extension; "2R"; "0.236" / "0.382":
              the first level back (take profit at the 23.6% / 38.2% level instead of the full move); "scale":
              half off at 23.6%, the rest rides to the swing - but if price comes back to 38.2% the rest is
              closed there; "scale75": the same with 75% off at 23.6% (Giacomo's exit, 11 Nov 2026). Level targets sit a tick (0.02%) in front of the level so a touch fills them (9 Nov
              23:06: a short's 23.6% target at 74,033.56 was missed by 96 cents).
  Time exit   whatever is still open is closed 60 / 240 (default) / 1440 minutes after the entry.
  Exit rules  stop first if a minute hits both (cautious); gaps fill at the minute's open.
  Costs       0 (default since 11 Nov 2026 evening, at Giacomo's request: costs are added back later; checked at
              0, 0.02, 0.05 and 0.1 in the one-switch table).
  Account     one trade at a time, the whole account in it, no leverage; new bursts are ignored while a setup or
              trade is open.

Train = 2021-2023, test = 2024 on. Buy-and-hold BTC is shown for comparison.

    python engine/fib_burst.py           # downloads from data.binance.vision (public, no key)
    python engine/fib_burst.py --demo    # synthetic minutes, writes /tmp/fib-burst-demo.json
Output: docs/data/fib/burst.json and docs/data/fib/BURST.md
"""
from __future__ import annotations

import argparse
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from array import array
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "data" / "fib"
START = date(2021, 1, 1)
TICK = 0.0002
SPLIT = "2024-01-01"

DEFAULT = {"n": "15", "x": "1", "vol": "2", "level": "0.5", "entry": "touch", "wait": "60",
           "stop": "0.786", "tp": "swing", "hold": "240", "cost": "0", "one": "keep", "slow": "off",
           "trend": "off", "pvol": "off", "clean": "off", "vwap": "off", "hours": "all", "cap": "off"}
VARIANTS = {
    "n": ["5", "15", "30"],
    "x": ["0.5", "1", "1.5", "2"],
    "vol": ["any", "2", "3"],
    "one": ["keep", "skip", "close"],
    "slow": ["off", "on"],
    "trend": ["off", "on"],
    "pvol": ["off", "on"],
    "clean": ["off", "on"],
    "vwap": ["off", "on"],
    "hours": ["all", "day", "day_wk"],
    "cap": ["off", "1day", "cool"],
    "level": ["0.382", "0.5", "0.618"],
    "entry": ["touch", "confirm", "bounce", "zone"],
    "wait": ["60", "180"],
    "stop": ["0.618", "0.7", "0.786", "1"],
    "tp": ["swing", "1.272", "2R", "0.236", "0.382", "scale", "scale75"],
    "hold": ["60", "240", "1440"],
    "cost": ["0", "0.02", "0.05", "0.1"],
}
GRID = ("n", "x", "vol", "one", "slow", "level", "stop", "tp")   # the full combination grid (others at default)
GRID_OPTS = {"one": ["keep", "close"], "tp": ["swing", "0.236", "scale75"]}   # trimmed in the grid (no effect / worse)
# Giacomo's fixes from reviewing the trades (11 Nov 2026), tested on their own and together (others at default).
BEST = {"slow": "on", "tp": "0.236"}                    # best fixes so far: the base for the filters
FILTERS = {
    "trend: with the 1h 50-EMA": {"trend": "on"},
    "pullback volume under 60% of the burst's": {"pvol": "on"},
    "clean burst (3+ candles, closes near the extreme)": {"clean": "on"},
    "VWAP side": {"vwap": "on"},
    "08:00-22:00 Rome": {"hours": "day"},
    "08:00-22:00 Rome, weekdays": {"hours": "day_wk"},
    "max 1 trade a day": {"cap": "1day"},
    "4 h pause after a loss": {"cap": "cool"},
}
FILTERS_ALL = {"trend": "on", "pvol": "on", "clean": "on", "vwap": "on", "hours": "day_wk", "cap": "1day"}
PRESETS = {
    "read one-candle bursts by their close": {"one": "close"},
    "skip one-candle bursts": {"one": "skip"},
    "enter anywhere in 38.2-50%": {"entry": "zone"},
    "take profit at 23.6%": {"tp": "0.236"},
    "all three (close, zone, 23.6%)": {"one": "close", "entry": "zone", "tp": "0.236"},
    "all three, skipping one-candle bursts": {"one": "skip", "entry": "zone", "tp": "0.236"},
    "close + confirmed entry at 50% + 23.6%": {"one": "close", "entry": "confirm", "tp": "0.236"},
    "bounce entry at 50% (green candle closing above)": {"entry": "bounce"},
    "stop at 70%": {"stop": "0.7"},
    "bounce entry + 70% stop": {"entry": "bounce", "stop": "0.7"},
    "close + bounce + 70% stop": {"one": "close", "entry": "bounce", "stop": "0.7"},
    "close + bounce + 70% stop + 23.6%": {"one": "close", "entry": "bounce", "stop": "0.7", "tp": "0.236"},
    "scale out: half at 23.6%, rest out at 38.2% or the swing": {"tp": "scale"},
    "close + scale out": {"one": "close", "tp": "scale"},
    "Giacomo's exit: 75% at 23.6%, rest to the swing or out at 38.2%": {"tp": "scale75"},
    "Giacomo's exit + one-candle bursts read by their close": {"one": "close", "tp": "scale75"},
    "Giacomo's rule: stop just beyond 61.8% + his exit": {"stop": "0.618", "tp": "scale75"},
    "Giacomo's rule + one-candle bursts read by their close": {"one": "close", "stop": "0.618", "tp": "scale75"},
    "close + bounce + 70% stop + scale out": {"one": "close", "entry": "bounce", "stop": "0.7", "tp": "scale"},
}


# ===================================================================== data
def _get(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(3 * (attempt + 1))
        except Exception:
            time.sleep(3 * (attempt + 1))
    return None


def _zip(b: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(b)) as z, z.open(z.namelist()[0]) as f:
        df = pd.read_csv(f, header=None, usecols=range(6))
    if not str(df.iloc[0, 0]).isdigit():
        df = df.iloc[1:]
    df.columns = ["t", "o", "h", "l", "c", "v"]
    df = df.astype(float)
    df["t"] = df["t"].astype("int64")
    df.loc[df["t"] > 10 ** 14, "t"] //= 1000                  # 2025+ files are in microseconds
    return df


def download() -> pd.DataFrame:
    base = "https://data.binance.vision/data/spot"
    parts, today = [], datetime.now(timezone.utc).date()
    y, m = START.year, START.month
    while (y, m) < (today.year, today.month):
        b = _get(f"{base}/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-{y}-{m:02d}.zip")
        if b:
            parts.append(_zip(b))
        else:                                                   # last month may not be published yet: use days
            d = date(y, m, 1)
            while d.month == m:
                bd = _get(f"{base}/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{d}.zip")
                if bd:
                    parts.append(_zip(bd))
                d += timedelta(days=1)
        print(f"  {y}-{m:02d}: {sum(len(p) for p in parts):,} minutes so far", flush=True)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    d = date(today.year, today.month, 1)
    while d < today:
        bd = _get(f"{base}/daily/klines/BTCUSDT/1m/BTCUSDT-1m-{d}.zip")
        if bd:
            parts.append(_zip(bd))
        d += timedelta(days=1)
    df = pd.concat(parts).drop_duplicates("t").sort_values("t").reset_index(drop=True)
    print(f"{len(df):,} minutes {pd.to_datetime(df.t.iloc[0], unit='ms')} -> {pd.to_datetime(df.t.iloc[-1], unit='ms')}")
    return df


def demo() -> pd.DataFrame:
    rng = np.random.default_rng(20261111)
    n = 400_000
    r = rng.normal(0, 0.0007, n)
    burst = rng.random(n) < 0.0008                               # occasional bursts of 10 minutes
    vol = rng.lognormal(0, 0.5, n) * 10
    for i in np.flatnonzero(burst):
        sgn = 1 if rng.random() < 0.5 else -1
        r[i:i + 10] += sgn * 0.0015
        vol[i:i + 10] *= 4
    c = 30000 * np.exp(np.cumsum(r))
    o = np.concatenate([[30000], c[:-1]])
    w = np.abs(rng.normal(0, 0.0004, n)) * c
    t0 = int(datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    return pd.DataFrame({"t": t0 + 60_000 * np.arange(n) * 15, "o": o, "h": np.maximum(o, c) + w,
                         "l": np.minimum(o, c) - w, "c": c, "v": vol})   # spread over 2021-2032 in demo


# ===================================================================== bursts
def bursts(df: pd.DataFrame, n: int, x: float, vol: str, one: str = "keep") -> np.ndarray:
    """Minutes where a burst is first seen: +i for an up burst, -i for a down burst (sorted by i)."""
    lo = df["l"].rolling(n, min_periods=n).min()
    hi = df["h"].rolling(n, min_periods=n).max()
    up = (df["h"] >= hi) & (df["h"] / lo - 1 >= x / 100)
    dn = (df["l"] <= lo) & (1 - df["l"] / hi >= x / 100)
    if one == "skip":                                           # the move's start must be in an earlier candle
        lo_prev = df["l"].shift(1).rolling(n - 1, min_periods=1).min()
        hi_prev = df["h"].shift(1).rolling(n - 1, min_periods=1).max()
        up &= ~(df["l"] < lo_prev)
        dn &= ~(df["h"] > hi_prev)
    elif one == "close":                                        # new high and new low in one candle: read the close
        both = up & dn
        red = df["c"] < df["o"]
        up &= ~(both & red)
        dn &= ~(both & ~red)
    if vol != "any":
        recent = df["v"].rolling(n).mean()
        base = df["v"].shift(n).rolling(1440, min_periods=720).mean()
        ok = recent >= float(vol) * base
        up &= ok
        dn &= ok
    ui = np.flatnonzero(up.to_numpy())
    di = np.flatnonzero(dn.to_numpy())
    ev = np.concatenate([ui, -di - 1])                          # down encoded as -(i+1) so 0 is unambiguous
    return ev[np.argsort(np.abs(np.where(ev < 0, -ev - 1, ev)), kind="stable")]


class Bars:
    def __init__(self, df: pd.DataFrame):
        self.o = array("d", df["o"].to_numpy())
        self.h = array("d", df["h"].to_numpy())
        self.l = array("d", df["l"].to_numpy())
        self.c = array("d", df["c"].to_numpy())
        self.t = df["t"].to_numpy()
        self.v = array("d", df["v"].to_numpy())
        t = df["t"].to_numpy()
        hour = t // 3_600_000
        last = pd.Series(df["c"].to_numpy(), index=hour).groupby(level=0).last()
        ema = last.ewm(span=50, adjust=False).mean().shift(1)    # EMA of COMPLETED hours only
        self.ema = array("d", ema.reindex(hour).to_numpy())
        day = t // 86_400_000
        tp = (df["h"] + df["l"] + df["c"]).to_numpy() / 3
        vol = df["v"].to_numpy()
        g = pd.DataFrame({"d": day, "pv": tp * vol, "v": vol}).groupby("d")
        vwap = (g["pv"].cumsum() / g["v"].cumsum()).to_numpy()   # through this minute's close (known at the close)
        self.vwap = array("d", np.nan_to_num(vwap, nan=np.nan))
        rome = pd.to_datetime(t, unit="ms", utc=True).tz_convert("Europe/Rome")
        hr, wd = rome.hour.to_numpy(), rome.dayofweek.to_numpy()
        self.hok = array("b", ((hr >= 8) & (hr < 22)).astype("int8"))
        self.hwk = array("b", ((hr >= 8) & (hr < 22) & (wd < 5)).astype("int8"))
        self.day = array("q", (rome.year.to_numpy() * 1000 + rome.dayofyear.to_numpy()).astype("int64"))
        self.lo = {}
        self.hi = {}
        self.n = len(self.o)

    def window(self, n: int):
        if n not in self.lo:
            self.lo[n] = array("d", np.nan_to_num(pd.Series(self.l).rolling(n, min_periods=1).min().to_numpy()))
            self.hi[n] = array("d", np.nan_to_num(pd.Series(self.h).rolling(n, min_periods=1).max().to_numpy()))
        return self.lo[n], self.hi[n]


def scale_exit(B: Bars, ie: int, j: int, end: int, confirm: bool, entry: float, stop: float, t1: float,
               back: float, ext: float, upb: bool, part: float = 0.5) -> tuple[float, str, int]:
    """`part` (half, or 75%) off at the first target (23.6%); the rest rides to the swing (a tick before it), but if
    price comes back to 38.2% the rest is closed there. Before the first half: the normal stop. Returns (average exit price, why, exit minute)."""
    o, h, l, c = B.o, B.h, B.l, B.c
    hit = (lambda j_, p: h[j_] >= p) if upb else (lambda j_, p: l[j_] <= p)       # toward the swing
    against = (lambda j_, p: l[j_] <= p) if upb else (lambda j_, p: h[j_] >= p)   # away from it
    fill_with = (lambda j_, p: max(o[j_], p)) if upb else (lambda j_, p: min(o[j_], p))
    fill_against = (lambda j_, p: min(o[j_], p)) if upb else (lambda j_, p: max(o[j_], p))
    p1 = None
    t2 = ext * (1 - (1 if upb else -1) * TICK)                  # the swing, a tick in front of it
    if not confirm:
        if against(ie, stop):
            return stop, "stop", ie                             # same minute: assume the worst
        if entry == o[ie] and hit(ie, t1):
            p1, j = t1, ie + 1                                  # filled at the open, stop untouched
    while j <= end:
        if j > ie:
            if p1 is None:
                if against(j, stop):
                    return fill_against(j, stop), "stop", j
                if hit(j, t1):
                    p1 = fill_with(j, t1)
                    j += 1
                    continue                                    # the rest is checked from the next minute
            else:
                if against(j, back):
                    return part * p1 + (1 - part) * fill_against(j, back), "part + back to 38.2%", j
                if hit(j, t2):
                    return part * p1 + (1 - part) * fill_with(j, t2), "part + swing", j
        j += 1
    return (c[end] if p1 is None else part * p1 + (1 - part) * c[end]), ("time" if p1 is None else "part + time"), end


def simulate(B: Bars, ev: np.ndarray, v: dict) -> list[dict]:
    o, h, l, c = B.o, B.h, B.l, B.c
    lo_w, hi_w = B.window(int(v["n"]))
    lvf, stf = float(v["level"]), float(v["stop"])
    wait, hold, cost = int(v["wait"]), int(v["hold"]), float(v["cost"]) / 100
    confirm = v["entry"] in ("confirm", "bounce")
    green = v["entry"] == "bounce"
    zone = v["entry"] == "zone"
    slow = v["slow"] == "on"
    trend, pvol, clean, vwap = (v[k] == "on" for k in ("trend", "pvol", "clean", "vwap"))
    hours, cap = v["hours"], v["cap"]
    last_day = -1
    if zone:
        lvf = 0.382                                             # zone 38.2% (first fill) .. 50% (deepest open)
    out, free_from = [], 0
    for e in ev.tolist():
        upb = e >= 0
        i0 = e if upb else -e - 1
        if i0 < free_from:
            continue
        sgn = 1 if upb else -1
        a = lo_w[i0] if upb else hi_w[i0]                      # burst start (100%)
        ext, last_new = (h[i0] if upb else l[i0]), i0          # burst extreme (0%), redrawn on new extremes
        n_ = int(v["n"])
        a_i = max(range(max(0, i0 - n_ + 1), i0 + 1), key=lambda j: -l[j] if upb else h[j])
        if cap == "1day" and B.day[i0] == last_day:
            continue
        if trend and B.ema[i0] == B.ema[i0] and ((c[i0] <= B.ema[i0]) if upb else (c[i0] >= B.ema[i0])):
            continue
        if vwap and B.vwap[i0] == B.vwap[i0] and ((c[i0] <= B.vwap[i0]) if upb else (c[i0] >= B.vwap[i0])):
            continue
        if clean and (i0 - a_i < 2 or ext == a or (c[i0] - a) / (ext - a) < 0.7):
            continue
        entry = ie = None
        i = i0 + 1
        while i < B.n and i - last_new <= wait:
            rng = ext - a
            lvl = ext - rng * lvf
            stop = (ext - rng * stf) * (1 - sgn * TICK)
            reach = (l[i] <= lvl) if upb else (h[i] >= lvl)
            if reach and zone:
                deep = ext - rng * 0.5
                if (o[i] < deep) if upb else (o[i] > deep):     # opened past 50%: not in the zone, setup dead
                    break
            if reach and slow:                                  # still running into the level: wait
                paused = any((c[k] >= o[k]) if upb else (c[k] <= o[k]) for k in (i - 2, i - 1))   # closed candles
                past = (lvl - o[i]) if upb else (o[i] - lvl)    # how far this minute OPENS past the level
                if not paused or past > 0.1 * abs(rng):
                    reach = False
            if reach and hours != "all" and not (B.hok[i] if hours == "day" else B.hwk[i]):
                reach = False
            if reach and pvol:
                pull = range(last_new + 1, i)
                burst = range(a_i, last_new + 1)
                if not pull or sum(B.v[k] for k in pull) / len(pull) >= 0.6 * sum(B.v[k] for k in burst) / len(burst):
                    reach = False
            if reach and zone:
                entry = min(o[i], lvl) if upb else max(o[i], lvl)
                ie = i
                break
            if reach and not confirm:
                entry = min(o[i], lvl) if upb else max(o[i], lvl)
                ie = i
                break
            if reach and confirm and ((c[i] > lvl) if upb else (c[i] < lvl)) and ((c[i] > stop) if upb else (c[i] < stop)) \
                    and (not green or ((c[i] > o[i]) if upb else (c[i] < o[i]))):
                entry, ie = c[i], i
                break
            if (l[i] <= stop) if upb else (h[i] >= stop):
                break                                           # gone straight through: setup dead
            if (h[i] > ext) if upb else (l[i] < ext):
                ext, last_new = (h[i] if upb else l[i]), i
            i += 1
        if entry is None or ((entry <= stop) if upb else (entry >= stop)):
            free_from = i + 1
            continue
        risk = abs(entry - stop)
        tp = v["tp"]
        if tp == "swing":
            tgt = ext
        elif tp == "2R":
            tgt = entry + sgn * 2 * risk
        elif tp == "1.272":
            tgt = a + (ext - a) * 1.272
        else:                                                   # a level back toward the swing (0.236 / 0.382),
            lv_ = ext - (ext - a) * (0.236 if tp.startswith("scale") else float(tp))   # a tick in front of the level
            tgt = lv_ * (1 - sgn * TICK)                        # so a touch of the level fills it
        back = ext - (ext - a) * 0.382                          # "scale": after the first half, back here = out
        if (tgt <= entry) if upb else (tgt >= entry):
            free_from = i + 1
            continue
        px = why = None
        j = ie if not confirm else ie + 1
        end = min(B.n - 1, ie + hold)
        if tp.startswith("scale"):
            px, why, j = scale_exit(B, ie, j, end, confirm, entry, stop, tgt, back, ext, upb, 0.75 if tp == "scale75" else 0.5)
        elif not confirm and ((l[ie] <= stop) if upb else (h[ie] >= stop)):
            px, why, j = stop, "stop", ie                       # same minute: assume the worst
        elif not confirm and entry == o[ie] and ((h[ie] >= tgt) if upb else (l[ie] <= tgt)):
            px, why, j = tgt, "target", ie                      # filled at the open, stop untouched: target counts
        while px is None and j <= end:
            if j > ie:
                if (l[j] <= stop) if upb else (h[j] >= stop):
                    px, why = (min(o[j], stop) if upb else max(o[j], stop)), "stop"
                    break
                if (h[j] >= tgt) if upb else (l[j] <= tgt):
                    px, why = (max(o[j], tgt) if upb else min(o[j], tgt)), "target"
                    break
            if j == end:
                px, why = c[j], "time"
                break
            j += 1
        if px is None:
            px, why, j = c[end], "time", end
        ret =sgn * (px / entry - 1) - 2 * cost
        out.append({"t": int(B.t[ie]), "dir": "long" if upb else "short", "a": round(a, 2), "ext": round(ext, 2),
                    "_i": (i0, a_i, last_new, ie, j),
                    "in": round(entry, 2), "sl": round(stop, 2), "tp": round(tgt, 2), "out": round(px, 2),
                    "why": why, "mins": j - ie, "pct": round(ret * 100, 4), "r": round(sgn * (px - entry) / risk, 2)})
        free_from = j + 1
        last_day = B.day[ie]
        if cap == "cool" and ret < 0:
            free_from = j + 240
    return out


def sample(ts: list[dict], B: Bars, n_last: int = 40, n_rand: int = 60) -> list[dict]:
    """Trades for the page's trade viewer, with the minutes around them (grouped into 2-5 minute candles if long)."""
    import random
    pick = ts[-n_last:] + random.Random(20261111).sample(ts[:-n_last], min(n_rand, max(0, len(ts) - n_last)))
    out = []
    for t in sorted(pick, key=lambda t: -t["t"]):
        i0, a_i, z_i, ie, ix = t["_i"]
        lo_i, hi_i = max(0, a_i - 30), min(B.n - 1, ix + 30)
        step = 1
        while (hi_i - lo_i + 1) / step > 240:
            step = {1: 2, 2: 3, 3: 5, 5: 10, 10: 15, 15: 30}.get(step, step * 2)
        lo_i -= lo_i % step if step > 1 else 0
        bars = []
        for s_ in range(lo_i, hi_i + 1, step):
            e_ = min(hi_i, s_ + step - 1)
            bars.append([round(B.o[s_], 2), round(max(B.h[s_:e_ + 1]), 2), round(min(B.l[s_:e_ + 1]), 2), round(B.c[e_], 2)])
        rel = lambda i: (i - lo_i) // step
        out.append({**{k: x for k, x in t.items() if k != "_i"}, "t0": int(B.t[lo_i]), "step": step, "bars": bars,
                    "draw": {"io": rel(a_i), "iz": rel(z_i), "ib": rel(i0), "ie": rel(ie), "ix": rel(ix)}})
    return out


# ===================================================================== stats
def stats(ts: list[dict]) -> dict:
    if not ts:
        return {"n": 0}
    p = np.array([t["pct"] for t in ts]) / 100
    eq = np.cumprod(1 + p)
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    gain, loss = p[p > 0].sum(), -p[p <= 0].sum()
    return {"n": len(ts), "long": sum(t["dir"] == "long" for t in ts), "win": round(float(100 * (p > 0).mean()), 1),
            "avg": round(float(100 * p.mean()), 3), "avg_r": round(float(np.mean([t["r"] for t in ts])), 2),
            "pf": round(float(gain / loss), 2) if loss else None, "acct": round(float(100 * (eq[-1] - 1)), 1),
            "maxdd": round(float(100 * dd), 1),
            "exits": dict(sorted(Counter(t["why"] for t in ts).items()))}


def day(t: int) -> str:
    return datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")


def split(ts: list[dict]) -> dict:
    by_year = defaultdict(list)
    for t in ts:
        by_year[day(t["t"])[:4]].append(t)
    return {"full": stats(ts), "train": stats([t for t in ts if day(t["t"]) < SPLIT]),
            "test": stats([t for t in ts if day(t["t"]) >= SPLIT]),
            "by_year": {y: stats(v).get("acct") for y, v in sorted(by_year.items())}}


# ===================================================================== summary
def summary(res: dict) -> str:
    def row(name, s):
        f, tr, te = s["full"], s["train"], s["test"]
        return (f"| {name} | {f.get('n')} | {f.get('win')}% | {f.get('avg')}% | {f.get('avg_r')} | {f.get('pf')} | "
                f"{f.get('acct')}% | {f.get('maxdd')}% | {tr.get('n')}: {tr.get('avg')}% / {tr.get('pf')} | "
                f"{te.get('n')}: {te.get('avg')}% / {te.get('pf')} |")
    hdr = ("| Variant | Trades | Win | Avg/trade | Avg R | PF | Account | Max DD | Train n: avg / PF | Test n: avg / PF |\n"
           "|---|---|---|---|---|---|---|---|---|---|")
    b = res["base"]
    L = [f"# Fibonacci on a burst - Bitcoin ({res['generated']})", "",
         f"BTCUSDT 1-minute, {res['from']} -> {res['to']}. Buy-and-hold BTC over the same time: {res['hold_btc']}%. "
         f"Train = 2021-2023, test = 2024 on.", "",
         "## Default rules", "", "`" + " | ".join(f"{k}={v}" for k, v in DEFAULT.items()) + "`", "", hdr,
         row("default", b), "", f"Account by year: {b['by_year']}", f"Exits: {b['full'].get('exits')}", "",
         "## Fixes from reviewing the trades (each on its own, then together, each without and with the slowing check; everything else at default)", "", hdr]
    for name, p_ in res.get("presets", {}).items():
        L.append(row(name, p_))
    if res.get("filters"):
        days = max(1, (date.fromisoformat(res["to"]) - date.fromisoformat(res["from"])).days)
        L += ["", "## Filters on top of the best rules (slowing + 23.6% target)", "",
              "| Variant | Trades | Per day | Win | Avg/trade | PF | Account | Max DD | Train n: avg / PF | Test n: avg / PF |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for name, s_ in res["filters"].items():
            f, tr, te = s_["full"], s_["train"], s_["test"]
            L.append(f"| {name} | {f.get('n')} | {round((f.get('n') or 0) / days, 2)} | {f.get('win')}% | {f.get('avg')}% | "
                     f"{f.get('pf')} | {f.get('acct')}% | {f.get('maxdd')}% | {tr.get('n')}: {tr.get('avg')}% / {tr.get('pf')} | "
                     f"{te.get('n')}: {te.get('avg')}% / {te.get('pf')} |")
    L += ["", "## One switch changed at a time", "", hdr]
    for k, opts in res["sweeps"].items():
        for o_, s in opts.items():
            L.append(row(f"{k}={o_}" + (" (default)" if DEFAULT[k] == o_ else ""), s))
    cb = res["combos"]
    top = sorted((c for c in cb if (c["train"].get("n") or 0) >= 100), key=lambda c: -(c["train"].get("avg") or -9))[:10]
    L += ["", "## Best 10 combinations chosen on TRAIN only (min 100 train trades) - does TEST hold?", "", hdr]
    for c in top:
        L.append(row(" ".join(f"{k}={c['v'][k]}" for k in GRID), c))
    both = [c for c in cb if (c["train"].get("avg") or 0) > 0 and (c["test"].get("avg") or 0) > 0
            and (c["train"].get("n") or 0) >= 30 and (c["test"].get("n") or 0) >= 30]
    L += ["", f"Of those 10, **{sum(1 for c in top if (c['test'].get('avg') or 0) > 0)}** were still profitable per "
              f"trade in the test years.",
          f"Combinations tested: {len(cb)}. Positive per trade in BOTH train and test (30+ trades each): **{len(both)}**."]
    if both:
        L += ["", "### Positive in both (by test avg)", "", hdr]
        for c in sorted(both, key=lambda c: -(c["test"].get("avg") or 0))[:15]:
            L.append(row(" ".join(f"{k}={c['v'][k]}" for k in GRID), c))
    return "\n".join(L) + "\n"


# ===================================================================== main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    df = demo() if a.demo else download()
    B = Bars(df)
    print(f"data ready ({time.time() - t0:.0f}s)", flush=True)
    ev_cache: dict = {}

    def run(v):
        key = (v["n"], v["x"], v["vol"], v["one"])
        if key not in ev_cache:
            ev_cache[key] = bursts(df, int(v["n"]), float(v["x"]), v["vol"], v["one"])
        return simulate(B, ev_cache[key], v)

    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
           "from": day(int(df.t.iloc[0])), "to": day(int(df.t.iloc[-1])),
           "rules": __doc__.split("Exact rules tested here")[1].split("Train =")[0].strip(),
           "default": DEFAULT, "variants": VARIANTS,
           "hold_btc": round((df.c.iloc[-1] / df.o.iloc[0] - 1) * 100, 1)}
    base = run(DEFAULT)
    res["base"] = split(base)
    res["base_trades_recent"] = [{k: x for k, x in t.items() if k != "_i"} for t in base[-100:]]
    res["sweeps"] = {k: {o_: split(run({**DEFAULT, k: o_})) for o_ in opts} for k, opts in VARIANTS.items()}
    res["filters"] = {"best: slowing + 23.6%": {"v": BEST, **split(run({**DEFAULT, **BEST}))}}
    for name, p in {**FILTERS, "ALL filters": FILTERS_ALL,
                    "ALL filters except max 1/day": {**FILTERS_ALL, "cap": "off"},
                    "ALL filters, 24/7": {**FILTERS_ALL, "hours": "all"}}.items():
        res["filters"]["best + " + name] = {"v": {**BEST, **p}, **split(run({**DEFAULT, **BEST, **p}))}
    for name, p in {"ALL filters": FILTERS_ALL}.items():
        q = {**BEST, **p, "tp": "scale"}
        res["filters"]["slowing + scale-out + " + name] = {"v": q, **split(run({**DEFAULT, **q}))}
    print(f"filters done ({time.time() - t0:.0f}s)", flush=True)
    res["presets"] = {}
    for name, p in {"default rules": {}, **PRESETS}.items():          # every fix without and with slowing
        res["presets"][name] = {"v": p, **split(run({**DEFAULT, **p}))}
        res["presets"][name + " + slowing"] = {"v": {**p, "slow": "on"}, **split(run({**DEFAULT, **p, "slow": "on"}))}
    print(f"sweeps done ({time.time() - t0:.0f}s)", flush=True)
    combos = [{}]
    for k in GRID:
        combos = [{**x, k: o_} for x in combos for o_ in GRID_OPTS.get(k, VARIANTS[k])]
    res["combos"] = []
    for k, x in enumerate(combos):
        v = {**DEFAULT, **x}
        res["combos"].append({"v": {g: v[g] for g in GRID}, **split(run(v))})
        if k % 100 == 0:
            print(f"  {k}/{len(combos)} combinations ({time.time() - t0:.0f}s)", flush=True)

    out = Path("/tmp/fib-burst-demo.json") if a.demo else OUT_DIR / "burst.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, separators=(",", ":")))
    (out.with_name("fib-burst-demo.md") if a.demo else OUT_DIR / "BURST.md").write_text(summary(res))
    (out.with_name("fib-burst-demo-trades.json") if a.demo else OUT_DIR / "trades_btc.json").write_text(
        json.dumps({"generated": res["generated"], "default": DEFAULT, "trades": sample(base, B)}, separators=(",", ":")))
    f = res["base"]["full"]
    print(f"default: {f.get('n')} trades, win {f.get('win')}%, avg {f.get('avg')}%, PF {f.get('pf')}, "
          f"account {f.get('acct')}% | BTC {res['hold_btc']}% -> {out} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
