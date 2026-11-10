"""Fibonacci Retracement: let the first 30 minutes draw the swing, then buy/sell the pullback to a fib level.

The idea (Giacomo, 11 Nov 2026):
  - Don't trade the open. Wait ~30 minutes and see how the stock is moving.
  - Draw Fibonacci retracement levels on that morning swing.
  - Wait for price to come back to a level and enter there, in the direction of the morning move.

Exact rules tested here (every one is a switch, see VARIANTS):
  Universe    S&P 100, current members; 20-day average dollar volume >= $10M.
  Window      watch the first 15, 30 (default), 45 or 60 minutes after the 09:30 New York open.
  Swing       the window's low and high. The move from the 09:30 open to the window's close must be at
              least 0.25%, 0.5% (default) or 1%. Up -> fibs from low (100%) to high (0%), we look to buy;
              down -> fibs from high to low, we look to short.
  Gap filter  "all" days (default), "gap": the open was at least 2% from yesterday's close, "quiet": within 1%.
  Entry level 38.2%, 50% or 61.8% (default) retracement of the swing.
  Entry       "touch" (default): a limit order at the level - filled the first time a 5-minute candle reaches
              it (at the candle's open if it gaps through). "confirm": wait for a 5-minute candle that reaches
              the level and then closes back on the trend side of it - enter at that close.
  Stop        78.6% (default) or 100% (the start of the swing), plus a small tick.
  Target      "swing" (default): back to the swing extreme (0%), "1.272" / "1.618": Fibonacci extensions
              beyond it, or "2R" (twice the entry-to-stop distance).
  Setup ends  if price hits the stop level before the entry, the setup is cancelled. No new entry after
              11:00, 12:00 (default) or the 15:55 close.
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

DEFAULT = {"win": "30", "move": "0.5", "gapf": "all", "level": "0.618", "entry": "touch",
           "stop": "0.786", "tp": "swing", "late": "1200", "exit": "eod"}
VARIANTS = {
    "win": ["15", "30", "45", "60"],
    "move": ["0.25", "0.5", "1"],
    "gapf": ["all", "gap", "quiet"],
    "level": ["0.382", "0.5", "0.618"],
    "entry": ["touch", "confirm"],
    "stop": ["0.786", "1"],
    "tp": ["swing", "1.272", "1.618", "2R"],
    "late": ["1100", "1200", "eod"],
    "exit": ["1200", "eod"],
}
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
    return {"s": s, "d": d, "o0": o0, "gap": r["o"] / r["pc"] - 1, "sw": sw, "bars": ohlc}


# ===================================================================== rules
def passes(c: dict, v: dict) -> bool:
    if abs(c["sw"][v["win"]][0]) < float(v["move"]) / 100:
        return False
    if v["gapf"] == "gap" and abs(c["gap"]) < 0.02:
        return False
    if v["gapf"] == "quiet" and abs(c["gap"]) > 0.01:
        return False
    return True


def trade(c: dict, v: dict) -> dict | None:
    b, n = c["bars"], len(c["bars"]) // 5
    k = int(v["win"]) // 5
    mv, hi, lo = c["sw"][v["win"]]
    sgn = 1 if mv > 0 else -1                                  # +1 = long (buy the pullback)
    s0, s1 = (lo, hi) if sgn > 0 else (hi, lo)                  # swing start -> swing end
    rng = s1 - s0                                               # signed
    if abs(rng) <= c["o0"] * 0.001:
        return None
    lvl = s1 - rng * float(v["level"])
    stop = (s1 - rng * float(v["stop"])) * (1 - sgn * TICK)
    if v["tp"] == "swing":
        tgt = s1
    elif v["tp"] == "2R":
        tgt = lvl + 2 * (lvl - stop)
    else:
        tgt = s0 + rng * float(v["tp"])
    late, last_m = MIN[v["late"]], MIN[v["exit"]]

    entry, ie = None, None
    for i in range(k, n):
        o, h, l, cl, m = b[5 * i:5 * i + 5]
        if m >= late or m >= last_m:
            return None
        reach = (l <= lvl) if sgn > 0 else (h >= lvl)
        broke = (l <= stop) if sgn > 0 else (h >= stop)
        if v["entry"] == "touch":
            if reach:
                entry = min(o, lvl) if sgn > 0 else max(o, lvl)
                ie = i
                if broke:                                       # same candle took the stop: cautious
                    px, why, at = stop, "stop", m
                    if (sgn > 0 and entry <= stop) or (sgn < 0 and entry >= stop):
                        return None                             # gapped straight through the stop
                    return finish(c, v, sgn, entry, ie, stop, tgt, px, why, at, mv)
                break
        else:
            if broke:
                return None
            if reach and ((cl > lvl) if sgn > 0 else (cl < lvl)):
                entry, ie = cl, i
                break
        if broke:
            return None
    if entry is None:
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
    return finish(c, v, sgn, entry, ie, stop, tgt, px, why, at, mv)


def finish(c, v, sgn, entry, ie, stop, tgt, px, why, at, mv) -> dict | None:
    risk = abs(entry - stop)
    if risk <= entry * 0.0003:
        return None
    ret = sgn * (px / entry - 1) - 2 * v.get("cost", COST)
    m_in = int(c["bars"][5 * ie + 4]) + (5 if v["entry"] == "confirm" else 0)
    return {"s": c["s"], "d": c["d"], "mv": round(mv * 100, 2), "dir": "long" if sgn > 0 else "short",
            "in": round(entry, 2), "tin": f"{m_in // 60:02d}:{m_in % 60:02d}", "sl": round(stop, 2),
            "tp": round(tgt, 2), "out": round(px, 2), "why": why,
            "at": f"{int(at) // 60:02d}:{int(at) % 60:02d}", "pct": round(ret * 100, 3),
            "r": round(sgn * (px - entry) / risk, 2), "rv": abs(mv)}


def run(cands: list[dict], v: dict) -> list[dict]:
    out = []
    for c in cands:
        if passes(c, v):
            t = trade(c, v)
            if t:
                out.append(t)
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
         "## Default rules", "", "`" + " | ".join(f"{k}={v}" for k, v in res["default"].items()) + "`", "",
         hdr, row("default", res["base_split"]), "",
         "## One switch changed at a time", "", hdr]
    for k, opts in res["sweeps"].items():
        for o, s in opts.items():
            L.append(row(f"{k}={o}" + (" (default)" if DEFAULT[k] == o else ""), s))
    top = sorted((c for c in res["combos"] if (c["train"].get("n") or 0) >= 200),
                 key=lambda c: -(c["train"].get("avg") or -9))[:10]
    L += ["", "## Best 10 combinations chosen on TRAIN only (min 200 train trades) - check whether TEST holds", "", hdr]
    for c in top:
        L.append(row(" ".join(f"{k}={c['v'][k]}" for k in ("win", "move", "level", "entry", "stop", "tp", "exit")), c))
    held = sum(1 for c in top if (c["test"].get("avg") or 0) > 0)
    L += ["", f"Of those 10, **{held}** were still profitable per trade in the test years.",
          f"Combinations tested: {len(res['combos'])}. Positive in BOTH train and test: "
          f"{sum(1 for c in res['combos'] if (c['train'].get('avg') or 0) > 0 and (c['test'].get('avg') or 0) > 0)}."]
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
           "costs_pct_a_side": COST * 100}
    base = run(cands, DEFAULT)
    res["base"] = stats(base, spy)
    res["base"].pop("_daily", None)
    res["base_split"] = split(base, spy)
    res["base_trades_recent"] = [{k: t[k] for k in t if k != "rv"} for t in sorted(base, key=lambda t: t["d"])[-300:]]

    # how often does a pullback to each level happen at all, and how often does price then make it back to the swing?
    res["level_hits"] = {}
    for lv in VARIANTS["level"]:
        ts = run(cands, {**DEFAULT, "level": lv, "tp": "swing", "exit": "eod"})
        n_days = sum(1 for c in cands if passes(c, DEFAULT))
        res["level_hits"][lv] = {"setups": n_days, "filled": len(ts),
                                 "back_to_swing": sum(t["why"] == "target" for t in ts),
                                 "stopped": sum(t["why"] == "stop" for t in ts)}

    res["cost_check"] = {f"{cst * 100:.2f}": cut(stats(run(cands, {**DEFAULT, "cost": cst}), spy))
                         for cst in (0.0, 0.0003, 0.0006, 0.001)}
    res["sweeps"] = {k: {o: split(run(cands, {**DEFAULT, k: o}), spy) for o in opts} for k, opts in VARIANTS.items()}
    print(f"sweeps done ({time.time() - t0:.0f}s)", flush=True)

    # the move threshold only filters days, so simulate once at the smallest move and filter the trades
    res["combos"] = []
    lo_mv = VARIANTS["move"][0]
    for w in VARIANTS["win"]:
        for lv in VARIANTS["level"]:
            for en in VARIANTS["entry"]:
                for st in VARIANTS["stop"]:
                    for tp in VARIANTS["tp"]:
                        for ex in VARIANTS["exit"]:
                            v = {**DEFAULT, "win": w, "move": lo_mv, "level": lv, "entry": en,
                                 "stop": st, "tp": tp, "exit": ex}
                            ts = run(cands, v)
                            for mv in VARIANTS["move"]:
                                sub = [t for t in ts if t["rv"] >= float(mv) / 100]
                                res["combos"].append({"v": {k: ({**v, "move": mv})[k] for k in PAGE_KEYS},
                                                      **split(sub, spy)})
    print(f"{len(res['combos'])} combinations ({time.time() - t0:.0f}s)", flush=True)

    out = Path("/tmp/fib-demo.json") if a.demo else OUT_DIR / "backtest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    res["base"].pop("_daily", None)
    out.write_text(json.dumps(res, separators=(",", ":")))
    md = summary(res)
    (out.with_name("fib-demo-SUMMARY.md") if a.demo else OUT_DIR / "SUMMARY.md").write_text(md)
    b = res["base"]
    print(f"default: {b.get('n')} trades, win {b.get('win')}%, avg {b.get('avg')}%, PF {b.get('pf')}, "
          f"account {b.get('acct_total')}% (max DD {b.get('acct_maxdd')}%) | SPY {spy_total}% -> {out} "
          f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
