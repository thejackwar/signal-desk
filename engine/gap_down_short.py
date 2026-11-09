"""Gap-Down Short: short a stock that opens sharply lower when it breaks the opening range low.

The idea (Giacomo, 9 Nov 2026 - the NVIDIA "CEO probe" morning):
  - A stock gaps down hard at the open on bad news.
  - Don't short the gap itself: wait for the opening range (first 5 minutes) to form.
  - Short only if price breaks BELOW the opening range low; stop above the opening range high.
  - If price breaks ABOVE the opening range high first, the gap is filling: skip the trade.
  - Quick trade: get out within the first hour (or at a stop / target before that).
  On the day itself the first candle dropped to $170.30 and NVDA then filled back to $176 - no trade
  was taken. This backtest checks how that kind of morning usually plays out.

Exact rules tested here (every one is a switch, see VARIANTS):
  Universe    S&P 500 (default) or S&P 100, current members; 20-day average dollar volume >= $10M.
  Gap         today's official open vs yesterday's close: at least -2%, -3% (default), -5% or -8%.
  Cap         "none" (default) or "15": skip gaps worse than -15% (fraud / guidance disasters / halts).
  Context     as of yesterday's close: "any" (default), "below20" close < SMA20 (already beaten down,
              like NVDA), "below50" close < SMA50, "above50" close > SMA50 (gap in a healthy uptrend).
  Range       the opening range: "5m" first 5-minute candle (default) or "15m" first three candles.
  Entry       "break": sell stop just under the range low - fills when price trades through it, at the
              level (or at a bar's open if it gaps through) (default);
              "close": only once a 5-minute candle CLOSES under the range low - short at that close.
  Reclaim     "skip" (default): if price trades above the range high before the entry fires, the gap is
              filling and the trade is cancelled; "ignore": keep waiting anyway.
  Deadline    no new entry after 09:45 (default), 10:00 or 10:30 New York.
  Stop        "range": just above the opening range high (default); "1" or "2": 1% / 2% above entry.
  Target      1R, 1.5R, 2R (default), 3R (R = distance from entry to stop), or "none".
  Time exit   whatever is still open is closed at 10:30 (default - the "within the first hour" plan),
              12:00 or the 15:55 close.
  Side        "short" (the strategy). "long" is the mirror check - buy the break ABOVE the range high,
              i.e. bet on the gap filling (the thing that happened on 9 Nov).
  Exit rules  stop first if a bar hits both (cautious); a gap through a level fills at the bar's open.
              On the entry bar itself a "break" fill counts as stopped if that bar closes beyond the stop.
  Costs       0.03% a side (CFD spreads at the open are often wider: see cost_check).
  Account     each day up to 5 trades (the biggest gaps first), 20% of the account each, no leverage.
              A 5x CFD multiplies every number - including the drawdown.

Data: Alpaca SIP daily bars (filters) and 5-minute regular-session bars, split/dividend adjusted, from 2021.
Current index members only (survivorship bias: stocks that gapped down and later left the index are
missing, which probably flatters the short side). No news filter: every gap counts, news or not.

    python engine/gap_down_short.py           # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/gap_down_short.py --demo    # synthetic data, writes /tmp/gapdown-demo.json
Output: docs/data/gapdown/backtest.json
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

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "data" / "gapdown"
FROM = "2021-01-04"
COST = 0.0003
MAX_POS = 5
GAP_MIN = 0.02            # loosest gap we download intraday bars for
TICK = 0.0005             # stop / entry buffer beyond the range (0.05%)

DEFAULT = {"univ": "sp500", "gap": "3", "cap": "none", "ctx": "any", "rng": "5m", "entry": "break",
           "reclaim": "skip", "dl": "0945", "stop": "range", "tp": "2", "exit": "1030", "side": "short"}
VARIANTS = {
    "univ": ["sp100", "sp500"],
    "gap": ["2", "3", "5", "8"],
    "cap": ["none", "15"],
    "ctx": ["any", "below20", "below50", "above50"],
    "rng": ["5m", "15m"],
    "entry": ["break", "close"],
    "reclaim": ["skip", "ignore"],
    "dl": ["0945", "1000", "1030"],
    "stop": ["range", "1", "2"],
    "tp": ["1", "1.5", "2", "3", "none"],
    "exit": ["1030", "1200", "eod"],
    "side": ["short", "long"],
}
DL_MIN = {"0945": 585, "1000": 600, "1030": 630}      # entries only on bars starting before this minute
EXIT_MIN = {"1030": 625, "1200": 715, "eod": 955}     # start minute of the bar whose close we exit at
RNG_BARS = {"5m": 1, "15m": 3}
# exit presets shown on the page: stop | target | time exit
EXIT_PRESETS = ["range|2|1030", "range|1|1030", "range|none|1030", "range|2|1200", "1|2|1030", "range|none|eod"]
PAGE_KEYS = ("side", "gap", "ctx", "entry", "stop", "tp", "exit")


# ===================================================================== data
def build_candidates(tab: dict, sp100: set[str], demo=None) -> list[dict]:
    by_day: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for s, rows in tab.items():
        for r in rows:
            if r["d"] >= FROM and r["usd"] >= 10e6 and r["o"] <= (1 - GAP_MIN) * r["pc"]:
                by_day[r["d"]].append((s, r))
    days = sorted(by_day)
    print(f"{sum(map(len, by_day.values()))} gap-down stock-days to check at 5 minutes over {len(days)} sessions",
          flush=True)
    cands = []
    for k, d in enumerate(days):
        items = by_day[d]
        if demo:
            intr = demo(d, items)
        else:
            start = datetime.fromisoformat(d + "T09:30").replace(tzinfo=NY).astimezone(timezone.utc)
            end = min(datetime.fromisoformat(d + "T16:00").replace(tzinfo=NY).astimezone(timezone.utc),
                      datetime.now(timezone.utc) - timedelta(minutes=20))    # free SIP data: nothing newer than 15 min
            syms, intr = [s for s, _ in items], {}
            try:
                for j in range(0, len(syms), 100):
                    intr.update(fetch(syms[j:j + 100], "5Min", start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      end.strftime("%Y-%m-%dT%H:%M:%SZ")))
            except Exception as e:                              # one bad day must not sink a 30-minute run
                print(f"  {d}: skipped ({e})", flush=True)
                continue
        for s, r in items:
            c = facts(s, d, r, intr.get(s) or [])
            if c:
                c["sp100"] = s in sp100
                cands.append(c)
        if k % 100 == 0:
            print(f"  {d}: {k + 1}/{len(days)} sessions, {len(cands)} candidates", flush=True)
    return cands


def facts(s: str, d: str, r: dict, bars: list[dict]) -> dict | None:
    today = []
    for b in bars:
        t = ny(b["t"])
        if t.date().isoformat() != d:
            continue
        m = t.hour * 60 + t.minute
        if 570 <= m <= 955:
            today.append((m, b))
    if len(today) < 4 or today[0][0] != 570:
        return None
    ohlc = array("f")
    for m, b in today:
        ohlc.extend((b["o"], b["h"], b["l"], b["c"], m))
    pc = r["pc"]
    return {"s": s, "d": d, "pc": pc, "gap": r["o"] / pc - 1,
            "ctx": {"any": True, "below20": pc < r["s20"], "below50": pc < r["s50"], "above50": pc > r["s50"]},
            "bars": ohlc}


# ===================================================================== rules
def passes(c: dict, v: dict) -> bool:
    if v["univ"] == "sp100" and not c["sp100"]:
        return False
    if c["gap"] > -float(v["gap"]) / 100:
        return False
    if v["cap"] != "none" and c["gap"] < -float(v["cap"]) / 100:
        return False
    return c["ctx"][v["ctx"]]


def trade(c: dict, v: dict) -> dict | None:
    b, n = c["bars"], len(c["bars"]) // 5
    short = v["side"] == "short"
    sgn = -1 if short else 1                       # +1 = profit when price rises
    k = RNG_BARS[v["rng"]]
    if n <= k:
        return None
    hi = max(b[5 * i + 1] for i in range(k))
    lo = min(b[5 * i + 2] for i in range(k))
    trig = lo * (1 - TICK) if short else hi * (1 + TICK)      # the break level we trade
    other = hi * (1 + TICK) if short else lo * (1 - TICK)     # the reclaim level (and the "range" stop)

    # ---- find the entry
    entry, ie = None, None
    for i in range(k, n):
        o, h, l, cl, m = b[5 * i:5 * i + 5]
        if m >= DL_MIN[v["dl"]]:
            break
        broke = l <= trig if short else h >= trig
        reclaimed = h >= other if short else l <= other
        if v["entry"] == "break":
            if broke:
                if reclaimed and v["reclaim"] == "skip" and not (o <= trig if short else o >= trig):
                    return None                    # both sides traded in one bar: assume the reclaim came first
                entry, ie = (min(o, trig) if short else max(o, trig)), i
                break
            if reclaimed and v["reclaim"] == "skip":
                return None
        else:                                       # "close": a candle has to close beyond the range
            if reclaimed and v["reclaim"] == "skip":
                return None
            if (cl < trig) if short else (cl > trig):
                entry, ie = cl, i
                break
    if entry is None:
        return None

    stop = other if v["stop"] == "range" else entry * (1 - sgn * float(v["stop"]) / 100)
    risk = abs(stop - entry)
    if risk <= entry * 0.0005:
        return None
    tgt = None if v["tp"] == "none" else entry + sgn * float(v["tp"]) * risk
    last_m = EXIT_MIN[v["exit"]]

    # ---- manage it
    px, why, at = None, "time", None
    o, h, l, cl, m = b[5 * ie:5 * ie + 5]
    if v["entry"] == "break" and ((cl >= stop) if short else (cl <= stop)):
        px, why, at = stop, "stop", m
    i = ie + 1
    while px is None and i < n:
        o, h, l, cl, m = b[5 * i:5 * i + 5]
        if (h >= stop) if short else (l <= stop):
            px, why = (max(o, stop) if short else min(o, stop)), "stop"
        elif tgt is not None and ((l <= tgt) if short else (h >= tgt)):
            px, why = (min(o, tgt) if short else max(o, tgt)), "target"
        elif m >= last_m:
            px, m = cl, m + 5
        at = m
        i += 1
    if px is None:                                  # half-day session: out at the last bar
        px, at = b[5 * (n - 1) + 3], b[5 * (n - 1) + 4] + 5
    ret = sgn * (px / entry - 1) - 2 * v.get("cost", COST)
    m_in = int(b[5 * ie + 4]) + (5 if v["entry"] == "close" else 0)
    return {"s": c["s"], "d": c["d"], "pc": round(c["pc"], 2), "gap": round(c["gap"] * 100, 2),
            "rh": round(hi, 2), "rl": round(lo, 2), "in": round(entry, 2), "tin": f"{m_in // 60:02d}:{m_in % 60:02d}",
            "sl": round(stop, 2), "tp": round(tgt, 2) if tgt else None, "out": round(px, 2), "why": why,
            "at": f"{int(at) // 60:02d}:{int(at) % 60:02d}", "pct": round(ret * 100, 3),
            "r": round(sgn * (px - entry) / risk, 2), "rv": -c["gap"]}


def run(cands: list[dict], v: dict) -> list[dict]:
    out = []
    for c in cands:
        if passes(c, v):
            t = trade(c, v)
            if t:
                out.append(t)
    return out


def stats(ts: list[dict], spy: dict[str, float]) -> dict:
    if not ts:
        return {"n": 0}
    w = [t for t in ts if t["pct"] > 0]
    gain, loss = sum(t["pct"] for t in w), -sum(t["pct"] for t in ts if t["pct"] <= 0)
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
            "avg_r": round(sum(t["r"] for t in ts) / len(ts), 2),
            "pf": round(gain / loss, 2) if loss else None,
            "exits": {k: sum(t["why"] == k for t in ts) for k in ("target", "stop", "time")},
            "acct_total": round((eq - 1) * 100, 1), "acct_cagr": round((eq ** (1 / yrs) - 1) * 100, 1),
            "acct_maxdd": round(dd * 100, 1), "by_year": by_year,
            "curve": curve[::max(1, len(curve) // 300)] + curve[-1:]}


KEYS = ("n", "win", "avg", "avg_r", "pf", "acct_total", "acct_cagr", "acct_maxdd")


def cut(st: dict) -> dict:
    return {k: st.get(k) for k in KEYS}


def funnel(cands: list[dict], v: dict) -> dict:
    """What happens to the gap-downs after the opening range: broke lower, filled first, or did nothing."""
    out = {"gaps": 0, "broke_low_first": 0, "broke_high_first": 0, "neither": 0}
    for c in cands:
        if not passes(c, v):
            continue
        out["gaps"] += 1
        tk = trade(c, {**v, "side": "short", "reclaim": "skip"})
        tl = trade(c, {**v, "side": "long", "reclaim": "skip"})
        if tk:
            out["broke_low_first"] += 1
        elif tl:
            out["broke_high_first"] += 1
        else:
            out["neither"] += 1
    return out


# ===================================================================== demo data
def demo_intraday(d: str, items: list) -> dict:
    rnd, out = random.Random(d), {}
    for s, r in items:
        dt = datetime.fromisoformat(d + "T09:30").replace(tzinfo=NY)
        px, bars = r["o"], []
        for i in range(78):
            o = px
            c = o * (1 + rnd.gauss(0.0002, 0.003 if i < 12 else 0.0015))
            bars.append({"t": (dt + timedelta(minutes=5 * i)).isoformat(), "o": o, "h": max(o, c) * 1.0015,
                         "l": min(o, c) * 0.9985, "c": c, "v": 2e5})
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
        syms = syms[:60]; sp100 = set(syms[:20])
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
    if a.demo:                                     # the random demo walk rarely gaps: force some gap-downs
        rnd = random.Random(7)
        for rows in tab.values():
            for r in rows:
                if rnd.random() < 0.03:
                    r["o"] = r["pc"] * (1 - rnd.uniform(0.02, 0.12))
    spy = {}
    for y in sorted({b["t"][:4] for b in spy_rows if b["t"][:10] >= FROM}):
        yb = [b for b in spy_rows if b["t"][:4] == y]
        prev = [b for b in spy_rows if b["t"][:4] < y]
        spy[y] = round((yb[-1]["c"] / (prev[-1]["c"] if prev else yb[0]["o"]) - 1) * 100, 1)
    first = [b for b in spy_rows if b["t"][:10] < FROM]
    spy_total = round((spy_rows[-1]["c"] / (first[-1]["c"] if first else spy_rows[0]["o"]) - 1) * 100, 1)

    cands = build_candidates(tab, sp100, demo_intraday if a.demo else None)
    print(f"{len(cands)} gap-downs with intraday bars ({time.time() - t0:.0f}s)", flush=True)

    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "from": FROM,
           "rules": __doc__.split("Exact rules tested here")[1].split("Data:")[0].strip(),
           "default": DEFAULT, "variants": VARIANTS, "spy_by_year": spy, "spy_total": spy_total,
           "costs_pct_a_side": COST * 100, "max_positions": MAX_POS}
    base = run(cands, DEFAULT)
    res["base"] = stats(base, spy)
    res["base_trades_recent"] = [{k: t[k] for k in t if k != "rv"} for t in sorted(base, key=lambda t: t["d"])[-300:]]
    mirror = {**DEFAULT, "side": "long"}
    st = stats(run(cands, mirror), spy); st.pop("curve", None)
    res["mirror"] = {"v": mirror, **st}
    res["funnel"] = {g: funnel(cands, {**DEFAULT, "gap": g}) for g in VARIANTS["gap"]}

    # the motivating morning, if the data reaches it
    nv = [c for c in cands if c["s"] == "NVDA" and c["d"] == "2026-11-09"]
    if nv:
        c = nv[0]
        res["example"] = {"s": "NVDA", "d": c["d"], "gap": round(c["gap"] * 100, 2),
                          "short": trade(c, DEFAULT), "long": trade(c, mirror),
                          "bars": [[round(x, 2) for x in c["bars"][5 * i:5 * i + 4]] + [int(c["bars"][5 * i + 4])]
                                   for i in range(min(24, len(c["bars"]) // 5))]}

    res["cost_check"] = {}
    for cst in (0.0, 0.0003, 0.0006, 0.001):
        st = stats(run(cands, {**DEFAULT, "cost": cst}), spy)
        res["cost_check"][f"{cst * 100:.2f}"] = cut(st)
    sc, prev_c = [], None
    for b_ in spy_rows:
        if b_["t"][:10] < FROM:
            prev_c = b_["c"]; continue
        sc.append([b_["t"][:10], round(b_["c"] / prev_c, 4)])
    res["spy_curve"] = sc[::max(1, len(sc) // 150)] + sc[-1:]

    res["sweeps"] = {}                              # one switch at a time around the default
    for k, opts in VARIANTS.items():
        res["sweeps"][k] = {}
        for o in opts:
            st = stats(run(cands, {**DEFAULT, k: o}), spy); st.pop("curve", None)
            res["sweeps"][k][o] = st
    print(f"sweeps done ({time.time() - t0:.0f}s)", flush=True)

    # both sides, every gap size x context x entry style x reclaim rule
    res["entry_grid"] = []
    for side in VARIANTS["side"]:
        for gap in VARIANTS["gap"]:
            for ctx in VARIANTS["ctx"]:
                for entry in VARIANTS["entry"]:
                    for rec in VARIANTS["reclaim"]:
                        v = {**DEFAULT, "side": side, "gap": gap, "ctx": ctx, "entry": entry, "reclaim": rec}
                        res["entry_grid"].append({**{k: v[k] for k in ("side", "gap", "ctx", "entry", "reclaim")},
                                                  **cut(stats(run(cands, v), spy))})
    # exits: range x stop x target x time exit, both sides
    res["exit_grid"] = []
    for side in VARIANTS["side"]:
        for rng in VARIANTS["rng"]:
            for stop in VARIANTS["stop"]:
                for tp in VARIANTS["tp"]:
                    for ex in VARIANTS["exit"]:
                        v = {**DEFAULT, "side": side, "rng": rng, "stop": stop, "tp": tp, "exit": ex}
                        res["exit_grid"].append({**{k: v[k] for k in ("side", "rng", "stop", "tp", "exit")},
                                                 **cut(stats(run(cands, v), spy))})
    print(f"grids done ({time.time() - t0:.0f}s)", flush=True)

    # pick the best set-ups on 2021-2023 only, then check them unseen on 2024+ (was the winner luck?)
    tried = []
    for side in VARIANTS["side"]:
        for gap in VARIANTS["gap"]:
            for ctx in VARIANTS["ctx"]:
                for rng in VARIANTS["rng"]:
                    for entry in VARIANTS["entry"]:
                        for tp in ("1", "2", "none"):
                            for ex in VARIANTS["exit"]:
                                v = {**DEFAULT, "side": side, "gap": gap, "ctx": ctx, "rng": rng,
                                     "entry": entry, "tp": tp, "exit": ex}
                                ts = run(cands, v)
                                ins = stats([t for t in ts if t["d"] < "2024-01-01"], spy)
                                if ins.get("n", 0) >= 80:
                                    oos = stats([t for t in ts if t["d"] >= "2024-01-01"], spy)
                                    tried.append((ins.get("avg", -9), v, ins, oos))
    tried.sort(key=lambda x: -x[0])
    res["walk_forward"] = {"tried": len(tried), "train": "2021-2023", "test": "2024 onwards",
                           "top": [{"v": v, "train": cut(i), "test": cut(o)} for _, v, i, o in tried[:15]]}
    print(f"walk-forward: {len(tried)} set-ups ({time.time() - t0:.0f}s)", flush=True)

    # every combination the page lets you pick, with its own curve, years, trades and train/test split
    res["combos"] = []
    for side in VARIANTS["side"]:
        for gap in VARIANTS["gap"]:
            for ctx in ("any", "below20", "above50"):
                for entry in VARIANTS["entry"]:
                    for ex in EXIT_PRESETS:
                        stop, tp, xt = ex.split("|")
                        v = {**DEFAULT, "side": side, "gap": gap, "ctx": ctx, "entry": entry,
                             "stop": stop, "tp": tp, "exit": xt}
                        ts = run(cands, v)
                        full = stats(ts, spy)
                        res["combos"].append({
                            "v": {k: v[k] for k in PAGE_KEYS},
                            "curve": (full.get("curve") or [])[::3] + (full.get("curve") or [])[-1:],
                            "recent": [{k: t[k] for k in ("s", "d", "gap", "tin", "in", "sl", "out", "why", "at", "pct")}
                                       for t in sorted(ts, key=lambda t: t["d"])[-25:]],
                            "full": cut(full), "exits": full.get("exits"),
                            "by_year": {y: o["acct"] for y, o in (full.get("by_year") or {}).items()},
                            "train": cut(stats([t for t in ts if t["d"] < "2024-01-01"], spy)),
                            "test": cut(stats([t for t in ts if t["d"] >= "2024-01-01"], spy))})
    print(f"{len(res['combos'])} page combinations ({time.time() - t0:.0f}s)", flush=True)

    out = Path("/tmp/gapdown-demo.json") if a.demo else OUT_DIR / "backtest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, separators=(",", ":")))
    b, mi = res["base"], res["mirror"]
    print(f"short (default): {b.get('n')} trades, win {b.get('win')}%, avg {b.get('avg')}%, PF {b.get('pf')}, "
          f"account {b.get('acct_total')}% (max DD {b.get('acct_maxdd')}%) | long mirror: {mi.get('n')} trades, "
          f"avg {mi.get('avg')}%, account {mi.get('acct_total')}% | SPY {spy_total}%  -> {out} "
          f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
