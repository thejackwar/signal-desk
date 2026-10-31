"""Download the full hourly BTC price history (and perpetual funding rates) for the dip study.

Sources, tried in order:
  1. Binance public data archive (data.binance.vision): BTCUSDT spot 1h klines, Aug 2017 -> today
  2. Coinbase Exchange BTC-USD 1h candles (fallback for anything the archive is missing)
Funding: Binance BTCUSDT perpetual funding rate history (cost of holding a leveraged long).

Writes research/btc-dip/data/btc_1h.csv and research/btc-dip/data/funding.csv
Run: python research/btc-dip/fetch_data.py
"""
from __future__ import annotations

import io
import json
import time
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

OUT = Path(__file__).parent / "data"
UA = {"User-Agent": "Mozilla/5.0"}


def get(url: str, raw: bool = False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        b = r.read()
    return b if raw else json.loads(b)


def months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def read_zip_csv(b: bytes, cols: list[str]) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        with z.open(z.namelist()[0]) as f:
            df = pd.read_csv(f, header=None)
    if not str(df.iloc[0, 0]).lstrip("-").isdigit():      # some files carry a header row
        df = df.iloc[1:]
    df = df.iloc[:, : len(cols)]
    df.columns = cols
    return df


def binance_archive() -> pd.DataFrame:
    base = "https://data.binance.vision/data/spot"
    cols = ["t", "open", "high", "low", "close", "volume"]
    parts, today = [], datetime.now(timezone.utc).date()
    for y, m in months(date(2017, 8, 1), today):
        url = f"{base}/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-{y}-{m:02d}.zip"
        try:
            parts.append(read_zip_csv(get(url, raw=True), cols))
        except Exception:
            if (y, m) != (today.year, today.month) and (y, m) != ((today.replace(day=1) - timedelta(days=1)).year,
                                                                  (today.replace(day=1) - timedelta(days=1)).month):
                print(f"  archive month {y}-{m:02d} missing")
    # the current (and possibly previous) month: daily files
    d = today.replace(day=1) - timedelta(days=1)
    d = d.replace(day=1)
    while d < today:
        url = f"{base}/daily/klines/BTCUSDT/1h/BTCUSDT-1h-{d:%Y-%m-%d}.zip"
        try:
            parts.append(read_zip_csv(get(url, raw=True), cols))
        except Exception:
            pass
        d += timedelta(days=1)
    df = pd.concat(parts)
    t = df["t"].astype("int64")
    t = t.where(t < 10**14, t // 1000)                  # 2025+ files are in microseconds
    df.index = pd.to_datetime(t, unit="ms", utc=True)
    return df[cols[1:]].astype(float)


def coinbase(start: datetime, end: datetime) -> pd.DataFrame:
    rows, s = [], start
    while s < end:
        e = min(s + timedelta(hours=300), end)
        url = ("https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=3600"
               f"&start={s:%Y-%m-%dT%H:%M:%SZ}&end={e:%Y-%m-%dT%H:%M:%SZ}")
        for attempt in range(4):
            try:
                rows += get(url)
                break
            except Exception as ex:
                time.sleep(1 + attempt * 2)
                if attempt == 3:
                    print(f"  coinbase {s} failed: {ex}")
        s = e
        time.sleep(0.15)
    df = pd.DataFrame(rows, columns=["t", "low", "high", "open", "close", "volume"])
    df.index = pd.to_datetime(df["t"], unit="s", utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def funding() -> pd.DataFrame:
    base = "https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT"
    parts = []
    for y, m in months(date(2019, 9, 1), datetime.now(timezone.utc).date()):
        try:
            df = read_zip_csv(get(f"{base}/BTCUSDT-fundingRate-{y}-{m:02d}.zip", raw=True),
                              ["t", "interval", "rate"])
            parts.append(df)
        except Exception:
            pass
    df = pd.concat(parts)
    df.index = pd.to_datetime(df["t"].astype("int64"), unit="ms", utc=True)
    out = df[["rate"]].astype(float)
    # recent funding from the live API (the archive lags a month)
    try:
        start = int((out.index.max() + timedelta(hours=1)).timestamp() * 1000)
        k = get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&startTime={start}&limit=1000")
        if k:
            r = pd.DataFrame(k)
            r.index = pd.to_datetime(r["fundingTime"].astype("int64"), unit="ms", utc=True)
            out = pd.concat([out, r[["fundingRate"]].astype(float).rename(columns={"fundingRate": "rate"})])
    except Exception as ex:
        print(f"  live funding unavailable ({ex})")
    return out[~out.index.duplicated()].sort_index()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    src = []
    try:
        px = binance_archive()
        src.append(f"Binance BTCUSDT spot archive ({len(px)} bars)")
    except Exception as ex:
        print(f"Binance archive failed: {ex}")
        px = pd.DataFrame()
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    last = px.index.max().to_pydatetime() if len(px) else datetime(2016, 1, 1, tzinfo=timezone.utc)
    if now - last > timedelta(hours=2):
        cb = coinbase(last + timedelta(hours=1), now)
        src.append(f"Coinbase BTC-USD to fill {last:%F %H}h -> now ({len(cb)} bars)")
        px = pd.concat([px, cb])
    px = px[~px.index.duplicated(keep="first")].sort_index()
    px = px[px.index < now]                                 # only closed candles
    px.to_csv(OUT / "btc_1h.csv", index_label="time")
    print("prices:", px.index.min(), "->", px.index.max(), len(px), "bars;", "; ".join(src))
    try:
        f = funding()
        f.to_csv(OUT / "funding.csv", index_label="time")
        print("funding:", f.index.min(), "->", f.index.max(), len(f))
    except Exception as ex:
        print(f"funding failed: {ex}")


if __name__ == "__main__":
    main()
