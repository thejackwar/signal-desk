"""Gamma level rejection: the "trapped buyers at a level" setup, as alerts and paper trades.

The idea (from an options-flow day trader): mark the key levels before the open, use the options
market to pick a side, and only act when the price tests a level and is rejected there with heavy
volume, which leaves the traders who bought (or sold) the test stuck on the wrong side.

Rules, on 5-minute bars of SPY, QQQ and IWM:
  Levels      today's gamma levels (engine/gex.py: gamma flip, call wall, put wall, magnet strikes)
              plus yesterday's high, low and close.
  Short       a bar that came from below (previous close under the level) pokes up to the level,
              closes back under it, in the lower half of its range, on volume of at least 1.5x the
              average of the previous 20 bars.  Long: the mirror image at a level below.
  Stop        just beyond the rejection bar's high (short) or low (long).
  Target      the nearest level in the trade's direction that pays at least 2x the risk; if there
              isn't one, 3x the risk.
  Exit        stop, target, or the 15:55 New York bar's close, whichever comes first. Stop first when
              a bar touches both (the cautious assumption).
  Per day     one trade per symbol and level, no new trades after 15:30 New York.

Every trade is tagged with whether the options market agreed, so the forward test can compare:
  gamma       short in negative gamma (price under the flip: dealers push moves further),
              long in positive gamma (above the flip: dips get bought).
  premium     short when last session's put premium was bigger than the call premium, long the reverse.

Live (every 5 minutes while the US market is open, .github/workflows/gamma-setup.yml):
  Alpaca IEX bars (real time; IEX is a small slice of all volume, so volume is compared with its own
  average). A rejection found on the last finished bar opens a paper trade at the latest price; an
  older one (a late run) is logged as missed, not traded, so the record stays honest. Trades, once
  closed, are never changed. Phone alerts on the ntfy topic <NTFY_PREFIX>-gamma.
  -> docs/data/gamma/live.json

Backtest (--backtest): the same rules on two years of 5-minute SIP bars, with yesterday's high, low and
close as the only levels (old gamma levels don't exist for free), entering at the rejection bar's
close. It answers: does the level rejection itself have an edge, before any options filter?
  -> docs/data/gamma/backtest.json

    python engine/gamma_setup.py                  # live, needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/gamma_setup.py --backtest
    python engine/gamma_setup.py --demo           # synthetic bars, writes to /tmp/gamma-demo
    python engine/gamma_setup.py --backtest --demo
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data" / "gamma"
GEX_FILE = ROOT / "docs" / "data" / "gex" / "latest.json"
SYMBOLS = ["SPY", "QQQ", "IWM"]
NY = ZoneInfo("America/New_York")
BARS = "https://data.alpaca.markets/v2/stocks/bars"
LATEST = "https://data.alpaca.markets/v2/stocks/trades/latest"
START = "2026-11-04"          # first day of the forward test

VOL_N, VOL_MULT = 20, 1.5     # volume spike: >= 1.5x the average of the previous 20 bars
TOUCH = 0.0003                # the high (low) must come within 0.03% of the level
STOP_PAD = 0.0005             # stop 0.05% beyond the rejection bar
MIN_RR, FALLBACK_RR = 2.0, 3.0
LAST_ENTRY = 15 * 60 + 30     # no new trades after 15:30 New York
EXIT_BAR = 15 * 60 + 55       # the 15:55 bar's close ends the day
FRESH_MIN = 10                # a rejection bar that ended more than 10 minutes ago is "missed"


# ===================================================================== rules (shared by live and backtest)
def dedupe_levels(levels: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """Merge levels closer than 0.05%; the first name wins (gamma levels are listed first)."""
    out: list[tuple[float, str]] = []
    for p, n in levels:
        if p and not any(abs(p / q - 1) < 0.0005 for q, _ in out):
            out.append((round(float(p), 2), n))
    return sorted(out)


def rejection(bars: list[dict], i: int, levels: list[tuple[float, str]]) -> list[dict]:
    """Rejections on bar i (dicts with o h l c v and t = start, New York time)."""
    if i < VOL_N:
        return []
    b, prev = bars[i], bars[i - 1]
    avg = sum(x["v"] for x in bars[i - VOL_N:i]) / VOL_N
    if avg <= 0 or b["v"] < VOL_MULT * avg:
        return []
    rng = b["h"] - b["l"]
    if rng <= 0:
        return []
    out = []
    for L, name in levels:
        if prev["c"] < L and b["h"] >= L * (1 - TOUCH) and b["c"] < L and (b["h"] - b["c"]) >= 0.5 * rng:
            out.append({"side": "short", "level": L, "level_name": name, "stop": round(b["h"] * (1 + STOP_PAD), 2)})
        elif prev["c"] > L and b["l"] <= L * (1 + TOUCH) and b["c"] > L and (b["c"] - b["l"]) >= 0.5 * rng:
            out.append({"side": "long", "level": L, "level_name": name, "stop": round(b["l"] * (1 - STOP_PAD), 2)})
    for s in out:
        s["vol_x"] = round(b["v"] / avg, 1)
    return out


def target_for(side: str, entry: float, stop: float, levels: list[tuple[float, str]]) -> tuple[float, str] | None:
    risk = abs(entry - stop)
    if risk <= 0 or (side == "short" and stop <= entry) or (side == "long" and stop >= entry):
        return None
    ahead = [(p, n) for p, n in levels if (p < entry if side == "short" else p > entry)]
    ahead.sort(key=lambda x: abs(x[0] - entry))
    for p, n in ahead:
        if abs(p - entry) >= MIN_RR * risk:
            return round(p, 2), n
    t = entry - FALLBACK_RR * risk if side == "short" else entry + FALLBACK_RR * risk
    return round(t, 2), f"{FALLBACK_RR:.0f}R"


def walk(trade: dict, bars: list[dict]) -> dict | None:
    """Follow a trade bar by bar. Returns the exit {price, at, reason} or None while still open."""
    short = trade["side"] == "short"
    for b in bars:
        if (short and b["h"] >= trade["stop"]) or (not short and b["l"] <= trade["stop"]):
            return {"price": trade["stop"], "at": b["t"], "reason": "stop"}
        if (short and b["l"] <= trade["target"]) or (not short and b["h"] >= trade["target"]):
            return {"price": trade["target"], "at": b["t"], "reason": "target"}
        if minute_of(b["t"]) >= EXIT_BAR:
            return {"price": b["c"], "at": b["t"], "reason": "close"}
    return None


def result(trade: dict) -> dict:
    sgn = -1 if trade["side"] == "short" else 1
    risk = abs(trade["entry"] - trade["stop"])
    pnl = sgn * (trade["exit"]["price"] - trade["entry"])
    return {"r": round(pnl / risk, 2) if risk else 0.0, "pct": round(100 * pnl / trade["entry"], 3)}


def minute_of(t: str) -> int:
    d = datetime.fromisoformat(t)
    return d.hour * 60 + d.minute


# ===================================================================== data
def _get(url: str, params: dict) -> dict:
    key, sec = os.environ["ALPACA_KEY_ID"], os.environ["ALPACA_SECRET_KEY"]
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                 headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
    for attempt in range(5):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=60).read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                time.sleep(3 * (attempt + 1))
                continue
            raise
    return {}


def fetch_bars(symbols: list[str], start: datetime, end: datetime, feed: str, tf: str = "5Min") -> dict[str, list[dict]]:
    fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"symbols": ",".join(symbols), "timeframe": tf, "start": fmt(start), "end": fmt(end),
              "limit": "10000", "adjustment": "all", "feed": feed}
    out: dict[str, list[dict]] = {s: [] for s in symbols}
    while True:
        js = _get(BARS, params)
        for s, bars in (js.get("bars") or {}).items():
            for b in bars:
                t = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(NY)
                out[s].append({"t": t.isoformat(timespec="minutes"), "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]})
        if not js.get("next_page_token"):
            break
        params["page_token"] = js["next_page_token"]
    for s in out:      # regular hours only
        out[s] = [b for b in out[s] if 570 <= minute_of(b["t"]) < 960]
    return out


def latest_prices(symbols: list[str]) -> dict[str, float]:
    js = _get(LATEST, {"symbols": ",".join(symbols), "feed": "iex"})
    return {s: float(t["p"]) for s, t in (js.get("trades") or {}).items()}


def by_day(bars: list[dict]) -> dict[str, list[dict]]:
    days: dict[str, list[dict]] = {}
    for b in bars:
        days.setdefault(b["t"][:10], []).append(b)
    return days


def demo_bars(symbols: list[str], days: int, seed: int = 3) -> dict[str, list[dict]]:
    rnd = random.Random(seed)
    out = {}
    d0 = date.today() - timedelta(days=int(days * 1.45))
    for k, s in enumerate(symbols):
        px, rows, d = [680, 610, 240][k % 3], [], d0
        while d <= date.today():
            if d.weekday() < 5:
                for m in range(570, 960, 5):
                    o = px
                    px *= math.exp(rnd.gauss(0, 0.0012))
                    hi, lo = max(o, px) * (1 + abs(rnd.gauss(0, .0006))), min(o, px) * (1 - abs(rnd.gauss(0, .0006)))
                    t = datetime(d.year, d.month, d.day, m // 60, m % 60, tzinfo=NY)
                    rows.append({"t": t.isoformat(timespec="minutes"), "o": round(o, 2), "h": round(hi, 2), "l": round(lo, 2),
                                 "c": round(px, 2), "v": int(rnd.lognormvariate(10, .6))})
            d += timedelta(days=1)
        out[s] = rows
    return out


# ===================================================================== backtest
def backtest(data: dict[str, list[dict]]) -> dict:
    trades = []
    for sym, bars in data.items():
        days = by_day(bars)
        keys = sorted(days)
        for k in range(1, len(keys)):
            prev, today = days[keys[k - 1]], days[keys[k]]
            levels = dedupe_levels([(max(b["h"] for b in prev), "prior high"), (min(b["l"] for b in prev), "prior low"),
                                    (prev[-1]["c"], "prior close")])
            seq = prev[-VOL_N:] + today            # yesterday's last bars give the first bars a volume average
            used: set[float] = set()
            for i in range(VOL_N, len(seq)):
                b = seq[i]
                if minute_of(b["t"]) > LAST_ENTRY or b["t"][:10] != keys[k]:
                    continue
                for s in rejection(seq, i, levels):
                    if s["level"] in used:
                        continue
                    tg = target_for(s["side"], b["c"], s["stop"], levels)
                    if not tg:
                        continue
                    used.add(s["level"])
                    tr = {"symbol": sym, "date": keys[k], "time": b["t"][11:16], "side": s["side"], "level": s["level"],
                          "level_name": s["level_name"], "entry": b["c"], "stop": s["stop"], "target": tg[0], "vol_x": s["vol_x"]}
                    ex = walk(tr, seq[i + 1:])
                    tr["exit"] = ex or {"price": seq[-1]["c"], "at": seq[-1]["t"], "reason": "close"}
                    tr.update(result(tr))
                    trades.append(tr)

    def stats(ts: list[dict]) -> dict:
        if not ts:
            return {"trades": 0}
        rs = [t["r"] for t in ts]
        wins = [r for r in rs if r > 0]
        eq, peak, dd = 0.0, 0.0, 0.0
        for r in rs:
            eq += r; peak = max(peak, eq); dd = min(dd, eq - peak)
        return {"trades": len(ts), "win_rate": round(len(wins) / len(ts), 3), "avg_r": round(sum(rs) / len(ts), 3),
                "total_r": round(sum(rs), 1), "max_drawdown_r": round(dd, 1),
                "targets": sum(t["exit"]["reason"] == "target" for t in ts), "stops": sum(t["exit"]["reason"] == "stop" for t in ts),
                "avg_pct": round(sum(t["pct"] for t in ts) / len(ts), 3)}

    trades.sort(key=lambda t: (t["date"], t["time"]))
    groups = {"All": trades}
    for side in ("short", "long"):
        groups[f"{side.title()} only"] = [t for t in trades if t["side"] == side]
    for sym in data:
        groups[sym] = [t for t in trades if t["symbol"] == sym]
    for n in ("prior high", "prior low", "prior close"):
        groups[f"At {n}"] = [t for t in trades if t["level_name"] == n]
    years = sorted({t["date"][:4] for t in trades})
    return {"rules": "5-min bars, regular hours; levels = prior day's high, low, close; rejection with >= 1.5x volume; "
                     "stop beyond the bar, target the next level >= 2R else 3R; out by 15:55. No costs.",
            "from": trades[0]["date"] if trades else None, "to": trades[-1]["date"] if trades else None,
            "groups": {k: stats(v) for k, v in groups.items()},
            "by_year": {y: stats([t for t in trades if t["date"][:4] == y]) for y in years},
            "recent": trades[-60:]}


# ===================================================================== live
def gex_today(today: str) -> dict:
    try:
        g = json.loads(GEX_FILE.read_text())
    except Exception:
        return {}
    if g.get("updated", "")[:10] != today:      # yesterday's levels: open interest has moved on
        return {}
    return g.get("underlyings") or {}


def notify(t: dict) -> None:
    prefix = os.environ.get("NTFY_PREFIX", "").strip()
    if not prefix:
        return
    arrow = "▼ Short" if t["side"] == "short" else "▲ Long"
    agree = ", ".join(k for k in ("gamma", "premium") if t["filters"].get(k)) or "no options filter agrees"
    msg = (f"{arrow} {t['symbol']} {t['entry']}: rejected at {t['level_name']} {t['level']} on {t['vol_x']}x volume. "
           f"Stop {t['stop']}, target {t['target']} ({t['rr']}R). Options: {agree}. Paper trade, not advice.")
    req = urllib.request.Request(f"https://ntfy.sh/{prefix}-gamma", data=msg.encode(), method="POST",
                                 headers={"Title": f"Gamma rejection: {t['symbol']}",
                                          "Tags": "chart_with_downwards_trend" if t["side"] == "short" else "chart_with_upwards_trend"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"  ! notification failed: {e}")


def live(now: datetime, demo: bool, force: bool) -> None:
    ny = now.astimezone(NY)
    mins = ny.hour * 60 + ny.minute
    if not force and (ny.weekday() >= 5 or not 575 <= mins <= 975):
        print(f"US market closed ({ny:%a %H:%M} New York): nothing to do.")
        return
    out = Path("/tmp/gamma-demo/live.json") if demo else DATA_DIR / "live.json"
    today = ny.date().isoformat()
    if not demo and today < START:
        print(f"Forward test starts {START}.")
        return
    book = json.loads(out.read_text()) if out.exists() else {"start": START, "trades": [], "missed": []}
    before = json.dumps(book, sort_keys=True)

    if demo:
        data = {s: v[-(78 * 2):] for s, v in demo_bars(SYMBOLS, 3).items()}
        last_day = max(b["t"][:10] for b in data["SPY"])
        ny = datetime.fromisoformat(data["SPY"][-1]["t"]) + timedelta(minutes=6)
        today, prices = last_day, {s: v[-1]["c"] for s, v in data.items()}
    else:
        data = fetch_bars(SYMBOLS, now - timedelta(days=6), now, "iex")
        prices = latest_prices(SYMBOLS)
    gex = gex_today(today) if not demo else {}

    for sym in SYMBOLS:
        days = by_day(data.get(sym, []))
        if today not in days or len(days) < 2:
            continue
        prev_day = days[sorted(d for d in days if d < today)[-1]]
        seq = prev_day[-VOL_N:] + days[today]
        # only finished bars: a 5-minute bar starting at t is done at t+5
        seq = [b for b in seq if datetime.fromisoformat(b["t"]) + timedelta(minutes=5) <= ny]
        G = gex.get(sym) or {}
        levels = dedupe_levels(
            [(G.get("flip"), "gamma flip"), (G.get("call_wall"), "call wall"), (G.get("put_wall"), "put wall")]
            + [(m["strike"], "magnet") for m in G.get("magnets", [])]
            + [(max(b["h"] for b in prev_day), "prior high"), (min(b["l"] for b in prev_day), "prior low"),
               (prev_day[-1]["c"], "prior close")])

        # 1) manage open trades
        for t in book["trades"]:
            if t["symbol"] != sym or t.get("exit") or t["date"] != today:
                continue
            after = [b for b in seq if b["t"] >= t["bar_after"]]
            ex = walk(t, after)
            if ex:
                t["exit"] = ex
                t.update(result(t))
                print(f"  closed {sym} {t['side']} {t['exit']['reason']} {t['r']:+}R")

        # 2) new rejections on today's finished bars
        seen = {(t["symbol"], t["date"], t["level"]) for t in book["trades"] + book["missed"]}
        for i in range(VOL_N, len(seq)):
            b = seq[i]
            if b["t"][:10] != today or minute_of(b["t"]) > LAST_ENTRY:
                continue
            for s in rejection(seq, i, levels):
                key = (sym, today, s["level"])
                if key in seen:
                    continue
                seen.add(key)
                bar_end = datetime.fromisoformat(b["t"]) + timedelta(minutes=5)
                base = {"symbol": sym, "date": today, "bar": b["t"][11:16], **s}
                if (ny - bar_end).total_seconds() > FRESH_MIN * 60:
                    book["missed"].append({**base, "why": "found late (the run was delayed)"})
                    continue
                entry = round(prices.get(sym) or b["c"], 2)
                tg = target_for(s["side"], entry, s["stop"], levels)
                if not tg:
                    book["missed"].append({**base, "why": "price already past the stop"})
                    continue
                flip, regime = G.get("flip"), None
                if flip:
                    regime = "negative" if entry < flip else "positive"
                cp, pp = G.get("call_premium"), G.get("put_premium")
                t = {**base, "opened": ny.isoformat(timespec="minutes"), "bar_after": bar_end.isoformat(timespec="minutes"),
                     "entry": entry, "target": tg[0], "target_name": tg[1],
                     "rr": round(abs(tg[0] - entry) / abs(entry - s["stop"]), 1), "regime": regime,
                     "filters": {"gamma": None if regime is None else (regime == "negative") == (s["side"] == "short"),
                                 "premium": None if not (cp and pp) else (pp > cp) == (s["side"] == "short")}}
                book["trades"].append(t)
                print(f"  NEW {sym} {t['side']} at {entry} ({t['level_name']} {t['level']}), stop {t['stop']}, target {t['target']}")
                if not demo:
                    notify(t)

    # a trade still open after the close (a missed run) ends at that day's 15:55 bar
    for t in book["trades"]:
        if not t.get("exit") and t["date"] < today:
            day = by_day(data.get(t["symbol"], [])).get(t["date"], [])
            ex = walk(t, [b for b in day if b["t"] >= t["bar_after"]])
            if ex:
                t["exit"] = ex
                t.update(result(t))

    if json.dumps(book, sort_keys=True) == before and out.exists():
        print("No change: file left as it is.")
        return
    book["updated"] = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(book, separators=(",", ":")))
    print(f"-> {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--force", action="store_true", help="run even when the market is closed")
    ap.add_argument("--years", type=float, default=2.0)
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    if not a.demo and not os.environ.get("ALPACA_KEY_ID"):
        sys.exit("ALPACA_KEY_ID / ALPACA_SECRET_KEY missing")
    if a.backtest:
        if a.demo:
            data, out = demo_bars(SYMBOLS, int(a.years * 252)), Path("/tmp/gamma-demo/backtest.json")
        else:
            # SIP (all exchanges) for history; the free plan wants it at least 15 minutes old
            data, out = fetch_bars(SYMBOLS, now - timedelta(days=int(a.years * 365)), now - timedelta(minutes=16), "sip"), \
                DATA_DIR / "backtest.json"
        res = backtest(data)
        res["updated"] = now.strftime("%Y-%m-%dT%H:%MZ")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, separators=(",", ":")))
        for k, v in res["groups"].items():
            print(f"  {k:16} {v}")
        print(f"-> {out}")
    else:
        live(now, a.demo, a.force or a.demo)


if __name__ == "__main__":
    main()
