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
              reaches the level and closes back above it, enter at that close; or "zone": enter the first minute
              price trades between 38.2% and 50% (at the 38.2% level, or at the minute's open if it opens inside
              the zone; a minute that opens past 50% does not count).
  Wait        if the level is not reached within 60 minutes (default) / 180 minutes of the last new high, the
              setup is dropped.
  Stop        78.6% (default) or 100% (the burst's low), plus a small tick.
  Target      "swing" (default): back to the burst's high; "1.272": the 127.2% extension; "2R"; "0.236" / "0.382":
              the first level back (take profit at the 23.6% / 38.2% level instead of the full move).
  Time exit   whatever is still open is closed 60 / 240 (default) / 1440 minutes after the entry.
  Exit rules  stop first if a minute hits both (cautious); gaps fill at the minute's open.
  Costs       0.05% a side (default; checked at 0, 0.02, 0.05 and 0.1).
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
from collections import defaultdict
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
           "stop": "0.786", "tp": "swing", "hold": "240", "cost": "0.05", "one": "keep"}
VARIANTS = {
    "n": ["5", "15", "30"],
    "x": ["0.5", "1", "1.5", "2"],
    "vol": ["any", "2", "3"],
    "one": ["keep", "skip", "close"],
    "level": ["0.382", "0.5", "0.618"],
    "entry": ["touch", "confirm", "zone"],
    "wait": ["60", "180"],
    "stop": ["0.786", "1"],
    "tp": ["swing", "1.272", "2R", "0.236", "0.382"],
    "hold": ["60", "240", "1440"],
    "cost": ["0", "0.02", "0.05", "0.1"],
}
GRID = ("n", "x", "vol", "one", "level", "stop", "tp")   # the full combination grid (others at default)
# Giacomo's fixes from reviewing the trades (11 Nov 2026), tested on their own and together (others at default).
PRESETS = {
    "read one-candle bursts by their close": {"one": "close"},
    "skip one-candle bursts": {"one": "skip"},
    "enter anywhere in 38.2-50%": {"entry": "zone"},
    "take profit at 23.6%": {"tp": "0.236"},
    "all three (close, zone, 23.6%)": {"one": "close", "entry": "zone", "tp": "0.236"},
    "all three, skipping one-candle bursts": {"one": "skip", "entry": "zone", "tp": "0.236"},
    "close + confirmed entry at 50% + 23.6%": {"one": "close", "entry": "confirm", "tp": "0.236"},
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
        self.lo = {}
        self.hi = {}
        self.n = len(self.o)

    def window(self, n: int):
        if n not in self.lo:
            self.lo[n] = array("d", np.nan_to_num(pd.Series(self.l).rolling(n, min_periods=1).min().to_numpy()))
            self.hi[n] = array("d", np.nan_to_num(pd.Series(self.h).rolling(n, min_periods=1).max().to_numpy()))
        return self.lo[n], self.hi[n]


def simulate(B: Bars, ev: np.ndarray, v: dict) -> list[dict]:
    o, h, l, c = B.o, B.h, B.l, B.c
    lo_w, hi_w = B.window(int(v["n"]))
    lvf, stf = float(v["level"]), float(v["stop"])
    wait, hold, cost = int(v["wait"]), int(v["hold"]), float(v["cost"]) / 100
    confirm = v["entry"] == "confirm"
    zone = v["entry"] == "zone"
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
                entry = min(o[i], lvl) if upb else max(o[i], lvl)
                ie = i
                break
            if reach and not confirm:
                entry = min(o[i], lvl) if upb else max(o[i], lvl)
                ie = i
                break
            if reach and confirm and ((c[i] > lvl) if upb else (c[i] < lvl)) and ((c[i] > stop) if upb else (c[i] < stop)):
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
        else:                                                   # a level back toward the swing: 0.236 / 0.382
            tgt = ext - (ext - a) * float(tp)
        if (tgt <= entry) if upb else (tgt >= entry):
            free_from = i + 1
            continue
        px = why = None
        j = ie if not confirm else ie + 1
        if not confirm and ((l[ie] <= stop) if upb else (h[ie] >= stop)):
            px, why, j = stop, "stop", ie                       # same minute: assume the worst
        elif not confirm and entry == o[ie] and ((h[ie] >= tgt) if upb else (l[ie] <= tgt)):
            px, why, j = tgt, "target", ie                      # filled at the open, stop untouched: target counts
        end = min(B.n - 1, ie + hold)
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
        ret = sgn * (px / entry - 1) - 2 * cost
        out.append({"t": int(B.t[ie]), "dir": "long" if upb else "short", "a": round(a, 2), "ext": round(ext, 2),
                    "_i": (i0, a_i, last_new, ie, j),
                    "in": round(entry, 2), "sl": round(stop, 2), "tp": round(tgt, 2), "out": round(px, 2),
                    "why": why, "mins": j - ie, "pct": round(ret * 100, 4), "r": round(sgn * (px - entry) / risk, 2)})
        free_from = j + 1
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
            "exits": {k: sum(t["why"] == k for t in ts) for k in ("target", "stop", "time")}}


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
         "## Fixes from reviewing the trades (each on its own, then together; everything else at default)", "", hdr,
         row("default (as before)", b)]
    for name, p_ in res.get("presets", {}).items():
        L.append(row(name, p_))
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
    res["presets"] = {name: {"v": p, **split(run({**DEFAULT, **p}))} for name, p in PRESETS.items()}
    print(f"sweeps done ({time.time() - t0:.0f}s)", flush=True)
    combos = [{}]
    for k in GRID:
        combos = [{**x, k: o_} for x in combos for o_ in VARIANTS[k]]
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
