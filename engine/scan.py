"""
Daily scanner: runs every strategy on every ticker and writes
docs/data/alerts.json for the web app.

  python engine/scan.py            # real data from Yahoo Finance (used by GitHub Actions)
  python engine/scan.py --demo     # synthetic prices, for testing without internet

Optional phone notifications: set the environment variable NTFY_PREFIX
(e.g. "giacomo-signals-7f3k"). Each new alert is then also sent to the
ntfy topic  <NTFY_PREFIX>-<strategy_id>, so you can subscribe in the free
ntfy iPhone app to just the strategies you follow.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from chart import payload  # noqa: E402
from strategies import REGISTRY  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "alerts.json"
CHART_DIR = ROOT / "docs" / "data" / "charts"   # one small file per alert, loaded when tapped
CHART_BARS = 120          # bars of history drawn with each alert
KEEP_DAYS = 45            # alerts older than this drop out of the feed
BACKFILL_BARS = 15        # first run: also collect alerts from the last N sessions


def load_tickers() -> list[str]:
    lines = (Path(__file__).parent / "tickers.txt").read_text().splitlines()
    return [x.strip() for x in lines if x.strip() and not x.startswith("#")]


def fetch_real(tickers: list[str], period: str = "2y") -> dict[str, pd.DataFrame]:
    import yfinance as yf
    raw = yf.download(tickers, period=period, interval="1d", group_by="ticker",
                      auto_adjust=True, progress=False, threads=True)
    data = {}
    for t in tickers:
        try:
            df = raw[t].rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
        except KeyError:
            continue
        if len(df) >= 220:
            data[t] = df
    return data


def fetch_demo(n_tickers: int = 30, bars: int = 500, seed: int = 7) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    names = ["ALFA", "BRVO", "CHRL", "DLTA", "ECHO", "FXTR", "GOLF", "HTEL", "INDG", "JLTT",
             "KILO", "LIMA", "MIKE", "NOVA", "OSCR", "PAPA", "QBEC", "ROMO", "SRRA", "TNGO",
             "UNFM", "VCTR", "WHSK", "XRAY", "YNKE", "ZULU", "ARCO", "BETA", "CORA", "DUNE"]
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=bars)
    data = {}
    for name in names[:n_tickers]:
        drift = rng.normal(0.0003, 0.0006)
        vol = rng.uniform(0.012, 0.025)
        r = rng.normal(drift, vol, bars)
        # add slow regime swings so trends and reversals happen
        r += 0.004 * np.sin(np.arange(bars) / rng.uniform(25, 60) + rng.uniform(0, 6))
        close = 50 * np.exp(np.cumsum(r)) * rng.uniform(0.5, 4)
        openp = close * (1 + rng.normal(0, vol / 3, bars))
        high = np.maximum(openp, close) * (1 + np.abs(rng.normal(0, vol / 2, bars)))
        low = np.minimum(openp, close) * (1 - np.abs(rng.normal(0, vol / 2, bars)))
        data[name] = pd.DataFrame({"open": openp, "high": high, "low": low, "close": close,
                                   "volume": rng.integers(1e6, 9e6, bars)}, index=idx)
    return data


def scan(data: dict[str, pd.DataFrame], lookback_bars: int) -> list[dict]:
    alerts = []
    for ticker, df in data.items():
        n = len(df)
        for sid, fn in REGISTRY.items():
            try:
                res = fn(df)
            except Exception as e:  # one bad series must not stop the scan
                print(f"  ! {sid} failed on {ticker}: {e}")
                continue
            for i in np.flatnonzero(res.signals.values[-lookback_bars:]):
                i = int(i) + n - lookback_bars
                start = max(0, i - CHART_BARS + 10)
                end = min(n, i + 10)
                alerts.append({
                    "id": f"{sid}:{ticker}:{df.index[i]:%Y-%m-%d}",
                    "strategy": sid,
                    "ticker": ticker,
                    "date": f"{df.index[i]:%Y-%m-%d}",
                    "side": "bullish" if res.signals.iloc[i] > 0 else "bearish",
                    "price": round(float(df["close"].iloc[i]), 2),
                    "note": res.notes.get(i, ""),
                    "chart": payload(df, res, start, end, only_marker=i),
                })
    return alerts


def notify(new_alerts: list[dict], prefix: str, names: dict):
    for a in new_alerts:
        arrow = "▲" if a["side"] == "bullish" else "▼"
        msg = f"{arrow} {a['ticker']} {a['price']}: {a['note']}".encode()
        req = urllib.request.Request(f"https://ntfy.sh/{prefix}-{a['strategy']}", data=msg, method="POST",
                                     headers={"Title": f"{names.get(a['strategy'], a['strategy'])}: {a['ticker']}",
                                              "Tags": "chart_with_upwards_trend" if a["side"] == "bullish"
                                              else "chart_with_downwards_trend"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print(f"  ! notification failed: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="use synthetic prices")
    args = ap.parse_args()

    previous = json.loads(OUT.read_text()) if OUT.exists() else None
    if previous and previous.get("demo") != args.demo:
        previous = None  # never mix demo and real alerts
    lookback = 1 if previous else BACKFILL_BARS

    tickers = load_tickers()
    data = fetch_demo() if args.demo else fetch_real(tickers)
    print(f"Loaded {len(data)} tickers")
    if not data:
        sys.exit("No price data downloaded; leaving the previous alerts untouched.")

    found = scan(data, lookback)
    old = {a["id"]: a for a in (previous or {}).get("alerts", [])}
    new = [a for a in found if a["id"] not in old]
    merged = {**old, **{a["id"]: a for a in found}}

    as_of = max(df.index[-1] for df in data.values())
    cutoff = (as_of - pd.Timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    alerts = sorted((a for a in merged.values() if a["date"] >= cutoff),
                    key=lambda a: (a["date"], a["ticker"]), reverse=True)

    # charts live in their own files; the feed only keeps the file name
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    keep = set()
    for a in alerts:
        a["file"] = a["id"].replace(":", "_") + ".json"
        keep.add(a["file"])
        if "chart" in a:
            (CHART_DIR / a["file"]).write_text(json.dumps(a.pop("chart"), separators=(",", ":")))
    for f in CHART_DIR.glob("*.json"):
        if f.name not in keep:
            f.unlink()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "as_of": f"{as_of:%Y-%m-%d}",
        "demo": args.demo,
        "universe": "Demo tickers (synthetic prices)" if args.demo else "S&P 100",
        "tickers": len(data),
        "ntfy_prefix": os.environ.get("NTFY_PREFIX", "").strip() or None,
        "alerts": alerts,
    }, separators=(",", ":")))
    print(f"{len(new)} new alerts, {len(alerts)} in feed -> {OUT.relative_to(ROOT)}")

    prefix = os.environ.get("NTFY_PREFIX", "").strip()
    if prefix and previous is not None and not args.demo:
        strat = json.loads((ROOT / "docs" / "data" / "strategies.json").read_text())
        notify(new, prefix, {s["id"]: s["name"] for s in strat["strategies"]})


if __name__ == "__main__":
    main()
