"""Paper trades for the Top-Down (trendline) alerts, with fixed rules, so we can see if they make money.

Called at the end of every Top-Down run (engine/topdown.py, stocks every 30 minutes in US hours, crypto 24/7)
with the bars that run already downloaded. Nothing is backfilled: only alerts found from START on are traded,
and a closed trade is never changed.

Rules (set before looking at any result):
  entry   the open of the first 15-minute bar that starts after the alert was published (found_at): the price
          you could really have got when the notification arrived
  side    bullish break = long, bearish break = short
  stop    "failed break": a bar of the alert's own timeframe closes back across the broken line's level by more than
          half its average range (ATR 14 of that timeframe), so ordinary wiggles around the line don't count
          (rules v2, 6 Nov 2026: v1 stopped on any close back across and was stopped out by noise within minutes;
          its trades were discarded the same day)
  exit    otherwise after a holding time that grows with the timeframe (HOLD), at that 15-minute bar's close
  costs   COST per side (spread + fees), taken off every trade
  vs mkt  the same trade's result minus the benchmark (SPY / BTC) over the same minutes, in the trade's
          direction, so a falling market doesn't make every short look clever

Writes docs/data/topdown/trades.json (stocks) or docs/data/crypto/topdown_trades.json (crypto):
  {start, rules, open: [...], closed: [...], summary: {all, by_tf, by_strength, by_side}}
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

START = "2026-11-06T12:00"
VERSION = 2
STOP_ATR = 0.5
HOLD = {"15m": dt.timedelta(days=1), "30m": dt.timedelta(days=2), "1H": dt.timedelta(days=3),
        "4H": dt.timedelta(days=7), "D": dt.timedelta(days=20), "W": dt.timedelta(days=60), "5m": dt.timedelta(hours=6)}
COST = {"stocks": 0.05, "crypto": 0.10}            # % per side
TF_ORDER = ["15m", "30m", "1H", "4H", "D", "W"]


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _utc(df: pd.DataFrame, tz) -> pd.DataFrame:
    d = df.copy()
    d.index = (d.index.tz_localize(tz) if d.index.tz is None else d.index).tz_convert("UTC")
    return d


TZ = "UTC"


def _bars(frames: dict) -> pd.DataFrame | None:
    for k in ("15m", "30m", "1H"):
        df = frames.get(k)
        if df is not None and len(df):
            return _utc(df, TZ)
    return None


def _px_at(df: pd.DataFrame | None, t: pd.Timestamp) -> float | None:
    """Last close at or before t."""
    if df is None or not len(df):
        return None
    s = df["Close"][df.index <= t]
    return float(s.iloc[-1]) if len(s) else None


def _summary(closed: list[dict]) -> dict:
    def st(rs):
        if not rs:
            return {"n": 0}
        n = len(rs)
        net = [x["ret"] for x in rs]
        ex = [x["vs"] for x in rs if x.get("vs") is not None]
        wins, losses = [v for v in net if v > 0], [v for v in net if v <= 0]
        return {"n": n, "win": _r(len(wins) / n * 100, 0), "avg": _r(sum(net) / n), "sum": _r(sum(net)),
                "vs": _r(sum(ex) / len(ex)) if ex else None, "best": _r(max(net)), "worst": _r(min(net)),
                "pf": _r(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
                "stopped": _r(sum(x["why"] == "stop" for x in rs) / n * 100, 0)}
    return {"all": st(closed),
            "by_tf": {tf: st([x for x in closed if x["tf"] == tf]) for tf in TF_ORDER if any(x["tf"] == tf for x in closed)},
            "by_strength": {s: st([x for x in closed if x.get("str") == s]) for s in ("Strong", "Moderate", "Weak")
                            if any(x.get("str") == s for x in closed)},
            "by_side": {s: st([x for x in closed if x["side"] == s]) for s in (1, -1) if any(x["side"] == s for x in closed)}}


def update(path: Path, alerts: list[dict], data: dict, now: dt.datetime, market: str, bench: str, tz="UTC",
           only: set[str] | None = None) -> dict:
    """only: trade just these tickers (crypto: Bitcoin only); None = every ticker."""
    global TZ
    TZ = tz
    led = json.loads(path.read_text()) if path.exists() else {}
    if led.get("version") != VERSION:                 # rules changed: start a clean record
        led = {"version": VERSION, "start": START}
    led.setdefault("start", START)
    led["rules"] = {"hold": {k: v.total_seconds() / 86400 for k, v in HOLD.items()}, "cost_per_side": COST[market], "stop_atr": STOP_ATR,
                    "bench": bench}
    led["only"] = sorted(only) if only else None
    keep = (lambda t: t in only) if only else (lambda t: True)
    closed: list[dict] = [x for x in led.get("closed", []) if keep(x["t"])]
    opened: dict[str, dict] = {x["id"]: x for x in led.get("open", []) if keep(x["t"])}
    done = {x["id"] for x in closed}
    now_u = _ts(now)
    cost = COST[market]
    bench_bars = _bars(data.get(bench, {}))

    # 1. new alerts since the start become open trades (entry filled below once a bar exists)
    for a in alerts:
        if a.get("strategy") != "topdown" or a["id"] in done or a["id"] in opened or not keep(a["ticker"]):
            continue
        if not a.get("found_at") or a["found_at"][:16] < led["start"]:
            continue
        opened[a["id"]] = {"id": a["id"], "t": a["ticker"], "tf": a["tf"], "side": 1 if a["side"] == "bullish" else -1,
                           "str": (a.get("strength") or {}).get("label"), "found": a["found_at"], "level": a.get("level"),
                           "alert_px": a.get("price"), "entry": None, "entry_t": None, "px": None, "ret": None}

    # 2. fill entries, mark open trades, close the ones that hit a rule
    still = []
    for x in opened.values():
        fr = data.get(x["t"])
        bars = _bars(fr) if fr else None
        if bars is None:
            still.append(x)
            continue
        found = _ts(x["found"])
        if x["entry"] is None:
            after = bars[bars.index >= found]
            if not len(after):
                still.append(x)                       # no bar since the alert yet
                continue
            x["entry"], x["entry_t"] = _r(after["Open"].iloc[0], 4), after.index[0].strftime("%Y-%m-%dT%H:%M")
            x["bench0"] = _px_at(bench_bars, after.index[0])
        t0 = _ts(x["entry_t"])
        until = t0 + HOLD.get(x["tf"], dt.timedelta(days=3))
        own = fr.get(x["tf"])
        exit_px = exit_t = why = None
        if own is not None and x.get("level") is not None:
            if x.get("stop") is None:
                ow = _utc(own, TZ)
                ow = ow[ow.index < t0].tail(15)
                if len(ow) > 2:
                    tr = pd.concat([ow["High"] - ow["Low"], (ow["High"] - ow["Close"].shift()).abs(),
                                    (ow["Low"] - ow["Close"].shift()).abs()], axis=1).max(axis=1).iloc[1:]
                    x["stop"] = _r(x["level"] - x["side"] * STOP_ATR * float(tr.mean()), 6)
                else:
                    x["stop"] = x["level"]
            o = _utc(own, TZ)
            o = o[(o.index >= t0) & (o.index < until)]
            back = o[(o["Close"] < x["stop"]) if x["side"] > 0 else (o["Close"] > x["stop"])]
            if len(back):
                # the own-timeframe bar is stamped at its start: the close is known one bar later
                stamp = back.index[0]
                nxt = bars[bars.index > stamp]
                bar_end = stamp + (own.index[1] - own.index[0] if len(own) > 1 else dt.timedelta(minutes=15))
                fill = bars[bars.index >= bar_end]
                if len(fill):
                    exit_px, exit_t, why = float(fill["Open"].iloc[0]), fill.index[0], "stop"
                elif len(nxt) and now_u >= bar_end:
                    exit_px, exit_t, why = float(nxt["Close"].iloc[-1]), nxt.index[-1], "stop"
        if why is None:
            lastb = bars.index[-1]
            if lastb >= until:
                b = bars[bars.index >= until]
                exit_px, exit_t, why = float(b["Close"].iloc[0]), b.index[0], "time"
        px_now = exit_px if why else float(bars["Close"].iloc[-1])
        raw = (px_now / x["entry"] - 1) * 100
        x["px"], x["ret"] = _r(px_now, 4), _r(x["side"] * raw - 2 * cost)
        b1 = _px_at(bench_bars, exit_t if why else bars.index[-1])
        x["vs"] = _r(x["side"] * (raw - (b1 / x["bench0"] - 1) * 100) - 2 * cost) if b1 and x.get("bench0") else None
        x["days"] = _r(((exit_t if why else bars.index[-1]) - t0).total_seconds() / 86400, 2)
        if why:
            x["exit"], x["exit_t"], x["why"] = _r(exit_px, 4), exit_t.strftime("%Y-%m-%dT%H:%M"), why
            closed.append(x)
        else:
            still.append(x)
    led["open"] = sorted(still, key=lambda x: x["found"], reverse=True)
    led["closed"] = sorted(closed, key=lambda x: x.get("exit_t", ""), reverse=True)
    led["generated_at"] = now.strftime("%Y-%m-%dT%H:%M+00:00")
    led["summary"] = _summary(led["closed"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(led, separators=(",", ":")))
    s = led["summary"]["all"]
    print(f"Top-Down paper trades: {len(led['open'])} open, {s['n']} closed"
          + (f", {s['win']}% won, avg {s['avg']}% after costs" if s["n"] else ""))
    return led
