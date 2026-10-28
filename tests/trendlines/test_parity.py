"""
Checks that the webpage's trendlines (docs/trendlines.js + worker/trendlines-worker.js) draw exactly
the same lines as thejackwar/sp500-bot trendlines.py (reference copy: trendlines_ref.py).

  1. Chains: thousands of random candle sets (prices rounded to coarse ticks so ties happen),
     several swing strengths, lookbacks and break modes. Every vertex, break, level and bias
     must match.
  2. Rules: no swing low below any up segment, no swing high above any down segment, and the
     slopes only get steeper along each chain.
  3. Data path: fake Alpaca responses (extended hours, DST changes, half days, duplicate bars,
     several pages) go through the Worker + buildFrames on one side and trendlines.py's own
     load() on the other. All 7 timeframes must have the same candles and the same lines.

Run:  python tests/trendlines/test_parity.py      (needs node, numpy, pandas, matplotlib)
"""
import datetime as dt
import json
import os
import subprocess
import sys
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import trendlines_ref as tl  # noqa: E402

ET = ZoneInfo("America/New_York")
EPS = 1e-9


def node(job):
    p = subprocess.run(["node", os.path.join(HERE, "run_js.mjs")], input=json.dumps(job),
                       capture_output=True, text=True, check=True)
    return json.loads(p.stdout)


def close(a, b):
    return abs(a - b) <= EPS * max(1.0, abs(a), abs(b))


def same_chain(py, js):
    """py: dict from trendlines.py build_chain; js: object from trendlines.js buildChain."""
    if len(py["vertices"]) != len(js["vertices"]):
        return f"vertex count {len(py['vertices'])} vs {len(js['vertices'])}"
    for (i, p), v in zip(py["vertices"], js["vertices"]):
        if int(i) != v["index"] or not close(float(p), v["price"]):
            return f"vertex ({i}, {p}) vs ({v['index']}, {v['price']})"
    if (py["broken"] is None) != (js["broken"] is None):
        return f"broken {py['broken']} vs {js['broken']}"
    if py["broken"] is not None and (int(py["broken"][0]) != js["broken"]["index"]
                                     or not close(float(py["broken"][1]), js["broken"]["price"])):
        return f"broken {py['broken']} vs {js['broken']}"
    for k in ("level", "slope"):
        a, b = py[k], js[k]
        if (a is None) != (b is None) or (a is not None and not close(float(a), b)):
            return f"{k} {a} vs {b}"
    return None


def random_candles(rng, n):
    tick = rng.choice([0.01, 0.25, 1.0, 5.0])            # coarse ticks -> lots of equal highs/lows
    px = 100 + np.cumsum(rng.normal(0, rng.choice([0.3, 1, 3]), n))
    if rng.random() < 0.3:                                 # a flat stretch
        a = rng.integers(0, n)
        px[a:a + rng.integers(1, 30)] = px[a]
    o = px + rng.normal(0, 0.5, n)
    c = px + rng.normal(0, 0.5, n)
    h = np.maximum(o, c) + np.abs(rng.normal(0, 0.8, n))
    l = np.minimum(o, c) - np.abs(rng.normal(0, 0.8, n))
    r = lambda x: np.round(x / tick) * tick               # noqa: E731
    o, h, l, c = r(o), r(h), r(l), r(c)
    h, l = np.maximum.reduce([h, o, c]), np.minimum.reduce([l, o, c])
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c})


def check_rules(js_res, tag):
    errs = []
    for side, piv_key in (("up", "pivLow"), ("down", "pivHigh")):
        ch, piv = js_res[side], js_res[piv_key]
        v = ch["vertices"]
        slopes = []
        for A, B in zip(v, v[1:]):
            s = (B["price"] - A["price"]) / (B["index"] - A["index"])
            slopes.append(s)
            for p in piv:
                if p["index"] <= A["index"]:
                    continue
                line = A["price"] + s * (p["index"] - A["index"])
                tol = 1e-7 * max(1, abs(line))
                if (side == "up" and p["price"] < line - tol) or (side == "down" and p["price"] > line + tol):
                    errs.append(f"{tag} {side}: swing {p} beyond segment {A}->{B}")
        for s0, s1 in zip(slopes, slopes[1:]):
            if (side == "up" and s1 < s0 - 1e-9) or (side == "down" and s1 > s0 + 1e-9):
                errs.append(f"{tag} {side}: slopes {s0} then {s1} (should get steeper)")
    return errs


def test_chains(n_cases=3000, seed=7):
    rng = np.random.default_rng(seed)
    cases, frames = [], []
    for _ in range(n_cases):
        n = int(rng.choice([rng.integers(1, 20), rng.integers(20, 200), rng.integers(200, 1600)]))
        df = random_candles(rng, n)
        swing = int(rng.choice([1, 2, 3, 5, 5, 5, 8]))
        lookback = int(rng.choice([5, 60, 250, 300, 600, 1500, 5000]))
        brk = str(rng.choice(["close", "wick"]))
        frames.append((df, swing, lookback, brk))
        cases.append({"candles": [{"time": i, "open": r.Open, "high": r.High, "low": r.Low, "close": r.Close}
                                  for i, r in enumerate(df.itertuples())],
                      "swing": swing, "lookback": lookback, "breakBy": brk})
    js = node({"mode": "chains", "cases": cases})
    errs, intact, broken, verts = [], 0, 0, 0
    for k, ((df, swing, lookback, brk), res) in enumerate(zip(frames, js)):
        h, l = df["High"].values, df["Low"].values
        up = tl.build_chain(df, tl.find_pivots(h, l, swing, "low"), True, lookback, brk)
        dn = tl.build_chain(df, tl.find_pivots(h, l, swing, "high"), False, lookback, brk)
        u, d = up["level"] is not None, dn["level"] is not None
        bias = "Up" if u and not d else "Down" if d and not u else "Squeeze" if u and d else "-"
        for side, py in (("up", up), ("down", dn)):
            e = same_chain(py, res[side])
            if e:
                errs.append(f"case {k} (n={len(df)} swing={swing} lb={lookback} {brk}) {side}: {e}")
            intact += py["level"] is not None
            broken += py["broken"] is not None
            verts += len(py["vertices"])
        if bias != res["bias"]:
            errs.append(f"case {k}: bias {bias} vs {res['bias']}")
        errs += check_rules(res, f"case {k}")
    print(f"chains: {n_cases} candle sets, {verts} vertices, {intact} intact / {broken} broken lines")
    return errs


# ------------------------------------------------------------------ data path
def fake_alpaca(rng):
    """Alpaca-style bars per timeframe, UTC timestamps, extended hours, DST, half days, duplicates."""
    end = dt.datetime(2026, 10, 27, 20, 0, tzinfo=dt.timezone.utc)
    days = pd.bdate_range(end=end.date() - dt.timedelta(days=0), periods=int(365 * 8 * 5 / 7))
    half = set(rng.choice(len(days), 6, replace=False))
    px = [150.0]

    def bar(t_ny):
        p = px[-1] * (1 + rng.normal(0, 0.004))
        px.append(p)
        o, c = p, p * (1 + rng.normal(0, 0.003))
        return {"t": t_ny.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "o": round(o, 2), "h": round(max(o, c) * (1 + abs(rng.normal(0, .002))), 2),
                "l": round(min(o, c) * (1 - abs(rng.normal(0, .002))), 2), "c": round(c, 2), "v": 1000}

    out = {}
    out["1Week"] = [bar(dt.datetime.combine(d.date(), dt.time(0), ET)) for d in days if d.weekday() == 0][-420:]
    out["1Day"] = [bar(dt.datetime.combine(d.date(), dt.time(0), ET)) for d in days[-330:]]
    for tf, mins, ndays in (("30Min", 30, 235), ("15Min", 15, 68), ("5Min", 5, 25)):
        rows = []
        for k, d in enumerate(days[-ndays:]):
            stop = 13 * 60 if (len(days) - ndays + k) in half else 20 * 60   # half day: closes 13:00
            for m in range(4 * 60, stop, mins):                              # 4:00-20:00 extended hours
                if rng.random() < 0.01:
                    continue                                                 # a missing bar
                rows.append(bar(dt.datetime.combine(d.date(), dt.time(m // 60, m % 60), ET)))
                if rng.random() < 0.002:
                    rows.append(dict(rows[-1], c=rows[-1]["c"] + 1))         # duplicate timestamp
        out[tf] = rows
    return out


def py_load(alpaca):
    """trendlines.py load(), with _alpaca reading the fake bars the way scanner.alpaca_bars does."""
    def fake(sym, timeframe, days):
        df = pd.DataFrame(alpaca[timeframe])
        df.index = pd.DatetimeIndex(pd.to_datetime(df["t"], utc=True)).tz_convert(ET)
        df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
        df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        return df[~df.index.duplicated()].sort_index()
    tl._alpaca = fake
    return tl.load("TEST")


def wall(ts):
    return int((ts.tz_localize(None) - pd.Timestamp("1970-01-01")).total_seconds())


def test_data_path(seed=11):
    rng = np.random.default_rng(seed)
    alpaca = fake_alpaca(rng)
    frames = py_load(alpaca)
    results = {t: tl.analyze(frames[t], tl.TFS[t][1]) for t in tl.ORDER}
    js = node({"mode": "worker", "symbol": "brk.b", "alpaca": alpaca, "page": 997})
    errs = []
    if js.get("error"):
        return [f"worker error: {js['error']}"]
    if js["symbol"] != "BRK-B":
        errs.append(f"symbol {js['symbol']}")
    for t in tl.ORDER:
        df, jf = frames[t], js["frames"][t]
        if len(df) != len(jf):
            errs.append(f"{t}: {len(df)} candles vs {len(jf)}")
            continue
        for (ts, r), c in zip(df.iterrows(), jf):
            if wall(ts) != c["time"] or any(not close(r[k], c[k.lower()]) for k in ("Open", "High", "Low", "Close")):
                errs.append(f"{t}: candle {ts} {tuple(r)} vs {c}")
                break
        for side in ("up", "down"):
            e = same_chain(results[t][side], js["results"][t][side])
            if e:
                errs.append(f"{t} {side}: {e}")
        if results[t]["bias"] != js["results"][t]["bias"]:
            errs.append(f"{t}: bias {results[t]['bias']} vs {js['results'][t]['bias']}")
        print(f"data {t:>3}: {len(df):5d} candles  up {len(results[t]['up']['vertices'])} pts  "
              f"down {len(results[t]['down']['vertices'])} pts  bias {results[t]['bias']}")
    print(f"data: {js['calls']} Alpaca requests (paging works)")
    return errs


if __name__ == "__main__":
    errs = test_chains() + test_data_path()
    for e in errs[:30]:
        print("FAIL", e)
    print("ALL OK" if not errs else f"{len(errs)} failures")
    sys.exit(1 if errs else 0)
