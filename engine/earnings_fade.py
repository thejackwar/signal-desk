"""Earnings-day fade: wait a few minutes after the open on earnings day, then trade the move.

The idea (Giacomo, 18 Nov 2026, before Walmart's Q3 report): on Walmart's earnings days since 2018 the
stock closed below its opening price 25 times out of 35. Shorting the open with a 2% stop made
~+0.9% a trade on daily bars - but daily bars can't say what happened in the first minutes.
This test waits to see the move first:

  Wait        let the first 5, 15 (default) or 30 minutes trade: that's the "opening range".
  Entry       "break" (default): short when price breaks BELOW the opening-range low (the gap is failing);
              "below_open": short at the end of the wait only if price is below the day's open;
              "any": short at the end of the wait whatever happened (baseline).
  Skip        if price breaks ABOVE the opening-range high before the short triggers, the move is
              still going up: no trade (only for "break").
  Deadline    no new entry after 11:00 New York.
  Stop        "range" (default): just above the opening-range high; "1" / "2": 1% / 2% above entry.
  Target      "none" (default, hold to the close), "1R" or "2R" (R = entry -> stop distance).
  Exit        15:59 close (default), 12:00, 10:30 (first hour) or 10:00 (first 30 minutes).
  Wait 1      = short the first minute's close: roughly "short the open".
  Filter      every report / gap up / gap up > 2% / gap down / beat / miss or in line / Q3-type (Nov) only.
  Side        "short" (the fade). "long" mirrors it: buy the break ABOVE the range high (follow the move).
  Exit rules  stop first if a bar hits both (cautious); a gap through a level fills at the bar's open.
  Costs       0.05% a side (0.10% round trip - CFD spreads at an earnings open are wide).

Also records the real pre-market: price at 09:25 vs the previous close, from 04:00-09:29 bars.

Data: Alpaca SIP 1-minute bars incl. extended hours (split/dividend adjusted), earnings dates from
docs/data/earnings/<TICKER>.json (written by engine/earnings.py). Alpaca SIP history starts 2016.

    python engine/earnings_fade.py                    # WMT (default), needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/earnings_fade.py --tickers WMT,TGT,COST
    python engine/earnings_fade.py --sp100            # every S&P 100 stock (slow: ~1 request per report)
    python engine/earnings_fade.py --demo             # synthetic bars, no keys, writes /tmp/earnings-fade-demo.json
Output: docs/data/earnings_fade/<TICKER>.json  (+ summary.json with every variant across all tickers run)
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from open_breakout import NY, fetch, ny  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EARN = ROOT / "docs" / "data" / "earnings"
OUT_DIR = ROOT / "docs" / "data" / "earnings_fade"
FROM = "2018-01-01"
COST = 0.0005            # per side
TICK = 0.0005            # 0.05% buffer beyond a level
DEADLINE = 660           # 11:00 NY, minutes from midnight
OPEN_M, CLOSE_M = 570, 959

WAITS = [1, 5, 15, 30]
ENTRIES = ["break", "below_open", "any"]
STOPS = ["range", "1", "2"]
TARGETS = ["none", "1R", "2R"]
EXITS = ["close", "1200", "1030", "1000"]
SIDES = ["short", "long"]
FILTERS = {
    "all": lambda e: True,
    "gap_up": lambda e: e["gap"] > 0,
    "gap_up_2": lambda e: e["gap"] > 2,
    "gap_down": lambda e: e["gap"] < 0,
    "beat": lambda e: e["res"] == "beat",
    "miss_inline": lambda e: e["res"] != "beat",
    "nov_only": lambda e: e["day"][5:7] == "11",
}


# ===================================================================== data
def reports(t: str) -> list[dict]:
    p = EARN / f"{t}.json"
    if not p.exists():
        print(f"  {t}: no earnings file, skipped", flush=True)
        return []
    out = []
    for e in json.loads(p.read_text())["events"]:
        if e["d"] < FROM or e.get("e") is None or e.get("r") is None:
            continue
        res = "beat" if e["r"] > e["e"] else ("miss" if e["r"] < e["e"] else "inline")
        out.append({"d": e["d"], "when": e.get("t", "bmo"), "e": e["e"], "r": e["r"], "s": e.get("s"), "res": res})
    return sorted(out, key=lambda x: x["d"])


def split_days(bars: list[dict]):
    by = defaultdict(list)
    for b in bars:
        x = ny(b["t"])
        by[x.date().isoformat()].append((x.hour * 60 + x.minute, b))
    sessions = sorted(k for k, v in by.items() if any(m == OPEN_M for m, _ in v))
    return by, sessions


def session_for(by, sessions, d: str, when: str):
    # bmo / during market: reaction session is the report day; amc: the next session
    after = [s for s in sessions if (s > d if when == "amc" else s >= d)]
    if not after:
        return None
    sd = after[0]
    prev = [s for s in sessions if s < sd]
    if not prev:
        return None
    pcl = [b for m, b in by[prev[-1]] if OPEN_M <= m <= CLOSE_M]
    if not pcl:
        return None
    return sd, pcl[-1]["c"], sorted(by[sd], key=lambda x: x[0])


# ===================================================================== simulation
def facts(info: dict, sd: str, pc: float, bars: list[tuple[int, dict]]) -> dict | None:
    reg = [(m, b) for m, b in bars if OPEN_M <= m <= CLOSE_M]
    if len(reg) < 60 or reg[0][0] != OPEN_M:
        return None
    pre = [b for m, b in bars if 240 <= m <= 565]
    o = reg[0][1]["o"]
    return {**info, "day": sd, "pc": pc, "open": o,
            "pm925": round((pre[-1]["c"] / pc - 1) * 100, 2) if pre else None,
            "gap": round((o / pc - 1) * 100, 2),
            "oc": round((reg[-1][1]["c"] / o - 1) * 100, 2),
            "_reg": [(m, b["o"], b["h"], b["l"], b["c"]) for m, b in reg]}


def simulate(f: dict, wait: int, entry: str, stop: str, tp: str, exit_: str, side: str) -> dict | None:
    reg = f["_reg"]
    sgn = -1 if side == "short" else 1
    rng = [x for x in reg if x[0] < OPEN_M + wait]
    rest = [x for x in reg if x[0] >= OPEN_M + wait]
    if not rng or not rest:
        return None
    rh, rl, o = max(x[2] for x in rng), min(x[3] for x in rng), f["open"]
    last_m = {"1000": 599, "1030": 629, "1200": 715}.get(exit_, CLOSE_M)
    px, tin, k0 = None, None, None
    if entry in ("any", "below_open"):
        c = rng[-1][4]
        if entry == "below_open" and (c >= o if side == "short" else c <= o):
            return None
        px, tin, k0 = c, rest[0][0], 0
    else:  # break of the range in the trade's direction; the opposite break first cancels
        lvl_in = rl * (1 - TICK) if side == "short" else rh * (1 + TICK)
        lvl_out = rh * (1 + TICK) if side == "short" else rl * (1 - TICK)
        for k, (m, bo, bh, bl, bc) in enumerate(rest):
            if m >= DEADLINE:
                return None
            hit_in = bl <= lvl_in if side == "short" else bh >= lvl_in
            hit_out = bh >= lvl_out if side == "short" else bl <= lvl_out
            if hit_in and hit_out:
                return None                      # can't tell which came first: skip (cautious)
            if hit_out:
                return None                      # the move kept going the other way: no trade
            if hit_in:
                gap_through = bo <= lvl_in if side == "short" else bo >= lvl_in
                px, tin, k0 = (bo if gap_through else lvl_in), m, k
                break
        if px is None:
            return None
    if stop == "range":
        sl = rh * (1 + TICK) if side == "short" else rl * (1 - TICK)
    else:
        sl = px * (1 - sgn * float(stop) / 100)
    risk = abs(sl - px)
    if risk <= 0 or (sl - px) * sgn > 0:
        return None
    tpx = None if tp == "none" else px + sgn * risk * float(tp[0])
    out, why, tout = None, "time", None
    for j, (m, bo, bh, bl, bc) in enumerate(rest[k0:]):
        if m > last_m:
            break
        stop_hit = bh >= sl if side == "short" else bl <= sl
        tgt_hit = tpx is not None and (bl <= tpx if side == "short" else bh >= tpx)
        if stop_hit:
            gap_thru = bo >= sl if side == "short" else bo <= sl
            out, why, tout = (bo if gap_thru else sl), "stop", m
            break
        if j == 0 and entry == "break" and ((bc >= sl) if side == "short" else (bc <= sl)):
            out, why, tout = sl, "stop", m
            break
        if tgt_hit:
            gap_thru = bo <= tpx if side == "short" else bo >= tpx
            out, why, tout = (bo if gap_thru else tpx), "target", m
            break
        out, tout = bc, m
    if out is None:
        return None
    pct = ((out / px - 1) * sgn - 2 * COST) * 100
    hm = lambda m: f"{m // 60:02d}:{m % 60:02d}"
    return {"in": round(px, 2), "tin": hm(tin), "sl": round(sl, 2), "out": round(out, 2), "tout": hm(tout),
            "why": why, "pct": round(pct, 3), "r": round(pct / (risk / px * 100), 2)}


def stats(pcts: list[float]) -> dict:
    if not pcts:
        return {"n": 0}
    wins = [p for p in pcts if p > 0]
    loss = [p for p in pcts if p <= 0]
    eq, peak, dd = 0.0, 0.0, 0.0
    for p in pcts:
        eq += p
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {"n": len(pcts), "win": round(len(wins) / len(pcts) * 100, 1), "avg": round(sum(pcts) / len(pcts), 3),
            "total": round(sum(pcts), 2), "worst": round(min(pcts), 2), "best": round(max(pcts), 2),
            "pf": round(sum(wins) / -sum(loss), 2) if loss and sum(loss) < 0 else None, "maxdd": round(dd, 2)}


def run(all_facts: list[dict]) -> list[dict]:
    rows = []
    for wait, entry, stop, tp, ex, side, (fname, flt) in itertools.product(
            WAITS, ENTRIES, STOPS, TARGETS, EXITS, SIDES, FILTERS.items()):
        trades = []
        for f in all_facts:
            if not flt(f):
                continue
            t = simulate(f, wait, entry, stop, tp, ex, side)
            if t:
                trades.append(t["pct"])
        eligible = sum(1 for f in all_facts if flt(f))
        rows.append({"wait": wait, "entry": entry, "stop": stop, "tp": tp, "exit": ex, "side": side,
                     "filter": fname, "eligible": eligible, **stats(trades)})
    return rows


# ===================================================================== demo
def demo_bars(d: str, when: str):
    rnd = random.Random(d)
    by = defaultdict(list)
    base = 100.0
    days = [date.fromisoformat(d) + timedelta(days=i) for i in (-4, -3, -2, -1, 0, 1, 2, 3)]
    wk = [x.isoformat() for x in days if x.weekday() < 5]
    rx = d if when != "amc" else next(x for x in wk if x > d)
    for dd in days:
        if dd.weekday() >= 5:
            continue
        ds = dd.isoformat()
        is_rx = ds == rx
        gap = rnd.gauss(0, 4) if is_rx else rnd.gauss(0, 0.5)
        p = base * (1 + gap / 100)
        drift = rnd.gauss(-0.004 if is_rx else 0, 0.0015) / 390
        for m in range(240, 960):
            vol = 0.0015 if OPEN_M <= m <= CLOSE_M else 0.0004
            o = p
            p = p * (1 + drift + rnd.gauss(0, vol))
            h, l = max(o, p) * (1 + abs(rnd.gauss(0, vol / 3))), min(o, p) * (1 - abs(rnd.gauss(0, vol / 3)))
            by[ds].append((m, {"o": o, "h": h, "l": l, "c": p}))
        base = p
    sessions = sorted(by)
    return by, sessions


# ===================================================================== main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="WMT")
    ap.add_argument("--sp100", action="store_true")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    tickers = [x.strip().upper() for x in a.tickers.split(",") if x.strip()]
    if a.sp100:
        tickers = [l.strip().replace("-", ".") for l in (ROOT / "engine" / "tickers.txt").read_text().splitlines()
                   if l.strip() and not l.startswith("#")]
    all_facts = []
    for t in tickers:
        reps, facts_t = reports(t), []
        print(f"{t}: {len(reps)} reports since {FROM}", flush=True)
        for rp in reps:
            try:
                if a.demo:
                    by, sessions = demo_bars(rp["d"], rp["when"])
                else:
                    d0 = date.fromisoformat(rp["d"])
                    start = datetime.combine(d0 - timedelta(days=6), datetime.min.time(), NY).astimezone(timezone.utc)
                    end = min(datetime.combine(d0 + timedelta(days=6), datetime.min.time(), NY).astimezone(timezone.utc),
                              datetime.now(timezone.utc) - timedelta(minutes=20))
                    bars = fetch([t], "1Min", start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 end.strftime("%Y-%m-%dT%H:%M:%SZ")).get(t, [])
                    by, sessions = split_days(bars)
                got = session_for(by, sessions, rp["d"], rp["when"])
                if not got:
                    print(f"  {t} {rp['d']}: no bars", flush=True)
                    continue
                f = facts({"t": t, **rp}, *got)
                if f:
                    facts_t.append(f)
            except Exception as e:  # noqa: BLE001 - one bad day must not sink the run
                print(f"  {t} {rp['d']}: skipped ({e})", flush=True)
        all_facts += facts_t
        if facts_t and not a.demo:
            write_ticker(t, facts_t)
    rows = run(all_facts)
    summary = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "tickers": tickers,
               "reports": len(all_facts), "cost_round_trip_pct": COST * 200, "variants": rows}
    out = Path("/tmp/earnings-fade-demo.json") if a.demo else OUT_DIR / ("summary.json" if len(tickers) > 1
                                                                          else f"summary_{tickers[0]}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, separators=(",", ":")))
    print_report(rows, all_facts)
    print("wrote", out)


def write_ticker(t: str, facts_t: list[dict]):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    evs = []
    for f in facts_t:
        ev = {k: v for k, v in f.items() if not k.startswith("_")}
        ev["trades"] = {f"{w}|{e}|{s}|{x}": simulate(f, w, e, s, "none", x, "short")
                        for w in WAITS for e in ENTRIES for s in STOPS for x in EXITS}
        evs.append(ev)
    (OUT_DIR / f"{t}.json").write_text(json.dumps({"ticker": t, "events": evs}, separators=(",", ":")))


def print_report(rows: list[dict], facts_: list[dict]):
    print(f"\n{len(facts_)} earnings days with 1-minute bars. Real pre-market (09:25 vs prev close) and day:")
    for f in facts_:
        print(f"  {f['t']} {f['day']} {f['res']:6} pm925 {f['pm925']!s:>6}  gap {f['gap']:+6.2f}  open->close {f['oc']:+6.2f}")
    print("\nShort, hold to close, no target - by wait / entry / stop / filter:")
    print(f"{'exit':>5} {'wait':>4} {'entry':11} {'stop':6} {'filter':12} {'n':>3}/{'of':<3} {'win%':>5} {'avg%':>6} {'total%':>7} {'worst':>6} {'pf':>5}")
    for r in rows:
        if r["side"] == "short" and r["tp"] == "none" and r["filter"] in ("all", "gap_up", "nov_only") and r["n"]:
            print(f"{r['exit']:>5} {r['wait']:>4} {r['entry']:11} {r['stop']:6} {r['filter']:12} {r['n']:>3}/{r['eligible']:<3} "
                  f"{r['win']:5.0f} {r['avg']:+6.2f} {r['total']:+7.1f} {r['worst']:+6.2f} {r['pf']!s:>5}")
    best = sorted([r for r in rows if r.get("n", 0) >= 8], key=lambda r: -r["avg"])[:15]
    print("\nTop 15 variants with >= 8 trades (beware: picked in hindsight):")
    for r in best:
        print(f"  {r['side']:5} wait {r['wait']:>2} {r['entry']:10} stop {r['stop']:5} tp {r['tp']:4} exit {r['exit']:5} "
              f"{r['filter']:11} n={r['n']:>3} win {r['win']:.0f}% avg {r['avg']:+.2f}% total {r['total']:+.1f}%")


if __name__ == "__main__":
    main()
