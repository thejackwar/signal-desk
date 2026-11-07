"""
Every chart pattern the site detects, backtested on Bitcoin, one file for the Terminal's Patterns tab.

Detectors: pattern_finder.py (the chart-pattern finder) and deck_patterns.py (multi-bar, short-term and
candlestick patterns), both run candle by candle on BTC/USDT daily and 4-hour candles from START.
Each detector sees only the candles up to that close, so nothing looks ahead.

Timeframes: daily (30-candle time exit), 4-hour (60 candles = 10 days), 1-hour (72 candles = 3 days).

Trade rules (the same for every pattern, so they can be compared):
  entry   next candle's open after the breakout close (skipped if it opens past the stop or the target)
  stop    the pattern's own protective stop
  target  the pattern's measured-move target; patterns without one (candlesticks, NR4, inside bar, gaps) use 2R
  time    not hit after MAX_HOLD candles: sold at that candle's close
  a candle touching both stop and target counts as the stop; a gap through either fills at the open
  0.1% fee per side, no leverage, long and short signals both traded

Output: docs/data/crypto/btc_patterns.json  {as_of, start, rules, notes, counts}  plus one trades file per
timeframe, docs/data/crypto/btc_patterns_<D|4H|1H>.json  {tf, trades: [...]}  (kept apart: the 1-hour file is big)
  each trade: id, tf, name, det, dir, ts (signal candle, unix s), te (entry candle), tx (exit candle),
  entry, stop, target, tr ("pattern" / "2R"), exit, why (stop / target / time), ret (% after fees),
  shape: {pts: [[t, price, label]], lines: [[t1, p1, t2, p2]], box: [t1, t2, lo, hi]}

Data: Binance BTCUSDT klines (public API); falls back to research/btc-dip/data/btc_1h.csv.gz.
Run: python engine/btc_patterns.py   (crypto.yml runs it every day after the crypto scan)
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deck_patterns as dp      # noqa: E402
import pattern_finder as pf     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data", "crypto", "btc_patterns.json")
CSV = os.path.join(ROOT, "research", "btc-dip", "data", "btc_1h.csv.gz")
START = pd.Timestamp("2021-01-01", tz="UTC")
FEE = 0.001
TFS = {"D": {"interval": "1d", "rule": "1D", "pf": "daily", "hold": 30, "warm": 420},
       "4H": {"interval": "4h", "rule": "4h", "pf": "hourly", "hold": 60, "warm": 90},
       "1H": {"interval": "1h", "rule": "1h", "pf": "hourly", "hold": 72, "warm": 30}}
DET = {"chart": "Chart", "deck-multi": "Deck · chart", "deck-short": "Deck · short-term", "deck-candle": "Deck · candlestick"}

# ------------------------------------------------------------------ stop / take-profit rules, per pattern
CH = {
    "Double top": ("Just above the second peak", "Neckline minus the pattern height"),
    "Double bottom": ("Just below the second trough", "Neckline plus the pattern height"),
    "Triple top": ("Just above the last peak", "Neckline minus the pattern height"),
    "Triple bottom": ("Just below the last trough", "Neckline plus the pattern height"),
    "Head & shoulders": ("Just above the right shoulder", "Neckline minus the head-to-neckline height"),
    "Inverse head & shoulders": ("Just below the right shoulder", "Neckline plus the head-to-neckline height"),
    "Bull flag": ("Bottom line of the flag", "Breakout + flagpole length"),
    "Bear flag": ("Top line of the flag", "Breakout − flagpole length"),
    "Bull pennant": ("Bottom line of the pennant", "Breakout + flagpole length"),
    "Bear pennant": ("Top line of the pennant", "Breakout − flagpole length"),
    "Rectangle": ("Opposite side of the box", "Breakout ± box height"),
    "Ascending triangle": ("Lower (rising) line", "Breakout + triangle height"),
    "Descending triangle": ("Upper (falling) line", "Breakout − triangle height"),
    "Symmetrical triangle": ("Opposite triangle line", "Breakout ± triangle height"),
    "Rising wedge": ("Upper wedge line", "Breakout − wedge height"),
    "Falling wedge": ("Lower wedge line", "Breakout + wedge height"),
    "Diamond": ("Opposite side of the diamond", "Breakout ± diamond height"),
    "Diamond top": ("Upper side of the diamond", "Breakout − diamond height"),
    "Diamond bottom": ("Lower side of the diamond", "Breakout + diamond height"),
    "Bump & run reversal": ("The bump's high", "The lead-in low"),
    "Rounding bottom": ("The bottom of the bowl", "Rim + bowl depth"),
    "ABCD": ("C ± 1.8 × the BC leg (beyond D)", "61.8% of the CD leg back toward C"),
    "Gartley": ("Point X", "61.8% of the CD leg back toward C"),
    "Butterfly": ("1.618 × XA extension", "61.8% of the CD leg back toward C"),
}
DK = {
    "Double top": "Breakout − pattern height", "Double bottom": "Breakout + pattern height",
    "Triple top": "Breakout − pattern height", "Triple bottom": "Breakout + pattern height",
    "Head & shoulders top": "Neckline − head depth", "Inverse head & shoulders": "Neckline + head depth",
    "Rectangle": "Breakout ± box height", "Ascending triangle": "Breakout ± triangle height",
    "Descending triangle": "Breakout ± triangle height", "Symmetrical triangle": "Breakout ± triangle height",
    "Rising wedge": "Up: breakout + height · down: the wedge's lowest low",
    "Falling wedge": "Up: the wedge's highest high · down: breakout − height",
    "Cup and handle": "Cup lip + cup depth",
    "Bull flag": "Flag low + flagpole length", "Bear flag": "Flag high − flagpole length",
    "Bull pennant": "Pennant low + flagpole length", "Bear pennant": "Pennant high − flagpole length",
}
SHORT = {
    "Narrow range": ("Other side of the NR4 candle", "2R (twice the risk)"),
    "Inside bar": ("Other side of the inside bar", "2R (twice the risk)"),
    "Pipe bottom": ("Low of the two pipe candles", "Breakout + pipe height"),
    "Pipe top": ("High of the two pipe candles", "Breakout − pipe height"),
    "Gap up": ("Under the pivot low", "2R (twice the risk)"),
    "Gap down": ("Above the pivot high", "2R (twice the risk)"),
}
TRIGGER = {
    "chart": "A close beyond the pattern's line by 0.1 ATR, on at least 1.3× the 20-candle average volume (harmonics: price reaches the D zone and the candle turns)",
    "deck-multi": "A close beyond the pattern's line by 0.2 ATR",
    "deck-short": "A close beyond the pattern's edge",
    "deck-candle": "A later candle (within 3) closes beyond the candle pattern's high (buy) or low (sell)",
}


def rule_text(fam, name):
    if fam == "chart":
        return CH.get(name, ("The pattern's stop", "The pattern's target"))
    if fam == "deck-candle":
        return ("Other side of the candle pattern", "2R (twice the risk)")
    if fam == "deck-short" and name in SHORT:
        return SHORT[name]
    return ("Just outside the breakout candle (0.2 ATR beyond it)", DK.get(name, "The pattern's measured move"))


# ------------------------------------------------------------------ data
def binance(interval, start):
    rows, t = [], int(start.timestamp() * 1000)
    while True:
        url = f"https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval={interval}&startTime={t}&limit=1000"
        req = urllib.request.Request(url, headers={"User-Agent": "signal-desk"})
        with urllib.request.urlopen(req, timeout=30) as r:
            js = json.load(r)
        if not js:
            break
        rows += js
        if len(js) < 1000:
            break
        t = js[-1][0] + 1
        time.sleep(0.2)
    now = time.time() * 1000
    rows = [k for k in rows if k[6] < now]                    # finished candles only
    df = pd.DataFrame([[k[0], *map(float, k[1:6])] for k in rows], columns=["t", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df.pop("t"), unit="ms", utc=True)
    return df[~df.index.duplicated()]


def from_csv(rule, start):
    raw = pd.read_csv(CSV)
    raw["time"] = pd.to_datetime(raw["time"], utc=True, format="mixed")
    raw = raw.set_index("time").sort_index()
    df = raw.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return df[df.index >= start].iloc[:-1]                 # the last candle may be unfinished


def candles(tf):
    cfg = TFS[tf]
    start = START - pd.Timedelta(days=cfg["warm"])
    try:
        df, src = binance(cfg["interval"], start), "Binance BTCUSDT"
        if len(df) < 500:
            raise ValueError("too few candles")
    except Exception as e:                                     # noqa: BLE001
        print(f"{tf}: Binance unavailable ({e}), using the hourly file")
        df, src = from_csv(cfg["rule"], start), "Binance BTCUSDT (hourly file)"
    return df, src


# ------------------------------------------------------------------ backtest
def run(tf):
    cfg = TFS[tf]
    df, src = candles(tf)
    D = dp.add_atr(df.rename(columns=str.capitalize))
    o, h, l, c, v = (D[k].values.astype(float) for k in ("Open", "High", "Low", "Close", "Volume"))
    ts = ((D.index - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)).values
    vavg = pd.Series(v).rolling(20).mean().values
    n = len(D)
    first = int(np.searchsorted(D.index, START))
    look = pf.SETTINGS[cfg["pf"]]["lookback"]
    T = lambda k: int(ts[k])                                    # noqa: E731
    sigs, seen = [], set()
    for i in range(max(first, 300), n - 1):
        # chart-pattern finder
        s0 = i - look + 1
        vr = float(v[i] / vavg[i]) if vavg[i] > 0 else None
        try:
            pats, _ = pf.find_patterns(D.iloc[s0:i + 1], cfg["pf"], c[i], c[i - 1], vr)
        except Exception:                                       # noqa: BLE001
            pats = []
        for p in pats:
            if p["stage"] != "breakout" or p["id"] in seen:
                continue
            seen.add(p["id"])
            ix = lambda x: min(max(s0 + int(x), 0), i)          # noqa: E731
            shape = {"pts": [[T(ix(x)), round(float(y), 2), lb] for x, y, lb in p.get("points") or []],
                     "lines": [[T(ix(a[0])), round(float(a[1]), 2), T(ix(b[0])), round(float(b[1]), 2)] for a, b in p.get("lines") or []]}
            sigs.append((i, "chart", p["name"], p["dir"], p["stop"], p["target"], shape))
        # deck patterns
        s1 = max(0, i - 289)
        w2 = D.iloc[s1:i + 1]
        try:
            dps = dp.detect(w2)
        except Exception:                                       # noqa: BLE001
            dps = []
        for p in dps:
            b = p.get("brk")
            if p["stage"] != "breakout" or not b or b["bar"] != len(w2) - 1:
                continue
            key = ("deck", p["name"], w2.index[p["start"]], b["dir"])
            if key in seen:
                continue
            seen.add(key)
            ix = lambda x: min(max(s1 + int(x), 0), i)          # noqa: E731
            shape = {"pts": [[T(ix(pt[0])), round(float(pt[1]), 2), pt[2] if len(pt) > 2 else ""] for pt in p.get("points") or []],
                     "lines": []}
            for ln in p.get("lines") or []:
                f, x0, x1 = ln[0], ln[1], ln[2] if ln[2] is not None else len(w2) - 1
                shape["lines"].append([T(ix(x0)), round(float(f(x0)), 2), T(ix(x1)), round(float(f(x1)), 2)])
            if p.get("box"):
                a0, a1 = p["box"]
                shape["box"] = [T(ix(a0)), T(ix(a1)), round(float(l[s1 + a0:s1 + a1 + 1].min()), 2), round(float(h[s1 + a0:s1 + a1 + 1].max()), 2)]
            sigs.append((i, "deck-" + p["family"], p["name"].split(" (")[0], b["dir"], b["stop"], b.get("target"), shape))

    trades = []
    for i, fam, name, d, stop, tgt, shape in sigs:
        e, long = i + 1, d == "LONG"
        entry = o[e]
        risk = (entry - stop) if long else (stop - entry)
        tr = "pattern"
        if tgt is None or not np.isfinite(tgt):
            tgt, tr = (entry + 2 * risk if long else entry - 2 * risk), "2R"
        if risk <= 0 or (long and tgt <= entry) or (not long and tgt >= entry):
            continue
        out, why, j = None, None, e
        for j in range(e, min(n, e + cfg["hold"])):
            if long:
                if j > e and o[j] <= stop: out, why = o[j], "stop"; break      # noqa: E701,E702
                if j > e and o[j] >= tgt: out, why = o[j], "target"; break     # noqa: E701,E702
                if l[j] <= stop: out, why = stop, "stop"; break                # noqa: E701,E702
                if h[j] >= tgt: out, why = tgt, "target"; break                # noqa: E701,E702
            else:
                if j > e and o[j] >= stop: out, why = o[j], "stop"; break      # noqa: E701,E702
                if j > e and o[j] <= tgt: out, why = o[j], "target"; break     # noqa: E701,E702
                if h[j] >= stop: out, why = stop, "stop"; break                # noqa: E701,E702
                if l[j] <= tgt: out, why = tgt, "target"; break                # noqa: E701,E702
        if out is None:
            if e + cfg["hold"] > n:
                why = "open"                                    # still running: shown, not counted
                j, out = n - 1, c[n - 1]
            else:
                out, why = c[j], "time"
        gross = (out / entry - 1) if long else (entry / out - 1)
        ret = ((1 + gross) * (1 - FEE) ** 2 - 1) * 100
        trades.append({"id": f"{tf}-{T(i)}-{name.lower().replace(' & ', '-').replace(' ', '-')}-{d[0]}-{fam.split('-')[-1][0]}",
                       "tf": tf, "name": name, "fam": fam, "dir": d, "ts": T(i), "te": T(e), "tx": T(j),
                       "entry": round(float(entry), 2), "stop": round(float(stop), 2), "target": round(float(tgt), 2), "tr": tr,
                       "exit": round(float(out), 2), "why": why, "ret": round(float(ret), 3), "bars": j - e + 1, "shape": shape})
    print(f"{tf}: {len(D)} candles, {len(sigs)} signals, {len(trades)} trades")
    return trades, src


def main():
    trades, srcs, counts = [], [], {}
    for tf in TFS:
        t, s = run(tf)
        t.sort(key=lambda x: -x["ts"])
        path = OUT.replace(".json", f"_{tf}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"tf": tf, "trades": t}, f, separators=(",", ":"))
        print(f"wrote {path}: {len(t)} trades, {os.path.getsize(path) / 1e6:.2f} MB")
        trades += t
        srcs.append(s)
        counts[tf] = len(t)
    rules = {}
    for x in trades:
        k = f"{x['fam']}|{x['name']}"
        if k not in rules:
            stop, tgt = rule_text(x["fam"], x["name"])
            rules[k] = {"stop": stop, "target": tgt}
    out = {"as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "start": str(START.date()),
           "source": srcs[0], "fee_pct": FEE * 100, "hold": {k: v["hold"] for k, v in TFS.items()},
           "families": DET, "trigger": TRIGGER, "rules": rules, "counts": counts}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
