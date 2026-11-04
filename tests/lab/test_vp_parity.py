"""
The volume-profile strategies give the same alerts in the Strategy Lab (docs/lab.js, docs/lab-rules.js)
as in the app's engine (engine/strategies.py: value_area, back_to_value, value_breakout), on simulated
prices with volume, at default and other settings. Also checks the levels themselves.

Run:  python tests/lab/test_vp_parity.py      (needs node, numpy, pandas)
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
import strategies as S  # noqa: E402
from scan import fetch_demo  # noqa: E402

JS = """
import * as Lab from "../../docs/lab.js";
let buf = ""; process.stdin.on("data", d => buf += d); process.stdin.on("end", () => {
  const job = JSON.parse(buf), out = [];
  for (const t of job.tickers) {
    const T = { t: "X", c: Float64Array.from(t.c), h: Float64Array.from(t.h), l: Float64Array.from(t.l),
      v: Float64Array.from(t.v, x => x > 0 ? x : NaN), idx: Int32Array.from(t.c.map((_, i) => i)) };
    const res = {};
    for (const [name, ind, p] of job.cases) {
      const cfg = Lab.clean({ ...Lab.defaults(ind), p });
      res[name] = Array.from(Lab.signals(T, cfg));
    }
    const lv = Lab.Rules.valueArea(T, 20, 0.7);
    res.levels = [Array.from(lv.poc), Array.from(lv.vah), Array.from(lv.val)].map(a => a.map(x => x === x ? x : null));
    out.push(res);
  }
  process.stdout.write(JSON.stringify(out));
});
"""

CASES = [
    ("btv", "back_to_value", {"n": 20, "va": 70}, lambda df: S.back_to_value(df)),
    ("btv_30_60", "back_to_value", {"n": 30, "va": 60}, lambda df: S.back_to_value(df, 30, 0.6)),
    ("vbo", "value_breakout", {"n": 20, "va": 70, "k": 2}, lambda df: S.value_breakout(df)),
    ("vbo_10_80_1", "value_breakout", {"n": 10, "va": 80, "k": 1}, lambda df: S.value_breakout(df, 10, 0.8, 1)),
    ("vbo_40_70_3", "value_breakout", {"n": 40, "va": 70, "k": 3}, lambda df: S.value_breakout(df, 40, 0.7, 3)),
]


def main():
    data = fetch_demo(n_tickers=8, bars=900, seed=11)
    rng = np.random.default_rng(1)
    frames = list(data.values())
    # edge cases: flat days, missing volume, a few zero-volume days
    d0 = frames[0].copy()
    d0.iloc[100:105, d0.columns.get_loc("high")] = d0["close"].iloc[100:105]
    d0.iloc[100:105, d0.columns.get_loc("low")] = d0["close"].iloc[100:105]
    d0.iloc[200:210, d0.columns.get_loc("volume")] = 0
    frames[0] = d0
    frames.append(frames[1].assign(volume=0))
    tickers = [{"c": f["close"].tolist(), "h": f["high"].tolist(), "l": f["low"].tolist(), "v": f["volume"].astype(float).tolist()}
               for f in frames]
    script = ROOT / "tests" / "lab" / "_vp_run.mjs"
    script.write_text(JS)
    try:
        js = json.loads(subprocess.run(["node", str(script)], input=json.dumps({"tickers": tickers, "cases": [c[:3] for c in CASES]}),
                                       capture_output=True, text=True, check=True).stdout)
    finally:
        script.unlink()
    bad = 0
    for t, (df, res) in enumerate(zip(frames, js)):
        poc, vah, val = S.value_area(df, 20, 0.7)
        for name, py in zip(("poc", "vah", "val"), (poc, vah, val)):
            jv = np.array([np.nan if x is None else x for x in res["levels"][("poc", "vah", "val").index(name)]])
            same = np.array_equal(np.isnan(py), np.isnan(jv)) and np.allclose(py[~np.isnan(py)], jv[~np.isnan(jv)], rtol=0, atol=1e-9)
            bad += not same
            if not same:
                print(f"BAD levels {name} ticker {t}")
        for name, _, _, fn in CASES:
            py = fn(df).signals.to_numpy()
            jv = np.array(res[name])
            same = np.array_equal(py, jv)
            bad += not same
            print(f"{'ok ' if same else 'BAD'} ticker {t} {name:12s} alerts py {int((py != 0).sum()):3d} js {int((jv != 0).sum()):3d}"
                  + ("" if same else f"  first diff at {int(np.flatnonzero(py != jv)[0])}"))
    if not any((np.array(r["btv"]) != 0).any() for r in js[:-1]) or any(np.array(js[-1][c[0]]).any() for c in CASES):
        bad += 1
        print("BAD: expected alerts with volume and none without")
    print("PASS" if not bad else f"FAIL ({bad})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
