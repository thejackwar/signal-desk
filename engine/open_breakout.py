"""Open Breakout: trade the break of yesterday's high at the New York open.

The idea (Giacomo, Nov 2026):
  1. Scan the S&P 100 / S&P 500 for liquid stocks (average volume of at least $10M a day).
  2. Keep the ones trending up.
  3. Draw a flat line at yesterday's high and keep it going into today.
  4. If the stock rises in the pre-market (maybe already through the line), wait for the first
     5-minute candle at the open: if it touches / is above the line and confirms, buy.
  5. Stop a little under the line. Take profit: to be decided -> we test several.

Exact rules tested here (every one is a switch, see VARIANTS):
  Liquidity   20-day average dollar volume >= $10M ("usd10m", the default) or 20-day average share
              volume >= 10M shares ("sh10m": almost every S&P 500 stock trades far more than $10M a day,
              so the dollar filter barely removes anything - the share filter keeps only the busiest names).
  Trend       as of yesterday's close: close > SMA50 > SMA200 ("50_200", default), close > SMA50 ("50"),
              or close > SMA20 > SMA50 ("20_50").
  Line        L = yesterday's regular-session high (09:30-16:00 New York, from 5-minute bars).
  Pre-market  "up": last pre-market price above yesterday's close (default); "above": the pre-market
              already traded above L; "any": no pre-market condition.
  Signal      the first 5-minute candle (09:30-09:35). "close": it closes above L (default) - so it either
              touched the line or opened above it, and held; "green": closes above L and above its own open.
  Entry       that candle's close (09:35).
  Stop        L minus a buffer: 0.10%, 0.25% (default), 0.50% under the line, or "low": the candle's low.
              (If the entry is already within 0.05% of the stop the stop is put 0.05% under the entry.)
  Target      1R, 1.5R, 2R, 3R (R = entry - stop) or "eod": no target, out at the 15:55 close.
  Exit        stop first if a bar hits both (cautious), a gap through the stop fills at the bar's open,
              everything is closed at the 15:55 bar's close. Long only, one trade per stock per day.
  Costs       0.03% a side.
  Account     each day up to 5 trades (the strongest first candles by relative volume), 20% of the account
              each, no leverage - so the account result is comparable with holding SPY.

Data: Alpaca SIP daily bars (filters) and 5-minute bars incl. pre-market (signals), split/dividend adjusted,
from 2021. Current index members only (survivorship bias: stocks that dropped out are missing).
Only days whose official open was within 1% of L (or above) are downloaded at 5 minutes - a first candle
that opens more than 1% under the line and closes above it is rare and is skipped.

    python engine/open_breakout.py               # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/open_breakout.py --demo        # synthetic data, writes /tmp/open-breakout-demo.json
Output: docs/data/breakout/backtest.json (results) and docs/data/breakout/watchlist.json (next session's list).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "data" / "breakout"
NY = ZoneInfo("America/New_York")
BARS = "https://data.alpaca.markets/v2/stocks/bars"
FROM = "2021-01-04"
WARMUP = "2020-01-01"
COST = 0.0003
MIN_RISK = 0.0005
MAX_POS = 5
OPEN_NEAR = 0.99

DEFAULT = {"univ": "sp500", "liq": "usd10m", "trend": "50_200", "pm": "up", "sig": "close", "stop": "0.25", "tp": "2"}
VARIANTS = {
    "univ": ["sp100", "sp500"],
    "liq": ["usd10m", "sh10m"],
    "trend": ["50", "50_200", "20_50"],
    "pm": ["any", "up", "above"],
    "sig": ["close", "green"],
    "stop": ["0.10", "0.25", "0.50", "low"],
    "tp": ["1", "1.5", "2", "3", "eod"],
}


# ===================================================================== data
def _get(params: dict) -> dict:
    key, sec = os.environ["ALPACA_KEY_ID"], os.environ["ALPACA_SECRET_KEY"]
    req = urllib.request.Request(BARS + "?" + urllib.parse.urlencode(params),
                                 headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
    for attempt in range(8):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=90).read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            code = getattr(e, "code", 0)
            if code in (0, 429, 500, 502, 503, 504) and attempt < 7:
                time.sleep(4 * (attempt + 1))
                continue
            raise
    return {}


def fetch(symbols: list[str], tf: str, start: str, end: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    params = {"symbols": ",".join(symbols), "timeframe": tf, "start": start, "end": end,
              "limit": "10000", "adjustment": "all", "feed": "sip"}
    while True:
        js = _get(params)
        for s, bars in (js.get("bars") or {}).items():
            out[s].extend(bars)
        if not js.get("next_page_token"):
            return out
        params["page_token"] = js["next_page_token"]


def load_tickers() -> tuple[list[str], set[str]]:
    read = lambda f: [l.strip().replace("-", ".") for l in (ROOT / "engine" / f).read_text().splitlines()
                      if l.strip() and not l.startswith("#")]
    sp100, sp500 = read("tickers.txt"), read("tickers500.txt")
    return sorted(set(sp500) | set(sp100)), set(sp100)


def daily_table(daily: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Per symbol, per day: the filters as of the PREVIOUS close (no look-ahead) plus today's open."""
    tab = {}
    for s, bars in daily.items():
        rows, c = [], [b["c"] for b in bars]
        for i, b in enumerate(bars):
            if i < 200:
                continue
            p = i - 1
            sma = lambda n: sum(c[p - n + 1:p + 1]) / n
            win = bars[p - 19:p + 1]
            rows.append({"d": b["t"][:10], "o": b["o"], "pc": c[p], "ph": bars[p]["h"], "pd": bars[p]["t"][:10],
                         "s20": sma(20), "s50": sma(50), "s200": sma(200),
                         "usd": sum(x["v"] * x["c"] for x in win) / 20, "sh": sum(x["v"] for x in win) / 20})
        tab[s] = rows
    return tab


def ny(t: str) -> datetime:
    return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(NY)


def build_candidates(tab: dict, sp100: set[str], demo_intraday=None) -> list[dict]:
    """Days that pass the loosest filters, with the intraday facts every variant needs."""
    by_day: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for s, rows in tab.items():
        for r in rows:
            if r["d"] >= FROM and r["usd"] >= 10e6 and r["pc"] > r["s50"] and r["o"] >= OPEN_NEAR * r["ph"]:
                by_day[r["d"]].append((s, r))
    days = sorted(by_day)
    print(f"{sum(map(len, by_day.values()))} stock-days to check at 5 minutes over {len(days)} sessions")
    cands = []
    for k, d in enumerate(days):
        items = by_day[d]
        if demo_intraday:
            intr = demo_intraday(d, items)
        else:
            pd = items[0][1]["pd"]
            start = datetime.fromisoformat(pd + "T09:30").replace(tzinfo=NY).astimezone(timezone.utc)
            end = datetime.fromisoformat(d + "T16:00").replace(tzinfo=NY).astimezone(timezone.utc)
            intr = {}
            syms = [s for s, _ in items]
            for j in range(0, len(syms), 100):
                intr.update(fetch(syms[j:j + 100], "5Min", start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                  end.strftime("%Y-%m-%dT%H:%M:%SZ")))
        for s, r in items:
            c = facts(s, d, r, intr.get(s) or [])
            if c:
                c["sp100"] = s in sp100
                cands.append(c)
        if k % 100 == 0:
            print(f"  {d}: {k + 1}/{len(days)} sessions, {len(cands)} first candles above the line so far", flush=True)
    return cands


def facts(s: str, d: str, r: dict, bars: list[dict]) -> dict | None:
    prev_rth, pm, today = [], [], []
    for b in bars:
        t = ny(b["t"]); m = t.hour * 60 + t.minute; day = t.date().isoformat()
        if day == r["pd"] and 570 <= m < 960:
            prev_rth.append(b)
        elif day == d and 240 <= m < 570:
            pm.append(b)
        elif day == d and 570 <= m < 960:
            today.append((m, b))
    if not prev_rth or not today or today[0][0] != 570:
        return None
    L = max(b["h"] for b in prev_rth)
    f = today[0][1]
    if f["c"] <= L:                      # every signal variant needs the first candle to close above the line
        return None
    return {"s": s, "d": d, "L": L, "pc": r["pc"], "o": f["o"], "h": f["h"], "l": f["l"], "c": f["c"],
            "rv": f["v"] * f["c"] / max(r["usd"], 1), "pm_last": pm[-1]["c"] if pm else None,
            "pm_high": max((b["h"] for b in pm), default=None),
            "trend": {"50": r["pc"] > r["s50"], "50_200": r["pc"] > r["s50"] > r["s200"],
                      "20_50": r["pc"] > r["s20"] > r["s50"]},
            "usd": r["usd"], "sh": r["sh"],
            "rest": [[b["o"], b["h"], b["l"], b["c"], m] for m, b in today[1:] if m <= 955]}


# ===================================================================== rules
def passes(c: dict, v: dict) -> bool:
    if v["univ"] == "sp100" and not c["sp100"]:
        return False
    if v["liq"] == "sh10m" and c["sh"] < 10e6:
        return False
    if not c["trend"][v["trend"]]:
        return False
    if v["pm"] == "up" and not (c["pm_last"] and c["pm_last"] > c["pc"]):
        return False
    if v["pm"] == "above" and not (c["pm_high"] and c["pm_high"] >= c["L"]):
        return False
    if v["sig"] == "green" and c["c"] <= c["o"]:
        return False
    return True


def trade(c: dict, v: dict) -> dict:
    entry = c["c"]
    stop = c["l"] if v["stop"] == "low" else c["L"] * (1 - float(v["stop"]) / 100)
    if entry - stop < MIN_RISK * entry:
        stop = entry * (1 - MIN_RISK)
    risk = entry - stop
    tgt = None if v["tp"] == "eod" else entry + float(v["tp"]) * risk
    px, why, at = None, "close", "15:55"
    for o, h, l, cl, m in c["rest"]:
        if l <= stop:
            px, why = min(o, stop), "stop"
        elif tgt and h >= tgt:
            px, why = max(o, tgt), "target"
        elif m >= 955:
            px = cl
        if px is not None:
            at = f"{m // 60:02d}:{m % 60:02d}"
            break
    if px is None:                       # half-day session: out at the last bar
        px = c["rest"][-1][3] if c["rest"] else entry
    ret = px / entry - 1 - 2 * COST
    return {"s": c["s"], "d": c["d"], "L": round(c["L"], 2), "in": round(entry, 2), "sl": round(stop, 2),
            "tp": round(tgt, 2) if tgt else None, "out": round(px, 2), "why": why, "at": at,
            "pct": round(ret * 100, 3), "r": round((px - entry) / risk, 2), "rv": round(c["rv"], 3)}


def stats(ts: list[dict], spy: dict[str, float]) -> dict:
    if not ts:
        return {"n": 0}
    w = [t for t in ts if t["pct"] > 0]
    gain, loss = sum(t["pct"] for t in w), -sum(t["pct"] for t in ts if t["pct"] <= 0)
    # account: up to MAX_POS trades a day, strongest first candles first, 1/MAX_POS of equity each
    days: dict[str, list[dict]] = defaultdict(list)
    for t in ts:
        days[t["d"]].append(t)
    eq, peak, dd, curve, yearly = 1.0, 1.0, 0.0, [], defaultdict(lambda: 1.0)
    for d in sorted(days):
        take = sorted(days[d], key=lambda t: -t["rv"])[:MAX_POS]
        r = sum(t["pct"] for t in take) / 100 / MAX_POS
        eq *= 1 + r; yearly[d[:4]] *= 1 + r
        peak = max(peak, eq); dd = min(dd, eq / peak - 1)
        curve.append([d, round(eq, 4)])
    d0, d1 = min(days), max(days)
    yrs = max((date.fromisoformat(d1) - date.fromisoformat(d0)).days / 365.25, 0.1)
    by_year = {}
    for y in sorted({t["d"][:4] for t in ts}):
        yt = [t for t in ts if t["d"][:4] == y]
        by_year[y] = {"n": len(yt), "win": round(100 * sum(t["pct"] > 0 for t in yt) / len(yt), 1),
                      "avg": round(sum(t["pct"] for t in yt) / len(yt), 3),
                      "acct": round((yearly[y] - 1) * 100, 1), "spy": spy.get(y)}
    return {"n": len(ts), "days": len(days), "win": round(100 * len(w) / len(ts), 1),
            "avg": round(sum(t["pct"] for t in ts) / len(ts), 3),
            "avg_r": round(sum(t["r"] for t in ts) / len(ts), 3),
            "pf": round(gain / loss, 2) if loss else None,
            "exits": {k: sum(t["why"] == k for t in ts) for k in ("target", "stop", "close")},
            "acct_total": round((eq - 1) * 100, 1), "acct_cagr": round((eq ** (1 / yrs) - 1) * 100, 1),
            "acct_maxdd": round(dd * 100, 1), "by_year": by_year,
            "curve": curve[::max(1, len(curve) // 300)] + curve[-1:]}


def run(cands: list[dict], v: dict) -> list[dict]:
    return [trade(c, v) for c in cands if passes(c, v)]


# ===================================================================== watchlist for the next session
def watchlist(tab: dict, daily: dict, sp100: set[str]) -> dict:
    out = []
    for s, bars in daily.items():
        if len(bars) < 201:
            continue
        c = [b["c"] for b in bars]
        sma = lambda n: sum(c[-n:]) / n
        usd = sum(b["v"] * b["c"] for b in bars[-20:]) / 20
        sh = sum(b["v"] for b in bars[-20:]) / 20
        if usd < 10e6 or not (c[-1] > sma(50) > sma(200)):
            continue
        out.append({"s": s, "sp100": s in sp100, "close": c[-1], "line": bars[-1]["h"],
                    "dist": round((bars[-1]["h"] / c[-1] - 1) * 100, 2), "usd_m": round(usd / 1e6),
                    "sh_m": round(sh / 1e6, 1), "sma50": round(sma(50), 2), "sma200": round(sma(200), 2)})
    out.sort(key=lambda x: x["dist"])
    last = max((b[-1]["t"][:10] for b in daily.values() if b), default=None)
    return {"as_of": last, "note": "line = that day's daily high (regular session); trend = close > SMA50 > SMA200; "
                                   "liquidity = 20-day average dollar volume >= $10M", "stocks": out}


# ===================================================================== demo data
def demo_daily(symbols: list[str]) -> dict[str, list[dict]]:
    rnd, out = random.Random(3), {}
    days = [date(2020, 1, 1) + timedelta(i) for i in range(2200)]
    days = [d for d in days if d.weekday() < 5 and d <= date(2026, 11, 6)]
    for s in symbols:
        px, rows, drift = 100.0, [], rnd.uniform(-0.0002, 0.001)
        for d in days:
            o = px * (1 + rnd.gauss(0.001, 0.008)); c = o * (1 + rnd.gauss(drift, 0.015))
            rows.append({"t": d.isoformat() + "T05:00:00Z", "o": o, "h": max(o, c) * 1.006, "l": min(o, c) * 0.994,
                         "c": c, "v": rnd.uniform(2e6, 20e6)})
            px = c
        out[s] = rows
    return out


def demo_intraday(d: str, items: list) -> dict:
    rnd, out = random.Random(d), {}
    for s, r in items:
        bars, px = [], r["pc"]
        pdt = datetime.fromisoformat(r["pd"] + "T09:30").replace(tzinfo=NY)
        for i in range(78):
            o = px; c = o * (1 + rnd.gauss(0, 0.002))
            bars.append({"t": (pdt + timedelta(minutes=5 * i)).isoformat(), "o": o, "h": max(o, c) * 1.001,
                         "l": min(o, c) * 0.999, "c": c, "v": 1e5})
            px = c
        L = max(b["h"] for b in bars)
        dt = datetime.fromisoformat(d + "T04:00").replace(tzinfo=NY)
        px = r["o"]
        for i in range(144):
            o = px; c = o * (1 + rnd.gauss(0.0002, 0.002 if i >= 66 else 0.001))
            bars.append({"t": (dt + timedelta(minutes=5 * i)).isoformat(), "o": o, "h": max(o, c) * 1.001,
                         "l": min(o, c) * 0.999, "c": c, "v": 2e5})
            px = c
        out[s] = bars
    return out


# ===================================================================== main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    syms, sp100 = load_tickers()
    if a.demo:
        syms = syms[:40]; sp100 = set(syms[:15])
        daily = demo_daily(syms)
    else:
        today = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        daily = {}
        for j in range(0, len(syms), 100):
            daily.update(fetch(syms[j:j + 100], "1Day", WARMUP + "T00:00:00Z", today))
        print(f"daily bars for {len(daily)} of {len(syms)} symbols ({time.time() - t0:.0f}s)")
    tab = daily_table(daily)
    spy_rows = daily.get("SPY") or []
    if not a.demo:
        spy_rows = fetch(["SPY"], "1Day", WARMUP + "T00:00:00Z", today)["SPY"]
    spy = {}
    for y in sorted({b["t"][:4] for b in spy_rows if b["t"][:10] >= FROM}):
        yb = [b for b in spy_rows if b["t"][:4] == y]
        prev = [b for b in spy_rows if b["t"][:4] < y]
        base = prev[-1]["c"] if prev else yb[0]["o"]
        spy[y] = round((yb[-1]["c"] / base - 1) * 100, 1)
    spy_total = None
    if spy_rows:
        first = [b for b in spy_rows if b["t"][:10] < FROM]
        spy_total = round((spy_rows[-1]["c"] / (first[-1]["c"] if first else spy_rows[0]["o"]) - 1) * 100, 1)

    cands = build_candidates(tab, sp100, demo_intraday if a.demo else None)
    print(f"{len(cands)} candidate first candles ({time.time() - t0:.0f}s)")

    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "from": FROM,
           "rules": __doc__.split("Exact rules tested here")[1].split("Data:")[0].strip(),
           "default": DEFAULT, "variants": VARIANTS, "spy_by_year": spy, "spy_total": spy_total,
           "costs_pct_a_side": COST * 100, "max_positions": MAX_POS}
    base = run(cands, DEFAULT)
    res["base"] = stats(base, spy)
    res["base_trades_recent"] = sorted(base, key=lambda t: t["d"])[-300:]
    # one switch at a time around the default
    res["sweeps"] = {}
    for k, opts in VARIANTS.items():
        res["sweeps"][k] = {}
        for o in opts:
            st = stats(run(cands, {**DEFAULT, k: o}), spy)
            st.pop("curve", None)
            res["sweeps"][k][o] = st
    # stop x target grid, both universes, both liquidity filters
    res["grid"] = []
    for univ in VARIANTS["univ"]:
        for liq in VARIANTS["liq"]:
            for stop in VARIANTS["stop"]:
                for tp in VARIANTS["tp"]:
                    v = {**DEFAULT, "univ": univ, "liq": liq, "stop": stop, "tp": tp}
                    st = stats(run(cands, v), spy)
                    res["grid"].append({**{k: v[k] for k in ("univ", "liq", "stop", "tp")},
                                        **{k: st.get(k) for k in ("n", "win", "avg", "avg_r", "pf", "acct_total",
                                                                  "acct_cagr", "acct_maxdd")}})
    # every filter combination at the default stop/target, to see which filters matter
    res["filters"] = []
    for univ in VARIANTS["univ"]:
        for liq in VARIANTS["liq"]:
            for tr in VARIANTS["trend"]:
                for pm in VARIANTS["pm"]:
                    for sig in VARIANTS["sig"]:
                        v = {**DEFAULT, "univ": univ, "liq": liq, "trend": tr, "pm": pm, "sig": sig}
                        st = stats(run(cands, v), spy)
                        res["filters"].append({**{k: v[k] for k in ("univ", "liq", "trend", "pm", "sig")},
                                               **{k: st.get(k) for k in ("n", "win", "avg", "avg_r", "pf",
                                                                         "acct_total", "acct_cagr", "acct_maxdd")}})
    wl = watchlist(tab, {s: b for s, b in daily.items()}, sp100)
    out = Path("/tmp/open-breakout-demo.json") if a.demo else OUT_DIR / "backtest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, separators=(",", ":")))
    (out.parent / ("open-breakout-watch-demo.json" if a.demo else "watchlist.json")).write_text(
        json.dumps(wl, separators=(",", ":")))
    b = res["base"]
    print(f"default: {b.get('n')} trades, win {b.get('win')}%, avg {b.get('avg')}%, PF {b.get('pf')}, "
          f"account {b.get('acct_total')}% (CAGR {b.get('acct_cagr')}%, max DD {b.get('acct_maxdd')}%) vs SPY {spy_total}%")
    print(f"watchlist: {len(wl['stocks'])} stocks as of {wl['as_of']}  ->  {out}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
