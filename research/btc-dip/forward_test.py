"""Forward (paper) test of the BTC buy-the-dip strategy, started 31 Oct 2026 10:00 UTC.

Runs every hour in GitHub Actions. Each run downloads the latest closed hourly BTC candles,
appends them to forward/candles.csv and replays the SAME engine as the backtest
(backtest.run) over every candle since the start date, for every variant below. Replaying
instead of keeping trade state means a missed or late run never loses a trade.

Variants: dip 5% and 7% below the 24h high, held 4h / 12h / 24h / 36h / 48h / 72h,
no leverage (the 2x / 3x columns are the same trades scaled, after funding is ignored).
Outputs: forward/summary.json (per variant), forward/trades.csv (every trade, open ones too).
Run: python research/btc-dip/forward_test.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from backtest import run  # noqa: E402

FWD = HERE / "forward"
START = pd.Timestamp("2026-10-31T10:00:00Z")
LOOKBACK = 24
DIPS = [0.05, 0.07]
HOLDS = [4, 12, 24, 36, 48, 72]
COST = 0.001                      # 0.1% per side, as in the backtest


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def binance(hours: int) -> pd.DataFrame:
    k = _get(f"https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1h&limit={min(hours, 1000)}")
    df = pd.DataFrame([r[:6] for r in k], columns=["t", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def coinbase(hours: int) -> pd.DataFrame:
    rows, end = [], datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    while start < end:
        e = min(start + timedelta(hours=300), end)
        rows += _get("https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=3600"
                     f"&start={start:%Y-%m-%dT%H:%M:%SZ}&end={e:%Y-%m-%dT%H:%M:%SZ}")
        start = e
        time.sleep(0.2)
    df = pd.DataFrame(rows, columns=["t", "low", "high", "open", "close", "volume"])
    df.index = pd.to_datetime(df["t"], unit="s", utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def latest(hours: int) -> tuple[pd.DataFrame, str]:
    for name, fn in (("Binance BTCUSDT", binance), ("Coinbase BTC-USD", coinbase)):
        try:
            df = fn(hours)
            if len(df):
                return df, name
        except Exception as ex:
            print(f"  {name} unavailable: {ex}")
    raise SystemExit("no price source reachable")


def main():
    FWD.mkdir(exist_ok=True)
    path = FWD / "candles.csv"
    have = pd.DataFrame()
    if path.exists():
        have = pd.read_csv(path, index_col=0)
        have.index = pd.to_datetime(have.index, utc=True, format="mixed")
    need_from = START - pd.Timedelta(hours=LOOKBACK + 2)
    hours = int((pd.Timestamp.now(tz="UTC") - (have.index.max() if len(have) else need_from)) / pd.Timedelta(hours=1)) + 3
    new, src = latest(max(hours, 30))
    now = pd.Timestamp.now(tz="UTC").floor("h")
    new = new[new.index < now]                                   # closed candles only
    px = pd.concat([have, new])
    px = px[~px.index.duplicated(keep="last")].sort_index()
    px = px[px.index >= need_from]
    px.to_csv(path, index_label="time")

    all_trades, summary = [], {"start": START.isoformat(), "updated": now.isoformat(),
                               "last_candle": px.index.max().isoformat(), "source": src,
                               "btc_price": float(px.close.iloc[-1]),
                               "btc_since_start": float(px.close.iloc[-1] / px.loc[px.index >= START, "open"].iloc[0] - 1)
                               if (px.index >= START).any() else 0.0,
                               "variants": []}
    for dip in DIPS:
        for hold in HOLDS:
            t = run(px, dip=dip, lookback=LOOKBACK, hold=hold, cost=COST)
            if len(t):
                t = t[t.entry_time >= START].copy()
            if not len(t):
                summary["variants"].append(dict(dip=dip, hold=hold, trades=0, open=None, closed=0,
                                                total=0.0, win=None, avg=None))
                continue
            last_i = px.index.get_loc(px.index.max())
            t["status"] = "closed"
            # a time exit on the final candle before the hold ran out = still open
            is_open = (t.exit_time == px.index.max()) & (t.hours < hold) & (t.reason == "time")
            t.loc[is_open, "status"] = "open"
            t["variant"] = f"{int(dip * 100)}% dip / {hold}h"
            closed = t[t.status == "closed"]
            op = t[t.status == "open"]
            eq = float((1 + closed.ret).prod() - 1)
            summary["variants"].append(dict(
                dip=dip, hold=hold, trades=len(t), closed=len(closed),
                total=eq, total_2x=float((1 + 2 * (closed.ret + 2 * COST) - 2 * 0.0005 * 2).prod() - 1) if len(closed) else 0.0,
                total_3x=float((1 + 3 * (closed.ret + 2 * COST) - 3 * 0.0005 * 2).prod() - 1) if len(closed) else 0.0,
                win=float((closed.ret > 0).mean()) if len(closed) else None,
                avg=float(closed.ret.mean()) if len(closed) else None,
                open=None if op.empty else dict(entry_time=op.entry_time.iloc[0].isoformat(),
                                                 entry=float(op.entry.iloc[0]),
                                                 exit_due=(op.entry_time.iloc[0] + pd.Timedelta(hours=hold)).isoformat(),
                                                 unrealised=float(px.close.iloc[-1] / op.entry.iloc[0] - 1 - 2 * COST)),
            ))
            all_trades.append(t)
            del last_i
    (FWD / "summary.json").write_text(json.dumps(summary, indent=2))
    cols = ["variant", "status", "entry_time", "entry", "exit_time", "exit", "hours", "gross", "ret", "reason", "dip_from"]
    out = pd.concat(all_trades)[cols] if all_trades else pd.DataFrame(columns=cols)
    out.to_csv(FWD / "trades.csv", index=False)
    print(f"{src}: {px.index.max()}  BTC {px.close.iloc[-1]:,.0f}  trades so far: {len(out)}")


if __name__ == "__main__":
    main()
