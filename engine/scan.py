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
from chart import _r as rp, payload  # noqa: E402
from strategies import REGISTRY, StrategyResult, rsi  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ENGINE = Path(__file__).parent

# Each market has its own stock list and writes to its own data folder.
MARKETS = {
    "stocks": {"dir": ROOT / "docs" / "data", "tickers": "tickers.txt", "names": "names.json",
               "universe": "S&P 100", "bt_universe": "Today's S&P 100", "topic": ""},
    "crypto": {"dir": ROOT / "docs" / "data" / "crypto", "tickers": "crypto.txt", "names": "crypto_names.json",
               "universe": "Top crypto", "bt_universe": "Today's top cryptocurrencies", "topic": "crypto-"},
    "commodities": {"dir": ROOT / "docs" / "data" / "commodities", "tickers": "commodities.txt", "names": "commodities_names.json",
                    "universe": "Commodity futures", "bt_universe": "19 commodity futures (front month)", "topic": "commodities-"},
}
MARKET = "stocks"


def configure(market: str):
    """Point every output path at the chosen market's data folder."""
    global MARKET, DATA, OUT, CHART_DIR, STOCK_DIR, STOCKS_OUT
    MARKET = market
    DATA = MARKETS[market]["dir"]
    OUT = DATA / "alerts.json"
    CHART_DIR = DATA / "charts"     # one small file per alert, loaded when tapped
    STOCK_DIR = DATA / "stocks"     # one chart file per stock / coin
    STOCKS_OUT = DATA / "stocks.json"

CHART_BARS = 120          # bars of history drawn with each alert
STOCK_BARS = 260          # about one year on the stock page
KEEP_DAYS = 45            # alerts older than this drop out of the feed
LOOKBACK_BARS = 32        # every run rescans the last ~45 days so follow-up returns stay current
CONFLUENCE_BARS = 3       # "other strategies agree" window: alert day and the 3 sessions before


def load_tickers() -> list[str]:
    lines = (ENGINE / MARKETS[MARKET]["tickers"]).read_text().splitlines()
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
        if MARKET == "crypto":
            # crypto trades around the clock: drop today's unfinished daily candle
            today = pd.Timestamp.now(tz="UTC").normalize().tz_localize(None)
            df = df[df.index.tz_localize(None) < today] if df.index.tz is not None else df[df.index < today]
        if MARKET == "commodities":
            # front-month futures: drop bad prints (WTI famously settled below zero in April 2020)
            df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
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


def strength(df, sigs: dict, sid: str, i: int, side: int, edge: dict | None) -> dict:
    """Score 0-4: how much backs this alert up. Built only from information known on the alert day."""
    checks, score = [], 0
    c = df["close"]
    m200 = c.rolling(200).mean().iloc[i]
    if np.isfinite(m200):
        ok = (c.iloc[i] > m200) if side > 0 else (c.iloc[i] < m200)
        score += ok
        where = "above" if c.iloc[i] > m200 else "below"
        checks.append({"ok": bool(ok), "text": f"Price is {where} its 200-day average"
                       + (" (with the long-term trend)" if ok else " (against the long-term trend)")})
    same, opposite = [], []
    for other, s in sigs.items():
        if other == sid:
            continue
        w = s[max(0, i - CONFLUENCE_BARS):i + 1]
        if (w == side).any():
            same.append(other)
        if (w == -side).any():
            opposite.append(other)
    if same:
        score += min(2, len(same))
        checks.append({"ok": True, "text": f"{len(same)} other strateg{'y' if len(same) == 1 else 'ies'} agree "
                       f"in the last {CONFLUENCE_BARS + 1} sessions", "ids": same})
    else:
        checks.append({"ok": False, "text": "No other strategy agrees right now"})
    if opposite:
        score -= 1
        checks.append({"ok": False, "text": f"{len(opposite)} strateg{'y points' if len(opposite) == 1 else 'ies point'} the other way", "ids": opposite})
    if edge is not None:
        ok = edge.get(sid, {}).get("bullish" if side > 0 else "bearish", False)
        score += ok
        checks.append({"ok": bool(ok), "text": "This strategy beat a random day in the 15-year backtest" if ok
                       else "This strategy didn't beat a random day in the 15-year backtest"})
    score = max(0, min(4, score))
    label = "Strong" if score >= 3 else "Moderate" if score == 2 else "Weak"
    return {"score": int(score), "label": label, "checks": checks}


def load_edge() -> dict | None:
    """From the backtest: did each strategy's alerts beat a random day after 20 sessions?"""
    f = DATA / "backtest.json"
    if not f.exists():
        return None
    bt = json.loads(f.read_text())
    out = {}
    for sid, r in bt["strategies"].items():
        out[sid] = {}
        for side in ("bullish", "bearish"):
            x = r[side]["20"]
            out[sid][side] = (x["avg"] is not None and x["hit"] is not None and
                              x["avg"] > x["base_avg"] and x["hit"] > x["base_hit"])
    return out


def scan(data: dict[str, pd.DataFrame], lookback_bars: int, edge: dict | None) -> list[dict]:
    alerts = []
    for ticker, df in data.items():
        n = len(df)
        results = {}
        for sid, fn in REGISTRY.items():
            try:
                results[sid] = fn(df)
            except Exception as e:  # one bad series must not stop the scan
                print(f"  ! {sid} failed on {ticker}: {e}")
        sigs = {sid: r.signals.values for sid, r in results.items()}
        for sid, res in results.items():
            for i in np.flatnonzero(res.signals.values[-lookback_bars:]):
                i = int(i) + n - lookback_bars
                side = int(res.signals.iloc[i])
                start = max(0, i - CHART_BARS + 10)
                price = float(df["close"].iloc[i])
                alerts.append({
                    "id": f"{sid}:{ticker}:{df.index[i]:%Y-%m-%d}",
                    "strategy": sid,
                    "ticker": ticker,
                    "date": f"{df.index[i]:%Y-%m-%d}",
                    "side": "bullish" if side > 0 else "bearish",
                    "price": rp(price),
                    "note": res.notes.get(i, ""),
                    "since": round((float(df["close"].iloc[-1]) / price - 1) * 100, 2),
                    "since_days": n - 1 - i,
                    "strength": strength(df, sigs, sid, i, side, edge),
                    "chart": payload(df, res, start, n, only_marker=i),
                })
    return alerts


def backdrop(data: dict[str, pd.DataFrame]) -> dict:
    """Real prices for the page background: last year of lines plus ~5 months of daily candles."""
    def series(v):
        return [rp(float(x)) for x in v]
    if MARKET == "crypto":
        bars, lines = 365, []
        for t, name in (("BTC-USD", "Bitcoin"), ("ETH-USD", "Ethereum"), ("SOL-USD", "Solana")):
            if t in data:
                lines.append({"name": name, "values": series(data[t]["close"].iloc[-bars:])})
        src = data.get("BTC-USD")
        if src is None:
            return {}
        cdl = src.iloc[-150:]
        return {"label": "Bitcoin, Ethereum and Solana, last 12 months · Bitcoin daily candles",
                "lines": lines, "candles": {k: series(cdl[k]) for k in ("open", "high", "low", "close")}}
    if MARKET == "commodities":
        bars, lines = 260, []
        for t, name in (("GC=F", "Gold"), ("CL=F", "Crude oil"), ("ZW=F", "Wheat")):
            if t in data:
                lines.append({"name": name, "values": series(data[t]["close"].iloc[-bars:])})
        src = data.get("GC=F")
        if src is None:
            return {}
        cdl = src.iloc[-150:]
        return {"label": "Gold, crude oil and wheat, last 12 months · gold daily candles",
                "lines": lines, "candles": {k: series(cdl[k]) for k in ("open", "high", "low", "close")}}
    # stocks: equal-weight average of every stock, each rebased to 1 a year ago
    bars = 260
    frames = []
    for t, df in data.items():
        if len(df) < bars + 200:
            continue
        base = float(df["close"].iloc[-bars - 200])
        frames.append(df[["open", "high", "low", "close"]].iloc[-(bars + 200):] / base * 100)
    if not frames:
        return {}
    idx = sum(f.reset_index(drop=True) for f in frames) / len(frames)
    c = idx["close"]
    m50, m200 = c.rolling(50).mean(), c.rolling(200).mean()
    cdl = idx.iloc[-150:]
    return {"label": f"S&P 100 average of {len(frames)} stocks, last 12 months · with its 50- and 200-day averages",
            "lines": [{"name": "S&P 100 average", "values": series(c.iloc[-bars:])},
                      {"name": "50-day average", "values": series(m50.iloc[-bars:])},
                      {"name": "200-day average", "values": series(m200.iloc[-bars:])}],
            "candles": {k: series(cdl[k]) for k in ("open", "high", "low", "close")}}


def stock_files(data: dict[str, pd.DataFrame], alerts: list[dict], names: dict) -> list[dict]:
    """One detail file per stock plus a summary row for the stock list."""
    STOCK_DIR.mkdir(parents=True, exist_ok=True)
    by_t = {}
    for a in alerts:
        by_t.setdefault(a["ticker"], []).append(a)
    rows = []
    for t, df in data.items():
        c = df["close"]
        m50, m200 = c.rolling(50).mean(), c.rolling(200).mean()
        r = rsi(c).iloc[-1]
        last, prev = float(c.iloc[-1]), float(c.iloc[-2])
        MONTH, YEAR = (30, 365) if MARKET == "crypto" else (21, 252)
        yr = df.iloc[-YEAR:]
        hi52, lo52 = float(yr["high"].max()), float(yr["low"].min())
        start = max(0, len(df) - (YEAR + 8 if MARKET == "crypto" else STOCK_BARS))
        res = StrategyResult(pd.Series(0, index=df.index), {}, [
            {"name": "SMA 50", "values": [None if pd.isna(v) else float(v) for v in m50], "style": "accent2"},
            {"name": "SMA 200", "values": [None if pd.isna(v) else float(v) for v in m200], "style": "accent"},
        ])
        chart = payload(df, res, start)
        mine = by_t.get(t, [])
        meta = names.get(t, [t, ""])
        row = {
            "ticker": t, "name": meta[0], "sector": meta[1],
            "price": rp(last),
            "chg1d": round((last / prev - 1) * 100, 2),
            "chg1m": round((last / float(c.iloc[-MONTH - 1]) - 1) * 100, 2),
            "chg1y": round((last / float(c.iloc[-YEAR - 1]) - 1) * 100, 2) if len(c) > YEAR + 1 else None,
            "vs200": round((last / float(m200.iloc[-1]) - 1) * 100, 2) if np.isfinite(m200.iloc[-1]) else None,
            "rsi": round(float(r), 1) if np.isfinite(r) else None,
            "hi52": rp(hi52), "lo52": rp(lo52),
            "pos52": round((last - lo52) / (hi52 - lo52) * 100, 1) if hi52 > lo52 else None,
            "spark": [rp(float(v)) for v in c.iloc[-60:]],
            "bull": sum(a["side"] == "bullish" for a in mine),
            "bear": sum(a["side"] == "bearish" for a in mine),
            "latest": mine[0]["date"] if mine else None,
        }
        if len(meta) >= 5:   # commodities: contract unit, exchange and what moves the price
            row.update(unit=meta[2], exch=meta[3], drivers=meta[4])
        rows.append(row)
        (STOCK_DIR / f"{t}.json").write_text(json.dumps(chart, separators=(",", ":")))
    for f in STOCK_DIR.glob("*.json"):
        if f.stem not in data:
            f.unlink()
    return sorted(rows, key=lambda r: r["ticker"])


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
    ap.add_argument("--market", choices=list(MARKETS), default="stocks")
    args = ap.parse_args()
    configure(args.market)

    previous = json.loads(OUT.read_text()) if OUT.exists() else None
    if previous and previous.get("demo") != args.demo:
        previous = None  # never mix demo and real alerts

    tickers = load_tickers()
    data = fetch_demo() if args.demo else fetch_real(tickers)
    print(f"Loaded {len(data)} tickers")
    if not data:
        sys.exit("No price data downloaded; leaving the previous alerts untouched.")

    found = scan(data, LOOKBACK_BARS, load_edge())
    old = {a["id"] for a in (previous or {}).get("alerts", [])}
    new = [a for a in found if a["id"] not in old]
    merged = {a["id"]: a for a in found}

    # remember when each alert was first spotted, so the app can show the exact time
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    seen = {a["id"]: a.get("found_at") for a in (previous or {}).get("alerts", [])}
    for a in merged.values():
        a["found_at"] = seen.get(a["id"]) or now

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

    names = json.loads((ENGINE / MARKETS[MARKET]["names"]).read_text())
    stocks = stock_files(data, alerts, names)
    above = [r for r in stocks if r["vs200"] is not None]
    STOCKS_OUT.write_text(json.dumps({
        "as_of": f"{as_of:%Y-%m-%d}",
        "breadth200": round(sum(r["vs200"] > 0 for r in above) / len(above) * 100, 1) if above else None,
        "advancers": sum(r["chg1d"] > 0 for r in stocks),
        "decliners": sum(r["chg1d"] < 0 for r in stocks),
        "backdrop": backdrop(data),
        "stocks": stocks,
    }, separators=(",", ":")))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": now,
        "as_of": f"{as_of:%Y-%m-%d}",
        "demo": args.demo,
        "market": MARKET,
        "universe": "Demo tickers (synthetic prices)" if args.demo else MARKETS[MARKET]["universe"],
        "tickers": len(data),
        "ntfy_prefix": os.environ.get("NTFY_PREFIX", "").strip() or None,
        "alerts": alerts,
    }, separators=(",", ":")))
    print(f"{len(new)} new alerts, {len(alerts)} in feed -> {OUT.relative_to(ROOT)}")

    prefix = os.environ.get("NTFY_PREFIX", "").strip()
    if prefix and previous is not None and not args.demo:
        strat = json.loads((ROOT / "docs" / "data" / "strategies.json").read_text())
        notify(new, prefix + "-" + MARKETS[MARKET]["topic"].rstrip("-") if MARKETS[MARKET]["topic"] else prefix,
               {s["id"]: s["name"] for s in strat["strategies"]})


configure("stocks")

if __name__ == "__main__":
    main()
