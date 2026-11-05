"""Session Momentum: one Nasdaq 100 trade a day, decided by the first 5-minute candle.

The idea (from a QuantLab / @thequantbuilder video, "our simplest Nasdaq strategy"):
  wait for the New York open, watch one candle - the first five minutes - and
  buy if it closes above the 12 EMA, sell if it closes below. No small fixed profit:
  the stop follows the trend and winners are allowed to run.
  The video claims 1,430 trades over six years, a 57% win rate and +508%.

Our version, on 5-minute bars of QQQ (the Nasdaq 100 ETF, standing in for the NAS100 CFD):
  EMA         12-period EMA of the 5-minute closes, carried across days. Default "ext": it also uses
              the pre-market and after-hours bars (closer to a CFD chart that trades nearly round the clock);
              "rth" uses the regular session only.
  Signal      the 09:30 bar (09:30-09:35 New York). Close above the EMA -> long, below -> short.
  Entry       that bar's close (backtest) / the latest price at 09:35 (live).
  Stop        the other end of the signal bar (its low for a long, its high for a short).
  Trailing    after every finished bar the stop moves in the trade's favour only. Default "swing":
              the lowest low (highest high for a short) of the last 3 finished bars - the stepped
              line in the video. Others: "ema" (out when a bar closes back across the 12 EMA),
              "atr" (2x ATR(14) from the best close), "none" (first stop only), "fixed1r" (a 1R target,
              the "small fixed profit" the video says not to take).
  Exit        the stop (at the stop price, or the bar's open if it gapped past), or the 15:55 bar's close.
  Costs       0.01% a side (QQQ's spread and fees are tiny); the full account each trade, no leverage.

Backtest (--backtest): SIP 5-minute bars from 2020 (Alpaca's free plan, at least 15 minutes old), every
variant, by year, against holding QQQ.          -> docs/data/session/backtest.json
Live (every 5 minutes while the US market is open, .github/workflows/session-momentum.yml): paper trades
from the go-live day on Alpaca IEX real-time bars. A signal found more than 10 minutes late is logged as
missed, not traded. Closed trades are never changed. Phone alerts: ntfy topic <NTFY_PREFIX>-session.
                                                -> docs/data/session/live.json

    python engine/session_momentum.py --backtest          # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/session_momentum.py                     # live
    python engine/session_momentum.py --backtest --demo   # synthetic bars, writes to /tmp/session-demo
    python engine/session_momentum.py --demo
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
DATA_DIR = ROOT / "docs" / "data" / "session"
NY = ZoneInfo("America/New_York")
BARS = "https://data.alpaca.markets/v2/stocks/bars"
LATEST = "https://data.alpaca.markets/v2/stocks/trades/latest"
SYMBOL = "QQQ"
CHECKS = ["SPY"]                 # the same rules on the S&P 500, as a check
START = "2026-11-06"             # first day of the live paper trades
BT_FROM = "2020-01-01"

EMA_N = 12
OPEN, EXIT_BAR = 9 * 60 + 30, 15 * 60 + 55
SWING_N, ATR_N, ATR_X = 3, 14, 2.0
COST = 0.0001                    # 0.01% a side
MIN_RISK = 0.0005                # a signal bar smaller than 0.05% gets a 0.05% stop
FRESH_MIN = 10
TRAILS = ["swing", "ema", "atr", "none", "fixed1r"]
DEFAULT = ("ext", "swing", "both")


def minute_of(t: str) -> int:
    return int(t[11:13]) * 60 + int(t[14:16])


def rth(b: dict) -> bool:
    return OPEN <= minute_of(b["t"]) < 16 * 60


# ===================================================================== rules (shared by live and backtest)
def add_ema(bars: list[dict], mode: str) -> None:
    """b['ema'] on every bar; in 'rth' mode only regular-session bars feed it (others copy the last value)."""
    k, e = 2 / (EMA_N + 1), None
    for b in bars:
        if mode == "ext" or rth(b):
            e = b["c"] if e is None else e + k * (b["c"] - e)
        b["ema"] = e


def signal(bar: dict) -> int:
    """+1 long, -1 short, 0 none for the 09:30 bar (which must carry its EMA)."""
    if bar.get("ema") is None or bar["c"] == bar["ema"]:
        return 0
    return 1 if bar["c"] > bar["ema"] else -1


def first_stop(bar: dict, side: int, entry: float) -> float:
    s = bar["l"] if side > 0 else bar["h"]
    if abs(entry - s) < MIN_RISK * entry or (side > 0 and s >= entry) or (side < 0 and s <= entry):
        s = entry * (1 - side * MIN_RISK)
    return s


def new_trade(day: str, bar: dict, side: int, entry: float, trail: str) -> dict:
    stop = first_stop(bar, side, entry)
    t = {"date": day, "side": side, "entry": round(entry, 4), "stop0": round(stop, 4), "stop": round(stop, 4),
         "ema": round(bar["ema"], 4), "sig_close": bar["c"], "best": entry, "trail": trail, "hist": []}
    if trail == "fixed1r":
        t["target"] = round(entry + side * abs(entry - stop), 4)
    return t


def step(t: dict, b: dict, done: list[dict]) -> dict | None:
    """Move trade t through bar b (done = the session's finished bars before b, with ema / atr).
    Returns the exit {price, at, reason} or None while still open."""
    side = t["side"]
    # 1) the stop, then a target, inside the bar (stop first: the cautious assumption)
    if (side > 0 and b["l"] <= t["stop"]) or (side < 0 and b["h"] >= t["stop"]):
        px = min(b["o"], t["stop"]) if side > 0 else max(b["o"], t["stop"])
        return {"price": round(px, 4), "at": b["t"], "reason": "stop" if t["stop"] == t["stop0"] else "trail"}
    if t.get("target") and ((side > 0 and b["h"] >= t["target"]) or (side < 0 and b["l"] <= t["target"])):
        px = max(b["o"], t["target"]) if side > 0 else min(b["o"], t["target"])
        return {"price": round(px, 4), "at": b["t"], "reason": "target"}
    if t["trail"] == "ema" and b.get("ema") is not None and (b["c"] - b["ema"]) * side < 0:
        return {"price": b["c"], "at": b["t"], "reason": "trail"}
    if minute_of(b["t"]) >= EXIT_BAR:
        return {"price": b["c"], "at": b["t"], "reason": "close"}
    # 2) the bar is finished: move the stop in the trade's favour
    t["best"] = max(t["best"], b["c"]) if side > 0 else min(t["best"], b["c"])
    seen = done + [b]
    lvl = None
    if t["trail"] == "swing" and len(seen) >= SWING_N:
        w = seen[-SWING_N:]
        lvl = min(x["l"] for x in w) if side > 0 else max(x["h"] for x in w)
    elif t["trail"] == "atr" and b.get("atr"):
        lvl = t["best"] - side * ATR_X * b["atr"]
    if lvl is not None and (lvl - t["stop"]) * side > 0 and (b["c"] - lvl) * side > 0:
        t["stop"] = round(lvl, 4)
        t["hist"].append([b["t"][11:16], t["stop"]])
    return None


def pct(t: dict) -> float:
    gross = t["side"] * (t["exit"]["price"] / t["entry"] - 1)
    return gross - 2 * COST


def add_atr(bars: list[dict]) -> None:
    trs, prev = [], None
    for b in bars:
        tr = b["h"] - b["l"] if prev is None else max(b["h"] - b["l"], abs(b["h"] - prev), abs(b["l"] - prev))
        trs.append(tr); prev = b["c"]
        b["atr"] = sum(trs[-ATR_N:]) / min(len(trs), ATR_N)


# ===================================================================== data
def _get(url: str, params: dict) -> dict:
    key, sec = os.environ["ALPACA_KEY_ID"], os.environ["ALPACA_SECRET_KEY"]
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                 headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
    for attempt in range(6):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=60).read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < 5:
                time.sleep(3 * (attempt + 1))
                continue
            raise
    return {}


def fetch_bars(symbols: list[str], start: datetime, end: datetime, feed: str) -> dict[str, list[dict]]:
    """5-minute bars, pre-market and after-hours included (04:00-20:00 New York), times in New York."""
    fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"symbols": ",".join(symbols), "timeframe": "5Min", "start": fmt(start), "end": fmt(end),
              "limit": "10000", "adjustment": "all", "feed": feed}
    out: dict[str, list[dict]] = {s: [] for s in symbols}
    pages = 0
    while True:
        js = _get(BARS, params)
        for s, bars in (js.get("bars") or {}).items():
            for b in bars:
                t = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(NY)
                out[s].append({"t": t.isoformat(timespec="minutes"), "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]})
        pages += 1
        if not js.get("next_page_token"):
            break
        params["page_token"] = js["next_page_token"]
    print(f"  {sum(map(len, out.values()))} bars in {pages} pages")
    return out


def latest_price(sym: str) -> float | None:
    js = _get(LATEST, {"symbols": sym, "feed": "iex"})
    t = (js.get("trades") or {}).get(sym)
    return float(t["p"]) if t else None


def by_day(bars: list[dict]) -> dict[str, list[dict]]:
    days: dict[str, list[dict]] = {}
    for b in bars:
        days.setdefault(b["t"][:10], []).append(b)
    return days


def demo_bars(days: int, seed: int = 7, px: float = 210.0) -> list[dict]:
    """Synthetic QQQ-like bars with a little opening momentum, so the demo has something to show."""
    rnd = random.Random(seed)
    rows, d = [], date.today() - timedelta(days=int(days * 1.45))
    while d <= date.today():
        if d.weekday() < 5:
            drift = rnd.gauss(0, 0.00035)
            for m in range(4 * 60, 20 * 60, 5):
                reg = OPEN <= m < 16 * 60
                o = px
                mu = drift * (1.6 if m == OPEN else 0.35) if reg else 0
                px *= math.exp(rnd.gauss(mu + 0.000004, 0.0013 if reg else 0.0004))
                hi, lo = max(o, px) * (1 + abs(rnd.gauss(0, .0005))), min(o, px) * (1 - abs(rnd.gauss(0, .0005)))
                t = datetime(d.year, d.month, d.day, m // 60, m % 60, tzinfo=NY)
                rows.append({"t": t.isoformat(timespec="minutes"), "o": round(o, 2), "h": round(hi, 2), "l": round(lo, 2),
                             "c": round(px, 2), "v": int(rnd.lognormvariate(10 if reg else 7, .6))})
        d += timedelta(days=1)
    return rows


# ===================================================================== backtest
def run_variant(bars: list[dict], ema: str, trail: str, side_rule: str) -> list[dict]:
    """bars: all 5-minute bars of one symbol, oldest first. One trade per session at most."""
    seq = [dict(b) for b in bars]
    add_ema(seq, ema)
    trades = []
    for day, ds in by_day(seq).items():
        if day < BT_FROM:            # the days before only warm up the EMA
            continue
        reg = [b for b in ds if rth(b)]
        if not reg or minute_of(reg[0]["t"]) != OPEN:      # a half day starting late, or a missing open bar
            continue
        add_atr(reg)
        sb = reg[0]
        s = signal(sb)
        if s == 0 or (side_rule == "long" and s < 0):
            trades.append({"date": day, "side": 0})
            continue
        t = new_trade(day, sb, s, sb["c"], trail)
        done = [sb]
        for b in reg[1:]:
            ex = step(t, b, done)
            if ex:
                t["exit"] = ex
                break
            done.append(b)
        if "exit" not in t:      # a session that ended early (half day): out at its last bar
            t["exit"] = {"price": reg[-1]["c"], "at": reg[-1]["t"], "reason": "close"}
        t["pct"] = pct(t)
        trades.append(t)
    return trades


def stats(ts: list[dict]) -> dict:
    ts = [t for t in ts if t["side"]]
    if not ts:
        return {"trades": 0}
    r = [t["pct"] for t in ts]
    eq, peak, dd = 1.0, 1.0, 0.0
    for x in r:
        eq *= 1 + x; peak = max(peak, eq); dd = min(dd, eq / peak - 1)
    wins, losses = [x for x in r if x > 0], [x for x in r if x <= 0]
    gw, gl = sum(wins), -sum(losses)
    risk = [abs(t["entry"] - t["stop0"]) / t["entry"] for t in ts]
    rr = [x / q for x, q in zip(r, risk) if q > 0]
    years = max(1e-9, (date.fromisoformat(ts[-1]["date"]) - date.fromisoformat(ts[0]["date"])).days / 365.25)
    return {"trades": len(ts), "win_rate": round(len(wins) / len(ts), 3), "total_pct": round((eq - 1) * 100, 1),
            "cagr_pct": round((eq ** (1 / years) - 1) * 100, 1) if eq > 0 else -100.0,
            "max_dd_pct": round(dd * 100, 1), "avg_pct": round(100 * sum(r) / len(r), 3),
            "avg_win_pct": round(100 * gw / len(wins), 3) if wins else 0, "avg_loss_pct": round(-100 * gl / len(losses), 3) if losses else 0,
            "profit_factor": round(gw / gl, 2) if gl else None, "avg_r": round(sum(rr) / len(rr), 3) if rr else None,
            "longs": sum(t["side"] > 0 for t in ts), "shorts": sum(t["side"] < 0 for t in ts),
            "exits": {k: sum(t["exit"]["reason"] == k for t in ts) for k in ("stop", "trail", "target", "close")}}


def buy_hold(bars: list[dict], d0: str, d1: str) -> float:
    reg = [b for b in bars if rth(b) and d0 <= b["t"][:10] <= d1]
    return round((reg[-1]["c"] / reg[0]["o"] - 1) * 100, 1) if reg else 0.0


def backtest(data: dict[str, list[dict]]) -> dict:
    main = data[SYMBOL]
    variants, days_ref = {}, None
    for ema in ("ext", "rth"):
        for trail in TRAILS:
            for side in ("both", "long"):
                ts = run_variant(main, ema, trail, side)
                key = f"{ema}|{trail}|{side}"
                yrs = sorted({t["date"][:4] for t in ts})
                v = {"all": stats(ts), "by_year": {y: stats([t for t in ts if t["date"][:4] == y]) for y in yrs},
                     # one number per session: the day's result in basis points (None = no trade)
                     "days": [round(t["pct"] * 1e4) if t["side"] else None for t in ts]}
                if days_ref is None:
                    days_ref = [t["date"] for t in ts]
                assert [t["date"] for t in ts] == days_ref
                variants[key] = v
                if (ema, trail, side) == DEFAULT:
                    detail = ts
                print(f"  {key:18} {v['all']}")
    d0, d1 = days_ref[0], days_ref[-1]
    checks = {}
    for sym in CHECKS:
        if data.get(sym):
            checks[sym] = {"strategy": stats(run_variant(data[sym], *DEFAULT)), "hold_pct": buy_hold(data[sym], d0, d1)}
    holdy = {}
    for y in sorted({d[:4] for d in days_ref}):
        ds = [d for d in days_ref if d[:4] == y]
        holdy[y] = buy_hold(main, ds[0], ds[-1])
    recent = [{k: t[k] for k in ("date", "side", "entry", "stop0", "stop", "ema", "exit", "pct", "hist")} for t in detail[-60:] if t["side"]]
    for t in recent:
        t["pct"] = round(t["pct"] * 100, 3)
    return {"symbol": SYMBOL, "from": d0, "to": d1, "default": "|".join(DEFAULT), "dates": days_ref,
            "hold_pct": buy_hold(main, d0, d1), "hold_by_year": holdy, "variants": variants, "checks": checks,
            "recent": recent, "cost_pct_side": COST * 100,
            "rules": "QQQ 5-min bars. 09:30 bar closes above the 12 EMA = long, below = short; entry at its close; "
                     "stop at the other end of the bar, then trailed; out at the 15:55 bar. 0.01% a side, no leverage."}


# ===================================================================== live
def notify(title: str, msg: str, up: bool) -> None:
    prefix = os.environ.get("NTFY_PREFIX", "").strip()
    if not prefix:
        return
    req = urllib.request.Request(f"https://ntfy.sh/{prefix}-session", data=msg.encode(), method="POST",
                                 headers={"Title": title, "Tags": "chart_with_upwards_trend" if up else "chart_with_downwards_trend"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"  ! notification failed: {e}")


def live(now: datetime, demo: bool, force: bool) -> None:
    ny = now.astimezone(NY)
    mins = ny.hour * 60 + ny.minute
    if not force and (ny.weekday() >= 5 or not OPEN + 5 <= mins <= 16 * 60 + 15):
        print(f"US market closed or not yet open ({ny:%a %H:%M} New York): nothing to do.")
        return
    out = Path("/tmp/session-demo/live.json") if demo else DATA_DIR / "live.json"
    today = ny.date().isoformat()
    if not demo and today < START:
        print(f"Live paper trades start {START}.")
        return
    book = json.loads(out.read_text()) if out.exists() else {"symbol": SYMBOL, "start": START, "trades": [], "missed": []}
    before = json.dumps(book, sort_keys=True)

    if demo:
        bars = demo_bars(12)
        today = bars[-1]["t"][:10]
        last_reg = [b for b in bars if rth(b) and b["t"][:10] == today]
        cut = random.Random(len(book["trades"])).randint(2, len(last_reg) - 1)
        stop_at = last_reg[cut]["t"]
        bars = [b for b in bars if b["t"] <= stop_at]
        ny = datetime.fromisoformat(stop_at) + timedelta(minutes=6)
        price = bars[-1]["c"]
    else:
        bars = fetch_bars([SYMBOL], now - timedelta(days=7), now, "iex")[SYMBOL]
        price = latest_price(SYMBOL)
    # only finished bars: a 5-minute bar starting at t is done at t+5
    bars = [b for b in bars if datetime.fromisoformat(b["t"]) + timedelta(minutes=5) <= ny]
    add_ema(bars, DEFAULT[0])
    days = by_day(bars)

    def manage(t: dict, day_bars: list[dict]) -> None:
        reg = [b for b in day_bars if rth(b)]
        add_atr(reg)
        done = [b for b in reg if b["t"] < t["bar_after"]]
        t["stop"], t["best"], t["hist"] = t["stop0"], t["entry"], []      # replay from the entry: same result every run
        for b in [b for b in reg if b["t"] >= t["bar_after"]]:
            ex = step(t, b, done)
            if ex:
                t["exit"] = ex
                t["pct"] = round(pct(t) * 100, 3)
                return
            done.append(b)

    # 1) open trades (today's, or a day a run was missed)
    for t in book["trades"]:
        if t.get("exit"):
            continue
        manage(t, days.get(t["date"], []))
        if t.get("exit"):
            print(f"  closed {t['date']} {t['exit']['reason']} {t['pct']:+.2f}%")
            if not demo:
                notify(f"Session Momentum: out {t['pct']:+.2f}%", f"{'Long' if t['side'] > 0 else 'Short'} {SYMBOL} closed at "
                       f"{t['exit']['price']} ({t['exit']['reason']}), {t['pct']:+.2f}%. Paper trade, not advice.", t["pct"] >= 0)

    # 2) today's signal
    known = {t["date"] for t in book["trades"]} | {m["date"] for m in book["missed"]}
    reg = [b for b in days.get(today, []) if rth(b)]
    if today not in known and reg and minute_of(reg[0]["t"]) == OPEN:
        sb = reg[0]
        s = signal(sb)
        bar_end = datetime.fromisoformat(sb["t"]) + timedelta(minutes=5)
        late = not demo and (ny - bar_end).total_seconds() > FRESH_MIN * 60
        base = {"date": today, "side": s, "sig_close": sb["c"], "ema": round(sb["ema"], 4) if sb["ema"] else None,
                "bar": [sb["o"], sb["h"], sb["l"], sb["c"]]}
        if s == 0:
            book["missed"].append({**base, "why": "the first candle closed exactly on the EMA"})
        elif late:
            book["missed"].append({**base, "why": "found late (the run was delayed)"})
        else:
            entry = price or sb["c"]
            if (s > 0 and entry <= first_stop(sb, s, entry)) or (s < 0 and entry >= first_stop(sb, s, entry)):
                book["missed"].append({**base, "why": "price already past the stop"})
            else:
                t = new_trade(today, sb, s, entry, DEFAULT[1])
                t.update(opened=ny.isoformat(timespec="minutes"), bar_after=bar_end.isoformat(timespec="minutes"),
                         bar=base["bar"], sig_close=sb["c"])
                book["trades"].append(t)
                manage(t, days[today])
                print(f"  NEW {'long' if s > 0 else 'short'} {SYMBOL} at {entry}, stop {t['stop']}")
                if not demo:
                    notify(f"Session Momentum: {'▲ Long' if s > 0 else '▼ Short'} {SYMBOL}",
                           f"First 5-min candle closed {'above' if s > 0 else 'below'} the 12 EMA ({sb['c']} vs {sb['ema']:.2f}). "
                           f"{'Long' if s > 0 else 'Short'} at {entry}, stop {t['stop']} (trailed). Paper trade, not advice.", s > 0)

    # today's candles for the page's live chart
    book["today"] = {"date": today, "bars": [[b["t"][11:16], b["o"], b["h"], b["l"], b["c"], round(b["ema"], 3) if b["ema"] else None]
                                            for b in days.get(today, []) if rth(b)]}
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
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    if not a.demo and not os.environ.get("ALPACA_KEY_ID"):
        sys.exit("ALPACA_KEY_ID / ALPACA_SECRET_KEY missing")
    if a.backtest:
        if a.demo:
            data = {SYMBOL: demo_bars(6 * 252), "SPY": demo_bars(6 * 252, seed=11, px=420)}
            out = Path("/tmp/session-demo/backtest.json")
        else:
            start = datetime.fromisoformat(BT_FROM).replace(tzinfo=NY) - timedelta(days=5)
            data = fetch_bars([SYMBOL] + CHECKS, start, now - timedelta(minutes=16), "sip")
        res = backtest(data)
        res["updated"] = now.strftime("%Y-%m-%dT%H:%MZ")
        if a.demo:
            res["demo"] = True
        else:
            out = DATA_DIR / "backtest.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, separators=(",", ":")))
        print(f"-> {out} (QQQ held: {res['hold_pct']}%)")
    else:
        live(now, a.demo, a.force or a.demo)


if __name__ == "__main__":
    main()
