"""Second round: near-edge entry with wider stops and bigger targets, plus a first/second-half split."""
import json, sys, itertools
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fvg
from run import btc, stocks, run_btc, run_stocks  # noqa  (run.py re-runs round 1; cheap)
rows = []
for stop_at, buf, tr in itertools.product(["gap", "A_low"], [0.1, 0.5], [2.0, 3.0]):
    p = {**fvg.DEFAULT, "entry": "near", "stop_at": stop_at, "stop_buf_atr": buf, "target_r": tr}
    name = f"near, stop {stop_at}-{buf}ATR, {tr}R"
    for tf in ["4H", "1D"]:
        st, eq, t = run_btc(tf, p)
        half = len(t)//2
        h1 = fvg.stats(t[:half], 0.001, 4.5); h2 = fvg.stats(t[half:], 0.001, 4.5)
        rows.append({"set": f"BTC {tf}", "rule": name, **st, **fvg.summary(eq), "PF_1st": h1.get("PF"), "PF_2nd": h2.get("PF")})
    st, eq, tr_ = run_stocks(p)
    allt = sorted([x for v in tr_.values() for x in v], key=lambda x: x["t_in"])
    half = len(allt)//2
    h1 = fvg.stats(allt[:half], 0.001, 7.5); h2 = fvg.stats(allt[half:], 0.001, 7.5)
    rows.append({"set": "Stocks 1D", "rule": name, **st, **fvg.summary(eq), "PF_1st": h1.get("PF"), "PF_2nd": h2.get("PF")})
    print(rows[-3:], flush=True)
(fvg.OUT / "grid.json").write_text(json.dumps(rows, indent=1, default=float))
