"""
Estimated BTC/USDT liquidation heatmap.

Where would leveraged traders be forced out? Each candle, new positions are
opened near that candle's price. A long opened at price P with leverage L is
liquidated roughly when price falls to P * (1 - 1/L + mm); a short when price
rises to P * (1 + 1/L - mm), where mm is the maintenance margin. We spread
the new positions over typical leverage levels and keep them on the map until
price reaches their liquidation level (they get wiped out), or until they
are closed: when open interest falls, that share of positions is removed.

How many new positions? The rise in open interest (contracts held open) in
that candle: that is money actually entering new positions. When open
interest isn't available, trading volume is used as a rougher stand-in.

Two maps are built:
  fine   - last 24 hours, 15-minute candles, fine price steps
  coarse - last 30 days, hourly candles, one column every 2 hours

This is the same kind of model the well-known heatmap sites use; it is an
estimate, not exchange data.

Data, in order of preference: Binance BTCUSDT perpetual (blocked from GitHub's
US servers), OKX BTC-USDT perpetual, Yahoo Finance BTC-USD.

  python engine/liquidations.py           # real data
  python engine/liquidations.py --demo    # simulated prices, no internet
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "crypto" / "liquidations.json"

LEVERAGE = {5: .10, 10: .25, 25: .30, 50: .20, 100: .15}   # share of new positions per leverage
MM = 0.004                      # maintenance margin
DECAY_PER_HOUR = 0.003          # positions quietly closed when open interest isn't known

VIEWS = {
    #          bar     minutes shown(bars) warmup(bars) step bins  pad
    "fine":   ("15m",  15,     96,         4 * 96,      1,   300,  .07),
    "coarse": ("1H",   60,     30 * 24,    10 * 24,     2,   200,  .14),
}


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


# ------------------------------------------------------------------ sources

def binance(bar: str, n: int) -> tuple[pd.DataFrame, str]:
    b = {"15m": "15m", "1H": "1h"}[bar]
    k = _get(f"https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval={b}&limit={min(n, 1500)}")
    df = pd.DataFrame([r[:8] for r in k], columns=["t", "open", "high", "low", "close", "v", "ct", "volume"])
    df.index = pd.to_datetime(df["t"], unit="ms")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    oi = _get(f"https://fapi.binance.com/futures/data/openInterestHist?symbol=BTCUSDT&period={b}&limit=500")
    s = pd.Series({pd.to_datetime(x["timestamp"], unit="ms"): float(x["sumOpenInterestValue"]) for x in oi}).sort_index()
    df["oi"] = s.reindex(df.index)
    return df, "Binance BTCUSDT perpetual"


def okx(bar: str, n: int) -> tuple[pd.DataFrame, str]:
    rows, after = [], ""
    while len(rows) < n:
        k = _get("https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT-SWAP"
                 f"&bar={bar}&limit=100" + (f"&after={after}" if after else ""))["data"]
        if not k:
            break
        rows += k
        after = k[-1][0]
        time.sleep(0.12)                      # stay well under OKX's rate limit
    df = pd.DataFrame([r[:8] for r in rows], columns=["t", "open", "high", "low", "close", "v", "vccy", "volume"])
    df = df.drop_duplicates("t")
    df.index = pd.to_datetime(df["t"].astype("int64"), unit="ms")
    df = df.sort_index()[["open", "high", "low", "close", "volume"]].astype(float).tail(n)
    df["oi"] = okx_open_interest(bar, df.index)
    return df, "OKX BTC-USDT perpetual"


def okx_open_interest(bar: str, index: pd.DatetimeIndex) -> pd.Series:
    """Open interest in USD for the BTC-USDT swap; empty where OKX has no history."""
    pts = {}
    try:
        end = ""
        for _ in range(15):
            d = _get("https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history?instId=BTC-USDT-SWAP"
                     f"&period={bar}&limit=100" + (f"&end={end}" if end else ""))["data"]
            if not d:
                break
            for r in d:
                pts[pd.to_datetime(int(r[0]), unit="ms")] = float(r[3])     # oiUsd
            oldest = min(int(r[0]) for r in d)
            if pd.to_datetime(oldest, unit="ms") <= index[0]:
                break
            end = str(oldest - 1)
            time.sleep(0.25)
    except Exception as e:
        print(f"  OKX open interest history unavailable ({e})")
    if not pts and bar == "1H":
        try:   # all OKX BTC contracts, hourly: still a good measure of new positions
            d = _get("https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume?ccy=BTC&period=1H")["data"]
            pts = {pd.to_datetime(int(r[0]), unit="ms"): float(r[1]) for r in d}
        except Exception as e:
            print(f"  OKX open interest (all contracts) unavailable ({e})")
    s = pd.Series(pts, dtype=float).sort_index()
    return s.reindex(index) if len(s) else pd.Series(np.nan, index=index)


def yahoo(bar: str, n: int) -> tuple[pd.DataFrame, str]:
    import yfinance as yf
    df = yf.download("BTC-USD", period="5d" if bar == "15m" else "60d", interval={"15m": "15m", "1H": "1h"}[bar],
                     auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna().tail(n)
    if df.index.tz is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)
    df["oi"] = np.nan
    return df, "Yahoo Finance BTC-USD"


def demo(bar: str, n: int) -> tuple[pd.DataFrame, str]:
    minutes = {"15m": 15, "1H": 60}[bar]
    rng = np.random.default_rng(3 if bar == "1H" else 4)
    idx = pd.date_range(end=pd.Timestamp.now("UTC").floor("h").tz_localize(None), periods=n, freq=f"{minutes}min")
    vol = .006 * (minutes / 60) ** .5
    r = rng.normal(0, vol, n) + vol * .3 * np.sin(np.arange(n) / 60)
    c = 80000 * np.exp(np.cumsum(r))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    v = rng.lognormal(17, .5, n) * (1 + 20 * np.abs(r))
    oi = 8e9 + np.cumsum(rng.normal(0, 2e7, n))
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v, "oi": oi}, index=idx), "Demo (simulated prices)"


def fetch(bar: str, n: int, use_demo: bool):
    if use_demo:
        return demo(bar, n)
    errors = []
    for fn in (binance, okx, yahoo):
        try:
            df, src = fn(bar, n)
            if len(df) >= n * .6:
                return df, src
            errors.append(f"{src}: only {len(df)} candles")
        except Exception as e:
            errors.append(f"{fn.__name__}: {e}")
    raise RuntimeError("; ".join(errors))


# -------------------------------------------------------------------- model

def model(df: pd.DataFrame, minutes: int, shown_n: int, step: int, bins: int, pad: float) -> dict:
    shown = df.iloc[-shown_n:]
    lo, hi = shown["low"].min() * (1 - pad), shown["high"].max() * (1 + pad)
    edges = np.linspace(lo, hi, bins + 1)
    mids = (edges[:-1] + edges[1:]) / 2

    oi = df["oi"]
    has_oi = oi.notna().sum() > len(df) * .5
    d_oi = oi.diff()
    if has_oi:
        # where OI is missing, estimate new positions from volume, scaled to match the OI-based numbers
        pos = d_oi.clip(lower=0)
        ratio = (pos[pos > 0].median() / df["volume"].median()) if (pos > 0).any() else .05
        new = pos.where(oi.notna() & d_oi.notna(), df["volume"] * ratio).fillna(0).values
        closed_share = (-d_oi.clip(upper=0) / oi.shift(1)).fillna(0).clip(0, .5).values
    else:
        new = (df["volume"] * .05).values
        closed_share = np.full(len(df), DECAY_PER_HOUR * minutes / 60)

    levs = list(LEVERAGE)
    longs, shorts = np.zeros((len(levs), bins)), np.zeros((len(levs), bins))   # open positions per leverage
    H, L, C = df["high"].values, df["low"].values, df["close"].values
    typ = (df["high"] + df["low"] + df["close"]).values / 3
    start = len(df) - len(shown)
    cols, times, rl, rs = [], [], [], []

    for t in range(len(df)):
        if t > 0:                                        # price sweeps through levels: liquidated
            hl, hs = mids >= L[t], mids <= H[t]
            rl.append(longs[:, hl].sum()); rs.append(shorts[:, hs].sum())
            longs[:, hl] = 0; shorts[:, hs] = 0
        keep = 1 - closed_share[t]                       # positions closed on their own
        longs *= keep; shorts *= keep
        for j, (lev, w) in enumerate(LEVERAGE.items()):  # new positions, half long, half short
            amt = new[t] * w / 2
            for p, arr in ((typ[t] * (1 - 1 / lev + MM), longs), (typ[t] * (1 + 1 / lev - MM), shorts)):
                i = int(np.searchsorted(edges, p)) - 1
                if 0 <= i < bins:
                    arr[j, i] += amt
        if t >= start and (t - start) % step == step - 1:
            cols.append(longs.sum(0) + shorts.sum(0))
            times.append(df.index[t])

    M = np.array(cols).T
    scale = np.percentile(M[M > 0], 99.5) if (M > 0).any() else 1
    q = np.clip(np.sqrt(np.clip(M / scale, 0, 1)) * 255, 0, 255).round().astype(int)

    price = float(C[-1])
    lev_longs, lev_shorts = longs, shorts
    longs, shorts = longs.sum(0), shorts.sum(0)

    def clusters(side_vals, above, k=3):
        items = sorted(((mids[i], side_vals[i]) for i in range(bins) if (mids[i] > price) == above), key=lambda x: -x[1])
        out = []
        for p, v in items:
            if v <= 0 or len(out) == k:
                break
            if all(abs(p - o["price"]) / price > .006 for o in out):
                out.append({"price": round(float(p)), "dist": round((p / price - 1) * 100, 1), "size": round(float(v))})
        return out

    g = shown.groupby(np.arange(len(shown)) // step)
    ohlc = {"o": g["open"].first(), "h": g["high"].max(), "l": g["low"].min(), "c": g["close"].last()}
    per_day = int(round(24 * 60 / minutes / step))
    return {
        "price": round(price, 2),
        "minutes": minutes * step, "cols_per_day": per_day,
        "bins": {"lo": round(float(lo), 2), "hi": round(float(hi), 2), "n": bins},
        "times": [f"{t:%Y-%m-%dT%H:%M}" for t in times],
        "matrix": q.tolist(),
        "ohlc": {k: [round(float(x), 2) for x in v.values[:len(times)]] for k, v in ohlc.items()},
        "now": {"longs": [round(float(x)) for x in longs], "shorts": [round(float(x)) for x in shorts]},
        # liquidation map: positions still open, by leverage (same order as "leverage" at the top of the file)
        "now_lev": {"lev": levs,
                    "longs": [[round(float(x)) for x in r] for r in lev_longs],
                    "shorts": [[round(float(x)) for x in r] for r in lev_shorts]},
        "short_clusters": clusters(shorts, True),
        "long_clusters": clusters(longs, False),
        "wiped_24h": {"longs": round(float(sum(rl[-per_day * step:]))), "shorts": round(float(sum(rs[-per_day * step:])))},
        "uses_oi": bool(has_oi),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    out, sources = {}, {}
    for name, (bar, minutes, shown, warm, step, bins, pad) in VIEWS.items():
        try:
            df, src = fetch(bar, shown + warm, args.demo)
        except Exception as e:
            print(f"{name}: no data ({e})")
            continue
        out[name] = model(df, minutes, shown, step, bins, pad)
        sources[name] = f"{src}, {bar} candles, " + ("open interest" if out[name]["uses_oi"] else "volume as a stand-in for new positions")
        print(f"{name}: {sources[name]} ({len(df)} candles)")
    if "coarse" not in out:
        sys.exit("Not enough price data; leaving the previous heatmap untouched.")
    main_view = out.get("fine") or out["coarse"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "symbol": "BTC/USDT", "demo": args.demo,
        "source": " · ".join(f"{'24h' if k == 'fine' else '3–30 days'}: {v}" for k, v in sources.items()),
        "leverage": {str(k): v for k, v in LEVERAGE.items()},
        "price": main_view["price"],
        "short_clusters": main_view["short_clusters"], "long_clusters": main_view["long_clusters"],
        "wiped_24h": main_view["wiped_24h"],
        "views": out,
    }, separators=(",", ":")))
    print(f"-> {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
