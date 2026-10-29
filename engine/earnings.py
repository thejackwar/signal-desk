"""
Earnings reactions: how each S&P 100 stock moved after its past earnings reports.

For every report since 2010 it records the analysts' EPS estimate, the reported EPS,
the surprise, and how the stock moved:
  m    earnings day: close before the report -> close of the first session that trades on it
  g    the opening gap on that session
  d1, d5, d20   from the earnings-day close onwards (what you'd get buying AFTER the news)
  mx, d20x      the same minus SPY over the same days (did it beat the market?)

Writes docs/data/earnings/<TICKER>.json (one file per stock, loaded when the page asks)
and docs/data/earnings/index.json (which stocks have data + S&P 100 totals per year,
so the page can compare one stock with the whole index over any range of years).

Data: Yahoo Finance via yfinance. Run: python engine/earnings.py
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ENGINE = Path(__file__).resolve().parent
OUT = ENGINE.parent / "docs" / "data" / "earnings"
START = "2009-06-01"           # first reports kept are from 2010
FIRST_YEAR = 2010
METRICS = ["m", "mx", "d1", "d5", "d20", "d20x"]


def log(*a):
    print(*a, flush=True)


def load_tickers() -> list[str]:
    lines = (ENGINE / "tickers.txt").read_text().splitlines()
    return [x.strip() for x in lines if x.strip() and not x.startswith("#")]


def load_names() -> dict:
    p = ENGINE / "names.json"
    return json.loads(p.read_text()) if p.exists() else {}


PAUSE = 2.0          # seconds between stocks: Yahoo throttles fast bursts of these lookups
BACKOFF = [15, 45, 90]


def get_reports(t: str) -> pd.DataFrame:
    for wait in BACKOFF + [None]:
        try:
            df = yf.Ticker(t).get_earnings_dates(limit=100)
            if df is not None and len(df):
                break
            log("  empty answer", t)
        except Exception as ex:  # noqa: BLE001
            log("  failed", t, str(ex)[:120])
        if wait is None:
            return pd.DataFrame()
        log(f"  waiting {wait}s")
        time.sleep(wait)
    df = df.rename(columns={"EPS Estimate": "est", "Reported EPS": "rep"})
    df = df.dropna(subset=["est", "rep"]).sort_index()
    idx = df.index.tz_convert("America/New_York") if df.index.tz is not None else df.index
    df["hour"] = idx.hour
    df["day"] = (idx.tz_localize(None) if idx.tz is not None else idx).normalize()
    return df.reset_index(drop=True)


def reaction_index(px: pd.DataFrame, day: pd.Timestamp, hour: int) -> tuple[int | None, str]:
    """Index of the first session that trades on the news, and the report time."""
    dates = px.index
    i = dates.searchsorted(day)
    if i >= len(dates) or i == 0:
        return None, ""
    same_day = dates[i] == day
    if 0 < hour < 12:                                  # before the open
        return i, "bmo"
    if hour >= 16:                                     # after the close
        return (i + 1 if same_day else i), "amc"
    if not same_day or i + 1 >= len(dates):           # time unknown: the bigger gap wins
        return i, ""
    g0 = abs(px["open"].iat[i] / px["close"].iat[i - 1] - 1)
    g1 = abs(px["open"].iat[i + 1] / px["close"].iat[i] - 1)
    return (i, "") if g0 >= g1 else (i + 1, "")


def pct(x) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, 2)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tickers, names = load_tickers(), load_names()
    raw = yf.download(tickers + ["SPY"], start=START, interval="1d", group_by="ticker",
                      auto_adjust=True, progress=False, threads=True)
    prices = {}
    for t in tickers + ["SPY"]:
        try:
            df = raw[t].rename(columns=str.lower)[["open", "close"]].dropna()
        except KeyError:
            continue
        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        if len(df) > 60:
            prices[t] = df
    spy = prices.pop("SPY")["close"]
    log("prices:", len(prices))

    fresh, failed = 0, []
    for k, t in enumerate(tickers):
        if k:
            time.sleep(PAUSE)
        px = prices.get(t)
        if px is None:
            continue
        reps = get_reports(t)
        events = []
        for _, e in reps.iterrows():
            ri, when = reaction_index(px, e["day"], int(e["hour"]))
            if ri is None or ri < 1 or ri >= len(px) or px.index[ri].year < FIRST_YEAR:   # not traded on yet: skip
                continue
            c = px["close"].values
            pre, rc, ro = c[ri - 1], c[ri], px["open"].iat[ri]
            d0, dr = px.index[ri - 1], px.index[ri]

            def spy_ret(a, b):
                return spy.asof(b) / spy.asof(a) - 1

            def fwd(n):
                return c[ri + n] / rc - 1 if ri + n < len(c) else None

            def fwd_x(n):
                f = fwd(n)
                return None if f is None else f - spy_ret(dr, px.index[ri + n])

            est, rep = float(e["est"]), float(e["rep"])
            ev = {"d": dr.strftime("%Y-%m-%d"), "t": when, "e": round(est, 4), "r": round(rep, 4),
                  "s": round((rep - est) / abs(est) * 100, 1) if est else None,
                  "g": pct(ro / pre - 1), "m": pct(rc / pre - 1), "mx": pct(rc / pre - 1 - spy_ret(d0, dr)),
                  "d1": pct(fwd(1)), "d5": pct(fwd(5)), "d20": pct(fwd(20)), "d20x": pct(fwd_x(20))}
            events.append(ev)
        events.sort(key=lambda x: x["d"], reverse=True)
        if not events:
            failed.append(t)            # its previous file (if any) is kept
            log(f"{k + 1}/{len(tickers)} {t}: no reports, keeping the last file")
            continue
        (OUT / f"{t}.json").write_text(json.dumps({"ticker": t, "name": names.get(t, t), "events": events},
                                                  separators=(",", ":")))
        fresh += 1
        log(f"{k + 1}/{len(tickers)} {t}: {len(events)} reports")
    log(f"updated {fresh}, failed {len(failed)}: {' '.join(failed)}")
    print(f"::notice title=Earnings reactions::updated {fresh} stocks, failed {len(failed)}: {' '.join(failed)}", flush=True)

    # the index covers every stock with a file (this run's or an earlier one's)
    index, totals = [], {}          # totals[year][beat|miss|all][metric] = [n, ups, sum]
    for t in tickers:
        f = OUT / f"{t}.json"
        if not f.exists():
            continue
        events = json.loads(f.read_text())["events"]
        index.append({"t": t, "n": len(events), "from": events[-1]["d"][:4], "to": events[0]["d"][:4]})
        for ev in events:
            kind = "beat" if ev["r"] > ev["e"] else "miss" if ev["r"] < ev["e"] else "inline"
            y = totals.setdefault(ev["d"][:4], {})
            for grp in ("all", kind):
                g = y.setdefault(grp, {})
                for mtr in METRICS:
                    v = ev[mtr]
                    if v is None:
                        continue
                    a = g.setdefault(mtr, [0, 0, 0.0])
                    a[0] += 1; a[1] += v > 0; a[2] = round(a[2] + v, 2)
    if not index:
        raise SystemExit("no earnings data at all")
    (OUT / "index.json").write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Yahoo Finance (EPS estimates and reported EPS, daily prices)",
        "stocks": index, "totals": dict(sorted(totals.items()))}, separators=(",", ":")))
    log("index:", len(index), "stocks")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except BaseException as ex:  # noqa: BLE001 - shown as a GitHub annotation, then re-raised
        tb = traceback.format_exc().strip().replace("\n", "%0A")
        print(f"::error title=Earnings reactions failed::{tb}", flush=True)
        raise
