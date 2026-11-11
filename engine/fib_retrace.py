"""Fibonacci Retracement: let the morning draw the swing, place Fibonacci levels, trade a level.

The idea (Giacomo, 11 Nov 2026):
  - Don't trade the open. Wait ~30 minutes and see how the stock is moving.
  - Draw Fibonacci retracement levels on that morning swing and wait for price to reach a level to enter.
  - Follow-up the same day: "many times it reverses in the morning" - so the reversal is tested too, with three
    ways of placing the levels. Five versions ("mode" + "anchor"), all side by side:

    trend / window  levels from the first-N-minutes low to high; buy the pullback to the level (with the move).
    trend / pdc     same, but levels from yesterday's close to the morning high/low.
    fade / window   levels from the first-N-minutes low to high; when a 5-minute candle CLOSES beyond the level,
                    trade AGAINST the morning move (it's reversing), at that close.
    fade / pdc      same, levels from yesterday's close to the morning high/low.
    leg             the reversal leg: the stock runs up (down), tops (bottoms) and gives back half the morning move
                    -> draw the levels on the new leg, from the top down to the lowest low since (redrawn as new
                    lows come), and short the bounce to the level (buy the dip for a morning sell-off).

Exact rules tested here (every one is a switch, see VARIANTS):
  Universe    S&P 100, current members; 20-day average dollar volume >= $10M.
  Window      watch the first 15, 30 (default), 45 or 60 minutes after the 09:30 New York open.
  Move        the move from the 09:30 open to the window's close must be at least 0.25%, 0.5% (default) or 1%.
  Gap filter  "all" days (default), "gap": the open was at least 2% from yesterday's close, "quiet": within 1%.
  Level       38.2%, 50% or 61.8% (default).
  Entry       trend / leg: "touch" (default) - a limit order at the level, filled the first time a 5-minute
              candle reaches it (at the candle's open if it gaps through); "confirm" - the candle reaches the level
              and closes back on the trade's side of it, enter at that close. fade: always at the close of the
              candle that closed beyond the level.
  Stop        trend / leg: at the 78.6% level (default) or 100% (the start of the swing / the top of the leg).
              fade ("fstop"): "extreme" (default) - beyond the morning high/low; "half" - halfway back from the
              level to that extreme.
  Target      "swing" (default): trend - back to the morning extreme; fade - all the way back to where the swing
              started (the low / yesterday's close); leg - the leg's low (high). "1.272" / "1.618": Fibonacci
              extensions beyond that. "2R": twice the entry-to-stop distance.
  Setup ends  trend: cancelled if the stop is hit before the entry. No new entry after 11:00, 12:00 (default)
              or the 15:55 close.
  Time exit   whatever is still open is closed at 12:00 or the 15:55 close (default).
  Exit rules  stop first if a candle hits both (cautious); a gap through a level fills at the candle's open.
  Costs       0.03% a side.
  Account     each day up to 5 trades (the biggest morning moves first), 20% of the account each, no leverage.

Data: Alpaca SIP 5-minute regular-session bars, split/dividend adjusted, from 2021. Current S&P 100 members
only (survivorship bias). Train 2021-2023, test 2024 on, reported side by side for every combination.

    python engine/fib_retrace.py           # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/fib_retrace.py --demo    # synthetic data, writes /tmp/fib-demo.json
Output: docs/data/fib/backtest.json and docs/data/fib/SUMMARY.md
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from array import array
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from open_breakout import NY, WARMUP, daily_table, demo_daily, fetch, load_tickers, ny  # noqa: E402
from opening_reversal import cut, demo_intraday, stats  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "data" / "fib"
FROM = "2021-01-04"
COST = 0.0003
TICK = 0.0005
MOVE_MIN = 0.0025
LEG_CONFIRM = 0.5          # leg mode: the reversal is "on" once price gives back half the morning move

DEFAULT = {"mode": "trend", "anchor": "window", "win": "30", "move": "0.5", "gapf": "all", "level": "0.618",
           "entry": "touch", "stop": "0.786", "fstop": "extreme", "tp": "swing", "late": "1200", "exit": "eod"}
VARIANTS = {
    "win": ["15", "30", "45", "60"],
    "move": ["0.25", "0.5", "1"],
    "gapf": ["all", "gap", "quiet"],
    "level": ["0.382", "0.5", "0.618"],
    "entry": ["touch", "confirm"],
    "stop": ["0.786", "1"],
    "fstop": ["extreme", "half"],
    "tp": ["swing", "1.272", "1.618", "2R"],
    "late": ["1100", "1200", "eod"],
    "exit": ["1200", "eod"],
}
MODES = [("trend", "window"), ("trend", "pdc"), ("fade", "window"), ("fade", "pdc"), ("leg", "window")]
MODE_NAME = {"trend|window": "Trend pullback, levels on first N min", "trend|pdc": "Trend pullback, levels from yesterday's close",
             "fade|window": "Reversal: fade the break, levels on first N min",
             "fade|pdc": "Reversal: fade the break, levels from yesterday's close",
             "leg|window": "Reversal leg: levels on the move after the top/bottom"}
# switches that do something in each mode (the others are left at the default)
USES = {"trend": ("win", "move", "gapf", "level", "entry", "stop", "tp", "late", "exit"),
        "fade": ("win", "move", "gapf", "level", "fstop", "tp", "late", "exit"),
        "leg": ("win", "move", "gapf", "level", "entry", "stop", "tp", "late", "exit")}
MIN = {"1100": 660, "1200": 720, "eod": 955}
PAGE_KEYS = tuple(DEFAULT)


# ===================================================================== data
def build_candidates(tab: dict, sp100: set[str], demo=None) -> list[dict]:
    by_day: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for s, rows in tab.items():
        if s not in sp100:
            continue
        for r in rows:
            if r["d"] >= FROM and r["usd"] >= 10e6:
                by_day[r["d"]].append((s, r))
    days = sorted(by_day)
    print(f"{sum(map(len, by_day.values()))} stock-days over {len(days)} sessions", flush=True)
    cands = []
    for k, d in enumerate(days):
        items = by_day[d]
        if demo:
            intr = demo(d, items)
        else:
            start = datetime.fromisoformat(d + "T09:30").replace(tzinfo=NY).astimezone(timezone.utc)
            end = min(datetime.fromisoformat(d + "T16:00").replace(tzinfo=NY).astimezone(timezone.utc),
                      datetime.now(timezone.utc) - timedelta(minutes=20))
            syms, intr = [s for s, _ in items], {}
            try:
                for j in range(0, len(syms), 100):
                    intr.update(fetch(syms[j:j + 100], "5Min", start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      end.strftime("%Y-%m-%dT%H:%M:%SZ")))
            except Exception as e:
                print(f"  {d}: skipped ({e})", flush=True)
                continue
        for s, r in items:
            c = facts(s, d, r, intr.get(s) or [])
            if c:
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
    if len(today) < 16 or today[0][0] != 570:
        return None
    o0 = today[0][1]["o"]
    sw = {}
    for w in VARIANTS["win"]:
        k = int(w) // 5
        if today[k - 1][0] != 570 + 5 * (k - 1):
            return None
        hi = max(b["h"] for _, b in today[:k])
        lo = min(b["l"] for _, b in today[:k])
        sw[w] = (today[k - 1][1]["c"] / o0 - 1, hi, lo)
    if max(abs(x[0]) for x in sw.values()) < MOVE_MIN:
        return None
    ohlc = array("f")
    for m, b in today:
        ohlc.extend((b["o"], b["h"], b["l"], b["c"], m))
    return {"s": s, "d": d, "o0": o0, "pc": r["pc"], "gap": r["o"] / r["pc"] - 1, "sw": sw, "bars": ohlc}


# ===================================================================== rules
def passes(c: dict, v: dict) -> bool:
    if abs(c["sw"][v["win"]][0]) < float(v["move"]) / 100:
        return False
    if v["gapf"] == "gap" and abs(c["gap"]) < 0.02:
        return False
    if v["gapf"] == "quiet" and abs(c["gap"]) > 0.01:
        return False
    return True


def swing(c: dict, v: dict):
    """(up, s0, s1): the morning swing from s0 (start) to s1 (extreme), or None."""
    mv, hi, lo = c["sw"][v["win"]]
    up = mv > 0
    s1 = hi if up else lo
    if v["anchor"] == "pdc":
        s0 = c["pc"]
        if (up and s0 >= s1) or (not up and s0 <= s1):
            return None
    else:
        s0 = lo if up else hi
    if abs(s1 - s0) <= c["o0"] * 0.001:
        return None
    return up, s0, s1


def mark(c: dict, k: int, up: bool, s0: float, s1: float, anchor: str) -> None:
    """Remember where the levels were drawn (for the trade viewer): 0% at s1, 100% at s0."""
    b = c["bars"]
    hi_i = max(range(k), key=lambda j: b[5 * j + 1])
    lo_i = min(range(k), key=lambda j: b[5 * j + 2])
    c["_lv"] = {"z": s1, "o": s0, "iz": hi_i if up else lo_i, "io": None if anchor == "pdc" else (lo_i if up else hi_i)}


def target(v, sgn, entry, stop, base, rng):
    """base = where the 'swing' target sits; rng = signed size of the move the extensions are measured on."""
    if v["tp"] == "swing":
        return base
    if v["tp"] == "2R":
        return entry + 2 * (entry - stop)
    return base + sgn * abs(rng) * (float(v["tp"]) - 1)


def trade(c: dict, v: dict) -> dict | None:
    if v["mode"] == "trend":
        return trade_trend(c, v)
    if v["mode"] == "fade":
        return trade_fade(c, v)
    return trade_leg(c, v)


def trade_trend(c: dict, v: dict) -> dict | None:
    sw = swing(c, v)
    if not sw:
        return None
    up, s0, s1 = sw
    b, n, k = c["bars"], len(c["bars"]) // 5, int(v["win"]) // 5
    mark(c, k, up, s0, s1, v["anchor"])
    sgn = 1 if up else -1                                      # with the morning move
    rng = s1 - s0
    lvl = s1 - rng * float(v["level"])
    stop = (s1 - rng * float(v["stop"])) * (1 - sgn * TICK)
    tgt = target(v, sgn, lvl, stop, s1, rng)
    late, last_m = MIN[v["late"]], MIN[v["exit"]]
    for i in range(k, n):
        o, h, l, cl, m = b[5 * i:5 * i + 5]
        if m >= late or m >= last_m:
            return None
        reach = (l <= lvl) if sgn > 0 else (h >= lvl)
        broke = (l <= stop) if sgn > 0 else (h >= stop)
        if v["entry"] == "touch" and reach:
            entry = min(o, lvl) if sgn > 0 else max(o, lvl)
            if (sgn > 0 and entry <= stop) or (sgn < 0 and entry >= stop):
                return None                                     # gapped straight through the stop
            if broke:
                return finish(c, v, sgn, entry, i, stop, tgt, stop, "stop", m, False)
            return manage(c, v, sgn, entry, i, stop, tgt, False)
        if broke:
            return None
        if v["entry"] == "confirm" and reach and ((cl > lvl) if sgn > 0 else (cl < lvl)):
            return manage(c, v, sgn, cl, i, stop, tgt, True)
    return None


def trade_fade(c: dict, v: dict) -> dict | None:
    sw = swing(c, v)
    if not sw:
        return None
    up, s0, s1 = sw
    b, n, k = c["bars"], len(c["bars"]) // 5, int(v["win"]) // 5
    mark(c, k, up, s0, s1, v["anchor"])
    sgn = -1 if up else 1                                      # against the morning move
    rng = s1 - s0
    lvl = s1 - rng * float(v["level"])
    late, last_m = MIN[v["late"]], MIN[v["exit"]]
    for i in range(k, n):
        o, h, l, cl, m = b[5 * i:5 * i + 5]
        if m >= late or m >= last_m:
            return None
        if (cl < lvl) if up else (cl > lvl):
            ext = s1 if v["fstop"] == "extreme" else (lvl + s1) / 2
            stop = ext * (1 - sgn * TICK)
            tgt = target(v, sgn, cl, stop, s0, rng)
            if (sgn < 0 and tgt >= cl) or (sgn > 0 and tgt <= cl):
                return None
            return manage(c, v, sgn, cl, i, stop, tgt, True)
    return None


def trade_leg(c: dict, v: dict) -> dict | None:
    mv, hi, lo = c["sw"][v["win"]]
    up = mv > 0
    b, n, k = c["bars"], len(c["bars"]) // 5, int(v["win"]) // 5
    sgn = -1 if up else 1                                      # trade the new leg, against the morning move
    base = lo if up else hi                                    # where the morning move started
    if up:                                                      # the morning extreme (top for an up morning)
        ext_i = max(range(k), key=lambda j: b[5 * j + 1]); ext = b[5 * ext_i + 1]
    else:
        ext_i = min(range(k), key=lambda j: b[5 * j + 2]); ext = b[5 * ext_i + 2]
    late, last_m = MIN[v["late"]], MIN[v["exit"]]
    lvf, stf = float(v["level"]), float(v["stop"])
    on, far, far_i = False, None, None                          # far = the leg's low (high) so far
    for i in range(k, n):
        o, h, l, cl, m = b[5 * i:5 * i + 5]
        if m >= late or m >= last_m:
            return None
        if not on:
            if (h > ext) if up else (l < ext):
                ext, ext_i = (h if up else l), i
            half = ext - (ext - base) * LEG_CONFIRM
            if (cl < half) if up else (cl > half):
                on = True
                if up:
                    far_i = min(range(ext_i, i + 1), key=lambda j: b[5 * j + 2]); far = b[5 * far_i + 2]
                else:
                    far_i = max(range(ext_i, i + 1), key=lambda j: b[5 * j + 1]); far = b[5 * far_i + 1]
            continue
        leg = ext - far                                         # signed: + for an up morning (leg goes down)
        lvl = far + leg * lvf
        stop = (far + leg * stf) * (1 - sgn * TICK)
        c["_lv"] = {"z": far, "o": ext, "iz": far_i, "io": ext_i}
        reach = (h >= lvl) if up else (l <= lvl)
        if v["entry"] == "touch" and reach:
            entry = max(o, lvl) if up else min(o, lvl)
            if (up and entry >= stop) or (not up and entry <= stop):
                return None
            tgt = target(v, sgn, entry, stop, far, leg)
            if (up and (h >= stop)) or (not up and (l <= stop)):
                return finish(c, v, sgn, entry, i, stop, tgt, stop, "stop", m, False)
            return manage(c, v, sgn, entry, i, stop, tgt, False)
        if v["entry"] == "confirm" and reach and ((cl < lvl) if up else (cl > lvl)) and \
                ((cl < stop) if up else (cl > stop)):
            return manage(c, v, sgn, cl, i, stop, target(v, sgn, cl, stop, far, leg), True)
        if (h >= ext) if up else (l <= ext):                    # back through the top: the reversal failed
            return None
        if (l < far) if up else (h > far):                      # redraw on new lows (highs)
            far, far_i = (l if up else h), i
    return None


def manage(c, v, sgn, entry, ie, stop, tgt, at_close) -> dict | None:
    b, n = c["bars"], len(c["bars"]) // 5
    last_m = MIN[v["exit"]]
    if (sgn > 0 and (stop >= entry or tgt <= entry)) or (sgn < 0 and (stop <= entry or tgt >= entry)):
        return None
    px, why, at = None, "time", None
    for i in range(ie + 1, n):
        o, h, l, cl, m = b[5 * i:5 * i + 5]
        if (l <= stop) if sgn > 0 else (h >= stop):
            px, why, at = (min(o, stop) if sgn > 0 else max(o, stop)), "stop", m
        elif (h >= tgt) if sgn > 0 else (l <= tgt):
            px, why, at = (max(o, tgt) if sgn > 0 else min(o, tgt)), "target", m
        elif m >= last_m:
            px, at = cl, m + 5
        if px is not None:
            break
    if px is None:
        px, at = b[5 * (n - 1) + 3], b[5 * (n - 1) + 4] + 5
    return finish(c, v, sgn, entry, ie, stop, tgt, px, why, at, at_close)


def finish(c, v, sgn, entry, ie, stop, tgt, px, why, at, at_close) -> dict | None:
    risk = abs(entry - stop)
    if risk <= entry * 0.0003:
        return None
    mv = c["sw"][v["win"]][0]
    ret = sgn * (px / entry - 1) - 2 * v.get("cost", COST)
    b = c["bars"]
    m_in = int(b[5 * ie + 4]) + (5 if at_close else 0)
    ix = ie
    while ix + 1 < len(b) // 5 and b[5 * (ix + 1) + 4] < at:
        ix += 1
    if why != "time" and b[5 * ix + 4] < at and ix + 1 < len(b) // 5:
        ix += 1
    return {"s": c["s"], "d": c["d"], "mv": round(mv * 100, 2), "dir": "long" if sgn > 0 else "short",
            "in": round(entry, 2), "tin": f"{m_in // 60:02d}:{m_in % 60:02d}", "sl": round(stop, 2),
            "tp": round(tgt, 2), "out": round(px, 2), "why": why,
            "at": f"{int(at) // 60:02d}:{int(at) % 60:02d}", "pct": round(ret * 100, 3),
            "r": round(sgn * (px - entry) / risk, 2), "rv": abs(mv),
            "_draw": {**{k: (round(x, 2) if isinstance(x, float) else x) for k, x in (c.get("_lv") or {}).items()},
                      "ie": ie, "ix": ix}}


def run(cands: list[dict], v: dict) -> list[dict]:
    out = []
    for c in cands:
        if passes(c, v):
            t = trade(c, v)
            if t:
                out.append(t)
    return out


def sample(ts: list[dict], by_key: dict, v: dict, n_last: int = 40, n_rand: int = 60) -> list[dict]:
    """Trades for the page's trade viewer: the latest ones plus a random spread over the years, with their day's bars."""
    import random
    ts = sorted(ts, key=lambda t: (t["d"], t["tin"], t["s"]))
    pick = ts[-n_last:]
    rest = ts[:-n_last]
    pick += random.Random(20261111).sample(rest, min(n_rand, len(rest)))
    out = []
    for t in sorted(pick, key=lambda t: (t["d"], t["tin"], t["s"]), reverse=True):
        c = by_key[(t["s"], t["d"])]
        b = c["bars"]
        bars = [[int(b[5 * i + 4])] + [round(float(x), 2) for x in b[5 * i:5 * i + 4]] for i in range(len(b) // 5)]
        out.append({**{k: t[k] for k in t if k not in ("rv", "_draw")}, "draw": t["_draw"], "pc": round(c["pc"], 2),
                    "win": v["win"], "bars": bars})
    return out


def split(ts: list[dict], spy: dict) -> dict:
    full = stats(ts, spy)
    return {"full": cut(full), "exits": full.get("exits"),
            "by_year": {y: o["acct"] for y, o in (full.get("by_year") or {}).items()},
            "train": cut(stats([t for t in ts if t["d"] < "2024-01-01"], spy)),
            "test": cut(stats([t for t in ts if t["d"] >= "2024-01-01"], spy))}


# ===================================================================== summary
def summary(res: dict) -> str:
    def row(name, s):
        f, tr, te = s["full"], s["train"], s["test"]
        return (f"| {name} | {f.get('n')} | {f.get('win')}% | {f.get('avg')}% | {f.get('avg_r')} | {f.get('pf')} | "
                f"{f.get('acct_total')}% | {f.get('acct_maxdd')}% | {tr.get('avg')}% / {tr.get('pf')} | "
                f"{te.get('avg')}% / {te.get('pf')} |")
    hdr = ("| Variant | Trades | Win | Avg/trade | Avg R | PF | Account | Max DD | Train avg / PF | Test avg / PF |\n"
           "|---|---|---|---|---|---|---|---|---|---|")
    L = [f"# Fibonacci Retracement backtest ({res['generated']})", "",
         f"S&P 100, 5-minute bars from {res['from']}, costs {res['costs_pct_a_side']}% a side. "
         f"SPY over the same period: {res['spy_total']}%. Train = 2021-2023, test = 2024 on.", "",
         "## The five versions on their default switches", "",
         "`" + " | ".join(f"{k}={v}" for k, v in res["default"].items() if k not in ("mode", "anchor")) + "`", "",
         hdr]
    for mk, s in res["modes"].items():
        L.append(row(MODE_NAME[mk], s["base"]))
    L += ["", "## All versions together", "",
          f"Combinations tested: {len(res['combos'])}. Positive per trade in BOTH train and test: "
          f"**{sum(1 for c in res['combos'] if (c['train'].get('avg') or 0) > 0 and (c['test'].get('avg') or 0) > 0)}**.", ""]
    for mk, s in res["modes"].items():
        mode = mk.split("|")[0]
        L += ["", f"## {MODE_NAME[mk]}", "", "### One switch changed at a time", "", hdr]
        for k, opts in s["sweeps"].items():
            for o, st in opts.items():
                L.append(row(f"{k}={o}" + (" (default)" if DEFAULT[k] == o else ""), st))
        mine = [c for c in res["combos"] if f"{c['v']['mode']}|{c['v']['anchor']}" == mk]
        top = sorted((c for c in mine if (c["train"].get("n") or 0) >= 200),
                     key=lambda c: -(c["train"].get("avg") or -9))[:10]
        L += ["", "### Best 10 combinations chosen on TRAIN only (min 200 train trades) - does TEST hold?", "", hdr]
        for c in top:
            L.append(row(" ".join(f"{k}={c['v'][k]}" for k in USES[mode] if k not in ("gapf", "late")), c))
        L += ["", f"Of those 10, **{sum(1 for c in top if (c['test'].get('avg') or 0) > 0)}** were still profitable "
                  f"per trade in the test years. Combinations in this version: {len(mine)}, positive in both train and "
                  f"test: **{sum(1 for c in mine if (c['train'].get('avg') or 0) > 0 and (c['test'].get('avg') or 0) > 0)}**."]
    return "\n".join(L) + "\n"


# ===================================================================== main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    _, sp100 = load_tickers()
    syms = sorted(sp100)
    if a.demo:
        syms = syms[:30]; sp100 = set(syms)
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

    cands = build_candidates(tab, sp100, demo_intraday if a.demo else None)
    print(f"{len(cands)} stock-days with a morning move ({time.time() - t0:.0f}s)", flush=True)

    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "from": FROM,
           "rules": __doc__.split("Exact rules tested here")[1].split("Data:")[0].strip(),
           "default": DEFAULT, "variants": VARIANTS, "spy_by_year": spy, "spy_total": spy_total,
           "costs_pct_a_side": COST * 100, "modes": {}, "combos": []}

    by_key = {(c["s"], c["d"]): c for c in cands}
    viewer: dict = {}
    for mode, anchor in MODES:
        mk = f"{mode}|{anchor}"
        dv = {**DEFAULT, "mode": mode, "anchor": anchor}
        base = run(cands, dv)
        m = {"base": split(base, spy),
             "base_trades_recent": [{k: t[k] for k in t if k not in ("rv", "_draw")} for t in sorted(base, key=lambda t: t["d"])[-100:]],
             "cost_check": {f"{cst * 100:.2f}": cut(stats(run(cands, {**dv, "cost": cst}), spy))
                            for cst in (0.0, 0.0003, 0.0006, 0.001)},
             "sweeps": {k: {o: split(run(cands, {**dv, k: o}), spy) for o in VARIANTS[k]} for k in USES[mode]}}
        res["modes"][mk] = m
        viewer[mk] = sample(base, by_key, dv)

        # the move threshold only filters days, so simulate once at the smallest move and filter the trades
        grid = [k for k in USES[mode] if k not in ("move", "gapf", "late")]
        combos = [{}]
        for k in grid:
            combos = [{**x, k: o} for x in combos for o in VARIANTS[k]]
        for x in combos:
            v = {**dv, **x, "move": VARIANTS["move"][0]}
            ts = run(cands, v)
            for mv in VARIANTS["move"]:
                sub = [t for t in ts if t["rv"] >= float(mv) / 100]
                res["combos"].append({"v": {k: ({**v, "move": mv})[k] for k in PAGE_KEYS}, **split(sub, spy)})
        b = m["base"]["full"]
        print(f"{mk}: default {b.get('n')} trades, win {b.get('win')}%, avg {b.get('avg')}%, PF {b.get('pf')}, "
              f"account {b.get('acct_total')}% | {len(combos) * 3} combinations ({time.time() - t0:.0f}s)", flush=True)

    out = Path("/tmp/fib-demo.json") if a.demo else OUT_DIR / "backtest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, separators=(",", ":")))
    (out.with_name("fib-demo-SUMMARY.md") if a.demo else OUT_DIR / "SUMMARY.md").write_text(summary(res))
    (out.with_name("fib-demo-trades.json") if a.demo else OUT_DIR / "trades_stocks.json").write_text(
        json.dumps({"generated": res["generated"], "default": DEFAULT, "names": MODE_NAME, "modes": viewer},
                   separators=(",", ":")))
    print(f"done -> {out} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
