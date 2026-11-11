"""Fibonacci + Adaptive MA on SPY: every entry level, exits at the first and second level back, 1-minute bars.

The idea (Giacomo, 11 Nov 2026): draw Fibonacci retracement levels on the morning swing, use the adaptive
moving average (TradingView "Moving Average Adaptive", length 50, 1-minute chart) for the direction, enter on
a pullback to a level and take profit one or two levels back toward the swing.

Exact rules tested here (every one is a switch, see VARIANTS):
  Market      SPY only, regular session 09:30-16:00 New York, 1-minute bars.
  Adaptive MA Kaufman's adaptive moving average (KAMA): efficiency ratio over 50 bars, fast 2, slow 30,
              computed on the 1-minute closes of the regular session, carried over from day to day.
  Swing       the low and high of the first 15, 30 (default) or 60 minutes. Its range must be at least
              0.2% of price. The move from the 09:30 open to the window's close sets the direction:
              up -> levels from the low (100%) to the high (0%), we look to buy the pullback;
              down -> levels from the high (100%) to the low (0%), we look to short the bounce.
              While waiting for the entry, a new high (long) or low (short) moves the 0% level there and
              every level is redrawn, the way you would redraw the tool on the chart.
  Filter      "none": direction from the morning move only (no adaptive MA, the baseline).
              "side" (default): price must also be on the trend side of the adaptive MA at the window's close.
              "slope": "side", and the adaptive MA must be moving in the trend direction over the last 10 minutes.
              "hold": "side", and at the entry the level must still be on the trend side of the adaptive MA
              (buying a pullback that has not fallen through the purple line).
  Entry       a limit order at the 23.6%, 38.2%, 50%, 61.8% or 78.6% level, filled the first minute that reaches
              it (at the minute's open if it gaps through). No new entries after 12:00 New York.
  Target      "next": the next level back toward the swing (enter 61.8% -> exit 50%),
              "second": two levels back (enter 61.8% -> exit 38.2%), "swing": back to 0%.
  Stop        "next": the next level further out (enter 61.8% -> stop 78.6%), "one": the 100% level.
              A small buffer (0.02%) beyond the level. If the stop price trades before the entry, no trade.
  Exits       stop first if one minute reaches both; whatever is still open is closed at the 16:00 close.
  Costs       0.02% a side (SPY's spread is tiny; Trading 212 adds its own spread, see the cost check).
  Account     at most one SPY trade a day, the whole account in it, no leverage.

Data: Alpaca SIP 1-minute bars, split/dividend adjusted, from 2021. Train 2021-2023, test 2024 on, reported
side by side for every combination so a rule that only fits the past shows up.

    python engine/fib_kama_spy.py           # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/fib_kama_spy.py --demo    # synthetic data, writes /tmp/fib-kama-demo.json
Output: docs/data/fib/spy_kama.json and docs/data/fib/SPY_KAMA.md
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from open_breakout import NY, fetch, ny  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "data" / "fib"
FROM = "2021-01-04"
WARMUP = "2020-12-01"
COST = 0.0002
BUF = 0.0002
MIN_RNG = 0.002
LATE = 720            # 12:00 New York, in minutes after midnight
OPEN, CLOSE = 570, 959
KAMA_N, KAMA_FAST, KAMA_SLOW = 50, 2, 30
LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
ENTRY_IDX = [1, 2, 3, 4, 5]   # 23.6% .. 78.6%

DEFAULT = {"win": "30", "filter": "side", "level": "0.618", "tp": "next", "stop": "next"}
VARIANTS = {
    "win": ["15", "30", "60"],
    "filter": ["none", "side", "slope", "hold"],
    "level": [str(LEVELS[i]) for i in ENTRY_IDX],
    "tp": ["next", "second", "swing"],
    "stop": ["next", "one"],
}
TRAIN_END = "2024-01-01"


# ===================================================================== data
def kama(closes: list[float]) -> list[float]:
    fast, slow = 2 / (KAMA_FAST + 1), 2 / (KAMA_SLOW + 1)
    out, k = [], None
    for i, c in enumerate(closes):
        if i < KAMA_N:
            k = c if k is None else k + (c - k) * slow ** 2
            out.append(k)
            continue
        change = abs(c - closes[i - KAMA_N])
        vol = sum(abs(closes[j] - closes[j - 1]) for j in range(i - KAMA_N + 1, i + 1))
        er = change / vol if vol > 0 else 0.0
        sc = (er * (fast - slow) + slow) ** 2
        k = k + sc * (c - k)
        out.append(k)
    return out


def sessions(bars: list[dict]) -> dict[str, list[tuple]]:
    """Regular-session minutes per New York date, each (o, h, l, c, minute, kama)."""
    rows = []
    for b in bars:
        t = ny(b["t"])
        m = t.hour * 60 + t.minute
        if OPEN <= m <= CLOSE:
            rows.append((t.date().isoformat(), b["o"], b["h"], b["l"], b["c"], m))
    ks = kama([r[4] for r in rows])
    days: dict[str, list[tuple]] = defaultdict(list)
    for r, k in zip(rows, ks):
        days[r[0]].append((r[1], r[2], r[3], r[4], r[5], k))
    return dict(days)


def demo_bars() -> list[dict]:
    """Synthetic SPY-like 1-minute bars with some trend days, for checking the code without keys."""
    rnd, px, out = random.Random(7), 370.0, []
    d = date(2020, 12, 1)
    while d <= date(2026, 10, 30):
        if d.weekday() < 5:
            drift = rnd.gauss(0, 0.00012)
            px *= 1 + rnd.gauss(0, 0.006)
            for m in range(OPEN, CLOSE + 1):
                o = px
                px *= 1 + drift + rnd.gauss(0, 0.0006)
                h, l = max(o, px) * (1 + abs(rnd.gauss(0, 0.0002))), min(o, px) * (1 - abs(rnd.gauss(0, 0.0002)))
                t = datetime(d.year, d.month, d.day, m // 60, m % 60, tzinfo=NY)
                out.append({"t": t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "o": o, "h": h, "l": l, "c": px})
        d += timedelta(days=1)
    return out


def facts(d: str, bars: list[tuple]) -> dict | None:
    if len(bars) < 300 or bars[0][4] != OPEN:
        return None
    o0, sw = bars[0][0], {}
    for w in VARIANTS["win"]:
        end = OPEN + int(w) - 1
        k = next((i for i, b in enumerate(bars) if b[4] >= end), None)
        if k is None or bars[k][4] != end:
            return None
        hi = max(b[1] for b in bars[:k + 1])
        lo = min(b[2] for b in bars[:k + 1])
        c, km = bars[k][3], bars[k][5]
        km10 = bars[max(0, k - 10)][5]
        sw[w] = {"k": k, "mv": c / o0 - 1, "hi": hi, "lo": lo, "c": c, "km": km, "slope": km - km10}
    return {"d": d, "o0": o0, "sw": sw, "bars": bars}


# ===================================================================== rules
def trade(c: dict, v: dict) -> dict | None:
    s = c["sw"][v["win"]]
    sgn = 1 if s["mv"] > 0 else -1
    if s["mv"] == 0 or (s["hi"] - s["lo"]) < c["o0"] * MIN_RNG:
        return None
    if v["filter"] != "none" and sgn * (s["c"] - s["km"]) <= 0:
        return None
    if v["filter"] == "slope" and sgn * s["slope"] <= 0:
        return None
    i_lv = LEVELS.index(float(v["level"]))
    i_tp = {"next": i_lv - 1, "second": i_lv - 2, "swing": 0}[v["tp"]]
    i_sl = {"next": i_lv + 1, "one": 6}[v["stop"]]
    if i_tp < 0:
        return None
    s0 = s["lo"] if sgn > 0 else s["hi"]          # 100%
    s1 = s["hi"] if sgn > 0 else s["lo"]          # 0%, moves with new extremes before the entry
    px_at = lambda f, s1: s1 - (s1 - s0) * f
    bars, entry, ie = c["bars"], None, None

    for i in range(s["k"] + 1, len(bars)):
        o, h, l, cl, m, km = bars[i]
        if m >= LATE:
            return None
        if (h > s1) if sgn > 0 else (l < s1):     # new extreme: redraw, no entry on this minute
            s1 = h if sgn > 0 else l
            continue
        lvl = px_at(LEVELS[i_lv], s1)
        stop = px_at(LEVELS[i_sl], s1) * (1 - sgn * BUF)
        if (l <= stop) if sgn > 0 else (h >= stop):
            return None                             # the setup failed before we got in
        if (l <= lvl) if sgn > 0 else (h >= lvl):
            if v["filter"] == "hold" and sgn * (lvl - km) <= 0:
                return None
            entry, ie = (min(o, lvl) if sgn > 0 else max(o, lvl)), i
            break
    if entry is None:
        return None
    tgt = px_at(LEVELS[i_tp], s1)
    stop = px_at(LEVELS[i_sl], s1) * (1 - sgn * BUF)
    risk = abs(entry - stop)
    if risk <= entry * 0.0001 or (tgt - entry) * sgn <= 0:
        return None

    px, why, at = None, "close", None
    for i in range(ie + 1, len(bars)):
        o, h, l, cl, m, _ = bars[i]
        if (l <= stop) if sgn > 0 else (h >= stop):
            px, why, at = (min(o, stop) if sgn > 0 else max(o, stop)), "stop", m
        elif (h >= tgt) if sgn > 0 else (l <= tgt):
            px, why, at = (max(o, tgt) if sgn > 0 else min(o, tgt)), "target", m
        if px is not None:
            break
    if px is None:
        px, at = bars[-1][3], bars[-1][4] + 1
    ret = sgn * (px / entry - 1) - 2 * v.get("cost", COST)
    tin = bars[ie][4]
    return {"d": c["d"], "dir": "long" if sgn > 0 else "short", "in": round(entry, 2), "tin": f"{tin // 60:02d}:{tin % 60:02d}",
            "sl": round(stop, 2), "tp": round(tgt, 2), "out": round(px, 2), "why": why,
            "at": f"{int(at) // 60:02d}:{int(at) % 60:02d}", "pct": round(ret * 100, 4),
            "r": round(sgn * (px - entry) / risk, 2)}


def run(cands: list[dict], v: dict) -> list[dict]:
    return [t for t in (trade(c, v) for c in cands) if t]


def stats(ts: list[dict]) -> dict:
    if not ts:
        return {"n": 0}
    win = [t for t in ts if t["pct"] > 0]
    gain, loss = sum(t["pct"] for t in win), -sum(t["pct"] for t in ts if t["pct"] <= 0)
    eq, peak, dd, years = 1.0, 1.0, 0.0, defaultdict(lambda: 1.0)
    for t in sorted(ts, key=lambda t: t["d"]):
        eq *= 1 + t["pct"] / 100
        years[t["d"][:4]] *= 1 + t["pct"] / 100
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    ex = defaultdict(int)
    for t in ts:
        ex[t["why"]] += 1
    return {"n": len(ts), "win": round(len(win) / len(ts) * 100, 1),
            "avg": round(sum(t["pct"] for t in ts) / len(ts), 4),
            "avg_r": round(sum(t["r"] for t in ts) / len(ts), 3),
            "pf": round(gain / loss, 2) if loss > 0 else None,
            "acct": round((eq - 1) * 100, 1), "maxdd": round(dd * 100, 1),
            "by_year": {y: round((x - 1) * 100, 1) for y, x in sorted(years.items())},
            "exits": dict(ex)}


def split(ts: list[dict]) -> dict:
    keep = ("n", "win", "avg", "pf")
    tr, te = stats([t for t in ts if t["d"] < TRAIN_END]), stats([t for t in ts if t["d"] >= TRAIN_END])
    return {"full": stats(ts), "train": {k: tr.get(k) for k in keep}, "test": {k: te.get(k) for k in keep}}


def combos() -> list[dict]:
    out, seen = [], set()
    for w in VARIANTS["win"]:
        for f in VARIANTS["filter"]:
            for lv in VARIANTS["level"]:
                i = LEVELS.index(float(lv))
                for tp in VARIANTS["tp"]:
                    if tp == "second" and i < 2:
                        continue
                    for st in VARIANTS["stop"]:
                        sl = {"next": i + 1, "one": 6}[st]
                        tpi = {"next": i - 1, "second": i - 2, "swing": 0}[tp]
                        key = (w, f, lv, tpi, sl)
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append({"win": w, "filter": f, "level": lv, "tp": tp, "stop": st})
    return out


# ===================================================================== summary
def pct(x, d=1):
    return "–" if x is None else f"{x:+.{d}f}%"


def name(v: dict) -> str:
    lv = lambda f: f"{float(f) * 100:g}%"
    i = LEVELS.index(float(v["level"]))
    tp = {"next": LEVELS[i - 1], "second": LEVELS[i - 2] if i >= 2 else None, "swing": 0.0}[v["tp"]]
    sl = {"next": LEVELS[i + 1], "one": 1.0}[v["stop"]]
    return f"enter {lv(v['level'])} → exit {lv(tp)}, stop {lv(sl)}"


def summary(res: dict) -> str:
    hdr = ("| Setup | Trades | Win | Avg/trade | PF | Account | Max DD | Train avg / PF | Test avg / PF |\n"
           "|---|---|---|---|---|---|---|---|---|")

    def row(label, c):
        f, tr, te = c["full"], c["train"], c["test"]
        return (f"| {label} | {f.get('n')} | {f.get('win', '–')}% | {pct(f.get('avg'), 3)} | {f.get('pf') or '–'} | "
                f"{pct(f.get('acct'))} | {f.get('maxdd', '–')}% | {pct(tr.get('avg'), 3)} / {tr.get('pf') or '–'} | "
                f"{pct(te.get('avg'), 3)} / {te.get('pf') or '–'} |")
    C = res["combos"]
    L = [f"# SPY Fibonacci + Adaptive MA backtest ({res['generated']})", "",
         f"SPY, 1-minute bars from {res['from']}, {res['days']} sessions, costs {res['cost_pct']}% a side. "
         f"Adaptive MA: KAMA({KAMA_N}, {KAMA_FAST}, {KAMA_SLOW}). SPY buy and hold over the same period: "
         f"{pct(res['spy_total'])}. Train = 2021-2023, test = 2024 on.", "",
         f"## Every entry level and exit, 30-minute swing, price on the trend side of the adaptive MA", "", hdr]
    for c in C:
        if c["v"]["win"] == "30" and c["v"]["filter"] == "side":
            L.append(row(name(c["v"]), c))

    L += ["", "## Does the adaptive MA help? Same setups, different filter (30-minute swing)", "",
          "| Filter | Setups tested | Avg trades | Avg result per trade | Setups with a positive average |",
          "|---|---|---|---|---|"]
    for f in VARIANTS["filter"]:
        g = [c for c in C if c["v"]["win"] == "30" and c["v"]["filter"] == f and c["full"].get("n")]
        if g:
            L.append(f"| {f} | {len(g)} | {round(sum(c['full']['n'] for c in g) / len(g))} | "
                     f"{pct(sum(c['full']['avg'] for c in g) / len(g), 3)} | "
                     f"{sum(1 for c in g if c['full']['avg'] > 0)} of {len(g)} |")

    L += ["", "## Swing window (filter = side, all levels and exits)", "",
          "| Window | Avg result per trade | Setups with a positive average |", "|---|---|---|"]
    for w in VARIANTS["win"]:
        g = [c for c in C if c["v"]["win"] == w and c["v"]["filter"] == "side" and c["full"].get("n")]
        if g:
            L.append(f"| first {w} min | {pct(sum(c['full']['avg'] for c in g) / len(g), 3)} | "
                     f"{sum(1 for c in g if c['full']['avg'] > 0)} of {len(g)} |")

    top = sorted((c for c in C if (c["train"].get("n") or 0) >= 100),
                 key=lambda c: -(c["train"].get("avg") or -9))[:10]
    L += ["", "## Best 10 setups picked on TRAIN only (at least 100 train trades): does TEST hold?", "", hdr]
    for c in top:
        L.append(row(f"{c['v']['win']} min, {c['v']['filter']}: {name(c['v'])}", c))
    held = sum(1 for c in top if (c["test"].get("avg") or 0) > 0)
    both = sum(1 for c in C if (c["train"].get("avg") or 0) > 0 and (c["test"].get("avg") or 0) > 0
               and (c["train"].get("n") or 0) >= 100 and (c["test"].get("n") or 0) >= 60)
    L += ["", f"Of those 10, **{held}** were still profitable per trade in the test years.",
          f"Setups tested: {len(C)}. Profitable in BOTH train and test (100+ train trades, 60+ test trades): **{both}**."]
    if res.get("cost_check"):
        L += ["", f"## Cost check for the best train setup ({name(top[0]['v'])})" if top else "", "",
              "| Cost a side | Avg/trade | Account |", "|---|---|---|"]
        for k, s in res["cost_check"].items():
            L.append(f"| {k}% | {pct(s.get('avg'), 3)} | {pct(s.get('acct'))} |")
    return "\n".join(L) + "\n"


# ===================================================================== main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    if a.demo:
        bars = demo_bars()
    else:
        bars, start = [], datetime.fromisoformat(WARMUP).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc) - timedelta(minutes=20)
        while start < now:
            end = min(start + timedelta(days=92), now)
            got = fetch(["SPY"], "1Min", start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ"))
            bars.extend(got.get("SPY") or [])
            print(f"  SPY 1-minute bars to {end:%Y-%m-%d}: {len(bars)} ({time.time() - t0:.0f}s)", flush=True)
            start = end
    days = sessions(bars)
    cands = [f for d, b in sorted(days.items()) if d >= FROM and (f := facts(d, b))]
    print(f"{len(cands)} sessions with a full morning ({time.time() - t0:.0f}s)", flush=True)

    first = next(b for d, b in sorted(days.items()) if d >= FROM)
    spy_total = round((cands[-1]["bars"][-1][3] / first[0][0] - 1) * 100, 1) if cands else None
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "from": FROM,
           "days": len(cands), "cost_pct": COST * 100, "spy_total": spy_total,
           "rules": __doc__.split("Exact rules tested here")[1].split("Data:")[0].strip(),
           "default": DEFAULT, "variants": VARIANTS, "combos": []}
    for v in combos():
        res["combos"].append({"v": v, **split(run(cands, v))})
    print(f"{len(res['combos'])} setups ({time.time() - t0:.0f}s)", flush=True)

    top = sorted((c for c in res["combos"] if (c["train"].get("n") or 0) >= 100),
                 key=lambda c: -(c["train"].get("avg") or -9))
    if top:
        best = top[0]["v"]
        res["cost_check"] = {f"{x * 100:.2f}": {k: stats(run(cands, {**best, "cost": x})).get(k) for k in ("avg", "acct")}
                             for x in (0.0, 0.0001, 0.0002, 0.0005, 0.001)}
        res["best_trades_recent"] = sorted(run(cands, best), key=lambda t: t["d"])[-200:]
    res["default_trades_recent"] = sorted(run(cands, DEFAULT), key=lambda t: t["d"])[-200:]

    out = Path("/tmp/fib-kama-demo.json") if a.demo else OUT_DIR / "spy_kama.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, separators=(",", ":")))
    md = summary(res)
    (out.with_name("fib-kama-demo.md") if a.demo else OUT_DIR / "SPY_KAMA.md").write_text(md)
    d = next(c for c in res["combos"] if c["v"] == DEFAULT)["full"]
    print(f"default ({name(DEFAULT)}): {d.get('n')} trades, win {d.get('win')}%, avg {d.get('avg')}%, "
          f"PF {d.get('pf')}, account {d.get('acct')}% | SPY {spy_total}% -> {out} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
