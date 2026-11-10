"""Open Drive & Snap-Back: ride the volume-driven opening push, then fade it back to the opening price.

The idea (Giacomo, 10 Nov 2026):
  - At the open an influx of volume pushes a stock "unfairly" away from its opening price.
  - Leg 1: trade WITH that push (continuation) while it lasts.
  - Leg 2: when the push runs out, flip and trade the reversion back to the opening price.

Exact rules tested here (every one is a switch, see VARIANTS):
  Universe    S&P 100, current members; 20-day average dollar volume >= $10M.
  ATR         14-day daily ATR as of yesterday's close (no look-ahead), used to size every distance.
  Drive       the first 5-minute candle (09:30-09:35 New York):
                - moves at least 0.15 / 0.25 (default) / 0.40 ATR from the 09:30 open,
                - on relative volume >= 1.5 / 2 (default) / 3x the stock's average first-5-minute volume
                  over the previous 20 sessions ("any" = no volume filter),
                - and closes in the top (up) / bottom (down) 25% (default) or 50% of its range ("off" = no check).
  Leg 1       enter with the drive at 09:35 (the first candle's close).
              Stop: the first candle's midpoint ("mid", default) or the opening price ("open").
              Exit, first of:
                - exhaustion: from 09:40 (default; or 09:36) the first 1-minute candle that closes under the
                  previous candle's low (long) / over its high (short); "2" = under the lowest low of the
                  previous two candles (stricter),
                - extension target: 0.5 / 0.75 / 1 (default) ATR from the open, or "none",
                - time: 10:00 (default), 10:15 or 10:30.
  Leg 2       only after Leg 1 ended on exhaustion ("exh", default) - or also after the extension target
              ("exh+tgt") - and only if price is still at least 0 / 0.25 / 0.5 (default) / 0.75 ATR away
              from the open. Enter against the drive at that candle's close.
              Target: the opening price ("open", default) or halfway back to it ("half").
              Stop: just beyond the day's high (after an up-drive) / low so far. Time stop 10:30 or 11:00 (default).
  Exit rules  stop first if a candle hits both (cautious); a gap through a level fills at the candle's open.
  Costs       0.03% a side, per leg.
  Account     each day up to 5 setups (the biggest drives in ATR units first), 20% of the account each,
              no leverage; both legs use the same 20% one after the other.

Data: Alpaca SIP 1-minute regular-session bars 09:30-11:00, split/dividend adjusted, from 2021.
Current S&P 100 members only (survivorship bias).

    python engine/open_snapback.py           # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/open_snapback.py --demo    # synthetic data, writes /tmp/snapback-demo.json
Output: docs/data/snapback/backtest.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from array import array
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from open_breakout import NY, WARMUP, daily_table, demo_daily, fetch, load_tickers, ny  # noqa: E402
from opening_reversal import stats  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "data" / "snapback"
FROM = "2021-01-04"
COST = 0.0003
TICK = 0.0005
DRIVE_MIN = 0.15          # loosest drive kept (ATR units)
RVOL_DAYS = 20

DEFAULT = {"drive": "0.25", "rvol": "2", "cloc": "25", "stop1": "mid", "exfrom": "0940", "exh": "1",
           "ext": "1", "last1": "1000", "leg2on": "exh", "dist": "0.5", "tgt2": "open", "last2": "1100"}
VARIANTS = {
    "drive": ["0.15", "0.25", "0.4"],
    "rvol": ["any", "1.5", "2", "3"],
    "cloc": ["off", "50", "25"],
    "stop1": ["mid", "open"],
    "exfrom": ["0936", "0940"],
    "exh": ["1", "2"],
    "ext": ["0.5", "0.75", "1", "none"],
    "last1": ["1000", "1015", "1030"],
    "leg2on": ["exh", "exh+tgt"],
    "dist": ["0", "0.25", "0.5", "0.75"],
    "tgt2": ["open", "half"],
    "last2": ["1030", "1100"],
}
MIN = {"0936": 576, "0940": 580, "1000": 600, "1015": 615, "1030": 630, "1100": 660}


# ===================================================================== data
def atr_table(daily: dict[str, list[dict]]) -> dict[tuple[str, str], float]:
    """(symbol, day) -> 14-day ATR as a fraction of yesterday's close, known before the open."""
    out = {}
    for s, bars in daily.items():
        tr = []
        for i in range(1, len(bars)):
            h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
            tr.append(max(h - l, abs(h - pc), abs(l - pc)))
            # tr[i-1] is day i's range; for day i+1 use days i-13..i
            if i >= 14 and i + 1 < len(bars):
                out[(s, bars[i + 1]["t"][:10])] = sum(tr[-14:]) / 14 / bars[i]["c"]
    return out


def build_candidates(tab: dict, atr: dict, sp100: set[str], demo=None) -> list[dict]:
    by_day: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for s, rows in tab.items():
        if s not in sp100:
            continue
        for r in rows:
            if r["d"] >= FROM and r["usd"] >= 10e6 and (s, r["d"]) in atr:
                by_day[r["d"]].append((s, r))
    days = sorted(by_day)
    print(f"{sum(map(len, by_day.values()))} stock-days over {len(days)} sessions", flush=True)
    vol5: dict[str, deque] = defaultdict(lambda: deque(maxlen=RVOL_DAYS))
    cands = []
    for k, d in enumerate(days):
        items = by_day[d]
        if demo:
            intr = demo(d, items)
        else:
            start = datetime.fromisoformat(d + "T09:30").replace(tzinfo=NY).astimezone(timezone.utc)
            end = min(datetime.fromisoformat(d + "T11:00").replace(tzinfo=NY).astimezone(timezone.utc),
                      datetime.now(timezone.utc) - timedelta(minutes=20))
            syms, intr = [s for s, _ in items], {}
            try:
                for j in range(0, len(syms), 100):
                    intr.update(fetch(syms[j:j + 100], "1Min", start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      end.strftime("%Y-%m-%dT%H:%M:%SZ")))
            except Exception as e:
                print(f"  {d}: skipped ({e})", flush=True)
                continue
        for s, r in items:
            c = facts(s, d, atr[(s, d)], intr.get(s) or [], vol5[s])
            if c:
                cands.append(c)
        if k % 100 == 0:
            print(f"  {d}: {k + 1}/{len(days)} sessions, {len(cands)} candidates", flush=True)
    return cands


def facts(s: str, d: str, atr: float, bars: list[dict], hist: deque) -> dict | None:
    today = []
    for b in bars:
        t = ny(b["t"])
        if t.date().isoformat() != d:
            continue
        m = t.hour * 60 + t.minute
        if 570 <= m < 660:
            today.append((m, b))
    if len(today) < 30 or today[0][0] != 570:
        return None
    first = [b for m, b in today if m < 575]
    if len(first) < 3:
        return None
    v5 = sum(b["v"] for b in first)
    avg = sum(hist) / len(hist) if len(hist) >= 10 else None
    hist.append(v5)
    if avg is None or atr <= 0:
        return None
    o0 = first[0]["o"]
    fh, fl, fc = max(b["h"] for b in first), min(b["l"] for b in first), first[-1]["c"]
    drv = (fc / o0 - 1) / atr
    if abs(drv) < DRIVE_MIN or today[-1][0] < 620:
        return None
    rng = fh - fl
    loc = (fc - fl) / rng if rng > 0 else 0.5            # 1 = closed at the high
    ohlc = array("f")
    for m, b in today:
        if m >= 575:
            ohlc.extend((b["o"], b["h"], b["l"], b["c"], m))
    return {"s": s, "d": d, "o0": o0, "atr": atr, "drv": drv, "rvol": v5 / avg if avg else 0,
            "loc": loc if drv > 0 else 1 - loc, "fh": fh, "fl": fl, "fc": fc, "bars": ohlc}


# ===================================================================== rules
def passes(c: dict, v: dict) -> bool:
    if abs(c["drv"]) < float(v["drive"]):
        return False
    if v["rvol"] != "any" and c["rvol"] < float(v["rvol"]):
        return False
    if v["cloc"] != "off" and c["loc"] < 1 - float(v["cloc"]) / 100:
        return False
    return True


def _walk(b, i0, n, sgn, entry, stop, tgt, last_m, exh=None, exfrom=None, why_t="target"):
    """Walk 1-minute candles from index i0. Returns (price, why, minute, index)."""
    for i in range(i0, n):
        o, h, l, cl, m = b[5 * i:5 * i + 5]
        if (l <= stop) if sgn > 0 else (h >= stop):
            return (min(o, stop) if sgn > 0 else max(o, stop)), "stop", m, i
        if tgt is not None and ((h >= tgt) if sgn > 0 else (l <= tgt)):
            return (max(o, tgt) if sgn > 0 else min(o, tgt)), why_t, m, i
        if exh and m >= exfrom and i >= exh:
            ref = [b[5 * j + 2] if sgn > 0 else b[5 * j + 1] for j in range(i - exh, i)]
            if (cl < min(ref)) if sgn > 0 else (cl > max(ref)):
                return cl, "exh", m, i
        if m + 1 >= last_m:
            return cl, "time", m, i
    i = n - 1
    return b[5 * i + 3], "time", b[5 * i + 4], i


def hhmm(m: float) -> str:
    m = int(m)
    return f"{m // 60:02d}:{m % 60:02d}"


def setup(c: dict, v: dict, leg1=True, leg2=True) -> dict | None:
    b, n = c["bars"], len(c["bars"]) // 5
    o0, atr_px = c["o0"], c["atr"] * c["o0"]
    sgn = 1 if c["drv"] > 0 else -1
    cost = v.get("cost", COST)
    entry = c["fc"]
    stop = (c["fh"] + c["fl"]) / 2 if v["stop1"] == "mid" else o0
    stop *= (1 - sgn * TICK)
    if sgn * (entry - stop) <= entry * 0.0005:
        return None
    tgt = None if v["ext"] == "none" else o0 + sgn * float(v["ext"]) * atr_px
    if tgt is not None and sgn * (tgt - entry) <= 0:
        return None                                   # the drive already went past the target
    exh = int(v["exh"]) if v.get("exh") != "off" else None
    px, why, at, i1 = _walk(b, 0, n, sgn, entry, stop, tgt, MIN[v["last1"]], exh, MIN[v["exfrom"]])
    r1 = sgn * (px / entry - 1) - 2 * cost
    t = {"s": c["s"], "d": c["d"], "drv": round(c["drv"], 2), "rvol": round(c["rvol"], 1),
         "dir": "long" if sgn > 0 else "short", "open": round(o0, 2),
         "l1": {"in": round(entry, 2), "sl": round(stop, 2), "tp": round(tgt, 2) if tgt else None,
                "out": round(px, 2), "why": why, "at": hhmm(at + 1), "pct": round(r1 * 100, 3)},
         "l2": None, "rv": abs(c["drv"])}
    pct = r1 * 100 if leg1 else 0.0
    ok = why == "exh" or (why == "target" and v["leg2on"] == "exh+tgt")
    if leg2 and ok and sgn * (px - o0) >= float(v["dist"]) * atr_px and i1 + 1 < n:
        ext = max(b[5 * j + 1] for j in range(i1 + 1)) if sgn > 0 else min(b[5 * j + 2] for j in range(i1 + 1))
        ext = max(ext, c["fh"]) if sgn > 0 else min(ext, c["fl"])
        s2, e2 = -sgn, px
        st2 = ext * (1 + sgn * TICK)
        tg2 = o0 if v["tgt2"] == "open" else (e2 + o0) / 2
        if abs(e2 - st2) > e2 * 0.0005 and s2 * (tg2 - e2) > 0:
            p2, w2, a2, _ = _walk(b, i1 + 1, n, s2, e2, st2, tg2, MIN[v["last2"]])
            r2 = s2 * (p2 / e2 - 1) - 2 * cost
            t["l2"] = {"in": round(e2, 2), "tin": hhmm(at + 1), "sl": round(st2, 2), "tp": round(tg2, 2),
                       "out": round(p2, 2), "why": w2, "at": hhmm(a2 + 1), "pct": round(r2 * 100, 3)}
            pct += r2 * 100
    if not leg1 and t["l2"] is None:
        return None
    t["pct"] = round(pct, 3)
    t["why"] = (t["l2"] or t["l1"])["why"] if not leg1 else t["l1"]["why"]
    t["r"] = round(t["pct"] / (abs(entry - stop) / entry * 100), 2)
    return t


def run(cands: list[dict], v: dict, leg1=True, leg2=True) -> list[dict]:
    out = []
    for c in cands:
        if passes(c, v):
            t = setup(c, v, leg1, leg2)
            if t:
                out.append(t)
    return out


KEYS = ("n", "win", "avg", "pf", "acct_total", "acct_cagr", "acct_maxdd")


def summary(ts: list[dict], spy: dict) -> dict:
    st = stats(ts, spy)
    st.pop("exits", None)
    if ts:
        st["exits_l1"] = {k: sum(t["l1"]["why"] == k for t in ts) for k in ("exh", "target", "stop", "time")}
        l2 = [t["l2"] for t in ts if t["l2"]]
        st["leg2_n"] = len(l2)
        if l2:
            st["exits_l2"] = {k: sum(x["why"] == k for x in l2) for k in ("target", "stop", "time")}
            st["leg2_win"] = round(100 * sum(x["pct"] > 0 for x in l2) / len(l2), 1)
            st["leg2_avg"] = round(sum(x["pct"] for x in l2) / len(l2), 3)
        st["leg1_win"] = round(100 * sum(t["l1"]["pct"] > 0 for t in ts) / len(ts), 1)
        st["leg1_avg"] = round(sum(t["l1"]["pct"] for t in ts) / len(ts), 3)
    return st


def cut(st: dict) -> dict:
    return {k: st.get(k) for k in KEYS + ("leg1_avg", "leg2_n", "leg2_avg")}


def split(ts, spy):
    return {"train": cut(stats([t for t in ts if t["d"] < "2024-01-01"], spy)),
            "test": cut(stats([t for t in ts if t["d"] >= "2024-01-01"], spy))}


# ===================================================================== demo data
def demo_intraday(d: str, items: list) -> dict:
    rnd, out = random.Random(d), {}
    for s, r in items:
        dt = datetime.fromisoformat(d + "T09:30").replace(tzinfo=NY)
        px, bars = r["o"], []
        drift, push = rnd.gauss(0, 0.0012), rnd.random() < 0.3
        for i in range(90):
            o = px
            dr = drift * (2 if push else 1) if i < 12 else -drift * 0.5 if i < 40 else 0
            c = o * (1 + dr + rnd.gauss(0, 0.0008))
            v = (6e5 if push else 2e5) if i < 5 else 1e5
            bars.append({"t": (dt + timedelta(minutes=i)).isoformat(), "o": o, "h": max(o, c) * 1.0004,
                         "l": min(o, c) * 0.9996, "c": c, "v": v})
            px = c
        out[s] = bars
    return out


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
    tab, atr = daily_table(daily), atr_table(daily)
    spy = {}
    for y in sorted({b["t"][:4] for b in spy_rows if b["t"][:10] >= FROM}):
        yb = [b for b in spy_rows if b["t"][:4] == y]
        prev = [b for b in spy_rows if b["t"][:4] < y]
        spy[y] = round((yb[-1]["c"] / (prev[-1]["c"] if prev else yb[0]["o"]) - 1) * 100, 1)
    first = [b for b in spy_rows if b["t"][:10] < FROM]
    spy_total = round((spy_rows[-1]["c"] / (first[-1]["c"] if first else spy_rows[0]["o"]) - 1) * 100, 1)

    cands = build_candidates(tab, atr, sp100, demo_intraday if a.demo else None)
    print(f"{len(cands)} stock-days with an opening drive >= {DRIVE_MIN} ATR ({time.time() - t0:.0f}s)", flush=True)

    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "from": FROM,
           "rules": __doc__.split("Exact rules tested here")[1].split("Data:")[0].strip(),
           "default": DEFAULT, "variants": VARIANTS, "spy_by_year": spy, "spy_total": spy_total,
           "costs_pct_a_side": COST * 100}

    base = run(cands, DEFAULT)
    res["base"] = summary(base, spy)
    res["base"].update(split(base, spy))
    res["base_trades_recent"] = [{k: t[k] for k in t if k != "rv"} for t in sorted(base, key=lambda t: t["d"])[-300:]]

    # the pieces on their own, same drive filter
    l1 = run(cands, DEFAULT, leg2=False)
    l2 = run(cands, DEFAULT, leg1=False)
    hold = run(cands, {**DEFAULT, "exh": "off", "ext": "none", "last1": "1030"}, leg2=False)
    res["compare"] = {
        "both_legs": {**cut(summary(base, spy)), **split(base, spy)},
        "leg1_only": {**cut(summary(l1, spy)), **split(l1, spy)},
        "leg2_only": {**cut(summary(l2, spy)), **split(l2, spy)},
        "ride_drive_to_1030": {**cut(summary(hold, spy)), **split(hold, spy)},
    }

    # how often does a qualifying drive come all the way back to the open by 11:00?
    back = {"drives": 0, "touched_open": 0, "made_1atr_ext": 0}
    for c in cands:
        if not passes(c, DEFAULT):
            continue
        b, sgn = c["bars"], (1 if c["drv"] > 0 else -1)
        back["drives"] += 1
        hit = any(((b[5 * i + 2] <= c["o0"]) if sgn > 0 else (b[5 * i + 1] >= c["o0"])) for i in range(len(b) // 5))
        ext = any(((b[5 * i + 1] >= c["o0"] * (1 + c["atr"])) if sgn > 0 else (b[5 * i + 2] <= c["o0"] * (1 - c["atr"])))
                  for i in range(len(b) // 5))
        back["touched_open"] += hit; back["made_1atr_ext"] += ext
    res["back_to_open"] = back

    res["cost_check"] = {f"{cst * 100:.2f}": cut(summary(run(cands, {**DEFAULT, "cost": cst}), spy))
                         for cst in (0.0, 0.0003, 0.0006, 0.001)}

    res["sweeps"] = {}
    for k, opts in VARIANTS.items():
        res["sweeps"][k] = {o: {**cut(summary(ts := run(cands, {**DEFAULT, k: o}), spy)), **split(ts, spy)} for o in opts}
    print(f"sweeps done ({time.time() - t0:.0f}s)", flush=True)

    res["combos"] = []
    for dr, rv, ex, ds, l2on in product(VARIANTS["drive"], VARIANTS["rvol"], VARIANTS["ext"], VARIANTS["dist"],
                                       VARIANTS["leg2on"]):
        v = {**DEFAULT, "drive": dr, "rvol": rv, "ext": ex, "dist": ds, "leg2on": l2on}
        ts = run(cands, v)
        res["combos"].append({"v": {k: v[k] for k in ("drive", "rvol", "ext", "dist", "leg2on")},
                              "full": cut(summary(ts, spy)), **split(ts, spy)})
    print(f"{len(res['combos'])} combinations ({time.time() - t0:.0f}s)", flush=True)

    out = Path("/tmp/snapback-demo.json") if a.demo else OUT_DIR / "backtest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, separators=(",", ":")))
    for name, st in res["compare"].items():
        print(f"{name:20s} n={st['n']} win={st['win']}% avg={st['avg']}% PF={st['pf']} acct={st['acct_total']}% "
              f"DD={st['acct_maxdd']}% | train {st['train']['acct_total']}% test {st['test']['acct_total']}%")
    print(f"SPY {spy_total}%  back-to-open {back}  -> {out} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
