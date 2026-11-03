"""The Options page (docs/options-lib.js) and the options engine (engine/options_model.py) must price
and pick strikes the same way.   python tests/options/test_parity.py"""
import json
import random
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
import options_model as M  # noqa: E402

rnd = random.Random(11)
bs = [[rnd.uniform(5, 900), 0, rnd.choice([0, 1 / 365, rnd.uniform(0.01, 1)]), rnd.uniform(0.05, 1.2), rnd.choice("CP")] for _ in range(400)]
for c in bs:
    c[1] = round(c[0] * rnd.uniform(0.6, 1.4), 2)
skew = [[rnd.uniform(0.08, 0.9), 100, rnd.uniform(50, 160), rnd.uniform(0.01, 0.5), rnd.choice([M.SKEW_INDEX, M.SKEW_STOCK])] for _ in range(300)]
pick = [[sid, rnd.uniform(8, 1200), rnd.uniform(0.1, 0.8), rnd.randint(21, 49), rnd.choice([M.SKEW_INDEX, M.SKEW_STOCK])]
        for sid in M.ORDER for _ in range(25)]
d0 = date(2010, 1, 1)
expiry = [(d0 + timedelta(days=rnd.randint(0, 6000))).isoformat() for _ in range(300)]

js = json.loads(subprocess.run(["node", str(ROOT / "tests/options/run_js.mjs")], input=json.dumps(
    {"bs": bs, "skew": skew, "pick": pick, "expiry": expiry}), capture_output=True, text=True, check=True).stdout)

bad = 0
def close(a, b, tol):
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))

for c, (p, g) in zip(bs, js["bs"]):
    pp, gg = M.bs(*c), M.greeks(*c)
    if not close(pp, p, 1e-9) or any(not close(gg[k], g[k], 1e-9) for k in gg):
        bad += 1; print("bs", c, pp, p, gg, g)
for c, v in zip(skew, js["skew"]):
    if not close(M.skew_iv(*c), v, 1e-12):
        bad += 1; print("skew", c)
for (sid, S, atm, days, slope), ks in zip(pick, js["pick"]):
    legs = M.STRATEGIES[sid]["legs"]
    py = M.pick_strikes(legs, S, lambda l: (days + 28 if l.get("exp") == "far" else days) / 365,
                        lambda K, T: M.skew_iv(atm, S, K, T, slope))
    if [round(k, 4) for k in py] != [round(k, 4) for k in ks]:
        bad += 1; print("pick", sid, S, atm, days, py, ks)
for d, (n, f) in zip(expiry, js["expiry"]):
    nn = M.next_expiry(date.fromisoformat(d))
    if nn.isoformat() != n or M.far_expiry(nn).isoformat() != f:
        bad += 1; print("expiry", d, nn, n)
if json.loads(json.dumps(M.STRATEGIES)) != js["strategies"]:
    bad += 1; print("strategy definitions differ")
print(f"{len(bs)} prices, {len(skew)} skews, {len(pick)} strike picks, {len(expiry)} expiries: {'OK' if not bad else f'{bad} differences'}")
sys.exit(1 if bad else 0)
