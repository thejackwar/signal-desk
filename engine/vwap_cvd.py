"""VWAP + CVD morning setups: backtest of the signals Giacomo wants as live Terminal alerts.

The goal (27 Nov 2026): from ~15:45-16:00 Italian time (15-30 minutes after the US open) get told when a
setup is forming on an S&P 100 stock, instead of scanning the market by hand. Before building the live
alert, this tests which setups actually work.

Exact rules tested here (signals on 5-minute candles built from 1-minute bars):
  Universe    S&P 100, current members, from 2021 (VWAP setups).
  CVD pilot   CVD needs every trade, so it is tested on 10 liquid S&P 100 stocks (PILOT) over the last
              months (CVD_FROM). Buy/sell is decided with the tick rule (trade above the previous price =
              buy, below = sell, same price = same side as before), the usual method when US stock data has
              no aggressor flag. CVD = running sum of buy volume - sell volume since 09:30.
  VWAP        session VWAP from 09:30: sum(bar vwap x volume) / sum(volume).
  Window      a signal can fire on a 5-minute candle closing between 09:45 and 11:30 New York
              (15:45-17:30 Italy). One trade per setup, per stock, per day (the first signal).
  Setups
    reclaim     LONG. At least 3 of the previous 6 candles closed under VWAP, the previous one too, and this
                one closes back above it. Stop just under the lowest low of the last 6 candles.
                CVD agrees if CVD rose over the last 3 candles.
    rejection   SHORT. The previous 2 candles closed under VWAP, this one reaches up to VWAP (high within
                0.05%) but closes under it. Stop just above the highest high of the last 3 candles.
                CVD agrees if CVD fell over the last 3 candles.
    divergence  (CVD pilot only) SHORT when a candle makes a new session high but CVD is lower than at the
                previous session high (set at least 3 candles earlier); LONG for the mirror at a new low.
                Stop just beyond the new extreme.
    drive       The opening drive, as a stand-in for "CVD as a filter on my open strategies": if the first
                15 minutes moved at least 0.5%, trade in that direction at the 09:45 close. Stop beyond the
                first 15 minutes' range. CVD agrees if CVD over those 15 minutes has the same sign.
  Filters     "all" (every signal), "cvd" (only when CVD agrees), "anti" (only when it disagrees) - the
              last two exist only for the pilot, which is also reported as "all" for a fair comparison.
  Target      1R, 2R (default) or 3R (R = entry to stop).
  Time exit   12:00 New York (default) or the 15:55 close.
  Exit rules  1-minute bars; stop first if one bar hits both (cautious); a gap through a level fills at the
              bar's open.
  Costs       0.03% a side.

Data: Alpaca SIP 1-minute bars (split adjusted) and SIP trades. The free plan has all of this except the last
15 minutes. Current S&P 100 members only (survivorship bias).

    python engine/vwap_cvd.py           # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/vwap_cvd.py --demo    # synthetic data, writes /tmp/vwapcvd-demo.json
Output: docs/data/vwapcvd/backtest.json and docs/data/vwapcvd/report.md
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "data" / "vwapcvd"
NY = ZoneInfo("America/New_York")
API = "https://data.alpaca.markets/v2/stocks/"
FROM = "2021-01-04"
CVD_FROM = "2026-06-01"
PILOT = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "GOOGL", "AMD", "AVGO", "NFLX"]
COST = 0.0003
TICK = 0.0005
SIG_FIRST, SIG_LAST = 585, 690          # 5-minute candle CLOSE times, minutes after midnight NY (09:45-11:30)
CVD_END = 720                          # trades are fetched 09:30-12:00
EXITS = {"1200": 720, "eod": 955}
TPS = ["1", "2", "3"]
SETUPS = ["reclaim", "rejection", "divergence", "drive"]
DEFAULT = {"tp": "2", "exit": "1200"}
# W avg price, Z/U/T out of sequence or extended, 4/7/9/C/G/H/P/V special, O/Q/X/5/6/M auction and cross
# prints (the opening cross is huge and has no buyer/seller side - it swamped CVD before this was excluded)
# Off-exchange prints (venue D: dark pools, retail wholesalers) are left out of CVD: retail buys filled at a
# sub-penny under the ask look like downticks, which dragged tick-rule CVD down all morning.
LIT_ONLY = os.environ.get("CVD_LIT_ONLY", "1") == "1"
SKIP_COND = {"W", "Z", "4", "7", "9", "C", "G", "H", "P", "T", "U", "V", "O", "Q", "X", "5", "6", "M"}


# ===================================================================== data
class Limiter:
    """Free Alpaca plan: 200 requests a minute. Stay a little under it across threads."""
    def __init__(self, per_min: int):
        self.gap, self.next, self.lock = 60.0 / per_min, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.gap
        time.sleep(max(0.0, t - now))


LIMIT = Limiter(int(os.environ.get("ALPACA_RPM", "190")))


def _get(path: str, params: dict) -> dict:
    key, sec = os.environ["ALPACA_KEY_ID"], os.environ["ALPACA_SECRET_KEY"]
    req = urllib.request.Request(API + path + "?" + urllib.parse.urlencode(params),
                                 headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
    for attempt in range(8):
        LIMIT.wait()
        try:
            return json.loads(urllib.request.urlopen(req, timeout=90).read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            code = getattr(e, "code", 0)
            if code in (0, 429, 500, 502, 503, 504) and attempt < 7:
                time.sleep(4 * (attempt + 1))
                continue
            raise
    return {}


def paged(path: str, field: str, params: dict) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    params = dict(params)
    while True:
        js = _get(path, params)
        for s, rows in (js.get(field) or {}).items():
            out[s].extend(rows)
        if not js.get("next_page_token"):
            return out
        params["page_token"] = js["next_page_token"]


def utc(d: str, hhmm: str) -> str:
    return (datetime.fromisoformat(f"{d}T{hhmm}").replace(tzinfo=NY).astimezone(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"))


def minute(t: str) -> tuple[str, int]:
    x = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(NY)
    return x.date().isoformat(), x.hour * 60 + x.minute


def load_sp100() -> list[str]:
    return sorted(l.strip().replace("-", ".") for l in (ROOT / "engine" / "tickers.txt").read_text().splitlines()
                  if l.strip() and not l.startswith("#"))


def day_bars(syms: list[str], d: str) -> dict[str, list[tuple]]:
    """1-minute regular-session bars: (minute, o, h, l, c, v, vw)."""
    end = min(datetime.fromisoformat(utc(d, "16:00").replace("Z", "+00:00")),
              datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = paged("bars", "bars", {"symbols": ",".join(syms), "timeframe": "1Min", "start": utc(d, "09:30"),
                                  "end": end, "limit": "10000", "adjustment": "split", "feed": "sip"})
    out = {}
    for s, rows in raw.items():
        bars = []
        for b in rows:
            dd, m = minute(b["t"])
            if dd == d and 570 <= m < 960 and b.get("v"):
                bars.append((m, b["o"], b["h"], b["l"], b["c"], b["v"], b.get("vw") or b["c"]))
        out[s] = bars
    return out


def day_cvd(sym: str, d: str) -> dict[int, float]:
    """Per minute 09:30-12:00: buy volume - sell volume (tick rule)."""
    raw = paged("trades", "trades", {"symbols": sym, "start": utc(d, "09:30"), "end": utc(d, "12:00"),
                                     "limit": "10000", "feed": "sip"})
    delta: dict[int, float] = defaultdict(float)
    last, side = None, 0
    for tr in raw.get(sym, []):
        if SKIP_COND & set(tr.get("c") or []) or (LIT_ONLY and tr.get("x") == "D"):
            continue
        p, sz = tr["p"], tr["s"]
        if last is not None:
            if p > last:
                side = 1
            elif p < last:
                side = -1
        last = p
        if side:
            delta[minute(tr["t"])[1]] += side * sz
    return delta


def trading_days() -> list[str]:
    now = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = paged("bars", "bars", {"symbols": "SPY", "timeframe": "1Day", "start": FROM + "T00:00:00Z",
                                   "end": now, "limit": "10000", "adjustment": "split", "feed": "sip"})["SPY"]
    return [b["t"][:10] for b in rows]


# ===================================================================== signals
def five_min(bars: list[tuple], delta: dict[int, float] | None):
    """5-minute candles with VWAP and CVD at each close. Returns list of dicts + 1-minute bars."""
    pv = vol = cvd = 0.0
    out, cur = [], None
    for m, o, h, l, c, v, vw in bars:
        pv += vw * v
        vol += v
        if delta is not None:
            cvd += delta.get(m, 0.0)
        k = (m - 570) // 5
        if cur is None or cur["k"] != k:
            if cur:
                out.append(cur)
            cur = {"k": k, "o": o, "h": h, "l": l}
        cur["h"], cur["l"] = max(cur["h"], h), min(cur["l"], l)
        cur.update(c=c, vwap=pv / vol, cvd=cvd, end=570 + 5 * k + 5)
    if cur:
        out.append(cur)
    return out


def signals(B: list[dict], has_cvd: bool) -> list[dict]:
    """First signal of each setup: {setup, side, entry, stop, at (minute), cvd (True/False/None)}."""
    got: dict[str, dict] = {}

    def add(name, side, entry, stop, at, ok):
        if name in got:
            return
        if (side > 0 and stop >= entry) or (side < 0 and stop <= entry) or abs(entry - stop) < entry * 0.0005:
            return
        got[name] = {"setup": name, "side": side, "entry": entry, "stop": stop, "at": at,
                     "cvd": ok if has_cvd else None}

    for i, b in enumerate(B):
        if b["end"] > SIG_LAST:
            break
        if b["end"] < SIG_FIRST:
            continue
        # opening drive at the 09:45 close (i == 2)
        if b["end"] == 585 and i == 2 and B[0]["k"] == 0:
            mv = b["c"] / B[0]["o"] - 1
            if abs(mv) >= 0.005:
                side = 1 if mv > 0 else -1
                stop = (min(x["l"] for x in B[:3]) * (1 - TICK) if side > 0 else max(x["h"] for x in B[:3]) * (1 + TICK))
                add("drive", side, b["c"], stop, b["end"], (b["cvd"] > 0) == (side > 0))
        if i >= 6:
            prev = B[i - 6:i]
            slope = b["cvd"] - B[i - 3]["cvd"]
            if (b["c"] > b["vwap"] and B[i - 1]["c"] < B[i - 1]["vwap"]
                    and sum(x["c"] < x["vwap"] for x in prev) >= 3):
                add("reclaim", 1, b["c"], min(x["l"] for x in B[i - 5:i + 1]) * (1 - TICK), b["end"], slope > 0)
            if (b["c"] < b["vwap"] and b["h"] >= b["vwap"] * (1 - 0.0005)
                    and B[i - 1]["c"] < B[i - 1]["vwap"] and B[i - 2]["c"] < B[i - 2]["vwap"]):
                add("rejection", -1, b["c"], max(x["h"] for x in B[i - 2:i + 1]) * (1 + TICK), b["end"], slope < 0)
        if has_cvd and i >= 4:
            hs = [x["h"] for x in B[:i]]
            ls = [x["l"] for x in B[:i]]
            p = max(range(i), key=lambda j: hs[j])
            if b["h"] > hs[p] and p <= i - 3 and b["cvd"] < B[p]["cvd"] and b["c"] < b["h"]:
                add("divergence", -1, b["c"], b["h"] * (1 + TICK), b["end"], True)
            q = min(range(i), key=lambda j: ls[j])
            if b["l"] < ls[q] and q <= i - 3 and b["cvd"] > B[q]["cvd"] and b["c"] > b["l"]:
                add("divergence", 1, b["c"], b["l"] * (1 - TICK), b["end"], True)
    return list(got.values())


def simulate(sig: dict, bars: list[tuple], tp: str, ex: str) -> dict | None:
    e, s, side = sig["entry"], sig["stop"], sig["side"]
    risk = abs(e - s)
    tgt = e + side * float(tp) * risk
    last = EXITS[ex]
    out, why = None, "time"
    for m, o, h, l, c, v, vw in bars:
        if m < sig["at"]:
            continue
        if m >= last:
            out = o
            break
        hit_s = l <= s if side > 0 else h >= s
        hit_t = h >= tgt if side > 0 else l <= tgt
        if hit_s:
            gap = o < s if side > 0 else o > s
            out, why = (o if gap else s), "stop"
            break
        if hit_t:
            gap = o > tgt if side > 0 else o < tgt
            out, why = (o if gap else tgt), "target"
            break
        out = c
    if out is None:
        return None
    pct = side * (out / e - 1) - 2 * COST
    return {"r": round(side * (out - e) / risk, 3), "pct": round(pct * 100, 3), "why": why}


# ===================================================================== stats
def stats(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    pct = [r["pct"] for r in rows]
    rs = [r["r"] for r in rows]
    wins = [p for p in pct if p > 0]
    loss = [-p for p in pct if p <= 0]
    mean = sum(pct) / len(pct)
    sd = math.sqrt(sum((p - mean) ** 2 for p in pct) / max(1, len(pct) - 1))
    years = defaultdict(list)
    for r in rows:
        years[r["d"][:4]].append(r["pct"])
    return {"n": len(rows), "win": round(100 * len(wins) / len(pct), 1), "avg_pct": round(mean, 3),
            "avg_r": round(sum(rs) / len(rs), 3), "pf": round(sum(wins) / sum(loss), 2) if sum(loss) else None,
            "t": round(mean / (sd / math.sqrt(len(pct))), 2) if sd else None,
            "by_year": {y: {"n": len(v), "avg_pct": round(sum(v) / len(v), 3)} for y, v in sorted(years.items())}}


# ===================================================================== demo
def demo_bars(syms, d):
    rnd = random.Random(hash((d, "b")))
    out = {}
    for s in syms:
        p, bars = 100 + rnd.random() * 50, []
        for m in range(570, 960):
            o = p
            p *= 1 + rnd.gauss(0, 0.0012)
            h, l = max(o, p) * (1 + abs(rnd.gauss(0, 0.0004))), min(o, p) * (1 - abs(rnd.gauss(0, 0.0004)))
            bars.append((m, o, h, l, p, rnd.randint(1000, 50000), (o + p) / 2))
        out[s] = bars
    return out


def demo_cvd(bars):
    rnd = random.Random(len(bars))
    return {m: (c - o) * 1e5 + rnd.gauss(0, 2000) for m, o, h, l, c, v, vw in bars if m < CVD_END}


# ===================================================================== main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    syms = load_sp100()
    if a.demo:
        syms = syms[:12]
        days = [(datetime(2026, 1, 5) + timedelta(days=k)).date().isoformat() for k in range(0, 300)]
        days = [d for d in days if datetime.fromisoformat(d).weekday() < 5]
        pilot = [s for s in PILOT if s in syms] or syms[:3]
        cvd_from = days[len(days) // 2]
    else:
        days, pilot, cvd_from = trading_days(), [s for s in PILOT if s in syms], CVD_FROM
    print(f"{len(syms)} stocks, {len(days)} sessions, CVD pilot {pilot} from {cvd_from}", flush=True)

    trades: list[dict] = []
    pool = ThreadPoolExecutor(max_workers=6)
    budget = float(os.environ.get("MAX_SECONDS", "19800"))      # stop early (newest days go first) before the
    done_from = days[-1] if days else FROM                       # GitHub job limit, and publish what is done
    for k, d in enumerate(reversed(days)):
        if time.time() - t0 > budget:
            print(f"  time budget reached - results cover {d} onwards only", flush=True)
            break
        done_from = d
        try:
            bars = demo_bars(syms, d) if a.demo else {}
            if not a.demo:
                for j in range(0, len(syms), 100):
                    bars.update(day_bars(syms[j:j + 100], d))
            cvds = {}
            if d >= cvd_from:
                if a.demo:
                    cvds = {s: demo_cvd(bars.get(s, [])) for s in pilot}
                else:
                    futs = {s: pool.submit(day_cvd, s, d) for s in pilot if bars.get(s)}
                    cvds = {s: f.result() for s, f in futs.items()}
        except Exception as e:
            print(f"  {d}: skipped ({e})", flush=True)
            continue
        for s, bb in bars.items():
            if len(bb) < 60 or bb[0][0] != 570:
                continue
            has = s in cvds
            B = five_min(bb, cvds.get(s))
            for sig in signals(B, has):
                row = {"s": s, "d": d, "setup": sig["setup"], "side": sig["side"], "at": sig["at"],
                       "pilot": has, "cvd": sig["cvd"], "res": {}}
                for tp in TPS:
                    for ex in EXITS:
                        r = simulate(sig, bb, tp, ex)
                        if r:
                            row["res"][f"{tp}|{ex}"] = r
                if row["res"]:
                    trades.append(row)
        if k % 50 == 0:
            print(f"  {d}: {k + 1}/{len(days)} sessions, {len(trades)} signals ({time.time() - t0:.0f}s)", flush=True)
    pool.shutdown()

    def pick(rows, key):
        return [{"d": r["d"], **r["res"][key]} for r in rows if key in r["res"]]

    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "from": done_from,
           "cvd_from": cvd_from, "pilot": pilot, "default": DEFAULT, "tps": TPS, "exits": list(EXITS),
           "costs_pct_a_side": COST * 100,
           "rules": __doc__.split("Exact rules tested here")[1].split("Data:")[0].strip(), "setups": {}}
    lines = [f"# VWAP + CVD morning setups - backtest ({res['generated']})", "",
             f"S&P 100 from {done_from}; CVD pilot {', '.join(pilot)} from {cvd_from}. "
             f"Default: {DEFAULT['tp']}R target, exit {DEFAULT['exit']} NY. Costs 0.03% a side.", "",
             "| Setup | Scope | Filter | Trades | Win % | Avg % | Avg R | PF | t |", "|---|---|---|---|---|---|---|---|---|"]
    dkey = f"{DEFAULT['tp']}|{DEFAULT['exit']}"
    for st in SETUPS:
        rows = [r for r in trades if r["setup"] == st]
        groups = {"full|all": rows,
                  "pilot|all": [r for r in rows if r["pilot"]],
                  "pilot|cvd": [r for r in rows if r["pilot"] and r["cvd"] is True],
                  "pilot|anti": [r for r in rows if r["pilot"] and r["cvd"] is False]}
        if st == "divergence":
            groups = {"pilot|all": groups["pilot|all"]}
        res["setups"][st] = {}
        for g, rr in groups.items():
            res["setups"][st][g] = {key: stats(pick(rr, key)) for key in (f"{tp}|{ex}" for tp in TPS for ex in EXITS)}
            for side, nm in ((1, "long"), (-1, "short")):
                res["setups"][st][g][f"{dkey}|{nm}"] = stats(pick([r for r in rr if r["side"] == side], dkey))
            s0 = res["setups"][st][g][dkey]
            if s0.get("n"):
                scope, flt = g.split("|")
                lines.append(f"| {st} | {scope} | {flt} | {s0['n']} | {s0['win']} | {s0['avg_pct']} | "
                             f"{s0['avg_r']} | {s0['pf']} | {s0['t']} |")
    lines += ["", "Full grids (every target/exit) and per-year results are in backtest.json."]
    res["recent"] = sorted(trades, key=lambda r: (r["d"], r["at"]))[-300:]
    out_dir = Path("/tmp") if a.demo else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    name = "vwapcvd-demo" if a.demo else "backtest"
    (out_dir / f"{name}.json").write_text(json.dumps(res, separators=(",", ":")))
    (out_dir / ("vwapcvd-demo.md" if a.demo else "report.md")).write_text("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    print(f"done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
