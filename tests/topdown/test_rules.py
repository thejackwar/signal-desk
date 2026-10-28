"""
Checks that the Top-Down alert scanner (engine/topdown.py) draws exactly the same lines as the
reference trendlines.py (tests/trendlines/trendlines_ref.py), which the Trendlines page also
matches (tests/trendlines/test_parity.py). So an alert always matches what the page shows.

  1. Rules: thousands of random candle sets, every vertex, break, level and bias must match.
  2. Candles: the scanner's 4H / 1H grouping and session filter match the reference.
  3. Alerts: a simulated trading day of 30-minute runs never alerts the same line twice, and
     every alert's line really is broken on the timeframe it names.

Run:  python tests/topdown/test_rules.py      (needs numpy, pandas, matplotlib)
"""
import datetime as dt
import json
import os
import sys
import tempfile
import subprocess

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests", "trendlines"))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import trendlines_ref as ref  # noqa: E402
import topdown as td  # noqa: E402


def candles(rng, n, tick):
    c = 100 + np.cumsum(rng.normal(0, 1, n))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + np.abs(rng.normal(0, .6, n))
    l = np.minimum(o, c) - np.abs(rng.normal(0, .6, n))
    r = lambda v: np.round(v / tick) * tick
    return pd.DataFrame({"Open": r(o), "High": r(np.maximum(h, np.maximum(r(o), r(c)))),
                         "Low": r(np.minimum(l, np.minimum(r(o), r(c)))), "Close": r(c)})


def same_chain(a, b):
    assert [(i, round(p, 9)) for i, p in a["vertices"]] == [(i, round(p, 9)) for i, p in b["vertices"]]
    assert (a["broken"] is None) == (b["broken"] is None)
    if a["broken"]:
        assert a["broken"][0] == b["broken"][0] and abs(a["broken"][1] - b["broken"][1]) < 1e-9
    assert (a["level"] is None) == (b["level"] is None)
    if a["level"] is not None:
        assert abs(a["level"] - b["level"]) < 1e-9


def test_rules():
    rng = np.random.default_rng(3)
    n_cases = 0
    for case in range(1500):
        df = candles(rng, int(rng.integers(20, 700)), [0.01, 0.5, 2][case % 3])
        lb = int(rng.integers(30, 800))
        a, b = td.analyze(df, lb), ref.analyze(df, lb)
        same_chain(a["up"], b["up"]); same_chain(a["down"], b["down"])
        assert a["bias"] == b["bias"]
        n_cases += 1
    print(f"rules: {n_cases} random candle sets match the reference")


def test_candles():
    idx = pd.date_range("2026-03-02 04:00", "2026-03-13 19:55", freq="5min", tz=td.ET)   # spans the DST change
    rng = np.random.default_rng(5)
    c = 100 + np.cumsum(rng.normal(0, .1, len(idx)))
    df = pd.DataFrame({"Open": c, "High": c + .1, "Low": c - .1, "Close": c}, index=idx)
    m = td._session(df)
    assert m.equals(ref._session(df))
    for mins in (60, 240):
        assert td._group(m, mins).equals(ref._group(m, mins))
    print("candles: session filter and 1H/4H grouping match the reference")


def test_alert_day():
    tickers = "SPY,AAPL,MSFT,NVDA,JPM,XOM,KO,PG,CAT,BA,AMD,GE"
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "alerts.json")
        runs = [f"2026-10-27T{h:02d}:{m:02d}" for h in range(14, 21) for m in (2, 32)]
        for now in runs:
            subprocess.run([sys.executable, os.path.join(ROOT, "engine", "topdown.py"), "--demo", "--force",
                            "--tickers", tickers, "--now", now, "--out", out], check=True, capture_output=True)
        alerts = json.load(open(out))["alerts"]
    assert alerts, "a simulated day should produce some breaks"
    assert len({a["id"] for a in alerts}) == len(alerts), "a line alerted twice"
    checked, cache = 0, {}
    for a in alerts:
        # the data the scanner saw when it raised the alert (the newest candle may still have been forming)
        end = pd.Timestamp(a["found_at"]).tz_convert("UTC") - pd.Timedelta(minutes=16)
        if end not in cache:
            cache[end] = td.fetch_demo(tickers.split(","), end.to_pydatetime())
        fr = td.frames_from(cache[end][a["ticker"]])[a["tf"]]
        bar = fr.index.get_indexer([pd.Timestamp(a["break_at"]).tz_localize(td.ET)])[0]
        assert bar >= 0, f"break bar missing: {a['id']}"
        close = fr["Close"].iloc[bar]
        ok = close > a["level"] - 0.006 if a["side"] == "bullish" else close < a["level"] + 0.006
        assert ok, f"not a break: {a['id']} close {close} level {a['level']}"
        checked += 1
    print(f"alerts: {len(alerts)} alerts over a simulated day, no line twice, {checked} breaks verified on the candles the scanner saw")


if __name__ == "__main__":
    test_rules()
    test_candles()
    test_alert_day()
    print("all good")
