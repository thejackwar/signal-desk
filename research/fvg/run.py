"""Run the FVG study: defaults, then one-rule-at-a-time variations. Writes results/summary.json."""
import json, sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fvg

btc = fvg.load_btc()
stocks = {t: df for t, df in fvg.load_stocks().items() if t != "SPY" and len(df) >= 300}
spy = fvg.spy_close()


def years(df):
    return (df.index[-1] - df.index[0]).days / 365.25


def run_btc(tf, p):
    df = btc[tf]
    tr = fvg.find_trades(df, p, use_volume=False)
    st = fvg.stats(tr, fvg.COST["btc"], years(df))
    eq = fvg.btc_account(df, tr, fvg.COST["btc"]).resample("1D").last().dropna()
    return st, eq, tr


def run_stocks(p, account=True):
    trades = {t: fvg.find_trades(df, p, use_volume=True) for t, df in stocks.items()}
    allt = [x for v in trades.values() for x in v]
    span = max(years(df) for df in stocks.values())
    st = fvg.stats(allt, fvg.COST["stocks"], span)
    eq = fvg.stock_account(stocks, trades, fvg.COST["stocks"]) if account else None
    return st, eq, trades


variants = {
    "default (mid entry, 2R)": {},
    "entry near edge": {"entry": "near"},
    "entry far edge": {"entry": "far"},
    "target 1R": {"target_r": 1.0},
    "target 3R": {"target_r": 3.0},
    "no trend filter": {"trend": False},
    "no strong-candle filter": {"b_range_mult": 0.0, "close_pos": 1.0},
    "bigger gaps (0.5 ATR)": {"min_gap_atr": 0.5},
    "no volume filter": {"volume": False},
    "expiry 5 bars": {"expiry": 5},
}

out = {"btc": {}, "stocks": {}, "bench": {}}

# benchmarks
for tf in ["1D"]:
    c = btc[tf]["close"]
    eq = c / c.iloc[0]
    out["bench"]["BTC buy & hold"] = {"start": f"{c.index[0]:%Y-%m-%d}", **fvg.summary(eq)}
first = min(df.index[0] for df in stocks.values())
s = spy[spy.index >= first]
out["bench"]["SPY buy & hold"] = {"start": f"{s.index[0]:%Y-%m-%d}", **fvg.summary(s / s.iloc[0])}

for tf in ["1H", "4H", "1D"]:
    out["btc"][tf] = {}
    for name, ch in variants.items():
        if name == "no volume filter":
            continue
        p = {**fvg.DEFAULT, **ch}
        st, eq, tr = run_btc(tf, p)
        out["btc"][tf][name] = {**st, **(fvg.summary(eq) if st["n"] else {})}
        print("BTC", tf, name, out["btc"][tf][name], flush=True)

for name, ch in variants.items():
    p = {**fvg.DEFAULT, **ch}
    st, eq, trades = run_stocks(p)
    out["stocks"][name] = {**st, **(fvg.summary(eq) if st["n"] else {})}
    if name.startswith("default"):
        per = {t: fvg.stats(v, fvg.COST["stocks"], 15)["n"] and fvg.stats(v, fvg.COST["stocks"], 15) for t, v in trades.items()}
        out["stocks_per_ticker"] = per
        rows = [dict(t=t, **x) for t, v in trades.items() for x in v]
        pd.DataFrame(rows).to_csv(fvg.OUT / "stock_trades_default.csv", index=False)
    print("STOCKS", name, out["stocks"][name], flush=True)

fvg.OUT.mkdir(exist_ok=True)
(fvg.OUT / "summary.json").write_text(json.dumps(out, indent=1, default=str))
