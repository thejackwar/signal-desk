"""
Top-Down Analysis alerts: the trendlines of the Trendlines page, checked every 30 minutes.

For every S&P 100 stock (and SPY) it builds the same seven timeframes as the Trendlines page
(W, D, 4H, 1H, 30m, 15m, 5m; Alpaca data, regular hours, 15+ minutes delayed) and draws the
same lines with the same rules. When the price crosses the newest rising line (bearish) or the
newest falling line (bullish), it writes an alert to docs/data/topdown/alerts.json and, if
NTFY_PREFIX is set, sends a phone notification on the topic <NTFY_PREFIX>-topdown-<tf>
(e.g. myname-topdown-1h), so people can subscribe to just the timeframes they want.

Each line alerts once. The app shows only the timeframes the viewer picked (W, D, 4H and 1H
by default).

Crypto (--market crypto): the same lines on the coins of engine/crypto.txt, from OKX spot candles
(USDT pairs, UTC, only finished candles), every 30 minutes around the clock, weekends included.
Alerts go to docs/data/crypto/topdown.json and the phone topics <NTFY_PREFIX>-crypto-topdown-<tf>.

Run:  ALPACA_KEY_ID=... ALPACA_SECRET_KEY=... python engine/topdown.py
      python engine/topdown.py --market crypto
      python engine/topdown.py --demo --force      (simulated prices, no keys needed)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "data" / "topdown" / "alerts.json"
ET = ZoneInfo("America/New_York")
ALPACA = "https://data.alpaca.markets/v2/stocks/bars"
SWING, BREAK_BY = 5, "close"
KEEP_DAYS = 21                                       # how long alerts stay in the feed

# timeframe -> (label, lookback bars, bar length) -- the same table as docs/trendlines.js
TFS = {
    "W":   ("Weekly", 300, dt.timedelta(days=7)),
    "D":   ("Daily", 250, dt.timedelta(days=1)),
    "4H":  ("4-hour", 400, dt.timedelta(hours=4)),
    "1H":  ("1-hour", 500, dt.timedelta(hours=1)),
    "30m": ("30-min", 600, dt.timedelta(minutes=30)),
    "15m": ("15-min", 1500, dt.timedelta(minutes=15)),
    "5m":  ("5-min", 1500, dt.timedelta(minutes=5)),
}
HIGHER = ["W", "D", "4H", "1H"]                      # used for the strength checks


# ===================================================================== the rules
# Copied from tests/trendlines/trendlines_ref.py (thejackwar/sp500-bot trendlines.py), which is
# also what docs/trendlines.js ports. tests/topdown/test_rules.py checks they still agree.
def find_pivots(h, l, n, kind):
    v = h if kind == "high" else l
    out = []
    for i in range(n, len(v) - n):
        x = v[i]
        left, right = v[i - n:i], v[i + 1:i + n + 1]
        ok = (left <= x).all() and (right < x).all() if kind == "high" else \
             (left >= x).all() and (right > x).all()
        if ok:
            out.append((i, x))
    if len(v) > n:
        tail = np.arange(max(0, len(v) - n), len(v))
        best = tail[0]
        for i in tail:
            if (v[i] >= v[best]) if kind == "high" else (v[i] <= v[best]):
                best = i
        if best >= n:
            left = v[best - n:best]
            if ((left < v[best]).all() if kind == "high" else (left > v[best]).all()):
                out.append((int(best), v[best]))
    return out


def build_chain(df, pivots, is_up, lookback, break_by=BREAK_BY):
    h, l, c = df["High"].values, df["Low"].values, df["Close"].values
    last = len(df) - 1
    s0 = max(0, len(df) - lookback)
    a = s0 + int(np.argmin(l[s0:]) if is_up else np.argmax(h[s0:]))
    cur = (a, l[a] if is_up else h[a])
    verts = [cur]
    while True:
        best, best_s = None, None
        for i, p in pivots:
            if i <= cur[0]:
                continue
            s = (p - cur[1]) / (i - cur[0])
            if best is None or (s <= best_s if is_up else s >= best_s):
                best, best_s = (i, p), s
        if best is None:
            break
        verts.append(best)
        cur = best
    res = {"vertices": verts, "broken": None, "level": None, "slope": None}
    if len(verts) < 2:
        return res
    (ia, pa), (ib, pb) = verts[-2], verts[-1]
    slope = (pb - pa) / (ib - ia)
    res["slope"] = slope
    for i in range(ib + 1, last + 1):
        v = pb + slope * (i - ib)
        px = c[i] if break_by == "close" else (l[i] if is_up else h[i])
        if (px < v) if is_up else (px > v):
            res["broken"] = (i, v)
            return res
    res["level"] = pb + slope * (last - ib)
    return res


def analyze(df, lookback):
    h, l = df["High"].values, df["Low"].values
    up = build_chain(df, find_pivots(h, l, SWING, "low"), True, lookback)
    dn = build_chain(df, find_pivots(h, l, SWING, "high"), False, lookback)
    u, d = up["level"] is not None, dn["level"] is not None
    bias = "Up" if u and not d else "Down" if d and not u else "Squeeze" if u and d else "-"
    return {"up": up, "down": dn, "bias": bias}


# ===================================================================== candles
def _session(df):
    m = df.index.hour * 60 + df.index.minute
    return df[(m >= 570) & (m < 960)]                # 9:30-16:00 New York


def _group(df, minutes):
    """Session-anchored candles: first one starts 9:30 (1H: 9:30..15:30, 4H: 9:30 and 13:30)."""
    m = df.index.hour * 60 + df.index.minute
    start = df.index.normalize() + pd.to_timedelta(570 + ((m - 570) // minutes) * minutes, unit="m")
    g = df.groupby(start)
    return pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(),
                         "Low": g["Low"].min(), "Close": g["Close"].last()}).sort_index()


def _day_index(df):
    df = df.copy()
    df.index = pd.DatetimeIndex([pd.Timestamp(d.date(), tz=ET) for d in df.index])
    return df[~df.index.duplicated(keep="last")]


def frames_from(series: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """series: Alpaca timeframe -> DataFrame (NY time index). Returns the 7 chart timeframes."""
    m30 = _session(series["30Min"])
    fr = {
        "W": _day_index(series["1Week"]),
        "D": _day_index(series["1Day"]),
        "4H": _group(m30, 240),
        "1H": _group(m30, 60),
        "30m": m30,
        "15m": _session(series["15Min"]),
        "5m": _session(series["5Min"]),
    }
    return {k: v[["Open", "High", "Low", "Close"]].astype(float) for k, v in fr.items() if len(v)}


# ===================================================================== data
SERIES = {"1Week": 365 * 8, "1Day": 460, "30Min": 330, "15Min": 95, "5Min": 35}   # days of history


def alpaca_symbol(t: str) -> str:
    return t.replace("-", ".")


def load_tickers() -> list[str]:
    raw = (ROOT / "engine" / "tickers.txt").read_text().splitlines()
    tickers = [x.strip() for x in raw if x.strip() and not x.startswith("#")]
    tickers = [t for t in tickers if t != "GOOG"]    # GOOGL is kept, like the Trendlines page
    return ["SPY"] + [t for t in tickers if t != "SPY"]


def fetch_alpaca(tickers: list[str], end: dt.datetime) -> dict[str, dict[str, pd.DataFrame]]:
    """ticker -> Alpaca timeframe -> DataFrame. Many symbols per request, paged."""
    key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        sys.exit("ALPACA_KEY_ID and ALPACA_SECRET_KEY must be set (GitHub: Settings -> Secrets -> Actions).")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    by_alp = {alpaca_symbol(t): t for t in tickers}
    rows: dict[str, dict[str, list]] = {t: {tf: [] for tf in SERIES} for t in tickers}
    fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    for tf, days in SERIES.items():
        syms = list(by_alp)
        for k in range(0, len(syms), 25):
            params = {"symbols": ",".join(syms[k:k + 25]), "timeframe": tf, "start": fmt(end - dt.timedelta(days=days)),
                      "end": fmt(end), "limit": "10000", "adjustment": "all", "feed": "sip"}
            while True:
                url = ALPACA + "?" + urllib.parse.urlencode(params)
                for attempt in range(5):
                    try:
                        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                            js = json.load(r)
                        break
                    except urllib.error.HTTPError as e:
                        if e.code == 429 and attempt < 4:
                            time.sleep(3 * (attempt + 1))
                            continue
                        raise
                for sym, bars in (js.get("bars") or {}).items():
                    rows[by_alp[sym]][tf].extend(bars)
                if not js.get("next_page_token"):
                    break
                params["page_token"] = js["next_page_token"]
    out = {}
    for t, per in rows.items():
        series = {}
        for tf, bars in per.items():
            if not bars:
                continue
            df = pd.DataFrame(bars)
            df.index = pd.DatetimeIndex(pd.to_datetime(df["t"], utc=True)).tz_convert(ET)
            df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close"})[["Open", "High", "Low", "Close"]]
            series[tf] = df[~df.index.duplicated()].sort_index()
        if len(series) == len(SERIES):
            out[t] = series
        else:
            print(f"  ! {t}: missing data, skipped")
    return out


def fetch_demo(tickers: list[str], end: dt.datetime, seed: int = 11) -> dict[str, dict[str, pd.DataFrame]]:
    """Simulated prices on a fixed calendar, cut at `end`: runs at different times see the same
    history, like real data, so repeated runs can be tested. No keys needed."""
    end_ny = pd.Timestamp(end).tz_convert(ET)
    m5 = pd.date_range(pd.Timestamp("2025-11-03 09:30", tz=ET), end_ny, freq="5min")
    m5 = m5[(m5.dayofweek < 5) & (m5.hour * 60 + m5.minute >= 570) & (m5.hour * 60 + m5.minute < 960)]
    agg = lambda df, rule: df.resample(rule, label="left", closed="left").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    out = {}
    for k, t in enumerate(tickers):
        g = lambda part: np.random.default_rng([seed, k, part])
        n, period = len(m5), 300 + int(g(0).integers(0, 400))
        r = g(1).normal(0.00002, 0.0018, 80000)[:n] + 0.0004 * np.sin(np.arange(n) / period)
        c = 100 * np.exp(np.cumsum(r))
        o = np.r_[c[0], c[:-1]]
        hi = np.maximum(o, c) * (1 + np.abs(g(2).normal(0, 0.0006, 80000)[:n]))
        lo = np.minimum(o, c) * (1 - np.abs(g(3).normal(0, 0.0006, 80000)[:n]))
        base = pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": c}, index=m5)
        daily = agg(base, "1D")
        long_days = pd.date_range("2018-01-01", (daily.index[0] - dt.timedelta(days=1)).date(), freq="B").tz_localize(ET)
        walk = np.exp(np.cumsum(g(4).normal(0.0003, 0.012, len(long_days))))
        lc = walk / walk[-1] * daily["Open"].iloc[0]          # joins the 5-minute history smoothly
        old = pd.DataFrame({"Open": lc, "High": lc * 1.008, "Low": lc * 0.992, "Close": lc}, index=long_days)
        days = pd.concat([old, daily])
        out[t] = {"1Week": agg(days, "W-MON"), "1Day": days.iloc[-330:],
                  "30Min": agg(base, "30min"), "15Min": agg(base, "15min").iloc[-2400:], "5Min": base.iloc[-2600:]}
    return out


# ===================================================================== alerts
def tstr(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M")


def scan_ticker(ticker: str, frames: dict[str, pd.DataFrame], data_end: pd.Timestamp, known: dict[str, dict]) -> list[dict]:
    """New line breaks for one ticker. `known` maps alert id -> alert (for dedupe and cooldown)."""
    res = {tf: analyze(df, TFS[tf][1]) for tf, df in frames.items()}
    new = []
    for tf, df in frames.items():
        label, _, step = TFS[tf]
        idx = df.index
        window = max(2 * step, dt.timedelta(hours=2))
        for side_key, side in (("down", "bullish"), ("up", "bearish")):
            ch = res[tf][side_key]
            if not ch["broken"]:
                continue
            bi, level = ch["broken"]
            if idx[bi] < data_end - window:              # an old break, not news
                continue
            (ia, _), (ib, _) = ch["vertices"][-2], ch["vertices"][-1]
            aid = f"topdown:{ticker}:{tf}:{side}:{tstr(idx[ia])}:{tstr(idx[ib])}"
            if aid in known:
                continue
            recent = [a for a in known.values() if a["ticker"] == ticker and a["tf"] == tf and a["side"] == side
                      and pd.Timestamp(a["break_at"]).tz_localize(ET) >= idx[bi] - 5 * step]
            if recent:                                   # same kind of break moments ago: skip the repeat
                continue
            price = float(df["Close"].iloc[-1])
            word = "above the falling" if side == "bullish" else "below the rising"
            rp = lambda v: round(float(v), 2) if abs(v) >= 10 else float(f"{float(v):.4g}")   # cheap coins keep their digits
            lv = f"{level:,.2f}" if abs(level) >= 10 else f"{float(level):.4g}"
            new.append({
                "id": aid, "strategy": "topdown", "ticker": ticker, "tf": tf,
                "date": data_end.strftime("%Y-%m-%d"), "side": side,
                "price": rp(price), "level": rp(level),
                "note": f"{label}: crossed {word} trendline ({lv})",
                "break_at": tstr(idx[bi]), "since": 0.0, "since_days": 0,
                "strength": strength(tf, side, res),
            })
    return new


def strength(tf: str, side: str, res: dict) -> dict:
    """Top-down check: do the bigger timeframes point the same way as this break?"""
    want = "Up" if side == "bullish" else "Down"
    checks, score = [], 0
    for other in [t for t in HIGHER if t != tf and t in res][:3]:
        b = res[other]["bias"]
        ok = b == want
        score += ok
        txt = {"Up": "rising line intact, no falling one", "Down": "falling line intact, no rising one",
               "Squeeze": "squeezed between both lines", "-": "no intact lines"}[b]
        checks.append({"ok": bool(ok), "text": f"{TFS[other][0]} chart: {txt}"})
    big = tf in ("W", "D", "4H")
    score += big
    checks.append({"ok": big, "text": "The break is on a 4-hour chart or bigger" if big
                   else "The break is on a short timeframe, where lines break often"})
    label = "Strong" if score >= 3 else "Moderate" if score == 2 else "Weak"
    return {"score": int(score), "label": label, "checks": checks}


def update_since(alerts: list[dict], data: dict[str, dict[str, pd.DataFrame]]):
    """Once a day after the close: how far the price moved since each alert."""
    for a in alerts:
        fr = data.get(a["ticker"])
        if not fr:
            continue
        d = _day_index(fr["1Day"])
        last = float(d["Close"].iloc[-1])
        a["since"] = round((last / a["price"] - 1) * 100, 2)
        a["since_days"] = int((d.index.strftime("%Y-%m-%d") > a["date"]).sum())


def notify(alerts: list[dict], prefix: str):
    for a in alerts:
        arrow = "▲" if a["side"] == "bullish" else "▼"
        req = urllib.request.Request(f"https://ntfy.sh/{prefix}-topdown-{a['tf'].lower()}",
                                     data=f"{arrow} {a['ticker']} {a['price']}: {a['note']}".encode(), method="POST",
                                     headers={"Title": f"Top-Down {a['tf']}: {a['ticker']}",
                                              "Tags": "chart_with_upwards_trend" if a["side"] == "bullish" else "chart_with_downwards_trend"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:                           # a failed ping never stops the scan
            print(f"  ! notification failed: {e}")


# ===================================================================== crypto (OKX, 24/7)
OKX_BARS = {"W": ("1Wutc", 300), "D": ("1Dutc", 300), "4H": ("4H", 400), "1H": ("1H", 500),
            "30m": ("30m", 600), "15m": ("15m", 1200), "5m": ("5m", 1200)}


def crypto_tickers() -> list[str]:
    raw = (ROOT / "engine" / "crypto.txt").read_text().splitlines()
    return [x.strip() for x in raw if x.strip() and not x.startswith("#")]


def _okx(url: str):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
                js = json.load(r)
            if js.get("code") == "0":
                return js["data"]
            if js.get("code") == "50011":                # too many requests
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(js.get("msg") or js.get("code"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    raise RuntimeError("OKX kept refusing")


def fetch_okx(tickers: list[str]) -> dict[str, dict[str, pd.DataFrame]]:
    """ticker (BTC-USD) -> our timeframe -> DataFrame in UTC, finished candles only."""
    out = {}
    for t in tickers:
        inst = t.split("-")[0] + "-USDT"
        frames = {}
        try:
            for tf, (bar, need) in OKX_BARS.items():
                rows, after = [], ""
                while len(rows) < need:
                    d = _okx(f"https://www.okx.com/api/v5/market/candles?instId={inst}&bar={bar}&limit=300{after}")
                    if not d:
                        break
                    rows += d
                    after = f"&after={d[-1][0]}"
                    time.sleep(0.07)
                    if len(d) < 300:
                        break
                rows = [r for r in rows if r[8] == "1"]   # confirm = 1: the candle has closed
                if not rows:
                    continue
                df = pd.DataFrame([[float(x) for x in r[1:5]] for r in rows], columns=["Open", "High", "Low", "Close"],
                                  index=pd.to_datetime([int(r[0]) for r in rows], unit="ms", utc=True))
                frames[tf] = df[~df.index.duplicated()].sort_index()
        except Exception as e:
            print(f"  ! {t}: {e}")
            continue
        if "D" in frames and "1H" in frames:
            out[t] = frames
        else:
            print(f"  ! {t}: not on OKX, skipped")
    return out


def main_crypto(args, now: dt.datetime):
    global ET
    ET = ZoneInfo("UTC")                                 # crypto candles and alert times are in UTC
    tickers = args.tickers.split(",") if args.tickers else crypto_tickers()
    out = Path(args.out if args.out != str(OUT) else ROOT / "docs" / "data" / "crypto" / "topdown.json")
    print(f"Fetching {len(tickers)} coins from OKX…")
    data = fetch_okx(tickers)
    old = json.loads(out.read_text()) if out.exists() else {"alerts": []}
    cutoff = (now.date() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    alerts = [a for a in old.get("alerts", []) if a["date"] >= cutoff]
    known = {a["id"]: a for a in alerts}
    new = []
    for t, frames in data.items():
        # "now" for each timeframe is the end of its last finished candle; alerts only on fresh breaks
        data_end = max(df.index[-1] for df in frames.values())
        found = scan_ticker(t, frames, data_end, known)
        for a in found:
            a["found_at"] = now.strftime("%Y-%m-%dT%H:%M+00:00")
            known[a["id"]] = a
        new += found
    print(f"{len(new)} new line break(s).")
    for a in new:
        print(f"  {a['side']:8} {a['ticker']:9} {a['tf']:4} {a['note']}")
    alerts = new + alerts
    update_since(alerts, {t: {"1Day": f["D"]} for t, f in data.items()})
    alerts.sort(key=lambda a: (a["date"], a.get("found_at", "")), reverse=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": now.strftime("%Y-%m-%dT%H:%M+00:00"), "as_of": now.date().isoformat(),
                               "demo": False, "market": "crypto", "alerts": alerts}, separators=(",", ":")))
    prefix = os.environ.get("NTFY_PREFIX", "").strip()
    if prefix and new:
        notify(new, prefix + "-crypto")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="simulated prices, no Alpaca keys needed")
    ap.add_argument("--force", action="store_true", help="run even when the US market is closed")
    ap.add_argument("--tickers", help="comma-separated subset, e.g. SPY,AAPL")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--now", help="pretend it is this UTC time, e.g. 2026-10-27T18:30 (for testing)")
    ap.add_argument("--market", default="stocks", choices=["stocks", "crypto"])
    args = ap.parse_args()

    now = dt.datetime.fromisoformat(args.now).replace(tzinfo=dt.timezone.utc) if args.now else dt.datetime.now(dt.timezone.utc)
    if args.market == "crypto":
        return main_crypto(args, now)
    ny = now.astimezone(ET)
    minutes = ny.hour * 60 + ny.minute
    if not args.force and (ny.weekday() >= 5 or not 570 <= minutes <= 985):   # 9:30 to 16:25 New York
        print(f"US market closed ({ny:%a %H:%M} New York): nothing to do.")
        return
    if not args.demo and not (os.environ.get("ALPACA_KEY_ID") and os.environ.get("ALPACA_SECRET_KEY")):
        print("Skipped: add the ALPACA_KEY_ID and ALPACA_SECRET_KEY secrets to the repository (see README).")
        return
    end = now - dt.timedelta(minutes=16)                 # free Alpaca plan: data at least 15 minutes old
    tickers = args.tickers.split(",") if args.tickers else load_tickers()
    print(f"Fetching {len(tickers)} tickers ({'demo' if args.demo else 'Alpaca'}) up to {end.astimezone(ET):%H:%M} New York…")
    data = fetch_demo(tickers, end) if args.demo else fetch_alpaca(tickers, end)

    out = Path(args.out)
    old = json.loads(out.read_text()) if out.exists() else {"alerts": []}
    cutoff = (ny.date() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    alerts = [a for a in old.get("alerts", []) if a["date"] >= cutoff and (not args.demo or old.get("demo"))]
    known = {a["id"]: a for a in alerts}
    data_end = pd.Timestamp(end).tz_convert(ET)

    new = []
    for t in tickers:
        if t in data:
            frames = frames_from(data[t])
            found = scan_ticker(t, frames, data_end, known)
            for a in found:
                a["found_at"] = now.strftime("%Y-%m-%dT%H:%M+00:00")
                known[a["id"]] = a
            new += found
    print(f"{len(new)} new line break(s).")
    for a in new:
        print(f"  {a['side']:8} {a['ticker']:6} {a['tf']:4} {a['note']}")

    alerts = new + alerts
    after_close = minutes >= 960
    if after_close or args.demo:
        update_since(alerts, data)
    alerts.sort(key=lambda a: (a["date"], a.get("found_at", "")), reverse=True)

    if not new and not after_close and out.exists() and not args.demo:
        print("No change: file left as it is (no commit needed).")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": now.strftime("%Y-%m-%dT%H:%M+00:00"), "as_of": ny.date().isoformat(),
                               "demo": bool(args.demo), "data_end": tstr(data_end), "alerts": alerts},
                              separators=(",", ":")))
    prefix = os.environ.get("NTFY_PREFIX", "").strip()
    if prefix and new and not args.demo:
        notify(new, prefix)


if __name__ == "__main__":
    main()
