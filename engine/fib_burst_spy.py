"""Fibonacci on a burst - the Bitcoin setup (engine/fib_burst.py), run on SPY.

Every rule, switch, filter, preset and the combination grid come straight from engine/fib_burst.py, so any change
made there applies here too. Only what has to differ for a stock index is changed:

  Data        Alpaca SPY 1-minute candles, regular session only (09:30-16:00 New York), from 2021-01-04,
              split/dividend adjusted.
  Sessions    no overnight: a burst is measured only on today's minutes (the overnight gap is not a burst), a
              setup that is still waiting at 16:00 is dropped, and an open trade is closed at the 16:00 close.
  Burst size  SPY moves about a quarter as much as Bitcoin, so the burst sizes tested are 0.25%, 0.5% (default),
              0.75% and 1% (Bitcoin's 1% is kept as an option).
  Volume      "2x / 3x the normal minute" is measured against the previous 390 minutes (one SPY session) instead
              of Bitcoin's 1,440 (one day).
  Hold        60, 240 (default) or 390 minutes (the rest of the day) - never overnight.
  Costs       0.03% a side by default (the S&P 100 tests use the same); 0 / 0.02 / 0.05 / 0.1 are checked too.
  Entry       by default WITH confirmation: "bounce" (a green 1-minute candle that reaches the level and closes back
              above it; red and below for a short) plus the slowing-down check. Plain "touch" is still tested.

    python engine/fib_burst_spy.py           # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/fib_burst_spy.py --demo    # synthetic sessions, writes /tmp/fib-burst-spy-demo*.json
Output: docs/data/fib/burst_spy.json, docs/data/fib/BURST_spy.md, docs/data/fib/trades_burst_spy.json
"""
from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fib_burst as FB  # noqa: E402
from open_breakout import fetch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "data" / "fib"
FROM = "2021-01-04"
SESSION = 390                     # minutes in a regular session
PAD = 400                         # empty minutes after each session (> the longest hold / wait), see pad()
NY = "America/New_York"

# Default entry with confirmation (Giacomo, 11 Nov 2026, from the 16 Sep 2026 20:47 trade that bought into falling
# red candles): a green candle that reaches the level and closes back above it, and the pullback must have slowed.
FB.DEFAULT.update({"x": "0.5", "cost": "0.03", "entry": "bounce", "slow": "on"})
FB.VARIANTS["x"] = ["0.25", "0.5", "0.75", "1"]
FB.VARIANTS["hold"] = ["60", "240", "390"]


# ===================================================================== data
def download() -> pd.DataFrame:
    end = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    bars = fetch(["SPY"], "1Min", FROM + "T00:00:00Z", end).get("SPY", [])
    df = pd.DataFrame(bars)
    ts = pd.to_datetime(df["t"], utc=True)
    loc = ts.dt.tz_convert(NY)
    m = loc.dt.hour * 60 + loc.dt.minute
    df = df[(m >= 570) & (m < 960)].copy()                       # 09:30-15:59 New York
    df["t"] = ts[df.index].dt.as_unit("ms").astype("int64").to_numpy()
    df = df.rename(columns={"o": "o", "h": "h", "l": "l", "c": "c", "v": "v"})[["t", "o", "h", "l", "c", "v"]]
    print(f"{len(df):,} SPY minutes", flush=True)
    return pad(df.astype({"o": float, "h": float, "l": float, "c": float, "v": float}).reset_index(drop=True))


def demo_raw() -> pd.DataFrame:
    rng = np.random.default_rng(20261111)
    rows = []
    for d in pd.bdate_range("2021-01-04", periods=700):
        t0 = int(pd.Timestamp(f"{d.date()} 09:30", tz=NY).timestamp() * 1000)
        px = 380 * (1 + 0.0003 * len(rows) / SESSION)
        r = rng.normal(0, 0.0004, SESSION)
        v = rng.lognormal(0, 0.4, SESSION) * 1e5
        for i in np.flatnonzero(rng.random(SESSION) < 0.004):
            r[i:i + 8] += (1 if rng.random() < .5 else -1) * 0.0009
            v[i:i + 8] *= 3
        c = px * np.exp(np.cumsum(r))
        o = np.r_[px, c[:-1]]
        w = np.abs(rng.normal(0, 0.0002, SESSION)) * c
        for i in range(SESSION):
            rows.append((t0 + 60_000 * i, o[i], max(o[i], c[i]) + w[i], min(o[i], c[i]) - w[i], c[i], v[i]))
    return pd.DataFrame(rows, columns=["t", "o", "h", "l", "c", "v"])


def pad(df: pd.DataFrame) -> pd.DataFrame:
    """After each session, PAD empty minutes: open/close frozen at the 16:00 close, high = -inf, low = +inf, volume 0.
    The Bitcoin engine then needs no change: nothing can trigger, fill, stop or hit a target in them, a rolling window
    never reaches back into yesterday, and a trade still open at 16:00 is closed at the 16:00 close."""
    day = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(NY).dt.date
    last = df.groupby(day, sort=True).tail(1)
    k = np.arange(1, PAD + 1)
    fill = pd.DataFrame({
        "t": (last["t"].to_numpy()[:, None] + 60_000 * k[None, :]).ravel(),
        "o": np.repeat(last["c"].to_numpy(), PAD), "h": -np.inf, "l": np.inf,
        "c": np.repeat(last["c"].to_numpy(), PAD), "v": 0.0})
    out = pd.concat([df.assign(real=True), fill.assign(real=False)]).sort_values("t", kind="stable").reset_index(drop=True)
    return out


# ===================================================================== the SPY parts of the engine
REAL: np.ndarray = np.zeros(0, bool)
SESS_END: np.ndarray = np.zeros(0, int)
SESS_START: np.ndarray = np.zeros(0, int)


def bursts(df: pd.DataFrame, n: int, x: float, vol: str, one: str = "keep") -> np.ndarray:
    """fib_burst.bursts, session by session: windows hold only today's minutes, volume against the last session."""
    h = df["h"].where(df["real"])
    l = df["l"].where(df["real"])
    lo = l.rolling(n, min_periods=1).min()
    hi = h.rolling(n, min_periods=1).max()
    up = df["real"] & (h >= hi) & (h / lo - 1 >= x / 100)
    dn = df["real"] & (l <= lo) & (1 - l / hi >= x / 100)
    if one == "skip":
        lo_prev = l.shift(1).rolling(max(1, n - 1), min_periods=1).min()
        hi_prev = h.shift(1).rolling(max(1, n - 1), min_periods=1).max()
        up &= ~(l < lo_prev)
        dn &= ~(h > hi_prev)
    elif one == "close":
        both = up & dn
        red = df["c"] < df["o"]
        up &= ~(both & red)
        dn &= ~(both & ~red)
    if vol != "any":
        v = df["v"].where(df["real"])
        recent = v.rolling(n, min_periods=1).mean()
        rv = v[df["real"]]
        base = rv.shift(n).rolling(SESSION, min_periods=SESSION // 2).mean().reindex(df.index).ffill()
        ok = recent >= float(vol) * base
        up &= ok
        dn &= ok
    up, dn = up.fillna(False).to_numpy(bool), dn.fillna(False).to_numpy(bool)
    ui, di = np.flatnonzero(up), np.flatnonzero(dn)
    ev = np.concatenate([ui, -di - 1])
    return ev[np.argsort(np.abs(np.where(ev < 0, -ev - 1, ev)), kind="stable")]


_simulate = FB.simulate
_sample = FB.sample


def simulate(B, ev, v):
    """fib_burst.simulate, then: no entries in the empty minutes; exits there are shown at the 16:00 close."""
    out = []
    for t in _simulate(B, ev, v):
        i0, a_i, z_i, ie, j = t["_i"]
        if not REAL[ie]:
            continue
        if not REAL[j]:
            j = int(SESS_END[ie])
            t["_i"] = (i0, a_i, z_i, ie, j)
            t["mins"] = j - ie + 1
        out.append(t)
    return out


def sample(ts, B, *a, **k):
    """fib_burst.sample, with each chart cut to its own session (09:30-16:00): the trade viewer counts time from
    the first candle, so the chart must not reach back across the night."""
    out = _sample(ts, B, *a, **k)
    for t in out:
        st, d = t["step"], t["draw"]
        lo_i = int(np.searchsorted(B.t, t["t0"]))
        ie = lo_i + d["ie"] * st
        s0, s1 = int(SESS_START[ie]), int(SESS_END[ie])
        cut = max(0, -(-(s0 - lo_i) // st))                      # candles before today's 09:30
        keep = max(cut + 1, (s1 - lo_i) // st + 1)               # through today's last minute
        t["bars"] = t["bars"][cut:keep]
        t["t0"] = int(B.t[lo_i + cut * st])
        for key in ("io", "iz", "ib", "ie", "ix"):
            if d.get(key) is not None:
                d[key] = min(max(0, d[key] - cut), len(t["bars"]) - 1)
        for b in t["bars"]:                                      # any empty minute left -> flat at the close
            if not all(map(math.isfinite, b)):
                b[1], b[2] = max(b[0], b[3]), min(b[0], b[3])
    return out


# ===================================================================== main
def main() -> None:
    global REAL, SESS_END, SESS_START
    demo = "--demo" in sys.argv
    df = pad(demo_raw()) if demo else download()
    REAL = df["real"].to_numpy(bool)
    idx = np.arange(len(df))
    last_real = pd.Series(np.where(REAL, idx, np.nan)).ffill().to_numpy()
    nxt_fill = pd.Series(np.where(~REAL, idx, np.nan)).bfill().fillna(len(df)).to_numpy()
    SESS_END = np.where(REAL, nxt_fill - 1, last_real).astype(int)    # last real minute of each minute's session
    prev_fill = pd.Series(np.where(~REAL, idx, np.nan)).ffill().fillna(-1).to_numpy()
    SESS_START = np.where(REAL, prev_fill + 1, 0).astype(int)          # first minute (09:30) of that session

    FB.download = lambda: df
    FB.demo = lambda: df
    FB.bursts = bursts
    FB.simulate = simulate
    FB.sample = sample
    tmp = Path(tempfile.mkdtemp())
    FB.OUT_DIR = tmp
    sys.argv = [sys.argv[0]]                                     # real-data paths inside fib_burst.main
    FB.main()

    res = json.loads((tmp / "burst.json").read_text())
    res["market"] = "SPY"
    last_day = FB.day(int(df["t"][df["real"]].iloc[-1]))         # the padding after the last close runs past midnight UTC
    md = None
    res["to"], old_to = last_day, res["to"]
    res["rules"] = __doc__.split("only what has to differ")[0] + "\n\n" + res["rules"]
    md = (tmp / "BURST.md").read_text().replace(f"-> {old_to}.", f"-> {last_day}.")
    md = (md.replace("Fibonacci on a burst - Bitcoin", "Fibonacci on a burst - SPY (the Bitcoin setup)")
            .replace("BTCUSDT 1-minute", "SPY 1-minute (regular session, Alpaca)")
            .replace("Buy-and-hold BTC", "Buy-and-hold SPY"))
    out = Path("/tmp") if demo else OUT_DIR
    pre = "fib-burst-spy-demo" if demo else None
    out.mkdir(parents=True, exist_ok=True)
    (out / (f"{pre}.json" if pre else "burst_spy.json")).write_text(json.dumps(res, separators=(",", ":")))
    (out / (f"{pre}.md" if pre else "BURST_spy.md")).write_text(md)
    shutil.copy(tmp / "trades_btc.json", out / (f"{pre}-trades.json" if pre else "trades_burst_spy.json"))
    print("SPY burst results ->", out)


if __name__ == "__main__":
    main()
