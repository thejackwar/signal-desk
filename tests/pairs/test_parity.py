"""The Pairs page (docs/pairs.js) and the engine (engine/pairs.py) must compute the gap and trade it
the same way, so the page's what-if settings match the published numbers.
    python tests/pairs/test_parity.py"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
import pairs as P  # noqa: E402

rng = np.random.default_rng(5)
cases, py = [], []
for k in range(40):
    n = int(rng.integers(150, 520))
    idx = pd.bdate_range("2023-01-02", periods=n)
    lb = np.cumsum(rng.normal(0, 0.012, n)) + 4
    noise = np.zeros(n)
    for i in range(1, n):
        noise[i] = rng.uniform(0.8, 0.97) * noise[i - 1] + rng.normal(0, 0.01)
    beta = float(rng.uniform(0.5, 1.6))
    la = beta * lb + 0.3 + noise
    rules = {**P.RULES, "entry_z": float(rng.choice([1.5, 2, 2.5])), "exit_z": float(rng.choice([0, 0.5, 1])),
             "stop_z": float(rng.choice([3, 3.5, 5])), "max_days": int(rng.choice([10, 30, 60])),
             "z_window": int(rng.choice([20, 60, 90]))}
    pa, pb = pd.Series(np.exp(la), idx), pd.Series(np.exp(lb), idx)
    spread = np.log(pa) - beta * np.log(pb)
    z = P.zscore(spread, rules["z_window"])
    ra, rb = pa.pct_change().fillna(0), pb.pct_change().fillna(0)
    daily, trades = P.simulate(z, ra, rb, beta, rules)
    py.append((z.values, daily.values, trades))
    cases.append({"spread": spread.tolist(), "ra": ra.tolist(), "rb": rb.tolist(), "beta": beta,
                  "rules": rules, "dates": [str(d.date()) for d in idx]})

js = json.loads(subprocess.run(["node", str(ROOT / "tests/pairs/run_js.mjs")], input=json.dumps(cases),
                               capture_output=True, text=True, check=True).stdout)
bad = 0
n_trades = 0
for (z, daily, trades), j in zip(py, js):
    jz = np.array([np.nan if v is None else v for v in j["z"]])
    if not np.allclose(z, jz, atol=1e-9, equal_nan=True):
        bad += 1; print("z differs")
    if not np.allclose(daily, j["pnl"], atol=1e-12):
        bad += 1; print("pnl differs")
    if len(trades) != len(j["trades"]) or any(
            t["open"] != u["open"] or t["close"] != u["close"] or t["why"] != u["why"] or abs(t["ret"] - u["ret"]) > 1e-5
            for t, u in zip(trades, j["trades"])):
        bad += 1; print("trades differ", trades[:2], j["trades"][:2])
    n_trades += len(trades)
print(f"{len(cases)} cases, {n_trades} trades: {'OK' if not bad else f'{bad} differences'}")
sys.exit(1 if bad else 0)
