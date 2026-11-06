"""4H Change of Character (CHoCH): a Smart Money Concepts reversal setup on 4-hour candles.

Rules (the same code drives the backtest and the live paper trades):
  Swings     a swing high is a 4H candle whose high is above the N candles before it and not below
             the N after it (N = pivot length, 2 / 3 / 5). It is only known N candles later.
  Structure  a close above the latest unbroken swing high is a break up, a close below the latest
             unbroken swing low a break down. A break AGAINST the current direction is a CHoCH
             (a break with it is a BOS and is ignored).
  Zone       the bullish CHoCH's leg runs from the lowest low since the broken swing high to the
             break candle. The zone is the latest fair value gap in that leg (low of a candle above
             the high two candles earlier); without one, the last red candle at the leg's low
             (the order block); without that, the lower half of the leg. Shorts are mirrored.
  Stop       0.1% beyond the leg's low (the low formed right before the CHoCH).
  Entry      "close"   at the close of the CHoCH candle
             "retest"  limit order at the middle of the zone, valid for 10 candles, cancelled if the
                       stop is hit first (default)
             "reject"  price must touch the zone, then a candle must close back above it (green);
                       entry at that close. Same 10 + 10 candle windows.
  Target     "2r"      twice the risk (default)
             "swing"   the nearest earlier swing high at least 1R away (2R if there is none)
             "trail"   no target: at +1R the stop goes to break-even, then follows each new swing low
  Time stop  out at the close after 60 candles.
  Filters    "trend"   longs only when the last daily close is above the 200-day average (shorts below)
             "vol"     the CHoCH candle's volume above the 20-candle average
  Sides      "long" (default) or "both"
  Costs      stocks 0.05% a side, bitcoin 0.10% a side. One trade at a time per market.
  Risk       skipped if the stop is less than 0.2% or more than 15% away.

Stocks: S&P 100 (engine/tickers.txt, today's members, so survivorship bias applies) on 4H candles
built from Alpaca 30-minute bars, regular hours only, anchored at the open: 09:30-13:30 and 13:30-16:00
New York (the second "4H" candle is only 2.5 hours: the US session is 6.5 hours long).
Bitcoin: OKX BTC-USDT 4H candles (UTC), round the clock.

Account results
  bitcoin  the whole account in each trade, compounding, no leverage, vs holding bitcoin
  stocks   €100,000, each trade risks 0.5% of the account (size = 0.5% / stop distance, at most 20% of
           the account), no leverage (a trade is skipped when the money is all in use), vs holding SPY
           and vs holding the same stocks in equal parts
In sample 2021-2023, out of sample 2024 onward: a setting that only works in the first part is curve fit.

    python engine/choch.py --backtest            # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY (stocks)
    python engine/choch.py --live stocks|btc     # paper trades, every finished 4H candle
    python engine/choch.py --backtest --demo     # synthetic candles, writes to /tmp/choch-demo
    python engine/choch.py --live btc --demo
-> docs/data/choch/backtest.json, docs/data/choch/live.json
Phone alerts: ntfy topics <NTFY_PREFIX>-choch (stocks) and <NTFY_PREFIX>-crypto-choch (bitcoin).
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
from universe import U  # noqa: E402

DATA_DIR = U.path("choch")       # UNIVERSE=sp500: docs/data/sp500/choch/ (stocks only; bitcoin stays in docs/data/choch/)
NY = ZoneInfo("America/New_York")
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"

BT_FROM = "2021-01-01"           # trades from here; data from a year earlier for the 200-day average
WARMUP_FROM = "2020-01-01"
OOS_FROM = "2024-01-01"          # out of sample
START = "2026-11-09"             # first day of the live paper trades (Monday)
STAKE = 1000                     # € per live paper trade, like the Live trades page

PIVOTS = [2, 3, 5]
ENTRIES = ["close", "retest", "reject"]
TARGETS = ["2r", "swing", "trail"]
SIDES = ["long", "both"]
FILTERS = ["none", "trend", "vol", "trend+vol"]
DEFAULT = (3, "retest", "2r", "long", "trend")

WAIT = 10                        # candles a retest order / rejection stays valid
MAX_HOLD = 60                    # candles
STOP_PAD = 0.001
MIN_RISK, MAX_RISK = 0.002, 0.15
COST = {"stocks": 0.0005, "btc": 0.001}
RISK_PER_TRADE, MAX_POS = 0.005, 0.20


def vkey(n, entry, target, side, filt) -> str:
    return f"{n}|{entry}|{target}|{side}|{filt}"


# ===================================================================== rules
def pivots(b: dict, n: int):
    """Swing highs/lows: index -> level. A swing at i is known at candle i + n."""
    h, l, m = b["h"], b["l"], len(b["h"])
    sh, sl = {}, {}
    for i in range(n, m - n):
        if h[i] > max(h[i - n:i]) and h[i] >= max(h[i + 1:i + n + 1]):
            sh[i] = h[i]
        if l[i] < min(l[i - n:i]) and l[i] <= min(l[i + 1:i + n + 1]):
            sl[i] = l[i]
    return sh, sl


def events(b: dict, n: int, sh: dict, sl: dict) -> list[dict]:
    """Every CHoCH: the candle that broke structure against the trend, with its zone and stop."""
    o, h, l, c = b["o"], b["h"], b["l"], b["c"]
    state, hi, lo, out = 0, None, None, []
    for k in range(len(c)):
        j = k - n                                   # the swing confirmed by this candle
        if j in sh:
            hi = [j, sh[j], False]
        if j in sl:
            lo = [j, sl[j], False]
        if hi and not hi[2] and c[k] > hi[1]:
            hi[2] = True
            if state == -1:
                out.append(_event(b, k, 1, hi[0], hi[1]))
            state = 1
        if lo and not lo[2] and c[k] < lo[1]:
            lo[2] = True
            if state == 1:
                out.append(_event(b, k, -1, lo[0], lo[1]))
            state = -1
    return [e for e in out if e]


def _event(b: dict, k: int, s: int, piv: int, level: float) -> dict | None:
    o, h, l, c = b["o"], b["h"], b["l"], b["c"]
    seg = range(piv, k + 1)
    ext = min(seg, key=lambda i: l[i]) if s > 0 else max(seg, key=lambda i: h[i])
    ref = l[ext] if s > 0 else h[ext]
    zone, kind = None, None
    for j in range(k, ext + 1, -1):               # latest fair value gap in the leg
        if s > 0 and l[j] > h[j - 2]:
            zone, kind = (h[j - 2], l[j]), "fvg"
            break
        if s < 0 and h[j] < l[j - 2]:
            zone, kind = (h[j], l[j - 2]), "fvg"
            break
    if not zone:                                    # order block: last opposite candle at the extreme
        for j in range(ext, max(ext - 5, -1), -1):
            if (s > 0 and c[j] < o[j]) or (s < 0 and c[j] > o[j]):
                zone, kind = (l[j], h[j]), "ob"
                break
    if not zone:
        mid = (ref + c[k]) / 2
        zone, kind = ((ref, mid) if s > 0 else (mid, ref)), "leg"
    lo_z, hi_z = min(zone), max(zone)
    if s > 0:
        hi_z = min(hi_z, c[k])
        lo_z = max(lo_z, ref)
    else:
        lo_z = max(lo_z, c[k])
        hi_z = min(hi_z, ref)
    if hi_z <= lo_z:
        return None
    return {"k": k, "side": s, "piv": piv, "level": level, "ext": ext, "ref": ref,
            "zone": (lo_z, hi_z), "zone_kind": kind}


def tags(b: dict, ev: dict, sma: list) -> None:
    k, s = ev["k"], ev["side"]
    v = b["v"]
    avg = sum(v[max(0, k - 20):k]) / max(1, k - max(0, k - 20))
    ev["vol_ok"] = k >= 20 and v[k] > avg
    m = sma[k]
    ev["trend_ok"] = m is not None and ((b["c"][k] > m) if s > 0 else (b["c"][k] < m))


def daily_sma(b: dict, day_of, n: int = 200) -> list:
    """For each 4H candle, the 200-day average of daily closes up to the PREVIOUS day."""
    days, closes = [], []
    for i, t in enumerate(b["t"]):
        d = day_of(t)
        if days and days[-1] == d:
            closes[-1] = b["c"][i]
        else:
            days.append(d)
            closes.append(b["c"][i])
    avg, run = {}, 0.0
    for i, x in enumerate(closes):
        run += x
        if i >= n:
            run -= closes[i - n]
        if i + 1 < len(days):
            avg[days[i + 1]] = run / n if i >= n - 1 else None
    return [avg.get(day_of(t)) for t in b["t"]]


def simulate(b: dict, ev: dict, entry: str, target: str, sh: dict, sl: dict, n: int, cost: float) -> dict | None:
    o, h, l, c, m = b["o"], b["h"], b["l"], b["c"], len(b["c"])
    s, k = ev["side"], ev["k"]
    stop = ev["ref"] * (1 - STOP_PAD) if s > 0 else ev["ref"] * (1 + STOP_PAD)
    zlo, zhi = ev["zone"]
    hit_stop = lambda j: (l[j] <= stop) if s > 0 else (h[j] >= stop)
    fill = px = None
    if entry == "close":
        fill, px = k, c[k]
    elif entry == "retest":
        lim = (zlo + zhi) / 2
        for j in range(k + 1, min(k + 1 + WAIT, m)):
            if (s > 0 and l[j] <= lim) or (s < 0 and h[j] >= lim):
                fill, px = j, (min(o[j], lim) if s > 0 else max(o[j], lim))
                break
            if hit_stop(j):
                return None
        if fill is None:
            return None if k + WAIT < m else {"pending": True, "k": k}
    else:  # reject
        touched = None
        for j in range(k + 1, min(k + 1 + 2 * WAIT, m)):
            if hit_stop(j):
                return None
            if touched is None:
                if j > k + WAIT:
                    return None
                if (s > 0 and l[j] <= zhi) or (s < 0 and h[j] >= zlo):
                    touched = j
            if touched is not None and ((s > 0 and c[j] > zhi and c[j] > o[j]) or (s < 0 and c[j] < zlo and c[j] < o[j])):
                fill, px = j, c[j]
                break
        if fill is None:
            return None if k + 2 * WAIT < m else {"pending": True, "k": k}
    risk = (px - stop) * s
    if risk <= 0 or not MIN_RISK <= risk / px <= MAX_RISK:
        return None
    tp = None
    if target == "2r":
        tp = px + 2 * risk * s
    elif target == "swing":
        piv = sh if s > 0 else sl
        best = None
        for i in sorted(piv, reverse=True):
            if i + n > fill:
                continue
            if i < fill - 300:
                break
            lv = piv[i]
            if (lv - px) * s >= risk and (best is None or (lv - best) * s < 0):
                best = lv
        tp = best if best is not None else px + 2 * risk * s
    trail_piv = sl if s > 0 else sh
    stop0, armed = stop, False
    start = fill + 1 if entry in ("close", "reject") else fill
    ex = None
    for j in range(start, m):
        if hit_stop(j):
            p = min(o[j], stop) if s > 0 else max(o[j], stop)
            if j == fill:
                p = stop
            ex = (j, p, "trail" if armed else "stop")
            break
        if tp is not None and ((s > 0 and h[j] >= tp) or (s < 0 and l[j] <= tp)):
            p = (max(o[j], tp) if s > 0 else min(o[j], tp)) if j > fill else tp
            ex = (j, p, "target")
            break
        if j - fill >= MAX_HOLD:
            ex = (j, c[j], "time")
            break
        if target == "trail":
            if not armed and (c[j] - px) * s >= risk:
                armed, stop = True, px
            if armed:
                q = j - n
                if q in trail_piv and q > fill and (trail_piv[q] - stop) * s > 0:
                    stop = trail_piv[q] * (1 - STOP_PAD) if s > 0 else trail_piv[q] * (1 + STOP_PAD)
    t = {"k": k, "fill": fill, "side": s, "entry": px, "stop0": stop0, "risk": risk / px, "tp": tp,
         "zone": [zlo, zhi], "zone_kind": ev["zone_kind"], "level": ev["level"]}
    if ex:
        j, p, why = ex
        pct = s * (p / px - 1) - 2 * cost
        t.update(out=j, exit=p, why=why, pct=pct, r=pct / (risk / px))
    return t


def run_symbol(b: dict, n: int, sma: list, cost: float, combos=None) -> dict:
    """All trades of one market, per (entry, target): every CHoCH simulated, with its filter tags."""
    sh, sl = pivots(b, n)
    evs = events(b, n, sh, sl)
    for e in evs:
        tags(b, e, sma)
    res = {}
    for entry in ENTRIES:
        for target in TARGETS:
            if combos and (entry, target) not in combos:
                continue
            ts = []
            for e in evs:
                t = simulate(b, e, entry, target, sh, sl, n, cost)
                if t and not t.get("pending"):
                    t["trend_ok"], t["vol_ok"] = e["trend_ok"], e["vol_ok"]
                    ts.append(t)
            res[(entry, target)] = ts
    return {"trades": res, "events": evs, "sh": sh, "sl": sl}


def keep(t: dict, side: str, filt: str) -> bool:
    if side == "long" and t["side"] < 0:
        return False
    if "trend" in filt and not t["trend_ok"]:
        return False
    if "vol" in filt and not t["vol_ok"]:
        return False
    return True


def one_at_a_time(ts: list[dict]) -> list[dict]:
    out, free = [], -1
    for t in sorted(ts, key=lambda t: t["fill"]):
        if t["fill"] <= free:
            continue
        out.append(t)
        free = t.get("out", 10 ** 9)
    return out


# ===================================================================== stats
def stats(ts: list[dict], years: float) -> dict:
    ts = [t for t in ts if "out" in t]
    if not ts:
        return {"trades": 0}
    r = [t["r"] for t in ts]
    p = [t["pct"] for t in ts]
    wins = [x for x in r if x > 0]
    gw, gl = sum(x for x in r if x > 0), -sum(x for x in r if x <= 0)
    return {"trades": len(ts), "per_year": round(len(ts) / max(years, 1e-9), 1),
            "win_rate": round(len(wins) / len(ts), 3), "avg_r": round(sum(r) / len(r), 3), "total_r": round(sum(r), 1),
            "profit_factor": round(gw / gl, 2) if gl else None, "avg_pct": round(100 * sum(p) / len(p), 3),
            "avg_hold": round(sum(t["out"] - t["fill"] for t in ts) / len(ts), 1),
            "longs": sum(t["side"] > 0 for t in ts), "shorts": sum(t["side"] < 0 for t in ts),
            "exits": {k: sum(t["why"] == k for t in ts) for k in ("target", "stop", "trail", "time")}}


def curve_stats(points: list[tuple[str, float]], d0: str, d1: str) -> dict:
    eq = [v for _, v in points] or [1.0]
    peak, dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
    yrs = max(1e-9, (date.fromisoformat(d1) - date.fromisoformat(d0)).days / 365.25)
    end = eq[-1] / eq[0]
    return {"total_pct": round((end - 1) * 100, 1), "cagr_pct": round((end ** (1 / yrs) - 1) * 100, 1) if end > 0 else -100.0,
            "max_dd_pct": round(dd * 100, 1)}


def monthly(points: list[tuple[str, float]]) -> list:
    out = {}
    for d, v in points:
        out[d[:7]] = round(v, 4)
    return [[m, v] for m, v in out.items()]


# ===================================================================== data
def _get(url: str, params: dict) -> dict:
    key, sec = os.environ["ALPACA_KEY_ID"], os.environ["ALPACA_SECRET_KEY"]
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                 headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
    for attempt in range(6):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=60).read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < 5:
                time.sleep(3 * (attempt + 1))
                continue
            raise
    return {}


def to_4h(rows: list[tuple]) -> dict:
    """30-minute regular-hours bars (NY time, ISO) -> session-anchored 4H candles: 09:30 and 13:30."""
    b = {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    cur = None
    for t, o, h, l, c, v in rows:
        mins = int(t[11:13]) * 60 + int(t[14:16])
        if not 570 <= mins < 960:
            continue
        key = t[:10] + ("T09:30" if mins < 810 else "T13:30")
        if key != cur:
            cur = key
            for f, x in zip("tohlcv", (key, o, h, l, c, v)):
                b[f].append(x)
        else:
            b["h"][-1] = max(b["h"][-1], h)
            b["l"][-1] = min(b["l"][-1], l)
            b["c"][-1] = c
            b["v"][-1] += v
    return b


def _fetch_batch(batch: list[str], start: datetime, end: datetime, feed: str) -> dict[str, dict]:
    fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw: dict[str, list] = {s: [] for s in batch}
    params = {"symbols": ",".join(s.replace("-", ".") for s in batch), "timeframe": "30Min", "start": fmt(start),
              "end": fmt(end), "limit": "10000", "adjustment": "all", "feed": feed}
    while True:
        js = _get(BARS_URL, params)
        for s, bars in (js.get("bars") or {}).items():
            s = s.replace(".", "-")
            rows = raw.setdefault(s, [])
            for x in bars:
                t = datetime.fromisoformat(x["t"].replace("Z", "+00:00")).astimezone(NY).isoformat(timespec="minutes")
                if "09:30" <= t[11:16] < "16:00":             # regular hours only (the feed has 04:00-20:00)
                    rows.append((t, x["o"], x["h"], x["l"], x["c"], x["v"]))
        if not js.get("next_page_token"):
            break
        params["page_token"] = js["next_page_token"]
    return {s: to_4h(sorted(rows)) for s, rows in raw.items() if rows}


CACHE_DIR = ROOT / ".cache" / "choch"      # 4H candles kept between runs (actions/cache in choch.yml)


def _load_cache() -> dict[str, dict]:
    f = CACHE_DIR / f"{U.key}.json.gz"
    try:
        return json.loads(gzip.decompress(f.read_bytes()))
    except Exception:
        return {}


def _save_cache(data: dict[str, dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{U.key}.json.gz").write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode(), 6))


def fetch_stocks(symbols: list[str], start: datetime, end: datetime, feed: str = "sip") -> dict[str, dict]:
    """4H candles since `start`. Candles from earlier runs come from the cache; only the last few days (and any
    stock not cached yet) are downloaded, 5 stocks per request, 4 requests at a time."""
    cache = _load_cache()
    jobs: dict[str, list[str]] = {}                 # first day to download -> stocks
    for s in symbols:
        b = cache.get(s)
        if b and b["t"] and b["t"][0][:10] <= start.astimezone(NY).strftime("%Y-%m-%d"):
            since = (date.fromisoformat(b["t"][-1][:10]) - timedelta(days=4)).isoformat()   # redo the last days
        else:
            since = start.astimezone(NY).strftime("%Y-%m-%d")
            cache.pop(s, None)
        jobs.setdefault(since, []).append(s)
    batches = [(since, syms[i:i + 5]) for since, syms in jobs.items() for i in range(0, len(syms), 5)]
    # The Alpaca key is shared with the live workflows: while the US market is open, one request at a time
    ny = datetime.now(NY)
    busy = ny.weekday() < 5 and 9 * 60 <= ny.hour * 60 + ny.minute <= 16 * 60 + 30
    workers = 1 if busy else 4
    print(f"  stocks: {len(symbols)} ({sum(len(v) for k, v in jobs.items() if k > '2025')} from the cache), {len(batches)} downloads", flush=True)
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(_fetch_batch, b, datetime.fromisoformat(since).replace(tzinfo=NY), end, feed): (since, b) for since, b in batches}
        for fu in as_completed(futs):
            since, b = futs[fu]
            try:
                got = fu.result()
            except Exception as e:                     # one failed download never sinks the rest
                print(f"  ! {','.join(b)}: {e}", flush=True)
                continue
            for s, new in got.items():
                old = cache.get(s)
                if old:
                    keep = sum(1 for t in old["t"] if t[:10] < since)
                    cache[s] = {f: old[f][:keep] + new[f] for f in "tohlcv"}
                else:
                    cache[s] = new
            done += 1
            if done % 10 == 0 or done == len(batches):
                print(f"  downloads {done}/{len(batches)}", flush=True)
                _save_cache(cache)                     # a run cut short keeps what it downloaded
    _save_cache(cache)
    lo = start.astimezone(NY).strftime("%Y-%m-%d")
    out = {}
    for s in symbols:
        b = cache.get(s)
        if b and b["t"]:
            i = next((k for k, t in enumerate(b["t"]) if t[:10] >= lo), len(b["t"]))
            out[s] = {f: b[f][i:] for f in "tohlcv"}
    return out


def _okx(url: str):
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
                js = json.load(r)
            if js.get("code") == "0":
                return js["data"]
            if js.get("code") == "50011":
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(js.get("msg") or js.get("code"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 5:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    raise RuntimeError("OKX kept refusing")


def fetch_btc(since: str) -> dict:
    """OKX BTC-USDT 4H candles (UTC), finished ones only, oldest first."""
    stop_ms = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp() * 1000)
    rows, after = [], ""
    while True:
        d = _okx(f"https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT&bar=4H&limit=100{after}")
        if not d:
            break
        rows += d
        oldest = int(d[-1][0])
        if oldest <= stop_ms:
            break
        after = f"&after={oldest}"
        time.sleep(0.12)
    rows = sorted({int(r[0]): r for r in rows if r[8] == "1" and int(r[0]) >= stop_ms}.values(), key=lambda r: int(r[0]))
    b = {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    for r in rows:
        b["t"].append(datetime.fromtimestamp(int(r[0]) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M"))
        for f, x in zip("ohlcv", r[1:6]):
            b[f].append(float(x))
    print(f"  bitcoin: {len(b['t'])} 4H candles from {b['t'][0] if b['t'] else '-'}")
    return b


def demo_series(n_bars: int, seed: int, px: float, step_h: int, start: str) -> dict:
    rnd = random.Random(seed)
    b = {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    t0 = datetime.fromisoformat(start)
    drift, p = 0.0, px
    for i in range(n_bars):
        if i % 150 == 0:
            drift = rnd.choice([-1, 1]) * rnd.uniform(0.0002, 0.0015)
        o = p
        c = o * (1 + drift + rnd.gauss(0, 0.012))
        h = max(o, c) * (1 + abs(rnd.gauss(0, 0.005)))
        l = min(o, c) * (1 - abs(rnd.gauss(0, 0.005)))
        for f, x in zip("tohlcv", ((t0 + timedelta(hours=step_h * i)).strftime("%Y-%m-%dT%H:%M"), o, h, l, c, rnd.uniform(1e5, 3e5))):
            b[f].append(x)
        p = c
    return b


def load_tickers() -> list[str]:
    return U.tickers(drop=("GOOG",))


# ===================================================================== backtest
def years_between(d0: str, d1: str) -> float:
    return max(1e-9, (date.fromisoformat(d1) - date.fromisoformat(d0)).days / 365.25)


def btc_account(b: dict, ts: list[dict]) -> list[tuple[str, float]]:
    eq, pts = 1.0, []
    for t in ts:
        if "out" not in t:
            continue
        eq *= 1 + t["pct"]
        pts.append((b["t"][t["out"]][:10], eq))
    return pts


def stock_account(trades: list[tuple[str, dict]], data: dict[str, dict]) -> tuple[list[tuple[str, float]], dict]:
    """€100k, 0.5% risk a trade, at most 20% of the account in one, no leverage. trades: (symbol, trade)."""
    evs = []
    for sym, t in trades:
        if "out" not in t:
            continue
        tt = data[sym]["t"]
        evs.append((tt[t["fill"]], 1, sym, t))
        evs.append((tt[t["out"]], 0, sym, t))
    evs.sort(key=lambda e: (e[0], e[1]))
    cash_eq, used, open_, pts, taken, skipped, peak_n = 100000.0, 0.0, {}, [], 0, 0, 0
    for when, kind, sym, t in evs:
        tid = id(t)
        if kind == 0:
            if tid in open_:
                size = open_.pop(tid)
                used -= size
                cash_eq += size * t["pct"]
                pts.append((when[:10], cash_eq))
        else:
            size = min(cash_eq * RISK_PER_TRADE / t["risk"], cash_eq * MAX_POS)
            if used + size > cash_eq + 1e-6:
                skipped += 1
                continue
            open_[tid] = size
            used += size
            taken += 1
            peak_n = max(peak_n, len(open_))
    return [(p[0], p[1] / 100000) for p in pts], {"taken": taken, "skipped_no_cash": skipped, "max_open": peak_n}


def hold_pct(b: dict, d0: str, d1: str) -> float:
    idx = [i for i, t in enumerate(b["t"]) if d0 <= t[:10] <= d1]
    return round((b["c"][idx[-1]] / b["o"][idx[0]] - 1) * 100, 1) if idx else 0.0


def hold_curve(b: dict, d0: str) -> list:
    idx = [i for i, t in enumerate(b["t"]) if t[:10] >= d0]
    if not idx:
        return []
    base = b["o"][idx[0]]
    return monthly([(b["t"][i][:10], b["c"][i] / base) for i in idx])


def example(b: dict, t: dict, sym: str) -> dict:
    lo, hi = max(0, t["k"] - 45), min(len(b["c"]), t.get("out", t["fill"]) + 12)
    r = lambda x: round(x, 2 if x > 10 else 4)
    return {"symbol": sym, "bars": [[b["t"][i], r(b["o"][i]), r(b["h"][i]), r(b["l"][i]), r(b["c"][i])] for i in range(lo, hi)],
            "choch": t["k"] - lo, "fill": t["fill"] - lo, "out": (t["out"] - lo) if "out" in t else None,
            "level": r(t["level"]), "zone": [r(z) for z in t["zone"]], "zone_kind": t["zone_kind"],
            "entry": r(t["entry"]), "stop": r(t["stop0"]), "tp": r(t["tp"]) if t["tp"] else None,
            "exit": r(t["exit"]) if "exit" in t else None, "why": t.get("why"), "r": round(t["r"], 2) if "r" in t else None}


def trade_row(b: dict, t: dict, sym: str) -> dict:
    r = lambda x: round(x, 2 if x > 10 else 4)
    return {"symbol": sym, "side": t["side"], "signal": b["t"][t["k"]], "in": b["t"][t["fill"]], "entry": r(t["entry"]),
            "stop": r(t["stop0"]), "tp": r(t["tp"]) if t["tp"] else None, "zone_kind": t["zone_kind"],
            "out": b["t"][t["out"]] if "out" in t else None, "exit": r(t["exit"]) if "exit" in t else None,
            "why": t.get("why"), "pct": round(t["pct"] * 100, 2) if "pct" in t else None, "r": round(t["r"], 2) if "r" in t else None}


def backtest_market(market: str, data: dict[str, dict], day_of, bench: dict | None) -> dict:
    cost = COST[market]
    syms = sorted(data)
    smas = {s: daily_sma(data[s], day_of) for s in syms}
    first = lambda b: next((i for i, t in enumerate(b["t"]) if t[:10] >= BT_FROM), len(b["t"]))
    d0 = BT_FROM
    d1 = max(data[s]["t"][-1][:10] for s in syms)
    yrs, yrs_is, yrs_oos = years_between(d0, d1), years_between(d0, OOS_FROM), years_between(OOS_FROM, d1)
    variants, default_detail = {}, None
    for n in PIVOTS:
        per = {}
        for s in syms:
            res = run_symbol(data[s], n, smas[s], cost)
            f0 = first(data[s])
            per[s] = {k: [t for t in v if t["k"] >= f0] for k, v in res["trades"].items()}
        for entry in ENTRIES:
            for target in TARGETS:
                for side in SIDES:
                    for filt in FILTERS:
                        key = vkey(n, entry, target, side, filt)
                        picked = []
                        for s in syms:
                            for t in one_at_a_time([t for t in per[s][(entry, target)] if keep(t, side, filt)]):
                                picked.append((s, t))
                        closed = [(s, t) for s, t in picked if "out" in t]
                        when = lambda st: data[st[0]]["t"][st[1]["fill"]][:10]
                        ins = [t for s, t in closed if when((s, t)) < OOS_FROM]
                        oos = [t for s, t in closed if when((s, t)) >= OOS_FROM]
                        by_year = {}
                        for s, t in closed:
                            by_year.setdefault(when((s, t))[:4], []).append(t)
                        if market == "btc":
                            pts = btc_account(data[syms[0]], [t for _, t in sorted(closed, key=lambda st: st[1]["fill"])])
                            acc_extra = {}
                        else:
                            pts, acc_extra = stock_account(closed, data)
                        acc = curve_stats([(d0, 1.0)] + pts, d0, d1)
                        acc.update(acc_extra)
                        v = {"all": stats([t for _, t in closed], yrs), "is": stats(ins, yrs_is), "oos": stats(oos, yrs_oos),
                             "by_year": {y: stats(ts, 1)["avg_r"] if ts else None for y, ts in sorted(by_year.items())},
                             "account": acc}
                        if (n, entry, target, side, filt) == DEFAULT:
                            v["curve"] = monthly([(d0, 1.0)] + pts)
                            default_detail = picked
                        variants[key] = v
        print(f"  {market} pivot {n}: done", flush=True)
    recent = sorted(default_detail, key=lambda st: data[st[0]]["t"][st[1]["fill"]], reverse=True)
    rows = [trade_row(data[s], t, s) for s, t in recent[:80]]
    exs = [example(data[s], t, s) for s, t in recent if "out" in t][:3]
    out = {"from": d0, "to": d1, "symbols": len(syms), "variants": variants, "recent": rows, "examples": exs,
           "cost_pct_side": cost * 100}
    if market == "btc":
        b = data[syms[0]]
        out["hold_pct"] = hold_pct(b, d0, d1)
        out["hold_curve"] = hold_curve(b, d0)
        out["hold_by_year"] = {y: hold_pct(b, f"{y}-01-01", f"{y}-12-31") for y in sorted({t[:4] for t in b["t"] if t[:10] >= d0})}
    else:
        if bench:
            out["hold_pct"] = hold_pct(bench, d0, d1)
            out["hold_curve"] = hold_curve(bench, d0)
            out["hold_by_year"] = {y: hold_pct(bench, f"{y}-01-01", f"{y}-12-31") for y in sorted({t[:4] for t in bench["t"] if t[:10] >= d0})}
        eq = [hold_pct(data[s], d0, d1) for s in syms]
        out["hold_same_pct"] = round(sum(eq) / len(eq), 1) if eq else None
    return out


def ny_day(t: str) -> str:
    return t[:10]


def run_backtest(demo: bool, now: datetime) -> dict:
    res = {"default": vkey(*DEFAULT), "oos_from": OOS_FROM, "wait": WAIT, "max_hold": MAX_HOLD,
           "grid": {"pivot": PIVOTS, "entry": ENTRIES, "target": TARGETS, "side": SIDES, "filter": FILTERS}, "markets": {}}
    if demo:
        btc = demo_series(6 * 365 * 6, 3, 9000.0, 4, WARMUP_FROM + "T00:00")
        stocks = {f"DEMO{i}": demo_series(6 * 252 * 2, 10 + i, 100.0 + 10 * i, 12, WARMUP_FROM + "T09:30") for i in range(6)}
        spy = demo_series(6 * 252 * 2, 99, 300.0, 12, WARMUP_FROM + "T09:30")
    else:
        btc = None if U.is500 else fetch_btc("2020-05-01")
        tick = load_tickers()
        start = datetime.fromisoformat(WARMUP_FROM).replace(tzinfo=NY)
        stocks = fetch_stocks(tick + ["SPY"], start, now - timedelta(minutes=16))
        spy = stocks.pop("SPY", None)
    if not U.is500:
        res["markets"]["btc"] = backtest_market("btc", {"BTC-USD": btc}, ny_day, None)
    res["markets"]["stocks"] = backtest_market("stocks", stocks, ny_day, spy)
    return res


# ===================================================================== live
def notify(topic: str, title: str, msg: str, up: bool) -> None:
    prefix = os.environ.get("NTFY_PREFIX", "").strip()
    if not prefix:
        return
    req = urllib.request.Request(f"https://ntfy.sh/{prefix}-{topic}", data=msg.encode(), method="POST",
                                 headers={"Title": title, "Tags": "chart_with_upwards_trend" if up else "chart_with_downwards_trend"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"  ! notification failed: {e}")


def finished_stocks(b: dict, now_ny: datetime) -> dict:
    """Drop today's 4H candle if it isn't finished yet (09:30 one at 13:30, 13:30 one at 16:00)."""
    keep_n = len(b["t"])
    while keep_n:
        t = b["t"][keep_n - 1]
        end = datetime.fromisoformat(t).replace(tzinfo=NY) + timedelta(minutes=240 if t.endswith("09:30") else 150)
        if end <= now_ny:
            break
        keep_n -= 1
    return {f: v[:keep_n] for f, v in b.items()}


def run_live(market: str, demo: bool, now: datetime) -> None:
    out = Path("/tmp/choch-demo/live.json") if demo else DATA_DIR / "live.json"
    book = json.loads(out.read_text()) if out.exists() else {"start": START, "stake": STAKE, "default": vkey(*DEFAULT), "markets": {}}
    mk = book["markets"].setdefault(market, {"trades": [], "pending": [], "seen": []})
    before = json.dumps(book, sort_keys=True)
    n, entry, target, side, filt = DEFAULT
    cost = COST[market]
    topic = "crypto-choch" if market == "btc" else U.topic("choch")
    if demo:
        data = {"BTC-USD": demo_series(1600, int(now.timestamp()) // 14400, 60000.0, 4, "2026-03-01T00:00")} if market == "btc" else \
               {f"DEMO{i}": demo_series(700, 40 + i, 100.0 + 5 * i, 12, "2025-12-01T09:30") for i in range(4)}
        start = "2000-01-01"
    else:
        start = START
        if now.strftime("%Y-%m-%d") < START:
            print(f"Live paper trades start {START}.")
            return
        if market == "btc":
            data = {"BTC-USD": fetch_btc((now - timedelta(days=330)).strftime("%Y-%m-%d"))}
        else:
            ny = now.astimezone(NY)
            got = fetch_stocks(load_tickers(), now - timedelta(days=440), now - timedelta(minutes=16))
            data = {s: finished_stocks(b, ny - timedelta(minutes=16)) for s, b in got.items()}
    trades, pending = {}, []
    for sym, b in data.items():
        if len(b["c"]) < 60:
            continue
        sma = daily_sma(b, ny_day)
        sh, sl = pivots(b, n)
        evs = [e for e in events(b, n, sh, sl) if b["t"][e["k"]][:10] >= start]
        busy = -1
        for e in evs:
            tags(b, e, sma)
            if not keep({"side": e["side"], "trend_ok": e["trend_ok"], "vol_ok": e["vol_ok"]}, side, filt):
                continue
            eid = f"{sym}|{b['t'][e['k']]}"
            if eid not in mk["seen"]:
                mk["seen"].append(eid)
                z = [round(x, 2) for x in e["zone"]]
                print(f"  CHoCH {sym} {b['t'][e['k']]} zone {z}")
                if not demo:
                    notify(topic, f"4H CHoCH {'▲' if e['side'] > 0 else '▼'} {sym}",
                           f"4H close broke the last {'lower high' if e['side'] > 0 else 'higher low'} at {round(e['level'], 2)}. "
                           f"Paper {'buy' if e['side'] > 0 else 'sell'} order at the middle of the {e['zone_kind'].upper()} zone {z[0]}-{z[1]}, stop {round(e['ref'] * (1 - STOP_PAD * e['side']), 2)}, "
                           f"valid for {WAIT} candles. Not advice.", e["side"] > 0)
            if e["k"] <= busy:
                continue
            t = simulate(b, e, entry, target, sh, sl, n, cost)
            if not t:
                continue
            if t.get("pending"):
                pending.append({"id": eid, "symbol": sym, "side": e["side"], "signal": b["t"][e["k"]],
                                "zone": [round(x, 2) for x in e["zone"]], "limit": round(sum(e["zone"]) / 2, 2),
                                "stop": round(e["ref"] * (1 - STOP_PAD) if e["side"] > 0 else e["ref"] * (1 + STOP_PAD), 2),
                                "expires_after": WAIT - (len(b["c"]) - 1 - e["k"])})
                continue
            busy = t.get("out", 10 ** 9)
            row = trade_row(b, t, sym)
            row["id"], row["eur"] = eid, round(STAKE * t["pct"], 2) if "pct" in t else None
            row["last"] = round(b["c"][-1], 2)
            row["open_pct"] = round(100 * (t["side"] * (b["c"][-1] / t["entry"] - 1) - cost), 2) if "out" not in t else None
            trades[eid] = row
    old = {t["id"]: t for t in mk["trades"]}
    for eid, row in trades.items():
        prev = old.get(eid)
        if prev and prev.get("out"):
            continue                                 # closed trades are never changed
        if not demo and not prev:
            notify(topic, f"4H CHoCH: in {row['symbol']}", f"Paper {'long' if row['side'] > 0 else 'short'} {row['symbol']} at {row['entry']}, "
                   f"stop {row['stop']}, target {row['tp']}. Not advice.", row["side"] > 0)
        if not demo and row.get("out") and not (prev and prev.get("out")):
            notify(topic, f"4H CHoCH: out {row['symbol']} {row['pct']:+.2f}%", f"{row['symbol']} closed at {row['exit']} ({row['why']}), "
                   f"{row['r']:+.2f}R, €{row['eur']:+.2f} on €{STAKE}. Paper trade, not advice.", row["pct"] >= 0)
        old[eid] = row
    mk["trades"] = sorted(old.values(), key=lambda t: t["in"])
    mk["pending"] = pending
    mk["seen"] = mk["seen"][-400:]
    if json.dumps(book, sort_keys=True) == before and out.exists():
        print("No change: file left as it is.")
        return
    book["updated"] = now.strftime("%Y-%m-%dT%H:%MZ")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(book, separators=(",", ":")))
    print(f"-> {out}: {len(mk['trades'])} trades, {len(pending)} orders waiting")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--live", choices=["stocks", "btc"])
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    if U.is500 and a.live == "btc":
        ap.error("bitcoin runs with the S&P 100 (no UNIVERSE=sp500)")
    now = datetime.now(timezone.utc)
    needs_alpaca = a.backtest or a.live == "stocks"
    if needs_alpaca and not a.demo and not os.environ.get("ALPACA_KEY_ID"):
        sys.exit("ALPACA_KEY_ID / ALPACA_SECRET_KEY missing")
    if a.backtest:
        res = run_backtest(a.demo, now)
        res["updated"] = now.strftime("%Y-%m-%dT%H:%MZ")
        out = Path("/tmp/choch-demo/backtest.json") if a.demo else DATA_DIR / "backtest.json"
        if a.demo:
            res["demo"] = True
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, separators=(",", ":")))
        d = res["markets"]
        for m in d:
            v = d[m]["variants"][res["default"]]
            print(f"{m}: default {v['all']} account {v['account']} hold {d[m].get('hold_pct')}%")
        print(f"-> {out} ({out.stat().st_size // 1024} KB)")
    elif a.live:
        run_live(a.live, a.demo, now)
    else:
        ap.error("choose --backtest or --live stocks|btc")


if __name__ == "__main__":
    main()
