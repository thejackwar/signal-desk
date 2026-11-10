"""Premarket Momentum: if a stock is climbing in the pre-market, buy it when the New York market opens.

The idea (Giacomo, Nov 2026):
  - Watch the pre-market (04:00-09:30 New York).
  - If the stock is on an upward trend there, enter when the market opens.
  - Play with how much the pre-market has to be up before we enter.
  - Play with entering only if the stock has been going up "for a bit" (the pre-market climb has lasted,
    and/or the stock is in an uptrend on the daily chart).

Exact rules tested here (every one is a switch, see VARIANTS):
  Universe    S&P 500 (default) or S&P 100, current members; 20-day average dollar volume >= $10M.
  Pre-market  gain = last pre-market price (before 09:30) vs yesterday's close. Must be at least
              0.5%, 1% (default), 2%, 3% or 5%.
  Cap         "none" (default) or "6": skip gains above 6% (huge gaps are mostly earnings / big news).
  Climbing    how long the pre-market has been going up:
                "any"    no condition
                "30m"    last pre-market price above the price 30 minutes earlier (09:00) (default)
                "60m"    ... above the price 60 minutes earlier (08:30) and above the pre-market VWAP
                "120m"   ... above the price 2 hours earlier (07:30) and above the pre-market VWAP
                "steady" every 15-minute checkpoint from 08:30 to 09:30 higher than the one before
              (no trade before a checkpoint = yesterday's close is used)
  PM volume   pre-market dollar volume: "any", at least $1M (default) or at least $5M (thin pre-markets
              move on a handful of trades).
  Daily trend as of yesterday's close: "any", "20" close > SMA20 (default), "50" close > SMA50,
              "50_200" close > SMA50 > SMA200, "up5" close above the close 5 days earlier.
  Entry       "open": buy at the 09:30 open (default); "5m": only if the first 5-minute candle closes green
              (above its open) - buy at its close, 09:35.
  Stop        1%, 2% (default), 3% under the entry, or "none".
  Target      1%, 2%, 3%, 5% (default) above the entry, or "none".
  Time exit   whatever is still open is sold at 10:30 (default), 12:00 or the 15:55 close.
  Side        "long" (the strategy). "short" is the same signal traded the other way, shown only as a
              check: a classic finding is that big pre-market gaps fade after the open.
  Exit rules  stop first if a bar hits both (cautious); a gap through a level fills at the bar's open.
  Costs       0.03% a side.
  Account     each day up to 5 trades (the biggest pre-market gains first), 20% of the account each,
              no leverage - comparable with holding SPY.

Data: Alpaca SIP daily bars (filters) and 5-minute bars incl. pre-market, split/dividend adjusted, from 2021.
Current index members only (survivorship bias). Only days whose official open was at least 0.4% above
yesterday's close are downloaded at 5 minutes (a +0.5% pre-market almost always opens up too).

    python engine/premarket_momentum.py           # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/premarket_momentum.py --demo    # synthetic data, writes /tmp/premarket-demo.json
Output: docs/data/premarket/backtest.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from array import array
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from open_breakout import NY, WARMUP, daily_table, demo_daily, fetch, load_tickers, ny  # noqa: E402

import board_series as BS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "data" / "premarket"
FROM = "2021-01-04"
COST = 0.0003
MAX_POS = 5
OPEN_MIN = 1.004

# default = the best set-up of the trend follow-up that held up on both 2021-2023 and the unseen 2024+
DEFAULT = {"univ": "sp500", "pm": "1", "cap": "none", "climb": "30m", "pmvol": "1m", "trend": "20",
           "entry": "open", "stop": "2", "tp": "5", "exit": "1030", "side": "long"}
# the first version (no daily trend, out at the close): kept on the page to show what the trend filter adds
PLAIN = {"univ": "sp500", "pm": "2", "cap": "none", "climb": "60m", "pmvol": "1m", "trend": "any",
         "entry": "open", "stop": "2", "tp": "3", "exit": "eod", "side": "long"}
VARIANTS = {
    "univ": ["sp100", "sp500"],
    "pm": ["0.5", "1", "2", "3", "5"],
    "cap": ["none", "6"],
    "climb": ["any", "30m", "60m", "120m", "steady"],
    "pmvol": ["any", "1m", "5m"],
    "trend": ["any", "20", "50", "50_200", "up5"],
    "entry": ["open", "5m"],
    "stop": ["1", "2", "3", "none"],
    "tp": ["1", "2", "3", "5", "none"],
    "exit": ["1030", "1200", "eod"],
    "side": ["long", "short"],
}
EXIT_MIN = {"1030": 625, "1200": 715, "eod": 955}   # start minute of the bar whose close we sell at


# ===================================================================== data
def build_candidates(tab: dict, daily: dict, sp100: set[str], demo=None) -> list[dict]:
    closes = {s: {b["t"][:10]: b["c"] for b in bars} for s, bars in daily.items()}
    dates = {s: [b["t"][:10] for b in bars] for s, bars in daily.items()}
    by_day: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for s, rows in tab.items():
        idx = {d: i for i, d in enumerate(dates[s])}
        for r in rows:
            if r["d"] >= FROM and r["usd"] >= 10e6 and r["o"] >= OPEN_MIN * r["pc"]:
                i = idx[r["d"]]
                r["c5"] = closes[s][dates[s][i - 6]] if i >= 6 else None   # close 5 sessions before yesterday's
                by_day[r["d"]].append((s, r))
    days = sorted(by_day)
    print(f"{sum(map(len, by_day.values()))} stock-days to check at 5 minutes over {len(days)} sessions", flush=True)
    cands = []
    for k, d in enumerate(days):
        items = by_day[d]
        if demo:
            intr = demo(d, items)
        else:
            start = datetime.fromisoformat(d + "T04:00").replace(tzinfo=NY).astimezone(timezone.utc)
            end = datetime.fromisoformat(d + "T16:00").replace(tzinfo=NY).astimezone(timezone.utc)
            syms, intr = [s for s, _ in items], {}
            for j in range(0, len(syms), 100):
                intr.update(fetch(syms[j:j + 100], "5Min", start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                  end.strftime("%Y-%m-%dT%H:%M:%SZ")))
        for s, r in items:
            c = facts(s, d, r, intr.get(s) or [])
            if c:
                c["sp100"] = s in sp100
                cands.append(c)
        if k % 100 == 0:
            print(f"  {d}: {k + 1}/{len(days)} sessions, {len(cands)} candidates", flush=True)
    return cands


def facts(s: str, d: str, r: dict, bars: list[dict]) -> dict | None:
    pm, today = [], []
    for b in bars:
        t = ny(b["t"])
        if t.date().isoformat() != d:
            continue
        m = t.hour * 60 + t.minute
        if 240 <= m < 570:
            pm.append((m, b))
        elif 570 <= m < 960:
            today.append((m, b))
    if not pm or not today or today[0][0] != 570:
        return None
    pc = r["pc"]
    last = pm[-1][1]["c"]
    gain = last / pc - 1
    if gain < 0.005:
        return None

    def at(minute: int) -> float:          # last pre-market price known at `minute`
        px = pc
        for m, b in pm:
            if m + 5 <= minute:
                px = b["c"]
            else:
                break
        return px

    usd = sum(b["v"] * b["c"] for _, b in pm)
    vol = sum(b["v"] for _, b in pm)
    vwap = sum(b["v"] * (b["h"] + b["l"] + b["c"]) / 3 for _, b in pm) / vol if vol else last
    chk = [at(m) for m in (510, 525, 540, 555, 570)]
    climb = {"any": True, "30m": last > at(540), "60m": last > at(510) and last >= vwap,
             "120m": last > at(450) and last >= vwap, "steady": all(b > a for a, b in zip(chk, chk[1:]))}
    pcl = r["pc"]
    trend = {"any": True, "20": pcl > r["s20"], "50": pcl > r["s50"], "50_200": pcl > r["s50"] > r["s200"],
             "up5": r["c5"] is not None and pcl > r["c5"]}
    ohlc = array("f")
    for m, b in today:
        if m > 955:
            break
        ohlc.extend((b["o"], b["h"], b["l"], b["c"], m))
    return {"s": s, "d": d, "pc": pc, "gain": gain, "usd": usd, "climb": climb, "trend": trend,
            "pm_high": max(b["h"] for _, b in pm), "pm_low": min(b["l"] for _, b in pm), "pm_last": last,
            "bars": ohlc}


# ===================================================================== rules
def passes(c: dict, v: dict) -> bool:
    if v["univ"] == "sp100" and not c["sp100"]:
        return False
    if c["gain"] < float(v["pm"]) / 100:
        return False
    if v["cap"] != "none" and c["gain"] > float(v["cap"]) / 100:
        return False
    if not c["climb"][v["climb"]] or not c["trend"][v["trend"]]:
        return False
    if v["pmvol"] == "1m" and c["usd"] < 1e6 or v["pmvol"] == "5m" and c["usd"] < 5e6:
        return False
    if v["entry"] == "5m":
        b = c["bars"]
        if b[3] <= b[0]:                    # first candle must close green
            return False
    return True


def trade(c: dict, v: dict) -> dict:
    b, n = c["bars"], len(c["bars"]) // 5
    long = v["side"] == "long"
    sgn = 1 if long else -1
    if v["entry"] == "open":
        entry, i0 = b[0], 0
    else:
        entry, i0 = b[3], 1
    stop = None if v["stop"] == "none" else entry * (1 - sgn * float(v["stop"]) / 100)
    tgt = None if v["tp"] == "none" else entry * (1 + sgn * float(v["tp"]) / 100)
    last_m = EXIT_MIN[v["exit"]]
    px, why, at = None, "time", None
    for i in range(i0, n):
        o, h, l, cl, m = b[5 * i:5 * i + 5]
        if long:
            if stop is not None and l <= stop:
                px, why = min(o, stop), "stop"
            elif tgt is not None and h >= tgt:
                px, why = max(o, tgt), "target"
        else:
            if stop is not None and h >= stop:
                px, why = max(o, stop), "stop"
            elif tgt is not None and l <= tgt:
                px, why = min(o, tgt), "target"
        if px is None and m >= last_m:
            px = cl
        if px is not None:
            m_out = m + 5 if why == "time" else m
            at = f"{int(m_out) // 60:02d}:{int(m_out) % 60:02d}"
            break
    if px is None:                          # half-day session: out at the last bar
        px, at = (b[5 * (n - 1) + 3] if n > i0 else entry), "close"
    ret = sgn * (px / entry - 1) - 2 * v.get("cost", COST)
    risk = abs(entry - stop) if stop else entry * 0.02
    return {"s": c["s"], "d": c["d"], "pc": round(c["pc"], 2), "gain": round(c["gain"] * 100, 2),
            "in": round(entry, 2), "sl": round(stop, 2) if stop else None, "tp": round(tgt, 2) if tgt else None,
            "out": round(px, 2), "why": why, "at": at, "pct": round(ret * 100, 3),
            "r": round(sgn * (px - entry) / risk, 2), "rv": c["gain"]}


def stats(ts: list[dict], spy: dict[str, float], lev: float = 1.0) -> dict:
    if not ts:
        return {"n": 0}
    w = [t for t in ts if t["pct"] > 0]
    gain, loss = sum(t["pct"] for t in w), -sum(t["pct"] for t in ts if t["pct"] <= 0)
    days: dict[str, list[dict]] = defaultdict(list)
    for t in ts:
        days[t["d"]].append(t)
    eq, peak, dd, curve, yearly, worst = 1.0, 1.0, 0.0, [], defaultdict(lambda: 1.0), 0.0
    daily = []
    for d in sorted(days):
        take = sorted(days[d], key=lambda t: -t["rv"])[:MAX_POS]
        r = lev * sum(t["pct"] for t in take) / 100 / MAX_POS
        worst = min(worst, r)
        eq *= max(0.0, 1 + r); yearly[d[:4]] *= max(0.0, 1 + r)
        peak = max(peak, eq); dd = min(dd, eq / peak - 1)
        curve.append([d, round(eq, 4)])
        daily.append([d, r])
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
            "pf": round(gain / loss, 2) if loss else None,
            "exits": {k: sum(t["why"] == k for t in ts) for k in ("target", "stop", "time")},
            "acct_total": round((eq - 1) * 100, 1), "acct_cagr": round((eq ** (1 / yrs) - 1) * 100, 1),
            "acct_maxdd": round(dd * 100, 1), "worst_day": round(worst * 100, 1), "by_year": by_year,
            "_daily": daily, "curve": curve[::max(1, len(curve) // 300)] + curve[-1:]}


def run(cands: list[dict], v: dict) -> list[dict]:
    return [trade(c, v) for c in cands if passes(c, v)]


KEYS = ("n", "win", "avg", "pf", "acct_total", "acct_cagr", "acct_maxdd")


def row(v: dict, st: dict, keys: tuple) -> dict:
    return {**{k: v[k] for k in keys}, **{k: st.get(k) for k in KEYS}}


# ===================================================================== demo data
def demo_intraday(d: str, items: list) -> dict:
    rnd, out = random.Random(d), {}
    for s, r in items:
        dt = datetime.fromisoformat(d + "T04:00").replace(tzinfo=NY)
        px, bars = r["pc"], []
        drift = (r["o"] / r["pc"] - 1) / 66
        for i in range(144):
            o = px
            c = o * (1 + (drift if i < 66 else 0) + rnd.gauss(0, 0.002 if i >= 66 else 0.0008))
            bars.append({"t": (dt + timedelta(minutes=5 * i)).isoformat(), "o": o, "h": max(o, c) * 1.001,
                         "l": min(o, c) * 0.999, "c": c, "v": 5e4 if i < 66 else 2e5})
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
        spy_rows = daily[syms[0]]
    else:
        today = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        daily = {}
        for j in range(0, len(syms), 100):
            daily.update(fetch(syms[j:j + 100], "1Day", WARMUP + "T00:00:00Z", today))
        spy_rows = fetch(["SPY"], "1Day", WARMUP + "T00:00:00Z", today)["SPY"]
        print(f"daily bars for {len(daily)} of {len(syms)} symbols ({time.time() - t0:.0f}s)", flush=True)
    tab = daily_table(daily)
    spy = {}
    for y in sorted({b["t"][:4] for b in spy_rows if b["t"][:10] >= FROM}):
        yb = [b for b in spy_rows if b["t"][:4] == y]
        prev = [b for b in spy_rows if b["t"][:4] < y]
        spy[y] = round((yb[-1]["c"] / (prev[-1]["c"] if prev else yb[0]["o"]) - 1) * 100, 1)
    first = [b for b in spy_rows if b["t"][:10] < FROM]
    spy_total = round((spy_rows[-1]["c"] / (first[-1]["c"] if first else spy_rows[0]["o"]) - 1) * 100, 1)

    cands = build_candidates(tab, daily, sp100, demo_intraday if a.demo else None)
    print(f"{len(cands)} pre-market risers ({time.time() - t0:.0f}s)", flush=True)

    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "from": FROM,
           "rules": __doc__.split("Exact rules tested here")[1].split("Data:")[0].strip(),
           "default": DEFAULT, "variants": VARIANTS, "spy_by_year": spy, "spy_total": spy_total,
           "costs_pct_a_side": COST * 100, "max_positions": MAX_POS}
    base = run(cands, DEFAULT)
    res["base"] = stats(base, spy)
    res["base_trades_recent"] = [{k: t[k] for k in t if k != "rv"} for t in sorted(base, key=lambda t: t["d"])[-300:]]

    st = stats(run(cands, PLAIN), spy); st.pop("curve", None)
    res["plain"] = {"v": PLAIN, **st}
    # does the edge survive worse fills at the open? (0.03% a side is the default)
    res["cost_check"] = {}
    for cst in (0.0, 0.0003, 0.0006, 0.001):
        st = stats(run(cands, {**DEFAULT, "cost": cst}), spy); st.pop("curve", None)
        res["cost_check"][f"{cst * 100:.2f}"] = {k: st.get(k) for k in KEYS}
    # leverage (CFD-style, intraday only so no overnight financing): every day's account move times L,
    # costs scale with it because they are paid on the bigger position
    res["leverage"] = {}
    for cst in (0.0003, 0.0006):
        ts_ = run(cands, {**DEFAULT, "cost": cst})
        for lv in (1, 2, 3, 5):
            st = stats(ts_, spy, lv)
            res["leverage"][f"{lv}x@{cst * 100:.2f}"] = {**{k: st.get(k) for k in KEYS}, "worst_day": st.get("worst_day"),
                                                       "by_year": {y: o["acct"] for y, o in st["by_year"].items()}}
    sc = []
    prev_c = None
    for b_ in spy_rows:
        if b_["t"][:10] < FROM:
            prev_c = b_["c"]; continue
        sc.append([b_["t"][:10], round(b_["c"] / prev_c, 4)])
    res["spy_curve"] = sc[::max(1, len(sc) // 150)] + sc[-1:]

    res["sweeps"] = {}                      # one switch at a time around the default
    for k, opts in VARIANTS.items():
        res["sweeps"][k] = {}
        for o in opts:
            st = stats(run(cands, {**DEFAULT, k: o}), spy)
            st.pop("curve", None)
            res["sweeps"][k][o] = st
    print(f"sweeps done ({time.time() - t0:.0f}s)", flush=True)

    # how much up, and for how long: pre-market gain x climbing x daily trend, at the default exits
    res["entry_grid"] = []
    for univ in VARIANTS["univ"]:
        for pm in VARIANTS["pm"]:
            for climb in VARIANTS["climb"]:
                for tr in VARIANTS["trend"]:
                    v = {**DEFAULT, "univ": univ, "pm": pm, "climb": climb, "trend": tr}
                    res["entry_grid"].append(row(v, stats(run(cands, v), spy), ("univ", "pm", "climb", "trend")))
    print(f"entry grid done ({time.time() - t0:.0f}s)", flush=True)

    # exits: stop x target x time exit, both entries, both sides
    res["exit_grid"] = []
    for side in VARIANTS["side"]:
        for entry in VARIANTS["entry"]:
            for stop in VARIANTS["stop"]:
                for tp in VARIANTS["tp"]:
                    for ex in VARIANTS["exit"]:
                        v = {**DEFAULT, "side": side, "entry": entry, "stop": stop, "tp": tp, "exit": ex}
                        res["exit_grid"].append(row(v, stats(run(cands, v), spy),
                                                    ("side", "entry", "stop", "tp", "exit")))
    print(f"exit grid done ({time.time() - t0:.0f}s)", flush=True)

    # best long set-ups on 2021-2023, then checked unseen on 2024+ (to see if the winner was luck)
    def split(v: dict) -> tuple[dict, dict]:
        ts = run(cands, v)
        a_ = stats([t for t in ts if t["d"] < "2024-01-01"], spy)
        b_ = stats([t for t in ts if t["d"] >= "2024-01-01"], spy)
        return a_, b_
    tried = []
    for pm in VARIANTS["pm"]:
        for climb in VARIANTS["climb"]:
            for tr in ("any", "50", "50_200"):
                for entry in VARIANTS["entry"]:
                    for stop, tp in (("1", "2"), ("2", "3"), ("2", "none"), ("3", "5"), ("none", "none")):
                        for ex in VARIANTS["exit"]:
                            v = {**DEFAULT, "pm": pm, "climb": climb, "trend": tr, "entry": entry,
                                 "stop": stop, "tp": tp, "exit": ex}
                            ins, oos = split(v)
                            if ins.get("n", 0) >= 100:
                                tried.append((ins.get("avg", -9), v, ins, oos))
    tried.sort(key=lambda x: -x[0])
    cut = lambda st: {k: st.get(k) for k in KEYS}
    res["walk_forward"] = {"tried": len(tried), "train": "2021-2023", "test": "2024 onwards",
                           "top": [{"v": v, "train": cut(i), "test": cut(o)} for _, v, i, o in tried[:15]]}
    print(f"walk-forward: {len(tried)} set-ups ({time.time() - t0:.0f}s)", flush=True)

    # follow-up (Nov 2026): the daily-trend set-ups, with earlier exits, checked year by year and
    # on 2021-2023 vs the unseen 2024+ - is the trend filter real or luck?
    res["trend_check"] = []
    for pm in ("0.5", "1", "2"):
        for climb in ("30m", "60m", "120m"):
            for tr in ("20", "50", "up5"):
                for stop, tp, ex in (("2", "3", "eod"), ("2", "3", "1030"), ("2", "5", "1030"),
                                     ("2", "none", "1030"), ("1", "2", "1030"), ("2", "none", "1200")):
                    v = {**DEFAULT, "pm": pm, "climb": climb, "trend": tr, "stop": stop, "tp": tp, "exit": ex}
                    ts = run(cands, v)
                    full = stats(ts, spy)
                    tr_ = stats([t for t in ts if t["d"] < "2024-01-01"], spy)
                    te_ = stats([t for t in ts if t["d"] >= "2024-01-01"], spy)
                    res["trend_check"].append({
                        "v": {k: v[k] for k in ("pm", "climb", "trend", "stop", "tp", "exit")},
                        "curve": (full.get("curve") or [])[::2] + (full.get("curve") or [])[-1:],
                        "recent": [{k: t[k] for k in ("s", "d", "gain", "in", "out", "why", "at", "pct")}
                                   for t in sorted(ts, key=lambda t: t["d"])[-40:]],
                        "full": {k: full.get(k) for k in KEYS},
                        "by_year": {y: o["acct"] for y, o in (full.get("by_year") or {}).items()},
                        "train": {k: tr_.get(k) for k in KEYS}, "test": {k: te_.get(k) for k in KEYS}})
    print(f"trend check done ({time.time() - t0:.0f}s)", flush=True)

    out = Path("/tmp/premarket-demo.json") if a.demo else OUT_DIR / "backtest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    res["board"] = BS.pack((res.get("base") or {}).get("_daily") or [])
    BS.strip(res)
    out.write_text(json.dumps(res, separators=(",", ":")))
    b = res["base"]
    print(f"default: {b.get('n')} trades, win {b.get('win')}%, avg {b.get('avg')}%, PF {b.get('pf')}, "
          f"account {b.get('acct_total')}% (CAGR {b.get('acct_cagr')}%, max DD {b.get('acct_maxdd')}%) "
          f"vs SPY {spy_total}%  -> {out} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
