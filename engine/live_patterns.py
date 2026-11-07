"""
Live chart patterns on stocks: the Terminal's "Live patterns" section.

Every 30 minutes while the US market is open, every stock in the list (S&P 100, or S&P 500 with UNIVERSE=sp500)
is scanned on 15-minute, 1-hour, 4-hour and daily candles with the same two detectors as the BTC pattern backtest
(pattern_finder.py = chart patterns, deck_patterns.py = multi-bar, short-term and candlestick patterns).

Two stages, both listed:
  forming    the shape is complete and price is still inside it; the trigger line(s), stop and target are shown.
             (Candlestick patterns, inside bars and NR4 candles only appear once they confirm: as "forming"
             they would be a new one on almost every candle.)
  confirmed  a candle closed through the trigger line (same rules as the backtest). Kept for a few days with
             what happened since: still open, take profit hit, stop loss hit or out of time.

Each pattern carries its track record from the weekly stock backtest (--stats): every breakout of that pattern on
S&P 100 stocks on that timeframe, traded with the BTC backtest's rules (in at the next open, out at the pattern's
own stop / target, 2R when it has no target, or after HOLD candles), 0.05% costs each way.

Candle times: New York wall clock written as if it were UTC (the same as the site's chart), sessions from 09:30:
1H candles 09:30, 10:30 … 15:30 (the last is half an hour), 4H candles 09:30 and 13:30, regular hours only.
Only finished candles count. Alpaca's free full-market (SIP) data is 15 minutes old, so a candle counts once its
end is more than 16 minutes ago.

Output
  docs/data/live_patterns.json   (docs/data/sp500/live_patterns.json for the S&P 500)
    {as_of, universe, data_end, tfs, forming: [...], confirmed: [...], counts}
  docs/data/live_patterns_stats.json   (--stats, S&P 100, shared by both lists)
    {as_of, universe, from: {tf: date}, hold, fee_pct, stats: {tf: {"fam|name|DIR": [trades, win %, avg %]}}}

Run
  python engine/live_patterns.py            live scan (needs ALPACA_KEY_ID / ALPACA_SECRET_KEY)
  python engine/live_patterns.py --stats 4H [--budget 80]   weekly backtest for one timeframe's win rates
  python engine/live_patterns.py --merge-stats             the four timeframes into docs/data/live_patterns_stats.json
  python engine/live_patterns.py --demo     made-up candles, no keys (to check the output and the page)
"""
from __future__ import annotations

import argparse
import gzip
import json
import multiprocessing
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deck_patterns as dp      # noqa: E402
import pattern_finder as pf     # noqa: E402
from btc_patterns import TRIGGER, rule_text   # noqa: E402  same stop / take-profit wording as the BTC page
from universe import U          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NY = ZoneInfo("America/New_York")
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
OUT = U.path("live_patterns.json")
STATS = ROOT / "docs" / "data" / "live_patterns_stats.json"
CACHE = ROOT / ".cache" / "live_patterns"
FEE = 0.0005
DELAY = 16 * 60                  # seconds: the free SIP feed is 15 minutes old

# src: which Alpaca bars build it.  look: candles kept for the detectors.  recent: candles checked for a new
# breakout on each run (a run every 30 minutes, plus one spare).  keep: how long a confirmed pattern stays listed.
TFS = {
    "15m": {"src": "15Min", "pf": "15min", "sec": 900, "hold": 104, "recent": 3, "keep": 2 * 86400, "label": "15-minute"},
    "1H": {"src": "30Min", "pf": "hourly", "sec": 3600, "hold": 72, "recent": 2, "keep": 4 * 86400, "label": "1-hour"},
    "4H": {"src": "30Min", "pf": "hourly", "sec": 14400, "hold": 60, "recent": 2, "keep": 8 * 86400, "label": "4-hour"},
    "D": {"src": "1Day", "pf": "daily", "sec": 86400, "hold": 30, "recent": 2, "keep": 21 * 86400, "label": "daily"},
}
WIN = 300                        # candles the detectors see (both need under 300)
# live: calendar days of history kept per bar size (enough for WIN candles of every timeframe built from it)
LIVE_DAYS = {"15Min": 21, "30Min": 240, "1Day": 620}
# --stats: trades counted from these dates (data starts earlier, for the detectors' warm-up)
STATS_FROM = {"D": "2021-01-01", "4H": "2021-01-01", "1H": "2024-01-01", "15m": "2025-11-01"}
STATS_DATA = {"1Day": "2019-09-01", "30Min": "2020-08-01", "15Min": "2025-09-15"}
NO_FORMING = {"deck-candle"}     # one-candle setups: only listed once they confirm
NO_FORMING_NAMES = {"Inside bar", "Narrow range"}


# ===================================================================== data
def _get(params: dict) -> dict:
    key, sec = os.environ["ALPACA_KEY_ID"], os.environ["ALPACA_SECRET_KEY"]
    req = urllib.request.Request(BARS_URL + "?" + urllib.parse.urlencode(params),
                                 headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
    for attempt in range(6):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=60).read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(3 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < 5:
                time.sleep(3 * (attempt + 1))
                continue
            raise
    return {}


def ny_ts(iso_utc: str) -> int:
    """Alpaca bar time (UTC) -> New York wall clock written as UTC seconds (the chart's convention)."""
    d = datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(NY)
    return int(datetime(d.year, d.month, d.day, d.hour, d.minute, tzinfo=timezone.utc).timestamp())


def _fetch_batch(syms: list[str], size: str, start: datetime, end: datetime) -> dict[str, list]:
    fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")   # noqa: E731
    params = {"symbols": ",".join(s.replace("-", ".") for s in syms), "timeframe": size, "start": fmt(start),
              "end": fmt(end), "limit": "10000", "adjustment": "all", "feed": "sip"}
    out: dict[str, list] = {}
    while True:
        js = _get(params)
        for s, bars in (js.get("bars") or {}).items():
            rows = out.setdefault(s.replace(".", "-"), [])
            for x in bars:
                t = ny_ts(x["t"])
                if size == "1Day":
                    t -= t % 86400                                # the session's date at 00:00
                elif not 570 <= (t % 86400) // 60 < 960:          # regular hours only (09:30-16:00)
                    continue
                rows.append([t, x["o"], x["h"], x["l"], x["c"], x["v"]])
        if not js.get("next_page_token"):
            break
        params["page_token"] = js["next_page_token"]
    return out


def fetch(symbols: list[str], size: str, start: datetime, end: datetime, cache: dict | None = None) -> dict[str, list]:
    """Bars since `start`. With a cache, only the last few days are downloaded for stocks already in it."""
    cache = cache if cache is not None else {}
    jobs: dict[str, list[str]] = {}
    lo = int(start.timestamp())
    for s in symbols:
        rows = cache.get(s)
        if rows and rows[0][0] <= lo + 10 * 86400:
            since = datetime.fromtimestamp(rows[-1][0] - 3 * 86400, timezone.utc).date().isoformat()   # redo the last days
        else:
            since = start.date().isoformat()
            cache.pop(s, None)
        jobs.setdefault(since, []).append(s)
    per = 10 if size != "1Day" else 50
    batches = [(since, syms[i:i + per]) for since, syms in jobs.items() for i in range(0, len(syms), per)]
    print(f"  {size}: {len(symbols)} stocks, {len(batches)} downloads", flush=True)
    with ThreadPoolExecutor(4) as ex:
        futs = {ex.submit(_fetch_batch, b, size, datetime.fromisoformat(since).replace(tzinfo=NY), end): (since, b)
                for since, b in batches}
        for fu in as_completed(futs):
            since, b = futs[fu]
            try:
                got = fu.result()
            except Exception as e:                                # one failed download never sinks the rest
                print(f"  ! {size} {','.join(b)}: {e}", flush=True)
                continue
            cut = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp())
            for s, new in got.items():
                old = [r for r in cache.get(s, []) if r[0] < cut]
                cache[s] = old + sorted(new)
    return {s: [r for r in cache[s] if r[0] >= lo] for s in symbols if cache.get(s)}


def load_cache(size: str) -> dict:
    try:
        return json.loads(gzip.decompress((CACHE / f"{size}.json.gz").read_bytes()))
    except Exception:
        return {}


def save_cache(size: str, data: dict, keep_from: int) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    data = {s: [r for r in rows if r[0] >= keep_from] for s, rows in data.items()}
    tmp = CACHE / f"{size}.json.gz.tmp"
    tmp.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode(), 6))
    tmp.replace(CACHE / f"{size}.json.gz")


# ===================================================================== candles
def candles(rows: list, tf: str, data_end: int) -> pd.DataFrame:
    """Bars -> finished candles of this timeframe, with ATR (capitalised columns, like the detectors want)."""
    if not rows:
        return pd.DataFrame()
    a = np.asarray(rows, float)
    t = a[:, 0].astype(np.int64)
    day = t - t % 86400
    if tf in ("1H", "4H"):
        mins = TFS[tf]["sec"] // 60
        m = (t - day) // 60
        key = day + (570 + (m - 570) // mins * mins) * 60
    else:
        key = t
    df = pd.DataFrame({"k": key, "Open": a[:, 1], "High": a[:, 2], "Low": a[:, 3], "Close": a[:, 4], "Volume": a[:, 5]})
    g = df.groupby("k", sort=True).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
    k = g.index.values.astype(np.int64)
    if tf == "D":
        end = k + 960 * 60                                        # 16:00 New York
    else:
        dk = k - k % 86400
        end = np.minimum(k + TFS[tf]["sec"], dk + 960 * 60)
    g = g[end <= data_end]                                       # finished candles only
    g.index = pd.to_datetime(g.index.values, unit="s", utc=True)
    return dp.add_atr(g)


def nyts_of(D: pd.DataFrame) -> np.ndarray:
    return (D.index.values.astype("datetime64[s]").astype(np.int64))


# ===================================================================== detection
def _shape_pf(p, s0, i, T):
    ix = lambda x: min(max(s0 + int(x), 0), i)                   # noqa: E731
    return {"pts": [[T(ix(x)), round(float(y), 4), lb] for x, y, lb in p.get("points") or []],
            "lines": [[T(ix(a[0])), round(float(a[1]), 4), T(ix(b[0])), round(float(b[1]), 4)] for a, b in p.get("lines") or []]}


def _shape_dp(p, w2, s1, i, T, h, l):
    ix = lambda x: min(max(s1 + int(x), 0), i)                   # noqa: E731
    shape = {"pts": [[T(ix(pt[0])), round(float(pt[1]), 4), pt[2] if len(pt) > 2 else ""] for pt in p.get("points") or []],
             "lines": []}
    for ln in p.get("lines") or []:
        f, x0, x1 = ln[0], ln[1], ln[2] if ln[2] is not None else len(w2) - 1
        shape["lines"].append([T(ix(x0)), round(float(f(x0)), 4), T(ix(x1)), round(float(f(x1)), 4)])
    if p.get("box"):
        a0, a1 = p["box"]
        shape["box"] = [T(ix(a0)), T(ix(a1)), round(float(l[s1 + a0:s1 + a1 + 1].min()), 4), round(float(h[s1 + a0:s1 + a1 + 1].max()), 4)]
    return shape


def detect_at(D: pd.DataFrame, A: dict, i: int, tf: str, forming: bool = True) -> list[dict]:
    """Every pattern the two detectors see on candle i (only the candles up to i, nothing looks ahead).
    Breakouts only when they happened on candle i itself; forming ones as they stand at candle i."""
    o, h, l, c, v, vavg, ts = A["o"], A["h"], A["l"], A["c"], A["v"], A["vavg"], A["ts"]
    T = lambda k: int(ts[k])                                     # noqa: E731
    out = []
    # chart-pattern finder
    look = pf.SETTINGS[TFS[tf]["pf"]]["lookback"]
    s0 = max(0, i - look + 1)
    vr = float(v[i] / vavg[i]) if vavg[i] and vavg[i] > 0 else None
    try:
        pats, _ = pf.find_patterns(D.iloc[s0:i + 1], TFS[tf]["pf"], c[i], c[i - 1], vr)
    except Exception:                                            # noqa: BLE001
        pats = []
    for p in pats:
        if p["stage"] == "breakout":
            out.append({"stage": "confirmed", "fam": "chart", "name": p["name"], "dir": p["dir"], "i": i,
                        "stop": float(p["stop"]), "target": None if p.get("target") is None else float(p["target"]),
                        "level": float(p["level"]), "shape": _shape_pf(p, s0, i, T)})
        elif forming and p["stage"] == "forming":
            sides = {}
            for b in p.get("breaks") or []:
                sides["up" if b["dir"] == "LONG" else "down"] = {"level": float(b["level"]), "stop": float(b["stop"]),
                                                                 "target": None if b.get("target") is None else float(b["target"])}
            out.append({"stage": "forming", "fam": "chart", "name": p["name"], "dir": p["dir"], "i": i, "sides": sides,
                        "shape": _shape_pf(p, s0, i, T)})
    # deck patterns
    s1 = max(0, i - 289)
    w2 = D.iloc[s1:i + 1]
    try:
        dps = dp.detect(w2)
    except Exception:                                            # noqa: BLE001
        dps = []
    for p in dps:
        fam, name = "deck-" + p["family"], p["name"].split(" (")[0]
        b = p.get("brk")
        if p["stage"] == "breakout":
            if not b or b["bar"] != len(w2) - 1:
                continue
            out.append({"stage": "confirmed", "fam": fam, "name": name, "dir": b["dir"], "i": i, "stop": float(b["stop"]),
                        "target": None if b.get("target") is None or not np.isfinite(b["target"]) else float(b["target"]),
                        "level": float(b["level"]), "shape": _shape_dp(p, w2, s1, i, T, h, l)})
        elif forming and p["stage"] == "forming" and fam not in NO_FORMING and name not in NO_FORMING_NAMES:
            tg = p.get("targets") or {}
            sides = {}
            if p.get("up") is not None:
                sides["up"] = {"level": float(p["up"]), "stop": None, "target": None if tg.get("LONG") is None else float(tg["LONG"])}
            if p.get("down") is not None:
                sides["down"] = {"level": float(p["down"]), "stop": None, "target": None if tg.get("SHORT") is None else float(tg["SHORT"])}
            if not sides:
                continue
            out.append({"stage": "forming", "fam": fam, "name": name, "dir": p["dir"], "i": i, "sides": sides,
                        "shape": _shape_dp(p, w2, s1, i, T, h, l)})
    return out


def arrays(D: pd.DataFrame) -> dict:
    v = D["Volume"].values.astype(float)
    return {"o": D["Open"].values.astype(float), "h": D["High"].values.astype(float), "l": D["Low"].values.astype(float),
            "c": D["Close"].values.astype(float), "v": v, "vavg": pd.Series(v).rolling(20).mean().values, "ts": nyts_of(D)}


def trade_levels(entry: float, d: str, stop: float, tgt):
    """Stop and take profit for a confirmed pattern: its own, or 2R when it has no target. None if they make no sense."""
    long = d == "LONG"
    risk = (entry - stop) if long else (stop - entry)
    tr = "pattern"
    if tgt is None or not np.isfinite(tgt):
        tgt, tr = (entry + 2 * risk if long else entry - 2 * risk), "2R"
    if risk <= 0 or (long and tgt <= entry) or (not long and tgt >= entry):
        return None
    return float(tgt), tr


def walk(A: dict, e: int, entry: float, long: bool, stop: float, tgt: float, hold: int, first_open: bool = True):
    """From candle e on: (exit index, exit price, why) or (last index, last close, "open")."""
    o, h, l, c = A["o"], A["h"], A["l"], A["c"]
    n = len(c)
    for j in range(e, min(n, e + hold)):
        if long:
            if (j > e or not first_open) and o[j] <= stop: return j, o[j], "stop"       # noqa: E701
            if (j > e or not first_open) and o[j] >= tgt: return j, o[j], "target"      # noqa: E701
            if l[j] <= stop: return j, stop, "stop"                                       # noqa: E701
            if h[j] >= tgt: return j, tgt, "target"                                       # noqa: E701
        else:
            if (j > e or not first_open) and o[j] >= stop: return j, o[j], "stop"       # noqa: E701
            if (j > e or not first_open) and o[j] <= tgt: return j, o[j], "target"      # noqa: E701
            if h[j] >= stop: return j, stop, "stop"                                       # noqa: E701
            if l[j] <= tgt: return j, tgt, "target"                                       # noqa: E701
    if e + hold <= n:
        j = e + hold - 1
        return j, c[j], "time"
    return n - 1, c[n - 1], "open"


# ===================================================================== --stats (weekly backtest for the win rates)
def _stats_one(args):
    sym, rows, tf = args
    cfg, res = TFS[tf], {}
    D = candles(rows or [], tf, 1 << 62)
    if len(D) < 320:
        return sym, res
    A = arrays(D)
    first = int(np.searchsorted(A["ts"], int(pd.Timestamp(STATS_FROM[tf], tz="UTC").timestamp())))
    o, n = A["o"], len(D)
    for i in range(max(first, 300), n - 1):
        for s in detect_at(D, A, i, tf, forming=False):
            long, e = s["dir"] == "LONG", i + 1
            entry = o[e]
            lv = trade_levels(entry, s["dir"], s["stop"], s["target"])
            if not lv:
                continue
            j, out, why = walk(A, e, entry, long, s["stop"], lv[0], cfg["hold"])
            if why == "open":
                continue
            gross = (out / entry - 1) if long else (entry / out - 1)
            ret = ((1 + gross) * (1 - FEE) ** 2 - 1) * 100
            res.setdefault(f"{s['fam']}|{s['name']}|{s['dir']}", []).append(round(float(ret), 4))
    return sym, res


STATS_PART = ROOT / ".cache" / "live_patterns_stats"     # one file per timeframe, merged by --merge-stats


def run_stats(demo: bool, tf: str, budget_min: float):
    """One timeframe's backtest (the workflow runs the four in parallel). Stops starting new stocks after
    budget_min minutes and keeps what it has, so a slow day never loses the whole run."""
    syms = U.tickers()
    now = datetime.now(timezone.utc)
    size = TFS[tf]["src"]
    if demo:
        data = {s: demo_rows(s, long=True)[size] for s in syms[:12]}
    else:
        data = fetch(syms, size, datetime.fromisoformat(STATS_DATA[size]).replace(tzinfo=timezone.utc), now - timedelta(seconds=DELAY))
    agg, done = {}, 0
    t0 = time.time()
    ex = ProcessPoolExecutor(max(1, (os.cpu_count() or 2)))
    futs = [ex.submit(_stats_one, (s, rows, tf)) for s, rows in data.items() if rows]
    try:
        for fu in as_completed(futs, timeout=budget_min * 60):
            try:
                _, res = fu.result()
            except Exception as e:                                # noqa: BLE001
                print(f"  ! {e}", flush=True)
                continue
            done += 1
            for key, rets in res.items():
                agg.setdefault(key, []).extend(rets)
            if done % 10 == 0 or done == len(futs):
                print(f"  {tf}: backtested {done}/{len(futs)} stocks ({time.time() - t0:.0f}s)", flush=True)
    except TimeoutError:
        print(f"  {tf}: time budget reached after {done}/{len(futs)} stocks, keeping those", flush=True)
    ex.shutdown(wait=False, cancel_futures=True)
    stats = {key: [len(r), round(100 * sum(x > 0 for x in r) / len(r), 1), round(float(np.mean(r)), 3)] for key, r in sorted(agg.items())}
    STATS_PART.mkdir(parents=True, exist_ok=True)
    (STATS_PART / f"{tf}.json").write_text(json.dumps({"tf": tf, "as_of": now.isoformat(timespec="seconds"), "demo": demo,
                                                       "stocks": done, "of": len(futs), "stats": stats}, separators=(",", ":")))
    print(f"stats {tf}: {sum(v[0] for v in stats.values())} trades on {done} stocks")
    for p in multiprocessing.active_children():               # don't wait for stocks still running past the budget
        p.kill()


def merge_stats():
    """The four timeframe files -> docs/data/live_patterns_stats.json (a timeframe that didn't run keeps its old numbers)."""
    try:
        out = json.loads(STATS.read_text())
    except Exception:
        out = {}
    out.update({"universe": "S&P 100", "from": STATS_FROM, "hold": {tf: c["hold"] for tf, c in TFS.items()}, "fee_pct": FEE * 100})
    out.setdefault("stats", {}); out.setdefault("stocks", {})
    for tf in TFS:
        try:
            part = json.loads((STATS_PART / f"{tf}.json").read_text())
        except Exception:
            continue
        if part.get("stats"):
            out["stats"][tf] = part["stats"]
            out["stocks"][tf] = [part["stocks"], part["of"]]
            out["as_of"] = max(out.get("as_of", ""), part["as_of"])
            out["demo"] = part.get("demo", False)
    STATS.parent.mkdir(parents=True, exist_ok=True)
    STATS.write_text(json.dumps(out, separators=(",", ":")))
    print(f"merged: {', '.join(f'{tf} {len(v)} patterns' for tf, v in out['stats'].items())} -> {STATS}")


# ===================================================================== live scan
def rules_for(fam: str, name: str) -> dict:
    stop, target = rule_text(fam, name)
    return {"stop": stop, "target": target, "trigger": TRIGGER.get(fam, "")}


def norm(name: str) -> str:
    return name.lower().replace(" top", "").replace("head & shoulders", "h&s").strip()


def scan_stock(sym: str, rows_by_size: dict, data_end: int, prev_conf: dict) -> tuple[list, list, dict]:
    forming, confirmed, last_px = [], [], {}
    for tf, cfg in TFS.items():
        D = candles(rows_by_size.get(cfg["src"]) or [], tf, data_end)
        if len(D) < 80:
            continue
        D = D.iloc[-(WIN + cfg["hold"] + cfg["recent"] + 5):]
        A = arrays(D)
        n, c = len(D), A["c"]
        last_px[tf] = float(c[-1])
        # forming, as the chart stands now (one per pattern; the chart finder's version when both see it)
        seen = {}
        for s in detect_at(D, A, n - 1, tf):
            if s["stage"] != "forming":
                continue
            k = norm(s["name"])
            if k in seen and seen[k]["fam"] == "chart":
                continue
            seen[k] = s
        for s in seen.values():
            t0 = min([q[0] for q in s["shape"].get("pts") or []] + [q[0] for q in s["shape"].get("lines") or []]
                     + ([s["shape"]["box"][0]] if s["shape"].get("box") else []) + [int(A["ts"][-1])])
            forming.append({"id": f"{sym}|{tf}|{s['fam']}|{s['name']}|{t0}", "t": sym, "tf": tf, "fam": s["fam"],
                            "name": s["name"], "dir": s["dir"], "ts": int(A["ts"][-1]), "px": round(float(c[-1]), 4),
                            "sides": {k: {kk: (None if vv is None else round(vv, 4)) for kk, vv in sd.items()} for k, sd in s["sides"].items()},
                            "shape": s["shape"]})
        # new breakouts on the last few candles (each judged with only the candles up to it)
        for i in range(max(WIN // 2, n - cfg["recent"]), n):
            for s in detect_at(D, A, i, tf, forming=False):
                cid = f"{sym}|{tf}|{s['fam']}|{s['name']}|{s['dir']}|{int(A['ts'][i])}"
                if cid in prev_conf:
                    continue
                entry = float(c[i])                               # the breakout close (the backtest buys the next open)
                lv = trade_levels(entry, s["dir"], s["stop"], s["target"])
                if not lv:
                    continue
                prev_conf[cid] = {"id": cid, "t": sym, "tf": tf, "fam": s["fam"], "name": s["name"], "dir": s["dir"],
                                  "ts": int(A["ts"][i]), "entry": round(entry, 4), "level": round(s["level"], 4),
                                  "stop": round(s["stop"], 4), "target": round(lv[0], 4), "tr": lv[1], "shape": s["shape"],
                                  "found": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        # where every confirmed one of this stock / timeframe stands now
        for cid, x in prev_conf.items():
            if x["t"] != sym or x["tf"] != tf:
                continue
            k = int(np.searchsorted(A["ts"], x["ts"]))
            if k >= n or int(A["ts"][k]) != x["ts"]:
                continue
            long = x["dir"] == "LONG"
            if k + 1 >= n:
                j, out, why = k, c[k], "open"
            else:
                j, out, why = walk(A, k + 1, x["entry"], long, x["stop"], x["target"], cfg["hold"], first_open=False)
            ret = ((out / x["entry"] - 1) if long else (x["entry"] / out - 1)) * 100
            x.update(px=round(float(c[-1]), 4), exit=round(float(out), 4), why=why, tx=int(A["ts"][j]), ret=round(float(ret), 3),
                     bars=int(n - 1 - k))
            confirmed.append(x)
    return forming, confirmed, last_px


def market_window(now: datetime) -> bool:
    """09:45-16:50 New York on weekdays: the first 15-minute candle is in, and the daily candle after the close."""
    ny = now.astimezone(NY)
    m = ny.hour * 60 + ny.minute
    return ny.weekday() < 5 and 585 <= m <= 1010


def run_live(demo: bool, force: bool = False):
    now = datetime.now(timezone.utc)
    if not demo and not force and not market_window(now):
        print("US market closed: nothing new to scan (use --force to run anyway)")
        return
    end = now - timedelta(seconds=DELAY)
    e_ny = end.astimezone(NY)
    data_end = int(datetime(e_ny.year, e_ny.month, e_ny.day, e_ny.hour, e_ny.minute, tzinfo=timezone.utc).timestamp())
    syms = U.tickers()
    if demo:
        syms = syms[:15]
        data = {s: demo_rows(s) for s in syms}
        data_end = max(r[0] for d in data.values() for r in d["15Min"]) + 900
    else:
        data = {s: {} for s in syms}
        for size, days in LIVE_DAYS.items():
            cache = load_cache(size)
            start = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
            got = fetch(syms, size, start, end, cache)
            # the cache is shared by both lists (the S&P 100 is part of the S&P 500): keep the longest window
            save_cache(size, cache, int((now - timedelta(days=days + 5)).timestamp()))
            for s, rows in got.items():
                data[s][size] = rows
    prev = {}
    try:
        old = json.loads(OUT.read_text())
        prev = {x["id"]: x for x in old.get("confirmed") or [] if not old.get("demo") or demo}
    except Exception:
        pass
    # drop confirmed ones older than their timeframe's keep window
    prev = {k: x for k, x in prev.items() if data_end - x["ts"] <= TFS[x["tf"]]["keep"]}
    forming, confirmed = [], []
    t0 = time.time()
    for k, s in enumerate(syms, 1):
        if not data.get(s):
            continue
        try:
            f, c, _ = scan_stock(s, data[s], data_end, prev)
        except Exception as e:                                    # noqa: BLE001
            print(f"  ! {s}: {e}", flush=True)
            continue
        forming += f
        confirmed += c
        if k % 50 == 0:
            print(f"  scanned {k}/{len(syms)} ({time.time() - t0:.0f}s)", flush=True)
    # a confirmed pattern whose candles fell out of this run's data keeps its last state
    have = {x["id"] for x in confirmed}
    confirmed += [x for k, x in prev.items() if k not in have and x.get("why")]
    confirmed.sort(key=lambda x: (-x["ts"], x["t"]))
    forming.sort(key=lambda x: (x["t"], x["tf"]))
    rules = {}
    for x in forming + confirmed:
        rules.setdefault(f"{x['fam']}|{x['name']}", rules_for(x["fam"], x["name"]))
    counts = {tf: {"forming": sum(x["tf"] == tf for x in forming), "confirmed": sum(x["tf"] == tf for x in confirmed)} for tf in TFS}
    out = {"as_of": now.isoformat(timespec="seconds"), "universe": U.label, "demo": demo,
           "data_end": datetime.fromtimestamp(data_end, timezone.utc).strftime("%Y-%m-%d %H:%M") + " New York",
           "tfs": {tf: {"label": c["label"], "hold": c["hold"], "keep_days": c["keep"] // 86400} for tf, c in TFS.items()},
           "fee_pct": FEE * 100, "rules": rules, "counts": counts, "forming": forming, "confirmed": confirmed}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print(f"{U.label}: {len(forming)} forming, {len(confirmed)} confirmed "
          f"({', '.join(f'{tf} {v['forming']}/{v['confirmed']}' for tf, v in counts.items())}) -> {OUT}")


# ===================================================================== demo candles
def demo_rows(sym: str, long: bool = False) -> dict:
    """Random-walk bars with some swings, so the detectors find things (no keys needed)."""
    rng = np.random.default_rng(sum(ord(ch) * (k + 1) for k, ch in enumerate(sym)))
    out = {}
    days = 700 if long else 240
    start = datetime(2026, 11, 6, tzinfo=timezone.utc) - timedelta(days=days)
    for size, step in (("15Min", 900), ("30Min", 1800), ("1Day", 86400)):
        rows, px = [], 100 + rng.random() * 200
        d = start
        while d <= datetime(2026, 11, 6, tzinfo=timezone.utc):
            if d.weekday() < 5:
                base = int(d.timestamp())
                times = [base] if size == "1Day" else [base + 570 * 60 + k * step for k in range((390 * 60) // step)]
                for t in times:
                    drift = 0.0015 * np.sin(t / (86400 * 9))
                    r = rng.normal(drift, 0.01 if size == "1Day" else 0.003)
                    o = px
                    px = max(1.0, px * (1 + r))
                    hi, lo = max(o, px) * (1 + abs(rng.normal(0, .002))), min(o, px) * (1 - abs(rng.normal(0, .002)))
                    rows.append([t, round(o, 2), round(hi, 2), round(lo, 2), round(px, 2), float(rng.integers(1e5, 1e6))])
            d += timedelta(days=1)
        out[size] = rows
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stats", choices=list(TFS), help="weekly backtest for the win rates, one timeframe")
    ap.add_argument("--budget", type=float, default=80, help="--stats: minutes of backtesting before keeping what's done")
    ap.add_argument("--merge-stats", action="store_true", help="merge the timeframes' backtests into the stats file")
    ap.add_argument("--demo", action="store_true", help="made-up candles, no keys")
    ap.add_argument("--force", action="store_true", help="scan even when the US market is closed")
    a = ap.parse_args()
    if a.merge_stats:
        merge_stats()
        return
    if not a.demo and not os.environ.get("ALPACA_KEY_ID"):
        sys.exit("ALPACA_KEY_ID / ALPACA_SECRET_KEY missing")
    if a.stats:
        run_stats(a.demo, a.stats, a.budget)
    else:
        run_live(a.demo, a.force)


if __name__ == "__main__":
    main()
