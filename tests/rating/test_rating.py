"""
Checks docs/rating.js (the technical rating on the stock pages) against the `ta` library, an
independent Python implementation of the same indicators, and checks the rating's behaviour.

  1. Indicator values: SMA, EMA, RSI, Stochastic, CCI, ADX/DI, MACD, Williams %R on random price
     histories. `ta` seeds its averages differently, so the comparison uses long histories where
     the seed no longer matters (the difference shrinks every candle).
  2. Ratings: a steady climb rates Buy or Strong buy, a steady fall Sell or Strong sell, scores stay
     in [-1, 1], labels match the thresholds, short histories leave the long averages out.

Run:  python tests/rating/test_rating.py     (needs node, numpy, pandas, ta)
"""
import json, os, subprocess, sys
import numpy as np, pandas as pd
import ta

HERE = os.path.dirname(os.path.abspath(__file__))

def node(cases):
    p = subprocess.run(["node", os.path.join(HERE, "run_js.mjs")], input=json.dumps({"cases": cases}),
                       capture_output=True, text=True, check=True)
    return json.loads(p.stdout)

def candles(df):
    return [{"time": i, "open": r.o, "high": r.h, "low": r.l, "close": r.c} for i, r in enumerate(df.itertuples())]

def walk(rng, n, drift=0.0, vol=1.0):
    c = 100 * np.exp(np.cumsum(rng.normal(drift / 100, vol / 100, n)))     # prices stay positive
    o = c + rng.normal(0, vol * .4, n)
    h = np.maximum(o, c) + np.abs(rng.normal(0, vol * .5, n)); l = np.minimum(o, c) - np.abs(rng.normal(0, vol * .5, n))
    return pd.DataFrame({"o": o, "h": h, "l": l, "c": c})

def close(a, b, rel=1e-6, abs_=1e-6):
    return a is not None and abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))

def test_indicators(n_cases=60, seed=3):
    rng = np.random.default_rng(seed)
    frames = [walk(rng, int(rng.integers(3000, 3600)), rng.normal(0, .05), rng.choice([.3, 1, 3])) for _ in range(n_cases)]
    js = node([{"candles": candles(df)} for df in frames])
    errs = []
    for k, (df, r) in enumerate(zip(frames, js)):
        H, L, C = df.h, df.l, df.c
        stk = ta.momentum.StochasticOscillator(H, L, C, window=14, smooth_window=3)
        k_slow = stk.stoch_signal()                      # SMA3 of raw %K = the "slow" %K
        adx = ta.trend.ADXIndicator(H, L, C, window=14)
        mac = ta.trend.MACD(C, 26, 12, 9)
        ref = {
            "sma50": ta.trend.SMAIndicator(C, 50).sma_indicator().iloc[-1],
            "ema50": ta.trend.EMAIndicator(C, 50).ema_indicator().iloc[-1],
            "ema200": ta.trend.EMAIndicator(C, 200).ema_indicator().iloc[-1],
            "rsi": ta.momentum.RSIIndicator(C, 14).rsi().iloc[-1],
            "stochK": k_slow.iloc[-1], "stochD": k_slow.rolling(3).mean().iloc[-1],
            "cci": ta.trend.CCIIndicator(H, L, C, 20).cci().iloc[-1],
            "adx": adx.adx().iloc[-1], "pdi": adx.adx_pos().iloc[-1], "ndi": adx.adx_neg().iloc[-1],
            "macd": mac.macd().iloc[-1], "signal": mac.macd_signal().iloc[-1],
            "wr": ta.momentum.WilliamsRIndicator(H, L, C, 14).williams_r().iloc[-1],
        }
        for name, want in ref.items():
            tol = 1e-4 if name in ("adx", "pdi", "ndi") else 1e-6
            if not close(r[name], float(want), rel=tol, abs_=tol):
                errs.append(f"case {k} {name}: js {r[name]} vs ta {want}")
    print(f"indicators: {n_cases} histories x 13 values compared with the ta library")
    return errs

def test_ratings(seed=5):
    rng = np.random.default_rng(seed)
    errs, cases, kinds = [], [], []
    for _ in range(40):
        cases.append({"candles": candles(walk(rng, 400, .6, 1))}); kinds.append("up")
        cases.append({"candles": candles(walk(rng, 400, -.6, 1))}); kinds.append("down")
        cases.append({"candles": candles(walk(rng, int(rng.integers(1, 400)), 0, 1))}); kinds.append("any")
    cases.append({"candles": candles(walk(rng, 60, 0, 1))}); kinds.append("short")
    cases.append({"candles": candles(pd.DataFrame({"o": [5.0] * 300, "h": [5.0] * 300, "l": [5.0] * 300, "c": [5.0] * 300}))}); kinds.append("flat")
    js = node(cases)
    thr = lambda s: None if s is None else "Strong buy" if s >= .5 else "Buy" if s >= .1 else "Neutral" if s > -.1 else "Sell" if s > -.5 else "Strong sell"
    for k, (kind, r) in enumerate(zip(kinds, js)):
        R = r["rating"]
        s = R["score"]
        if s is not None and not -1 <= s <= 1: errs.append(f"case {k}: score {s}")
        if R["label"] != thr(s): errs.append(f"case {k}: label {R['label']} for score {s}")
        for g in ("ma", "osc"):
            G = R[g]
            if G["buy"] + G["sell"] + G["neutral"] != G["used"]: errs.append(f"case {k} {g}: counts")
        if kind == "up" and R["label"] not in ("Buy", "Strong buy"): errs.append(f"steady climb rated {R['label']} ({s})")
        if kind == "down" and R["label"] not in ("Sell", "Strong sell"): errs.append(f"steady fall rated {R['label']} ({s})")
        if kind == "short":
            left_out = [x["name"] for x in R["ma"]["items"] if x["vote"] is None]
            if sorted(left_out) != sorted(["SMA 100", "EMA 100", "SMA 200", "EMA 200"]): errs.append(f"short history left out {left_out}")
        if kind == "flat" and R["label"] != "Neutral": errs.append(f"flat price rated {R['label']}")
    print(f"ratings: {len(cases)} histories (climbing, falling, random, short, flat)")
    return errs

if __name__ == "__main__":
    errs = test_indicators() + test_ratings()
    for e in errs[:30]: print("FAIL", e)
    print("ALL OK" if not errs else f"{len(errs)} failures")
    sys.exit(1 if errs else 0)
