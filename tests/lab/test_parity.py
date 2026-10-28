"""
Checks that the Strategy Lab (docs/lab.js) gives the same results as the app's own backtest
(engine/backtest.py + engine/strategies.py) when it runs with default settings.

Simulated prices (with gaps, late starters and early leavers) go through backtest.lab_prices,
get decoded the way the browser decodes them, and then:
  - the Python engine runs every indicator strategy on the decoded prices (backtest.run), and
  - node runs docs/lab.js on the same lab file.
The EUR 100,000 account (long + short and long only) must match week by week, and the
number of closed trades and wins must match.

Run:  python tests/lab/test_parity.py      (needs node, numpy, pandas)
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
import backtest  # noqa: E402
import strategies  # noqa: E402
from scan import fetch_demo  # noqa: E402

CONFIGS = {"ma200": "ma", "golden_cross": "cross", "bollinger": "bollinger", "rsi": "rsi", "macd": "macd", "donchian": "donchian", "supertrend": "supertrend"}


def decoded(lab: dict) -> dict:
    """Python copy of decode() in docs/lab.js: the exact prices the browser will see."""
    cal = pd.DatetimeIndex(lab["dates"])
    out = {}
    for T in lab["tickers"]:
        miss, idx, g = set(T["miss"]), [], T["s"]
        for _ in T["c"]:
            while g in miss:
                g += 1
            idx.append(g)
            g += 1
        L = np.cumsum(T["c"])
        out[T["t"]] = pd.DataFrame({"open": np.exp(L / 1e4), "high": np.exp((L + T["h"]) / 1e4),
                                    "low": np.exp((L + T["l"]) / 1e4), "close": np.exp(L / 1e4),
                                    "volume": 0}, index=cal[idx])
    return out


def main():
    rng = np.random.default_rng(3)
    data = fetch_demo(n_tickers=24, bars=1600, seed=5)
    for i, t in enumerate(list(data)):
        df = data[t]
        if i % 4 == 1:
            df = df.iloc[rng.integers(100, 400):]            # listed later
        if i % 5 == 2:
            df = df.iloc[: -rng.integers(50, 300)]           # dropped out
        if i % 3 == 0:
            df = df.drop(df.index[rng.choice(np.arange(5, len(df) - 5), 12, replace=False)])  # missing days
        data[t] = df
    lab = backtest.lab_prices(data, None)
    dec = decoded(lab)

    registry = {k: strategies.REGISTRY[k] for k in CONFIGS}
    backtest.REGISTRY, saved = registry, backtest.REGISTRY
    try:
        res = backtest.run(dec)
    finally:
        backtest.REGISTRY = saved
    weeks = res.pop("_weeks")
    pos = {d: i for i, d in enumerate(pd.DatetimeIndex(lab["dates"]))}
    week_idx = [pos[d] for d in pd.DatetimeIndex(weeks.values)]

    job = {"lab": lab, "configs": CONFIGS, "weeks": week_idx}
    js = json.loads(subprocess.run(["node", str(Path(__file__).with_name("run_js.mjs"))], input=json.dumps(job),
                                   capture_output=True, text=True, check=True).stdout)
    bad = 0
    for sid in CONFIGS:
        for key in ("all", "long"):
            py, jv = np.array(res[sid]["account"][key], float), np.array(js[sid][key], float)
            worst = np.max(np.abs(jv - py) / np.maximum(py, 1))
            ok = worst < 1e-4   # Python rounds to whole euros
            bad += not ok
            print(f"{'ok ' if ok else 'BAD'} {sid:13s} {key:4s} end py {py[-1]:>12,.0f}  js {jv[-1]:>12,.0f}  worst gap {worst:.2e}")
        t_py, t_js = res[sid]["trades"]["all"], js[sid]["trades"]
        ok = t_py["n"] == t_js["n"] and t_py.get("wins", 0) == t_js["wins"]
        bad += not ok
        print(f"{'ok ' if ok else 'BAD'} {sid:13s} trades py {t_py['n']}/{t_py.get('wins')} js {t_js['n']}/{t_js['wins']}")
    print("PASS" if not bad else f"FAIL ({bad})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
