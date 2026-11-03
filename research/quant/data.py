"""
Daily prices with a local cache, so 15 years of ~700 stocks aren't downloaded on every run.

Each ticker is one parquet file in research/quant/cache/ (not committed: GitHub Actions keeps
it between runs with actions/cache). A cached file is refreshed when it's more than a day old.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(__file__).parent / "cache"
COLS = ["open", "high", "low", "close", "volume"]


def _fresh(p: Path, max_age_h: float) -> bool:
    return p.exists() and (time.time() - p.stat().st_mtime) < max_age_h * 3600


def load(tickers: list[str], start: str = "2009-01-01", max_age_h: float = 20) -> dict[str, pd.DataFrame]:
    CACHE.mkdir(exist_ok=True)
    need = [t for t in tickers if not _fresh(CACHE / f"{t}.parquet", max_age_h)]
    if need:
        import yfinance as yf
        for i in range(0, len(need), 100):
            chunk = need[i:i + 100]
            raw = yf.download(chunk, start=start, interval="1d", group_by="ticker",
                              auto_adjust=True, progress=False, threads=True)
            for t in chunk:
                try:
                    df = raw[t].rename(columns=str.lower)[COLS].dropna(subset=["close"])
                except KeyError:
                    df = pd.DataFrame(columns=COLS)
                df.index = pd.DatetimeIndex(df.index).tz_localize(None)
                df.to_parquet(CACHE / f"{t}.parquet")   # empty files remember "no data" too
            print(f"  downloaded {min(i + 100, len(need))}/{len(need)}", flush=True)
    out = {}
    for t in tickers:
        df = pd.read_parquet(CACHE / f"{t}.parquet")
        df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
        if len(df) >= 60:
            out[t] = df
    return out


def demo(tickers: list[str], bars: int = 3800, seed: int = 7) -> dict[str, pd.DataFrame]:
    """Random-walk prices for testing the pipeline without a network."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end="2026-10-30", periods=bars)
    out = {}
    for k, t in enumerate(tickers):
        n = int(rng.integers(300, bars + 1)) if k % 5 == 0 else bars   # some start late
        r = rng.normal(0.0004, 0.018, n)
        c = 50 * np.exp(np.cumsum(r))
        hi = c * (1 + np.abs(rng.normal(0, 0.008, n)))
        lo = c * (1 - np.abs(rng.normal(0, 0.008, n)))
        op = np.clip(c * (1 + rng.normal(0, 0.004, n)), lo, hi)
        v = rng.lognormal(15, 0.5, n)
        out[t] = pd.DataFrame({"open": op, "high": hi, "low": lo, "close": c, "volume": v}, index=idx[-n:])
    return out
