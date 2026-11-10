"""Premarket Momentum, live: the morning scan, phone alerts and paper trades.

Same rules as the backtest default (engine/premarket_momentum.py, DEFAULT):
  S&P 500, 20-day average dollar volume >= $10M, yesterday's close above its 20-day average,
  pre-market up >= 1% vs yesterday's close, higher than at 09:00, >= $1M traded pre-market.
  Top 5 by pre-market gain, buy at the 09:30 open, 2% stop, 5% target, anything left sold at 10:30.

Three steps a day, each run once (the script works out which one is due from the New York time):
  09:15-09:29  scan   -> the picks go to the phone (ntfy topic <NTFY_PREFIX>-premarket) and live.json
  10:25-10:34  remind -> "sell what's left at 10:30"
  10:47-15:59  settle -> paper trades replayed on official 5-minute bars, exactly like the backtest,
                         result to the phone and live.json
Data: Alpaca's free plan has full-market (SIP) bars only 15 minutes late, so the scan takes the 09:00 price
and the pre-market volume from SIP and the live price from IEX (real time). Settling waits for SIP.

    python engine/premarket_live.py                 # does what is due now
    python engine/premarket_live.py --force scan    # run a step now regardless of the time
    python engine/premarket_live.py --demo          # synthetic data, no API, no phone, /tmp/premarket-live.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import urllib.parse
import urllib.request
from array import array
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from open_breakout import NY, fetch, load_tickers, ny  # noqa: E402
from premarket_momentum import DEFAULT, trade  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "premarket" / "live.json"
LATEST = "https://data.alpaca.markets/v2/stocks/trades/latest"
PM_MIN, PM_USD, LIQ_USD, MAX_POS = 0.01, 1e6, 10e6, 5


def notify(title: str, msg: str, up: bool = True) -> None:
    prefix = os.environ.get("NTFY_PREFIX", "").strip()
    if not prefix:
        print("  (no NTFY_PREFIX: no phone notification)")
        return
    req = urllib.request.Request(f"https://ntfy.sh/{prefix}-premarket", data=msg.encode(), method="POST",
                                 headers={"Title": title.encode("utf-8").decode("latin-1", "ignore"),
                                          "Tags": "chart_with_upwards_trend" if up else "chart_with_downwards_trend"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"  ! notification failed: {e}")


def latest_iex(symbols: list[str]) -> dict[str, tuple[float, datetime]]:
    out = {}
    key, sec = os.environ["ALPACA_KEY_ID"], os.environ["ALPACA_SECRET_KEY"]
    for j in range(0, len(symbols), 200):
        q = urllib.parse.urlencode({"symbols": ",".join(symbols[j:j + 200]), "feed": "iex"})
        req = urllib.request.Request(f"{LATEST}?{q}", headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
        try:
            js = json.loads(urllib.request.urlopen(req, timeout=60).read())
        except Exception as e:
            print(f"  ! IEX latest trades failed: {e}")
            continue
        for s, t in (js.get("trades") or {}).items():
            out[s] = (float(t["p"]), datetime.fromisoformat(t["t"][:19] + "+00:00"))
    return out


def utc(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ===================================================================== scan
def scan(now: datetime, today: str, demo: bool) -> dict:
    syms, _ = load_tickers()
    if demo:
        return demo_scan(today, syms)
    daily = {}
    start = utc(now - timedelta(days=50))
    for j in range(0, len(syms), 100):
        daily.update(fetch(syms[j:j + 100], "1Day", start, utc(now - timedelta(minutes=16))))
    ok = {}
    for s, bars in daily.items():
        bars = [b for b in bars if b["t"][:10] < today]
        if len(bars) < 20:
            continue
        win = bars[-20:]
        pc, s20 = bars[-1]["c"], sum(b["c"] for b in win) / 20
        usd = sum(b["c"] * b["v"] for b in win) / 20
        if usd >= LIQ_USD and pc > s20:
            ok[s] = {"pc": pc, "s20": round(s20, 2), "usd": usd}
    print(f"{len(ok)} of {len(syms)} stocks liquid and above their 20-day average")
    t4 = datetime.fromisoformat(today + "T04:00").replace(tzinfo=NY)
    intr = {}
    names = sorted(ok)
    for j in range(0, len(names), 100):
        intr.update(fetch(names[j:j + 100], "5Min", utc(t4), utc(now - timedelta(minutes=16))))
    live = latest_iex(names)
    picks = []
    for s in names:
        pc = ok[s]["pc"]
        pm = []
        for b in intr.get(s, []):
            t = ny(b["t"])
            m = t.hour * 60 + t.minute
            if t.date().isoformat() == today and 240 <= m < 570:
                pm.append((m, b))
        p900, pm_usd = pc, 0.0
        for m, b in pm:
            pm_usd += b["v"] * b["c"]
            if m + 5 <= 540:
                p900 = b["c"]
        px, src = None, ""
        if s in live and (now - live[s][1]).total_seconds() < 45 * 60:
            px, src = live[s][0], "IEX live"
        elif pm:
            px, src = pm[-1][1]["c"], "SIP 15 min late"
        if px is None:
            continue
        gain = px / pc - 1
        if gain >= PM_MIN and px > p900 and pm_usd >= PM_USD:
            picks.append({"s": s, "pc": round(pc, 2), "px": round(px, 2), "gain": round(gain * 100, 2),
                          "p0900": round(p900, 2), "pm_usd": round(pm_usd), "src": src})
    picks.sort(key=lambda p: -p["gain"])
    return {"picks": picks[:MAX_POS], "backups": picks[MAX_POS:MAX_POS + 5], "checked": len(ok)}


def demo_scan(today: str, syms: list[str]) -> dict:
    rnd = random.Random(today)
    picks = []
    for s in rnd.sample(syms, 9):
        pc = round(rnd.uniform(40, 400), 2)
        g = rnd.uniform(1, 4)
        picks.append({"s": s, "pc": pc, "px": round(pc * (1 + g / 100), 2), "gain": round(g, 2),
                      "p0900": round(pc * (1 + g / 200), 2), "pm_usd": rnd.randint(1, 30) * 1e6, "src": "demo"})
    picks.sort(key=lambda p: -p["gain"])
    return {"picks": picks[:MAX_POS], "backups": picks[MAX_POS:], "checked": 250}


# ===================================================================== settle
def settle(day: dict, demo: bool) -> None:
    names = [p["s"] for p in day["picks"]]
    if not names:
        day["settled"] = True
        return
    if demo:
        bars = {}
        for p in day["picks"]:
            rnd, px, bs = random.Random(p["s"] + day["date"]), p["px"], []
            for i in range(13):
                o = px; c = o * (1 + rnd.gauss(0, 0.004))
                bs.append({"t": f"{day['date']}T{(570 + 5 * i) // 60:02d}:{(570 + 5 * i) % 60:02d}:00-05:00",
                           "o": o, "h": max(o, c) * 1.002, "l": min(o, c) * 0.998, "c": c, "v": 1})
                px = c
            bars[p["s"]] = bs
    else:
        t0 = datetime.fromisoformat(day["date"] + "T09:30").replace(tzinfo=NY)
        bars = fetch(names, "5Min", utc(t0), utc(t0 + timedelta(minutes=65)))
    trades = []
    for p in day["picks"]:
        ohlc = array("f")
        for b in bars.get(p["s"], []):
            t = ny(b["t"]) if not demo else datetime.fromisoformat(b["t"])
            m = t.hour * 60 + t.minute
            if 570 <= m <= 625:
                ohlc.extend((b["o"], b["h"], b["l"], b["c"], m))
        if len(ohlc) < 5 or ohlc[4] != 570:
            trades.append({"s": p["s"], "why": "no opening trade found", "pct": None})
            continue
        t = trade({"s": p["s"], "d": day["date"], "pc": p["pc"], "gain": p["gain"] / 100, "bars": ohlc}, DEFAULT)
        trades.append({k: t[k] for k in ("s", "in", "sl", "tp", "out", "why", "at", "pct")})
    done = [t for t in trades if t["pct"] is not None]
    day["trades"] = trades
    day["acct_pct"] = round(sum(t["pct"] for t in done) / MAX_POS, 3) if done else 0.0
    day["settled"] = True


# ===================================================================== main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", choices=["scan", "remind", "settle"])
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    nyt = now.astimezone(NY)
    m = nyt.hour * 60 + nyt.minute
    today = nyt.date().isoformat()
    if a.force:
        step = a.force
    elif nyt.weekday() >= 5:
        print("Weekend: nothing to do."); return
    elif 555 <= m < 570:
        step = "scan"
    elif 625 <= m < 635:
        step = "remind"
    elif 647 <= m < 960:
        step = "settle"
    else:
        print(f"{nyt:%a %H:%M} New York: no step due."); return

    out = Path("/tmp/premarket-live.json") if a.demo else OUT
    book = json.loads(out.read_text()) if out.exists() else {"start": today, "rules": DEFAULT, "days": []}
    days = {d["date"]: d for d in book["days"]}
    day = days.get(today)
    changed = False

    if step == "scan":
        if day and not a.force:
            print("Already scanned today."); return
        r = scan(now, today, a.demo)
        day = {"date": today, "scanned": nyt.strftime("%H:%M"), **r, "settled": False}
        book["days"] = [d for d in book["days"] if d["date"] != today] + [day]
        changed = True
        P = r["picks"]
        print(f"{len(P)} picks: " + ", ".join(f"{p['s']} +{p['gain']}%" for p in P))
        if P:
            lines = [f"{p['s']}  +{p['gain']}% pre-market ({p['px']}) · stop ≈ {p['px'] * 0.98:.2f} · target ≈ {p['px'] * 1.05:.2f}"
                     for p in P]
            msg = ("\n".join(lines) + "\n\nBuy at the open (15:30 Italy). Stop 2% under your fill, target 5% over it. "
                   "Sell what's left at 10:30 New York (16:30 Italy). Levels are from the pre-market price: "
                   "set them from your real fill. Not advice.")
            if r["backups"]:
                msg += "\nNext in line: " + ", ".join(f"{p['s']} +{p['gain']}%" for p in r["backups"])
            if not a.demo:
                notify(f"Premarket Momentum: {len(P)} for the open", msg)
        elif not a.demo:
            notify("Premarket Momentum: nothing today", f"None of the {r['checked']} S&P 500 stocks in an uptrend "
                   "is up 1%+ and still climbing in the pre-market. No trades today.", False)
    elif step == "remind":
        if not day or not day["picks"] or day.get("reminded"):
            print("Nothing to remind."); return
        day["reminded"] = True; changed = True
        if not a.demo:
            notify("Premarket Momentum: sell at 10:30", "It's 10:30 in New York (16:30 Italy): close what's still open from "
                   "this morning (" + ", ".join(p["s"] for p in day["picks"]) + ").")
    elif step == "settle":
        todo = [d for d in book["days"] if not d.get("settled")]
        if not todo:
            print("Nothing to settle."); return
        for d in todo:
            if not a.demo and d["date"] == today and m < 647:
                continue
            settle(d, a.demo)
            changed = True
            done = [t for t in d["trades"] if t["pct"] is not None]
            txt = "\n".join(f"{t['s']}: {t['pct']:+.2f}% ({t['why']} {t['at']})" for t in done)
            print(f"settled {d['date']}: account {d['acct_pct']:+.2f}%\n{txt}")
            if not a.demo and done:
                notify(f"Premarket Momentum: day {d['acct_pct']:+.2f}%",
                       f"Paper trades {d['date']} (20% of the account each):\n{txt}", d["acct_pct"] >= 0)

    if changed:
        eq = 1.0
        for d in sorted(book["days"], key=lambda d: d["date"]):
            if d.get("settled"):
                eq *= 1 + d.get("acct_pct", 0) / 100
                d["equity"] = round(eq, 5)
        book["updated"] = now.strftime("%Y-%m-%dT%H:%MZ")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(book, separators=(",", ":")))
        print(f"-> {out}")


if __name__ == "__main__":
    main()
