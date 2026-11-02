"""Round 3: other exits (trailing stop, time exit) and a strength filter, on the best round-2 rule sets.
Stocks: strength = 63-day return above SPY's. BTC (no benchmark): strong trend = close > EMA50 > EMA200."""
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fvg

btc = fvg.load_btc()
stocks = {t: df for t, df in fvg.load_stocks().items() if t != "SPY" and len(df) >= 300}
spy = fvg.spy_close()
spy_r = spy.pct_change(63)

rs_stock = {t: (df["close"].pct_change(63) > spy_r.reindex(df.index).ffill()).to_numpy() for t, df in stocks.items()}


def strong_trend(df):
    c = df["close"]
    e50, e200 = c.ewm(span=50, adjust=False).mean(), c.ewm(span=200, adjust=False).mean()
    return ((c > e50) & (e50 > e200)).to_numpy()


rs_btc = {tf: strong_trend(btc[tf]) for tf in ["4H", "1D"]}

BASE = {
    "Stocks 1D": {**fvg.DEFAULT, "entry": "near", "stop_at": "A_low", "stop_buf_atr": 0.5, "target_r": 3.0},
    "BTC 4H": {**fvg.DEFAULT, "entry": "near", "stop_at": "gap", "stop_buf_atr": 0.5, "target_r": 3.0},
    "BTC 1D": {**fvg.DEFAULT, "entry": "near", "stop_at": "gap", "stop_buf_atr": 0.1, "target_r": 3.0},
}
VAR = {
    "round-2 best (fixed 3R)": {},
    "trailing 2 ATR, no target": {"target_r": None, "trail_atr": 2.0},
    "trailing 3 ATR, no target": {"target_r": None, "trail_atr": 3.0},
    "3R target + trailing 3 ATR": {"trail_atr": 3.0},
    "3R target, exit after 10 bars": {"max_hold": 10},
    "3R target, exit after 20 bars": {"max_hold": 20},
    "strength filter (fixed 3R)": {"rs": True},
    "strength + trailing 3 ATR": {"rs": True, "target_r": None, "trail_atr": 3.0},
}


def halves(trades, cost):
    t = sorted(trades, key=lambda x: x["t_in"])
    h = len(t) // 2
    return fvg.stats(t[:h], cost, 1).get("PF"), fvg.stats(t[h:], cost, 1).get("PF")


rows = []
for name, ch in VAR.items():
    for tf in ["4H", "1D"]:
        key = f"BTC {tf}"
        p = {**BASE[key], **ch}
        df = btc[tf]
        tr = fvg.find_trades(df, p, use_volume=False, rs_ok=rs_btc[tf])
        yrs = (df.index[-1] - df.index[0]).days / 365.25
        eq = fvg.btc_account(df, tr, fvg.COST["btc"]).resample("1D").last().dropna()
        h1, h2 = halves(tr, fvg.COST["btc"])
        rows.append({"set": key, "rule": name, **fvg.stats(tr, fvg.COST["btc"], yrs), **fvg.summary(eq), "PF_1st": h1, "PF_2nd": h2})
    p = {**BASE["Stocks 1D"], **ch}
    trades = {t: fvg.find_trades(df, p, use_volume=True, rs_ok=rs_stock[t]) for t, df in stocks.items()}
    allt = [x for v in trades.values() for x in v]
    eq = fvg.stock_account(stocks, trades, fvg.COST["stocks"])
    h1, h2 = halves(allt, fvg.COST["stocks"])
    rows.append({"set": "Stocks 1D", "rule": name, **fvg.stats(allt, fvg.COST["stocks"], 15), **fvg.summary(eq), "PF_1st": h1, "PF_2nd": h2})
    print(name, flush=True)

(fvg.OUT / "round3.json").write_text(json.dumps(rows, indent=1, default=float))
r = pd.DataFrame(rows)
print(r[["set", "rule", "n", "win%", "avg%", "PF", "bars", "x", "cagr%", "maxDD%", "after_tax_cagr%", "PF_1st", "PF_2nd"]].sort_values(["set", "cagr%"]).to_string())
