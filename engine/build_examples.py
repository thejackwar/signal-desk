"""
Builds docs/data/strategies.json: the written explanations from
strategies_content.json plus worked chart examples.

Examples use simulated prices (clearly labelled in the app) and are
produced by running the *same* detector the live scanner uses, so the
example chart shows exactly what an alert looks like.

  python engine/build_examples.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from chart import payload  # noqa: E402
from strategies import REGISTRY, set_benchmark  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AFTER = 20  # sessions shown after the signal, to see what happened next


def ohlc_from_close(close, rng, vol):
    n = len(close)
    openp = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, vol / 4, n))
    high = np.maximum(openp, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    low = np.minimum(openp, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    idx = pd.bdate_range("2025-01-02", periods=n)
    return pd.DataFrame({"open": openp, "high": high, "low": low, "close": close, "volume": 1e6}, index=idx)


def from_waypoints(pts, rng, noise=0.006):
    xs, ys = zip(*pts)
    base = np.interp(np.arange(xs[-1] + 1), xs, ys)
    wiggle = np.cumsum(rng.normal(0, noise, len(base)))
    wiggle -= np.convolve(wiggle, np.ones(9) / 9, mode="same")  # keep the shape, add texture
    return ohlc_from_close(base * (1 + wiggle), rng, noise * 1.6)


def random_walk(rng, n=420):
    r = rng.normal(0.0002, 0.015, n) + 0.005 * np.sin(np.arange(n) / rng.uniform(20, 50) + rng.uniform(0, 6))
    return ohlc_from_close(100 * np.exp(np.cumsum(r)), rng, 0.015)


def flip(pts):
    """Mirror a waypoint path vertically (turns a bottom into a top)."""
    return [(x, 200 - y) for x, y in pts]


SHAPES = {
    ("double_top_bottom", 1): [(0, 118), (30, 100), (45, 84), (62, 94), (80, 84.5), (98, 99), (115, 104), (130, 108)],
    ("head_shoulders", 1): [(0, 118), (25, 100), (40, 90), (52, 99), (70, 82), (88, 99.5), (103, 90.5), (122, 104), (140, 108)],
    ("triangle", 1): [(0, 86), (12, 100), (24, 88), (36, 100), (48, 92), (60, 100), (71, 95), (82, 100), (92, 97.5), (100, 105), (118, 110)],
    ("ma200", 1): [(0, 100), (110, 118), (200, 86), (240, 92), (262, 101), (300, 110)],
    ("golden_cross", 1): [(0, 110), (150, 78), (290, 108), (340, 118)],
}
SHAPES[("double_top_bottom", -1)] = flip(SHAPES[("double_top_bottom", 1)])
SHAPES[("head_shoulders", -1)] = flip(SHAPES[("head_shoulders", 1)])
SHAPES[("ma200", -1)] = flip(SHAPES[("ma200", 1)])
SHAPES[("golden_cross", -1)] = flip(SHAPES[("golden_cross", 1)])
SHAPES[("triangle", -1)] = [(0, 114), (12, 100), (24, 112), (36, 100), (48, 108), (60, 100),
                            (71, 105), (82, 100), (92, 102.5), (100, 95), (118, 90)]

WINDOW = {"ma200": 150, "golden_cross": 150}  # bars of history before the signal


def with_volume(df, seed):
    """Simulated volume: busier on big-move days, with the odd unrelated spike."""
    vr = np.random.default_rng(seed + 99991)
    move = df["close"].pct_change().abs().fillna(0).values
    vol = 1e6 * np.exp(vr.normal(0, 0.25, len(df))) * (1 + 40 * move)
    return df.assign(volume=np.round(vol))


def simulated_market(df, seed):
    """A calmer random walk on the same days, standing in for SPY in the relative-strength example."""
    mr = np.random.default_rng(seed + 77773)
    r = mr.normal(0.0004, 0.009, len(df))
    return pd.Series(400 * np.exp(np.cumsum(r)), index=df.index)


def find_example(sid, side, max_tries=4000):
    fn = REGISTRY[sid]
    for seed in range(max_tries):
        rng = np.random.default_rng(seed * 31 + (7 if side > 0 else 3))
        shaped = (sid, side) in SHAPES
        df = from_waypoints(SHAPES[(sid, side)], rng) if shaped else random_walk(rng)
        if sid == "volume_breakout":
            df = with_volume(df, seed)
        if sid == "relative_strength":
            set_benchmark(simulated_market(df, seed), "SPY")
        res = fn(df)
        sig = res.signals.values
        hits = [i for i in np.flatnonzero(sig == side) if i + AFTER < len(df) and i >= 60]
        for i in reversed(hits):
            back = WINDOW.get(sid, 100)
            start = max(0, i - back)
            end = min(len(df), i + AFTER + 1)
            others = np.count_nonzero(sig[start:end]) - 1
            move = df["close"].iloc[i + AFTER] / df["close"].iloc[i] - 1
            if shaped:
                good = others == 0 and side * move > 0.02
            else:
                good = others <= 2 and side * move > 0.04 and df["close"].iloc[start:end].min() > 20
            if good:
                return df, res, int(i), int(start), int(end), float(move)
    raise RuntimeError(f"no clean example for {sid} {side}")


def main():
    content = json.loads((Path(__file__).parent / "strategies_content.json").read_text())
    for s in content["strategies"]:
        s["examples"] = []
        if s.get("kind") == "lines":                     # Top-Down: drawn live on the Trendlines page instead
            continue
        for side in (1, -1):
            df, res, i, start, end, move = find_example(s["id"], side)
            chart = payload(df, res, start, end)
            chart["focus"] = i - start
            label = "Bullish" if side > 0 else "Bearish"
            s["examples"].append({
                "side": "bullish" if side > 0 else "bearish",
                "title": f"{label} example",
                "signal": res.notes.get(i, ""),
                "price": round(float(df["close"].iloc[i]), 2),
                "after_days": AFTER,
                "after_move": round(move * 100, 1),
                "chart": chart,
            })
            print(f"{s['id']:18s} {label:8s} bar {i - start:3d}  +{AFTER}d: {move * 100:+.1f}%  {res.notes.get(i, '')}")
    out = ROOT / "docs" / "data" / "strategies.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(content, separators=(",", ":")))
    print(f"-> {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
