"""
Estimated BTC/USDT liquidation heatmap.

Where would leveraged traders be forced out? Every hour, new positions are
opened near that hour's price. A long opened at price P with leverage L is
liquidated roughly when price falls to P * (1 - 1/L + mm); a short when price
rises to P * (1 + 1/L - mm), where mm is the maintenance margin. We spread
each hour's new positions over typical leverage levels, keep them on the map
until price reaches their liquidation level (they get wiped out) or they
slowly close on their own, and record the map every two hours.

This is the same kind of model the well-known heatmap sites use; it is an
estimate, not exchange data.

Data: BTCUSDT perpetual futures from Binance (hourly candles plus open
interest, so only *new* positions are counted) when reachable; otherwise
Yahoo Finance BTC-USD hourly candles, using volume as the measure of new
positions.

  python engine/liquidations.py           # real data
  python engine/liquidations.py --demo    # simulated prices, no internet
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "crypto" / "liquidations.json"

HOURS = 30 * 24                 # history shown
WARMUP = 10 * 24                # extra hours so the map is already "full" at the start
STEP = 2                        # one heatmap column every 2 hours
BINS = 140                      # price rows
LEVERAGE = {5: .10, 10: .25, 25: .30, 50: .20, 100: .15}   # share of new positions per leverage
MM = 0.004                      # maintenance margin
DECAY = 0.006                   # share of open positions closed voluntarily each hour


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def fetch_binance(hours: int) -> tuple[pd.DataFrame, str]:
    base = "https://fapi.binance.com"
    rows, end = [], None
    while len(rows) < hours:
        url = f"{base}/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=1500" + (f"&endTime={end}" if end else "")
        k = _get(url)
        if not k:
            break
        rows = k + rows
        end = k[0][0] - 1
        if len(k) < 1500:
            break
    df = pd.DataFrame(rows, columns=list("tohlcv") + ["ct", "qv", "n", "tb", "tq", "x"])
    df = df.drop_duplicates("t").sort_values("t").tail(hours)
    df.index = pd.to_datetime(df["t"], unit="ms")
    df = df[["o", "h", "l", "c", "qv"]].astype(float).rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "qv": "volume"})
    # open interest history (Binance keeps the last 30 days)
    oi = _get(f"{base}/futures/data/openInterestHist?symbol=BTCUSDT&period=1h&limit=500")
    s = pd.Series({pd.to_datetime(x["timestamp"], unit="ms"): float(x["sumOpenInterestValue"]) for x in oi}).sort_index()
    df["oi"] = s.reindex(df.index).ffill()
    return df, "Binance BTCUSDT perpetual futures (candles + open interest)"


def fetch_yahoo(hours: int) -> tuple[pd.DataFrame, str]:
    import yfinance as yf
    df = yf.download("BTC-USD", period="60d", interval="1h", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna().tail(hours)
    if df.index.tz is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)
    df["oi"] = np.nan
    return df, "Yahoo Finance BTC-USD hourly candles (volume as a proxy for new positions)"


def fetch_demo(hours: int) -> tuple[pd.DataFrame, str]:
    rng = np.random.default_rng(3)
    idx = pd.date_range(end=pd.Timestamp.now("UTC").floor("h").tz_localize(None), periods=hours, freq="h")
    r = rng.normal(0, .006, hours) + .002 * np.sin(np.arange(hours) / 60)
    c = 80000 * np.exp(np.cumsum(r))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, .003, hours)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, .003, hours)))
    v = rng.lognormal(18, .5, hours) * (1 + 20 * np.abs(r))
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v, "oi": np.nan}, index=idx), "Demo (simulated prices)"


def model(df: pd.DataFrame) -> dict:
    shown = df.iloc[-HOURS:]
    lo = shown["low"].min() * 0.86
    hi = shown["high"].max() * 1.14
    edges = np.linspace(lo, hi, BINS + 1)
    mids = (edges[:-1] + edges[1:]) / 2

    # how much new leverage enters each hour
    if df["oi"].notna().sum() > 48:
        new = df["oi"].diff().clip(lower=0).fillna(0)
        # hours without OI data: fall back to a volume share
        vol_share = df["volume"] * (new[new > 0].median() / df["volume"].median() if (new > 0).any() else 0.05)
        new = new.where(df["oi"].notna(), vol_share)
    else:
        new = df["volume"] * 0.05   # assume ~5% of traded value opens new leveraged positions
    new = new.values

    longs = np.zeros(BINS)
    shorts = np.zeros(BINS)
    cols, times, removed_l, removed_s = [], [], [], []
    H, L, C = df["high"].values, df["low"].values, df["close"].values
    typ = (df["high"] + df["low"] + df["close"]).values / 3
    start = len(df) - len(shown)

    def bin_of(p):
        i = int(np.searchsorted(edges, p)) - 1
        return i if 0 <= i < BINS else None   # off the chart: not drawn

    for t in range(len(df)):
        # 1. price this hour wipes out positions whose liquidation level it touched
        if t > 0:
            hit_l = mids >= L[t]          # longs liquidate when price falls to their level
            hit_s = mids <= H[t]          # shorts liquidate when price rises to their level
            removed_l.append(longs[hit_l].sum()); removed_s.append(shorts[hit_s].sum())
            longs[hit_l] = 0
            shorts[hit_s] = 0
        # 2. some positions close voluntarily
        longs *= 1 - DECAY
        shorts *= 1 - DECAY
        # 3. new positions opened around this hour's price, half long, half short
        for lev, w in LEVERAGE.items():
            amt = new[t] * w / 2
            ll = typ[t] * (1 - 1 / lev + MM)
            sl = typ[t] * (1 + 1 / lev - MM)
            bl, bs = bin_of(ll), bin_of(sl)
            if bl is not None:
                longs[bl] += amt
            if bs is not None:
                shorts[bs] += amt
        if t >= start and (t - start) % STEP == STEP - 1:
            cols.append(longs + shorts)
            times.append(df.index[t])

    M = np.array(cols).T                                   # BINS x columns
    scale = np.percentile(M[M > 0], 99.5) if (M > 0).any() else 1
    q = np.clip(np.sqrt(np.clip(M / scale, 0, 1)) * 255, 0, 255).round().astype(int)

    price = float(C[-1])
    total = longs + shorts
    above = [(mids[i], shorts[i]) for i in range(BINS) if mids[i] > price]
    below = [(mids[i], longs[i]) for i in range(BINS) if mids[i] < price]

    def clusters(items, k=3):
        items = sorted(items, key=lambda x: -x[1])
        out = []
        for p, v in items:
            if v <= 0:
                break
            if all(abs(p - o["price"]) / price > .012 for o in out):
                out.append({"price": round(float(p)), "dist": round((p / price - 1) * 100, 1), "size": round(float(v))})
            if len(out) == k:
                break
        return out

    hrs = shown.iloc[STEP - 1::STEP]
    return {
        "price": round(price, 2),
        "bins": {"lo": round(float(lo), 2), "hi": round(float(hi), 2), "n": BINS},
        "times": [f"{t:%Y-%m-%dT%H:00}" for t in times],
        "matrix": q.tolist(),
        "line": {"close": [round(float(x), 2) for x in hrs["close"]],
                 "high": [round(float(x), 2) for x in shown["high"].rolling(STEP).max().iloc[STEP - 1::STEP]],
                 "low": [round(float(x), 2) for x in shown["low"].rolling(STEP).min().iloc[STEP - 1::STEP]]},
        "now": {"longs": [round(float(x)) for x in longs], "shorts": [round(float(x)) for x in shorts]},
        "short_clusters": clusters(above),
        "long_clusters": clusters(below),
        "wiped_24h": {"longs": round(float(sum(removed_l[-24:]))), "shorts": round(float(sum(removed_s[-24:])))},
        "total_at_risk": round(float(total.sum())),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    hours = HOURS + WARMUP
    if args.demo:
        df, src = fetch_demo(hours)
    else:
        try:
            df, src = fetch_binance(hours)
            if len(df) < HOURS:
                raise RuntimeError("not enough Binance history")
        except Exception as e:
            print(f"Binance not reachable ({e}); using Yahoo Finance")
            df, src = fetch_yahoo(hours)
    if len(df) < 200:
        sys.exit("Not enough price data; leaving the previous heatmap untouched.")
    res = model(df)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "symbol": "BTC/USDT", "source": src, "demo": args.demo,
        "leverage": {str(k): v for k, v in LEVERAGE.items()},
        **res,
    }, separators=(",", ":")))
    print(f"{src}: {len(df)} hours -> {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
