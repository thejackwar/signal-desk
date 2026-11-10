"""Opening Reversal: let the first 20 minutes set a trend, then trade the reversal.

The idea (Giacomo, 9 Nov 2026, after the NVIDIA morning):
  - Don't trade the open. Wait ~20 minutes and see which way the stock has trended.
  - Then trade AGAINST that move, for a reversal (NVDA ran from $170 to $177 in the first 20 minutes,
    stalled near $179 and pulled back; the short that waited for the stall worked).
  - The same signal traded WITH the trend ("continuation") is tested too, to see which one usually wins.

Exact rules tested here (every one is a switch, see VARIANTS):
  Universe    S&P 100, current members; 20-day average dollar volume >= $10M.
  Window      watch the first 15, 20 (default) or 30 minutes after the 09:30 New York open.
  Trend       the move from the 09:30 open to the close of the window: at least 0.5%, 1% (default)
              or 2%, up or down.
  Gap filter  "all" days (default), "gap": the open was at least 2% away from yesterday's close
              (news mornings like NVDA), "quiet": the open was within 1% of yesterday's close.
  Side        "reversal" (the strategy): up-trend -> short, down-trend -> buy.
              "continuation" (the check): up-trend -> buy, down-trend -> short.
  Entry       "now": at the close of the window (e.g. 09:50) (default for the check);
              "confirm" (default): wait for a 5-minute candle that closes beyond the previous candle
              against the trend for a reversal (up-trend: closes under the previous candle's low), or
              with it for a continuation - enter at that close. No entry later than 30 minutes after the
              window ends.
  Stop        "struct" (default): reversal - just beyond the most extreme price since the open (above the
              high for a short); continuation - just beyond the last 15 minutes' extreme against you.
              "0.5" / "1": 0.5% / 1% from the entry.
  Target      1R, 2R (default), 3R (R = entry to stop), "half": back half of the morning move (open to
              extreme) for a reversal / a further half for a continuation, or "none".
  Time exit   whatever is still open is closed at 10:30 (default), 12:00 or the 15:55 close.
  Exit rules  stop first if a bar hits both (cautious); a gap through a level fills at the bar's open.
  Costs       0.03% a side.
  Account     each day up to 5 trades (the biggest morning moves first), 20% of the account each, no leverage.

Data: Alpaca SIP 5-minute regular-session bars, split/dividend adjusted, from 2021. Current S&P 100 members
only (survivorship bias). Only stock-days that moved at least 0.5% in some window are kept.

    python engine/opening_reversal.py           # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/opening_reversal.py --demo    # synthetic data, writes /tmp/reversal-demo.json
Output: docs/data/reversal/backtest.json
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
OUT_DIR = ROOT / "docs" / "data" / "reversal"
FROM = "2021-01-04"
COST = 0.0003
MAX_POS = 5
TICK = 0.0005
MOVE_MIN = 0.005

DEFAULT = {"win": "20", "move": "1", "gapf": "all", "side": "reversal", "entry": "confirm",
           "stop": "struct", "tp": "2", "exit": "1030"}
VARIANTS = {
    "win": ["15", "20", "30"],
    "move": ["0.5", "1", "2"],
    "gapf": ["all", "gap", "quiet"],
    "side": ["reversal", "continuation"],
    "entry": ["now", "confirm"],
    "stop": ["struct", "0.5", "1"],
    "tp": ["1", "2", "3", "half", "none"],
    "exit": ["1030", "1200", "eod"],
}
EXIT_MIN = {"1030": 625, "1200": 715, "eod": 955}
EXIT_PRESETS = ["struct|2|1030", "struct|1|1030", "struct|half|1030", "struct|none|1030", "struct|2|1200", "0.5|2|1030"]
PAGE_KEYS = ("win", "move", "gapf", "side", "entry", "stop", "tp", "exit")


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
    if len(today) < 8 or today[0][0] != 570:
        return None
    o0 = today[0][1]["o"]
    mv = {}
    for w in VARIANTS["win"]:
        k = int(w) // 5
        if today[k - 1][0] != 570 + 5 * (k - 1):
            return None
        mv[w] = today[k - 1][1]["c"] / o0 - 1
    if max(abs(x) for x in mv.values()) < MOVE_MIN:
        return None
    ohlc = array("f")
    for m, b in today:
        ohlc.extend((b["o"], b["h"], b["l"], b["c"], m))
    return {"s": s, "d": d, "o0": o0, "gap": r["o"] / r["pc"] - 1, "mv": mv, "bars": ohlc}


# ===================================================================== rules
def passes(c: dict, v: dict) -> bool:
    if abs(c["mv"][v["win"]]) < float(v["move"]) / 100:
        return False
    if v["gapf"] == "gap" and abs(c["gap"]) < 0.02:
        return False
    if v["gapf"] == "quiet" and abs(c["gap"]) > 0.01:
        return False
    return True


def trade(c: dict, v: dict) -> dict | None:
    b, n = c["bars"], len(c["bars"]) // 5
    k = int(v["win"]) // 5
    up = c["mv"][v["win"]] > 0
    rev = v["side"] == "reversal"
    sgn = (-1 if up else 1) if rev else (1 if up else -1)     # +1 = long
    last_entry = k + 6                                         # up to 30 minutes after the window

    entry, ie = None, None
    if v["entry"] == "now":
        entry, ie = b[5 * (k - 1) + 3], k - 1
    else:
        for i in range(k, min(n, last_entry)):
            pl, ph, cl = b[5 * (i - 1) + 2], b[5 * (i - 1) + 1], b[5 * i + 3]
            if (sgn < 0 and cl < pl) or (sgn > 0 and cl > ph):
                entry, ie = cl, i
                break
    if entry is None:
        return None

    hi = max(b[5 * i + 1] for i in range(ie + 1))
    lo = min(b[5 * i + 2] for i in range(ie + 1))
    if v["stop"] == "struct":
        if rev:
            stop = hi * (1 + TICK) if sgn < 0 else lo * (1 - TICK)
        else:
            j0 = max(0, ie - 2)
            stop = (min(b[5 * i + 2] for i in range(j0, ie + 1)) * (1 - TICK) if sgn > 0
                    else max(b[5 * i + 1] for i in range(j0, ie + 1)) * (1 + TICK))
    else:
        stop = entry * (1 - sgn * float(v["stop"]) / 100)
    risk = abs(entry - stop)
    if risk <= entry * 0.0005 or (sgn > 0 and stop >= entry) or (sgn < 0 and stop <= entry):
        return None
    if v["tp"] == "none":
        tgt = None
    elif v["tp"] == "half":
        ext = hi if up else lo
        half = abs(ext - c["o0"]) / 2
        tgt = entry + sgn * half
        if half <= 0:
            return None
    else:
        tgt = entry + sgn * float(v["tp"]) * risk
    last_m = EXIT_MIN[v["exit"]]

    px, why, at = None, "time", None
    for i in range(ie + 1, n):
        o, h, l, cl, m = b[5 * i:5 * i + 5]
        if (l <= stop) if sgn > 0 else (h >= stop):
            px, why, at = (min(o, stop) if sgn > 0 else max(o, stop)), "stop", m
        elif tgt is not None and ((h >= tgt) if sgn > 0 else (l <= tgt)):
            px, why, at = (max(o, tgt) if sgn > 0 else min(o, tgt)), "target", m
        elif m >= last_m:
            px, at = cl, m + 5
        if px is not None:
            break
    if px is None:
        px, at = b[5 * (n - 1) + 3], b[5 * (n - 1) + 4] + 5
    ret = sgn * (px / entry - 1) - 2 * v.get("cost", COST)
    m_in = int(b[5 * ie + 4]) + 5
    return {"s": c["s"], "d": c["d"], "mv": round(c["mv"][v["win"]] * 100, 2), "dir": "long" if sgn > 0 else "short",
            "in": round(entry, 2), "tin": f"{m_in // 60:02d}:{m_in % 60:02d}", "sl": round(stop, 2),
            "tp": round(tgt, 2) if tgt else None, "out": round(px, 2), "why": why,
            "at": f"{int(at) // 60:02d}:{int(at) % 60:02d}", "pct": round(ret * 100, 3),
            "r": round(sgn * (px - entry) / risk, 2), "rv": abs(c["mv"][v["win"]])}


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
    daily = []
    for d in sorted(days):
        take = sorted(days[d], key=lambda t: -t["rv"])[:MAX_POS]
        r = sum(t["pct"] for t in take) / 100 / MAX_POS
        eq *= 1 + r; yearly[d[:4]] *= 1 + r
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
            "avg_r": round(sum(t["r"] for t in ts) / len(ts), 2),
            "pf": round(gain / loss, 2) if loss else None,
            "exits": {k: sum(t["why"] == k for t in ts) for k in ("target", "stop", "time")},
            "acct_total": round((eq - 1) * 100, 1), "acct_cagr": round((eq ** (1 / yrs) - 1) * 100, 1),
            "acct_maxdd": round(dd * 100, 1), "by_year": by_year,
            "_daily": daily, "curve": curve[::max(1, len(curve) // 300)] + curve[-1:]}


KEYS = ("n", "win", "avg", "avg_r", "pf", "acct_total", "acct_cagr", "acct_maxdd")


def cut(st: dict) -> dict:
    return {k: st.get(k) for k in KEYS}


# ===================================================================== demo data
def demo_intraday(d: str, items: list) -> dict:
    rnd, out = random.Random(d), {}
    for s, r in items:
        dt = datetime.fromisoformat(d + "T09:30").replace(tzinfo=NY)
        px, bars, drift = r["o"], [], rnd.gauss(0, 0.002)
        for i in range(78):
            o = px
            c = o * (1 + (drift if i < 4 else -drift * 0.3 if i < 12 else 0) + rnd.gauss(0, 0.002))
            bars.append({"t": (dt + timedelta(minutes=5 * i)).isoformat(), "o": o, "h": max(o, c) * 1.001,
                         "l": min(o, c) * 0.999, "c": c, "v": 2e5})
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
           "costs_pct_a_side": COST * 100, "max_positions": MAX_POS}
    base = run(cands, DEFAULT)
    res["base"] = stats(base, spy)
    res["base_trades_recent"] = [{k: t[k] for k in t if k != "rv"} for t in sorted(base, key=lambda t: t["d"])[-300:]]
    cont = {**DEFAULT, "side": "continuation"}
    st = stats(run(cands, cont), spy); st.pop("curve", None)
    res["mirror"] = {"v": cont, **st}

    # after the morning move, how often does price give back half of it vs extend it by half, by 10:30?
    res["follow"] = {}
    for w in VARIANTS["win"]:
        for mv in VARIANTS["move"]:
            got = {"moves": 0, "half_back_first": 0, "half_more_first": 0, "neither": 0}
            for c in cands:
                if abs(c["mv"][w]) < float(mv) / 100:
                    continue
                b, k, up = c["bars"], int(w) // 5, c["mv"][w] > 0
                ref = b[5 * (k - 1) + 3]
                size = abs(ref - c["o0"]) / 2
                back, more = (ref - size, ref + size) if up else (ref + size, ref - size)
                res_ = "neither"
                for i in range(k, len(b) // 5):
                    h, l, m = b[5 * i + 1], b[5 * i + 2], b[5 * i + 4]
                    if m > 625:
                        break
                    hit_back = (l <= back) if up else (h >= back)
                    hit_more = (h >= more) if up else (l <= more)
                    if hit_back or hit_more:
                        res_ = "half_back_first" if hit_back and not hit_more else "half_more_first" if hit_more and not hit_back else "neither"
                        break
                got["moves"] += 1; got[res_] += 1
            res["follow"][f"{w}|{mv}"] = got

    nv = [c for c in cands if c["s"] == "NVDA" and c["d"] == "2026-11-09"]
    if nv:
        c = nv[0]
        res["example"] = {"s": "NVDA", "d": c["d"], "mv": {w: round(x * 100, 2) for w, x in c["mv"].items()},
                          "reversal": trade(c, DEFAULT), "continuation": trade(c, cont)}

    res["cost_check"] = {}
    for cst in (0.0, 0.0003, 0.0006, 0.001):
        res["cost_check"][f"{cst * 100:.2f}"] = cut(stats(run(cands, {**DEFAULT, "cost": cst}), spy))
    sc, prev_c = [], None
    for b_ in spy_rows:
        if b_["t"][:10] < FROM:
            prev_c = b_["c"]; continue
        sc.append([b_["t"][:10], round(b_["c"] / prev_c, 4)])
    res["spy_curve"] = sc[::max(1, len(sc) // 150)] + sc[-1:]

    res["sweeps"] = {}
    for k, opts in VARIANTS.items():
        res["sweeps"][k] = {}
        for o in opts:
            st = stats(run(cands, {**DEFAULT, k: o}), spy); st.pop("curve", None)
            res["sweeps"][k][o] = st
    print(f"sweeps done ({time.time() - t0:.0f}s)", flush=True)

    res["combos"] = []
    for side in VARIANTS["side"]:
        for w in VARIANTS["win"]:
            for mv in VARIANTS["move"]:
                for gf in VARIANTS["gapf"]:
                    for entry in VARIANTS["entry"]:
                        for ex in EXIT_PRESETS:
                            stop, tp, xt = ex.split("|")
                            v = {**DEFAULT, "side": side, "win": w, "move": mv, "gapf": gf, "entry": entry,
                                 "stop": stop, "tp": tp, "exit": xt}
                            ts = run(cands, v)
                            full = stats(ts, spy)
                            res["combos"].append({
                                "v": {k: v[k] for k in PAGE_KEYS},
                                "curve": (full.get("curve") or [])[::4] + (full.get("curve") or [])[-1:],
                                "recent": [{k: t[k] for k in ("s", "d", "mv", "dir", "tin", "in", "sl", "out", "why", "at", "pct")}
                                           for t in sorted(ts, key=lambda t: t["d"])[-20:]],
                                "full": cut(full), "exits": full.get("exits"),
                                "by_year": {y: o["acct"] for y, o in (full.get("by_year") or {}).items()},
                                "train": cut(stats([t for t in ts if t["d"] < "2024-01-01"], spy)),
                                "test": cut(stats([t for t in ts if t["d"] >= "2024-01-01"], spy))})
    print(f"{len(res['combos'])} page combinations ({time.time() - t0:.0f}s)", flush=True)

    out = Path("/tmp/reversal-demo.json") if a.demo else OUT_DIR / "backtest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    res["board"] = BS.pack((res.get("base") or {}).get("_daily") or [])
    BS.strip(res)
    out.write_text(json.dumps(res, separators=(",", ":")))
    b, mi = res["base"], res["mirror"]
    print(f"reversal (default): {b.get('n')} trades, win {b.get('win')}%, avg {b.get('avg')}%, PF {b.get('pf')}, "
          f"account {b.get('acct_total')}% (max DD {b.get('acct_maxdd')}%) | continuation: {mi.get('n')} trades, "
          f"avg {mi.get('avg')}%, account {mi.get('acct_total')}% | SPY {spy_total}%  -> {out} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
