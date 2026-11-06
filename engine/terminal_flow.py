"""Terminal data: who's buying and selling each stock, its options activity, the market's mood, and the
short "Focus" list of alerts worth a look (docs/index.html, #terminal).

Runs every 30 minutes while the US market is open (right after the dark pool blocks) and once more after the
evening scan (.github/workflows/terminal-flow.yml). Everything is free data:

  Volume (Yahoo Finance, daily + today's 5-minute bars)
    Nobody outside the exchanges knows who was the buyer or the seller of each trade. The usual free estimate
    is where the price closes inside each bar's range: a bar that closes at its high counts as all buying, at
    its low as all selling, in the middle as half and half ("close location value", the basis of Chaikin
    Money Flow). Over a day of 5-minute bars that gives a decent read of the pressure. It is an ESTIMATE.
      buy_pct      today's estimated buying share of volume (5-minute bars)
      rel_vol      today's volume vs its 20-day average, scaled to how much of the session has passed
      cmf20        Chaikin Money Flow over 20 days (-1 all selling ... +1 all buying)
      up_vol20     share of the last 20 days' volume traded on up days
      obv_slope    on-balance volume trend over 20 days, in % of average daily volume per day

  Options (Alpaca, free indicative feed, 15 minutes delayed; open interest from the last session)
    Calls and puts traded today, their premium (volume x average price x 100), put/call ratios, near-term
    at-the-money implied volatility, the busiest contracts and "unusual" ones (volume well above open
    interest = new positions). The free feed doesn't say if a trade was bought or sold.

  Market mood (0 extreme fear ... 100 extreme greed), from ten readings each scored -2..+2: S&P 500 today,
  trend vs the 200-day, stocks up today, stocks above their 200-day, VIX level, VIX curve (VIX vs VIX3M),
  put/call volume, SPY gamma regime, dark pool index, news tone. It describes the mood; it isn't a forecast.

  Focus: every strategy alert of the last 3 sessions plus today's provisional ones, grouped by stock and side,
  scored 0-100 on the evidence behind the strategy (backtest grade + live track record), agreement between
  strategies, trend, market mood, volume/options/dark pool/news confirmation and earnings risk. The score's
  reasons are saved with it so the page can show WHY. New "Focus" entries (score >= FOCUS_MIN) go to the ntfy
  channel <NTFY_PREFIX>-focus, once.

Writes:
  docs/data/terminal/flow.json        per-stock volume + options, the market mood, the Focus list
  docs/data/terminal/flow_hist.json   options volume per stock and day (for "vs usual"), last 40 sessions
  docs/data/terminal/focus_sent.json  which Focus entries were already sent to the phone

    python engine/terminal_flow.py            # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY for options
    python engine/terminal_flow.py --demo     # synthetic data, no network, writes to /tmp/terminal-demo
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
NY = ZoneInfo("America/New_York")
FOCUS_MIN, WATCH_MIN = 75, 60        # score tiers
FOCUS_MAX, WATCH_MAX = 8, 15          # at most this many per tier: the point is less noise
# strategies that are variants of one idea count once when they agree
FAMILY = {"deck_live": "deck", "deck_ma50": "deck", "deck_v6": "deck", "deck_v8": "deck",
          "donchian": "breakout", "breakout_bot": "breakout", "volume_breakout": "breakout", "value_breakout": "breakout",
          "back_to_value": "value", "buy_the_dip": "dip", "rsi": "meanrev", "bollinger": "meanrev"}
LOOKBACK = 3                          # sessions of confirmed alerts considered
BAND = 0.25                           # option strikes within +-25% of the price


def r(x, n=2):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), n)


def load(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False,
                              default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    tmp.replace(p)


def tickers() -> list[str]:
    lines = (ROOT / "engine" / "tickers.txt").read_text().splitlines()
    return [s.strip().upper() for s in lines if s.strip() and not s.startswith("#")]


def session_progress(now: datetime) -> float:
    """Share of the regular session that has passed (1 outside hours / on weekends)."""
    ny = now.astimezone(NY)
    if ny.weekday() >= 5:
        return 1.0
    mins = ny.hour * 60 + ny.minute - 570
    return 1.0 if mins >= 390 or mins <= 0 else max(mins / 390, 0.05)


# ====================================================================== volume
def clv(o, h, l, c, prev=None):
    """Buying share of a bar's volume from where it closed in its range (0..1)."""
    rng = h - l
    if rng > 0:
        return min(max((c - l) / rng, 0.0), 1.0)
    ref = prev if prev is not None else o
    return 1.0 if c > ref else 0.0 if c < ref else 0.5


def volume_stats(daily, intra, progress: float) -> dict:
    """daily / intra: pandas frames with open, high, low, close, volume (oldest first)."""
    import numpy as np
    out: dict = {}
    d = daily.dropna(subset=["close"])
    if len(d) < 25:
        return out
    o, h, l, c, v = (d[k].values.astype(float) for k in ("open", "high", "low", "close", "volume"))
    v = np.nan_to_num(v)
    last_v = v[-1]
    avg20 = v[-21:-1].mean() if len(v) > 21 else v[:-1].mean()
    out["vol"] = int(last_v)
    out["avg_vol"] = int(avg20) if avg20 else None
    out["rel_vol"] = r(last_v / (avg20 * progress), 2) if avg20 else None
    mfm = np.array([2 * clv(o[i], h[i], l[i], c[i], c[i - 1] if i else None) - 1 for i in range(len(c))])
    w = slice(-20, None)
    out["cmf20"] = r((mfm[w] * v[w]).sum() / v[w].sum(), 3) if v[w].sum() else None
    up = np.r_[False, c[1:] > c[:-1]]
    out["up_vol20"] = r(v[w][up[w]].sum() / v[w].sum() * 100, 1) if v[w].sum() else None
    sign = np.r_[0, np.sign(c[1:] - c[:-1])]
    obv = np.cumsum(sign * v)
    if avg20:
        y = obv[-20:]
        x = np.arange(len(y))
        out["obv_slope"] = r(np.polyfit(x, y, 1)[0] / avg20 * 100, 1)
    # the last 10 sessions: [date, volume, estimated buy share %, close change %]
    hist = []
    for i in range(max(1, len(c) - 10), len(c)):
        hist.append([d.index[i].strftime("%Y-%m-%d"), int(v[i]), r(clv(o[i], h[i], l[i], c[i], c[i - 1]) * 100, 0),
                     r((c[i] / c[i - 1] - 1) * 100, 2)])
    out["days"] = hist
    # today, bar by bar
    if intra is not None and len(intra):
        t = intra.dropna(subset=["close"])
        t = t[t["volume"] > 0]
        if len(t):
            iv = t["volume"].values.astype(float)
            share = np.array([clv(a, b, cc, dd) for a, b, cc, dd in
                              zip(t["open"].values, t["high"].values, t["low"].values, t["close"].values)])
            buy = (share * iv).sum()
            out["buy_pct"] = r(buy / iv.sum() * 100, 1)
            out["buy_vol"], out["sell_vol"] = int(buy), int(iv.sum() - buy)
            out["intra_date"] = t.index[-1].astimezone(NY).strftime("%Y-%m-%d")
            # half-hour buckets (New York time): [HH:MM, buy, sell]
            b: dict[str, list[float]] = {}
            for ts, vv, sh in zip(t.index, iv, share):
                ny = ts.astimezone(NY)
                k = f"{ny.hour:02d}:{'00' if ny.minute < 30 else '30'}"
                x = b.setdefault(k, [0.0, 0.0])
                x[0] += vv * sh
                x[1] += vv * (1 - sh)
            out["buckets"] = [[k, int(x[0]), int(x[1])] for k, x in sorted(b.items())]
    return out


def fetch_prices(syms: list[str]):
    import yfinance as yf
    def split(raw, s):
        try:
            df = raw[s] if len(syms) > 1 else raw
            df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
            return df.dropna(how="all")
        except Exception:
            return None
    daily = yf.download(syms, period="1y", interval="1d", group_by="ticker", auto_adjust=True,
                        progress=False, threads=True)
    intra = yf.download(syms, period="1d", interval="5m", group_by="ticker", auto_adjust=True,
                        prepost=False, progress=False, threads=True)
    return {s: split(daily, s) for s in syms}, {s: split(intra, s) for s in syms}


# ====================================================================== options
def options_stats(contracts: list[dict], spot: float, today: date, hist_vols: list[float]) -> dict:
    """contracts: [{exp: date, type: C/P, strike, oi, vol, px, iv}]"""
    cv = sum(c["vol"] for c in contracts if c["type"] == "C")
    pv = sum(c["vol"] for c in contracts if c["type"] == "P")
    coi = sum(c["oi"] for c in contracts if c["type"] == "C")
    poi = sum(c["oi"] for c in contracts if c["type"] == "P")
    cp = sum(c["vol"] * c["px"] * 100 for c in contracts if c["type"] == "C")
    pp = sum(c["vol"] * c["px"] * 100 for c in contracts if c["type"] == "P")
    out = {"call_vol": int(cv), "put_vol": int(pv), "call_oi": int(coi), "put_oi": int(poi),
           "pc_vol": r(pv / cv, 2) if cv else None, "pc_oi": r(poi / coi, 2) if coi else None,
           "call_prem": int(cp), "put_prem": int(pp)}
    usual = sorted(hist_vols)[len(hist_vols) // 2] if len(hist_vols) >= 5 else None
    out["vol_vs_usual"] = r((cv + pv) / usual, 2) if usual else None
    # near-term at-the-money IV: the first expiry at least 7 days out
    exps = sorted({c["exp"] for c in contracts if (c["exp"] - today).days >= 7})
    if exps:
        e = exps[0]
        near = [c for c in contracts if c["exp"] == e and c["iv"] and 0.01 < c["iv"] < 5]
        if near:
            k = min({c["strike"] for c in near}, key=lambda s: abs(s - spot))
            ivs = [c["iv"] for c in near if c["strike"] == k]
            out["atm_iv"] = r(sum(ivs) / len(ivs) * 100, 1)
            out["atm_exp"] = e.isoformat()
            # the move the options price in until that expiry (1 standard deviation)
            out["exp_move"] = r(sum(ivs) / len(ivs) * math.sqrt(max((e - today).days, 1) / 365) * 100, 1)
    def lbl(c):
        return f"{c['exp'].strftime('%d %b')} {c['strike']:g}{c['type']}"
    rows = [c for c in contracts if c["vol"] > 0]
    top = sorted(rows, key=lambda c: -c["vol"])[:8]
    out["top"] = [[lbl(c), int(c["vol"]), int(c["oi"]), r(c["px"]), int(c["vol"] * c["px"] * 100),
                   r((c["iv"] or 0) * 100, 0)] for c in top]
    unusual = [c for c in rows if c["vol"] >= 300 and c["vol"] >= 1.5 * max(c["oi"], 1)
               and c["vol"] * c["px"] * 100 >= 50_000]
    unusual.sort(key=lambda c: -c["vol"] * c["px"])
    out["unusual"] = [[lbl(c), int(c["vol"]), int(c["oi"]), r(c["px"]), int(c["vol"] * c["px"] * 100),
                       r(c["vol"] / max(c["oi"], 1), 1)] for c in unusual[:8]]
    by_exp: dict[date, list[int]] = {}
    for c in contracts:
        x = by_exp.setdefault(c["exp"], [0, 0])
        x[0 if c["type"] == "C" else 1] += int(c["vol"])
    out["by_exp"] = [[e.isoformat(), v[0], v[1]] for e, v in sorted(by_exp.items())[:6]]
    return out


def fetch_options(syms: list[str], spots: dict[str, float], today: date) -> dict[str, list[dict]]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gex
    gex.DAYS_OUT = 45
    real_get, last = gex._get, [0.0]

    def paced(url, params):             # Alpaca's free plan: 200 requests a minute
        wait = 0.33 - (time.time() - last[0])
        if wait > 0:
            time.sleep(wait)
        last[0] = time.time()
        return real_get(url, params)
    gex._get = paced
    out = {}
    for s in syms:
        spot = spots.get(s)
        if not spot:
            continue
        a = s.replace("-", ".")
        lo, hi = spot * (1 - BAND), spot * (1 + BAND)
        try:
            oi, _ = gex.open_interest(a, lo, hi, today)
            snap = gex.snapshots(a, lo, hi, today)
        except Exception as e:
            print(f"  ! options {s}: {e}")
            continue
        rows = []
        for occ in set(oi) | set(snap):
            t = occ[-15:]
            try:
                exp = date(2000 + int(t[:2]), int(t[2:4]), int(t[4:6]))
            except ValueError:
                continue
            sn = snap.get(occ, {})
            rows.append({"exp": exp, "type": t[6], "strike": int(t[7:]) / 1000, "oi": oi.get(occ, (0, 0, 0, 0))[3],
                         "vol": sn.get("vol", 0.0), "px": sn.get("px", 0.0), "iv": sn.get("iv")})
        out[s] = rows
    return out


# ====================================================================== market mood
def band(x, cuts, scores=(-2, -1, 0, 1, 2)):
    """cuts ascending, 4 values -> score from the 5 bands."""
    if x is None:
        return None
    for c, s in zip(cuts, scores):
        if x < c:
            return s
    return scores[-1]


def market_mood(ctx: dict) -> dict:
    parts = []

    def add(key, name, value, score, text):
        if score is not None:
            parts.append({"k": key, "name": name, "value": value, "score": score, "text": text})

    sp = ctx.get("spx_chg")
    add("spx", "S&P 500 today", sp, band(sp, [-2, -0.75, 0.75, 2]),
        "Today's move of the index.")
    gap = ctx.get("spy_vs200")
    add("trend", "S&P 500 vs 200-day", gap, band(gap, [-3, 0, 3, 7]),
        "Above its 200-day average = long-term uptrend.")
    adv = ctx.get("adv_pct")
    add("adv", "Stocks up today", adv, band(adv, [25, 40, 60, 75]),
        "Share of S&P 100 stocks trading higher today.")
    b200 = ctx.get("breadth200")
    add("b200", "Stocks above 200-day", b200, band(b200, [30, 45, 55, 70]),
        "Share of S&P 100 stocks in a long-term uptrend.")
    vix = ctx.get("vix")
    add("vix", "VIX", vix, band(vix, [14, 18, 22, 30], (2, 1, 0, -1, -2)),
        "Expected S&P 500 swings over 30 days. Above 30 = fear.")
    ts = ctx.get("vix_ts")
    add("vixts", "VIX vs 3-month VIX", ts, band(ts, [0.85, 0.92, 1.0, 1.08], (2, 1, 0, -1, -2)),
        "Above 1 (short-term fear higher than long-term) usually only happens in sell-offs.")
    pc = ctx.get("pc")
    add("pc", "Put/call volume", pc, band(pc, [0.6, 0.75, 0.95, 1.2], (2, 1, 0, -1, -2)),
        "More puts than calls traded = people buying protection.")
    reg = ctx.get("gamma")
    add("gamma", "SPY options gamma", reg, None if reg is None else 1 if reg == "positive" else -1,
        "Negative gamma: dealers' hedging tends to make moves bigger in both directions.")
    dix = ctx.get("dix_pct")
    add("dix", "Dark pool index", dix, band(dix, [20, 40, 60, 80]),
        "Percentile of the last year. High = more buying in dark pools.")
    tone = ctx.get("news_pct")
    add("news", "News tone", tone, band(tone, [20, 40, 60, 80]),
        "How positive market news is vs the last year (GDELT).")
    if not parts:
        return {}
    avg = sum(p["score"] for p in parts) / len(parts)
    g = round(50 + 25 * avg)
    label = ("Extreme fear" if g < 20 else "Fear" if g < 40 else "Neutral" if g < 60
             else "Greed" if g < 80 else "Extreme greed")
    return {"score": g, "label": label, "parts": parts, "n": len(parts)}


# ====================================================================== Focus
def strategy_records(history: dict) -> dict:
    """(strategy, side) -> {n, edge, win}: alerts' 5-session move in their direction, minus the average
    5-session move of all alerts that day (so a falling market doesn't make every short look clever)."""
    rows, S = history.get("rows") or [], history.get("strategies") or []
    by_day: dict[str, list[float]] = {}
    for x in rows:
        if x[6] is not None:
            by_day.setdefault(x[0], []).append(x[6])
    base = {k: sum(v) / len(v) for k, v in by_day.items()}
    out: dict[tuple, list[float]] = {}
    for x in rows:
        if x[6] is None or x[2] >= len(S):
            continue
        out.setdefault((S[x[2]], x[3]), []).append(x[3] * (x[6] - base[x[0]]))
    return {k: {"n": len(v), "edge": sum(v) / len(v), "win": sum(e > 0 for e in v) / len(v)} for k, v in out.items()}


def score_group(g: dict, ctx: dict) -> tuple[int, list]:
    why = []

    def add(pts, text):
        why.append([round(pts), text])

    long_ = g["side"] == 1
    grades, rec = ctx["grades"], ctx["records"]
    gp = {"A": 25, "B": 18, "C": 6, "D": 0}
    best = max(g["strats"], key=lambda s: gp.get(grades.get(s, {}).get("grade"), 3))
    gr = grades.get(best, {}).get("grade")
    add(gp.get(gr, 3), f"Backtest grade {gr} ({ctx['names'].get(best, best)})" if gr else "No backtest grade yet")
    # live record of the best-known strategy-side among those agreeing, shrunk toward 0 when there are few alerts
    recs = [(s, rec.get((s, g["side"]))) for s in g["strats"]]
    recs = [(s, x) for s, x in recs if x and x["n"] >= 8]
    if recs:
        s, x = max(recs, key=lambda sx: sx[1]["edge"] * sx[1]["n"] / (sx[1]["n"] + 30))
        e = x["edge"] * x["n"] / (x["n"] + 30)
        pts = max(min(e * 8, 15), -10)
        add(pts, f"Live record: {ctx['names'].get(s, s)} {'+' if x['edge'] >= 0 else ''}{x['edge']:.1f}% vs other alerts after 5 days ({x['n']} alerts, {x['win'] * 100:.0f}% right)")
    fam = {FAMILY.get(x, x) for x in g["strats"]}
    n = len(fam)
    if n > 1:
        add(min((n - 1) * 7, 18), f"{len(g['strats'])} strategies agree ({n} different ideas)")
    elif len(g["strats"]) > 1:
        add(3, f"{len(g['strats'])} variants of the same idea agree")
    lab = g.get("strength")
    if lab in ("Strong", "Moderate"):
        add(10 if lab == "Strong" else 5, f"Scanner strength: {lab}")
    st = ctx["stocks"].get(g["t"], {})
    vs = st.get("vs200")
    if vs is not None:
        if (vs > 0) == long_:
            add(8, "With the long-term trend (200-day)")
        else:
            add(-5, "Against the long-term trend (200-day)")
    mood = ctx.get("mood", {}).get("score")
    if mood is not None:
        if mood < 25:
            add(-6 if long_ else -3, "Market in extreme fear: longs fight the tide" if long_
                else "Market in extreme fear: late to short, bounces are violent")
        elif mood < 40:
            add(-3 if long_ else 5, "Fearful market: longs fight the tide" if long_ else "Fearful market: shorts go with it")
        elif mood >= 75:
            add(2 if long_ else -6, "Greedy market: longs go with it" if long_ else "Greedy market: shorts fight the tide")
        elif mood >= 55:
            add(5 if long_ else -3, "Market mood supports longs" if long_ else "Market mood supports longs, not shorts")
    f = ctx["flow"].get(g["t"], {})
    cmf = f.get("cmf20")
    if cmf is not None and abs(cmf) >= 0.05:
        add(6 if (cmf > 0) == long_ else -4, f"20-day money flow {'buying' if cmf > 0 else 'selling'} ({cmf:+.2f})")
    bp = f.get("buy_pct")
    if bp is not None and abs(bp - 50) >= 8:
        add(4 if (bp > 50) == long_ else -3, f"Today's volume {bp:.0f}% buying (estimate)")
    rv = f.get("rel_vol")
    if rv and rv >= 1.5:
        add(4, f"Volume {rv:.1f}x normal")
    o = ctx["opts"].get(g["t"], {})
    pc = o.get("pc_vol")
    if pc is not None and (o.get("call_vol", 0) + o.get("put_vol", 0)) >= 2000:
        if pc <= 0.6:
            add(4 if long_ else -3, f"Options: calls dominate (put/call {pc:.2f})")
        elif pc >= 1.4:
            add(4 if not long_ else -3, f"Options: puts dominate (put/call {pc:.2f})")
    dp = ctx["dark"].get(g["t"])
    if dp:
        add(4 if (dp == "buying") == long_ else -3, f"Dark pool short-volume signal: {dp}")
    nm = ctx["news"].get(g["t"])
    if nm in ("bullish", "bearish"):
        add(3 if (nm == "bullish") == long_ else -3, f"News mood {nm}")
    px = (ctx["flow"].get(g["t"]) or {}).get("px") or g.get("price")
    if g.get("stop") and px:
        risk = abs(g["stop"] / px - 1) * 100
        if (g["stop"] > px) != (not long_):
            add(-20, "Price is already past the “wrong if” level")
        elif risk > 10:
            add(-6, f"“Wrong if” level is {risk:.0f}% away: a lot of risk per share")
        elif risk < 3:
            add(3, f"Tight “wrong if” level, {risk:.1f}% away")
    ed = ctx["earn"].get(g["t"])
    if ed is not None and 0 <= ed <= 3:
        add(-15, f"Earnings in {ed} day{'s' if ed != 1 else ''}: a gap can blow through any stop")
    age = g["age"]
    if g["live"]:
        add(0, "Provisional: fires only if it holds until the close")
    elif age == 0:
        add(5, "Fresh: fired at the last close")
    elif age >= 2:
        add(-5, f"Fired {age} sessions ago")
    raw = 25 + sum(p for p, _ in why)
    return max(0, min(100, round(raw))), why


def build_focus(ctx: dict, alerts: dict, live: dict) -> list[dict]:
    feed = alerts.get("alerts") or []
    days = sorted({a["date"] for a in feed}, reverse=True)[:LOOKBACK]
    groups: dict[tuple, dict] = {}
    def put(a, live_):
        if a.get("stop_hit"):
            return
        side = 1 if a["side"] == "bullish" else -1
        k = (a["ticker"], side)
        g = groups.setdefault(k, {"t": a["ticker"], "side": side, "strats": [], "ids": [], "age": 9,
                                  "live": True, "strength": None, "price": a.get("price"), "stop": None,
                                  "notes": []})
        if a["strategy"] not in g["strats"]:
            g["strats"].append(a["strategy"])
            g["notes"].append(a.get("note") or "")
        g["ids"].append(a.get("id"))
        age = days.index(a["date"]) if a["date"] in days else 0
        if not live_:
            g["live"] = False
            if age < g["age"]:
                g["age"], g["price"] = age, a.get("price")
        else:
            g["age"] = min(g["age"], 0)
        lab = (a.get("strength") or {}).get("label")
        order = {"Strong": 3, "Moderate": 2, "Weak": 1}
        if order.get(lab, 0) > order.get(g["strength"], 0):
            g["strength"] = lab
        st = a.get("stop")
        if st is not None:
            g["stop"] = st if g["stop"] is None else (max(g["stop"], st) if side == 1 else min(g["stop"], st))
    for a in feed:
        if a["date"] in days:
            put(a, False)
    for a in (live.get("alerts") or []):
        put(a, True)
    out = []
    for g in groups.values():
        sc, why = score_group(g, ctx)
        g["score"], g["why"] = sc, sorted(why, key=lambda x: -abs(x[0]))
        out.append(g)
    out.sort(key=lambda g: -g["score"])
    nf = nw = 0
    for g in out:
        if g["score"] >= FOCUS_MIN and nf < FOCUS_MAX:
            g["tier"], nf = "focus", nf + 1
        elif g["score"] >= WATCH_MIN and nw < WATCH_MAX:
            g["tier"], nw = "watch", nw + 1
        else:
            g["tier"] = "noise"
    return out


def notify_focus(focus: list[dict], names: dict, today: str) -> None:
    prefix = os.environ.get("NTFY_PREFIX", "").strip()
    path = DATA / "terminal" / "focus_sent.json"
    sent = load(path, {}) or {}
    cut = (date.fromisoformat(today) - timedelta(days=30)).isoformat()
    sent = {k: v for k, v in sent.items() if v >= cut}
    new = [g for g in focus if g["tier"] == "focus" and f"{g['t']}:{g['side']}:{today}" not in sent]
    for g in new[:8]:
        sent[f"{g['t']}:{g['side']}:{today}"] = today
        if not prefix:
            continue
        side = "LONG" if g["side"] == 1 else "SHORT"
        top = "; ".join(t for p, t in g["why"][:3])
        msg = (f"{side} {g['t']} · score {g['score']}{' · provisional' if g['live'] else ''}\n"
               f"{', '.join(names.get(s, s) for s in g['strats'])}\n{top}"
               + (f"\nWrong if: {g['stop']}" if g.get("stop") else ""))
        try:
            req = urllib.request.Request(f"https://ntfy.sh/{prefix}-focus", data=msg.encode(), method="POST",
                                         headers={"Title": f"Focus: {side} {g['t']}", "Tags": "dart",
                                                  "Click": "https://thejackwar.github.io/signal-desk/#terminal-" + g["t"]})
            urllib.request.urlopen(req, timeout=20)
        except Exception as e:
            print(f"  ! ntfy: {e}")
    write_json(path, sent)


# ====================================================================== main
def context_files() -> dict:
    lineup = load(DATA / "lineup.json", {}) or {}
    grades = ((lineup.get("evidence") or {}).get("stocks") or {}).get("strategies") or {}
    names = {}
    for s in (load(DATA / "strategies.json", {}) or {}).get("strategies", []) or []:
        if isinstance(s, dict) and s.get("id"):
            names[s["id"]] = s.get("name") or s["id"]
    return {
        "grades": grades, "names": names,
        "records": strategy_records(load(DATA / "history.json", {}) or {}),
        "alerts": load(DATA / "alerts.json", {}) or {}, "live": load(DATA / "live.json", {}) or {},
        "dark": {s["t"]: s.get("sig") for s in ((load(DATA / "darkpool" / "daily.json", {}) or {}).get("stocks") or [])},
        "darkidx": ((load(DATA / "darkpool" / "daily.json", {}) or {}).get("index") or {}),
        "news": {k: v.get("mood") for k, v in ((load(DATA / "sentiment.json", {}) or {}).get("stocks") or {}).items()},
        "cal": load(DATA / "calendar.json", {}) or {},
        "gex": ((load(DATA / "gex" / "latest.json", {}) or {}).get("underlyings") or {}).get("SPY") or {},
        "newsmkt": (load(DATA / "news_market.json", {}) or {}).get("days") or {},
        "stocksjson": load(DATA / "stocks.json", {}) or {},
    }


def pctile(vals: list[float], x: float) -> float | None:
    if not vals or x is None:
        return None
    return round(sum(v <= x for v in vals) / len(vals) * 100)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--no-options", action="store_true")
    ap.add_argument("--fast", action="store_true",
                    help="skip the options download and keep the last options figures (quick refresh between full runs)")
    args = ap.parse_args()
    global DATA
    now = datetime.now(timezone.utc)
    syms = tickers()
    C = context_files()
    if args.demo:
        out_dir = Path("/tmp/terminal-demo")
        daily, intra, chains, spots, idx = demo_data(syms, now)
    else:
        out_dir = DATA / "terminal"
        print(f"Prices for {len(syms)} stocks + indices…")
        daily, intra = fetch_prices(syms + ["SPY", "^GSPC", "^VIX", "^VIX3M"])
        idx = {k: daily.pop(k, None) for k in ["SPY", "^GSPC", "^VIX", "^VIX3M"]}
        for k in ["SPY", "^GSPC", "^VIX", "^VIX3M"]:
            intra.pop(k, None)
        spots = {s: float(df["close"].dropna().iloc[-1]) for s, df in daily.items() if df is not None and len(df.dropna())}
        chains = {}
        if not args.no_options and not args.fast and os.environ.get("ALPACA_KEY_ID"):
            print("Options…")
            chains = fetch_options(syms, spots, now.astimezone(NY).date())
    today = now.astimezone(NY).date()
    prog = session_progress(now)

    # ---------------------------------------------------------------- per stock
    hist = load(out_dir / "flow_hist.json", {}) or {}
    prev = load(out_dir / "flow.json", {}) or {}
    flow, opts, stocks_ctx = {}, {}, {}
    ups = downs = 0
    for s in syms:
        df = daily.get(s)
        if df is None or len(df.dropna(subset=["close"])) < 25:
            continue
        f = volume_stats(df, intra.get(s), prog)
        c = df["close"].dropna()
        chg = (c.iloc[-1] / c.iloc[-2] - 1) * 100
        f["chg"] = r(chg, 2)
        f["px"] = r(c.iloc[-1], 2)
        sma200 = c.iloc[-200:].mean() if len(c) >= 200 else None
        stocks_ctx[s] = {"vs200": (c.iloc[-1] / sma200 - 1) * 100 if sma200 else None}
        f["vs200"] = r(stocks_ctx[s]["vs200"], 1)
        ups += chg > 0
        downs += chg < 0
        flow[s] = f
        if s in chains and chains[s]:
            hv = [x[1] + x[2] for x in hist.get(s, []) if x[0] != today.isoformat()][-20:]
            o = options_stats(chains[s], spots.get(s) or f["px"], today, hv)
            opts[s] = o
            h = [x for x in hist.get(s, []) if x[0] != today.isoformat()]
            h.append([today.isoformat(), o["call_vol"], o["put_vol"]])
            hist[s] = h[-40:]
            # a running daily series for the page: [date, calls, puts]
            o["days"] = hist[s][-10:]

    options_at = now.strftime("%Y-%m-%dT%H:%MZ") if opts else None
    if not opts and prev.get("options"):          # quick run: keep the last full run's options
        opts, options_at = prev["options"], prev.get("options_at") or prev.get("generated_at")

    # ---------------------------------------------------------------- market mood
    def last_chg(df):
        if df is None:
            return None
        c = df["close"].dropna()
        return r((c.iloc[-1] / c.iloc[-2] - 1) * 100, 2) if len(c) > 1 else None
    def last(df):
        if df is None:
            return None
        c = df["close"].dropna()
        return float(c.iloc[-1]) if len(c) else None
    spy = idx.get("SPY")
    spy_vs200 = None
    if spy is not None and len(spy.dropna()) >= 200:
        cc = spy["close"].dropna()
        spy_vs200 = r((cc.iloc[-1] / cc.iloc[-200:].mean() - 1) * 100, 2)
    vix, vix3 = last(idx.get("^VIX")), last(idx.get("^VIX3M"))
    tot_c = sum(o["call_vol"] for o in opts.values())
    tot_p = sum(o["put_vol"] for o in opts.values())
    pc = r(tot_p / tot_c, 2) if tot_c else C["gex"].get("put_call_volume")
    nd = C["newsmkt"]
    tone_pct = None
    if nd:
        ks = sorted(nd)[-365:]
        tones = [nd[k][1] for k in ks if nd[k] and nd[k][1] is not None]
        if tones:
            tone_pct = pctile(tones[:-1] or tones, tones[-1])
    b200 = sum(1 for v in stocks_ctx.values() if v["vs200"] is not None and v["vs200"] > 0)
    nb = sum(1 for v in stocks_ctx.values() if v["vs200"] is not None)
    mctx = {"spx_chg": last_chg(idx.get("^GSPC")), "spy_vs200": spy_vs200,
            "adv_pct": r(ups / (ups + downs) * 100, 0) if ups + downs else None,
            "breadth200": r(b200 / nb * 100, 0) if nb else C["stocksjson"].get("breadth200"),
            "vix": r(vix, 2), "vix_ts": r(vix / vix3, 3) if vix and vix3 else None, "pc": pc,
            "gamma": C["gex"].get("regime"), "dix_pct": C["darkidx"].get("pct_1y"), "news_pct": tone_pct}
    mood = market_mood(mctx)
    mood["inputs"] = mctx
    mood["adv"], mood["dec"] = ups, downs
    mood["opt_calls"], mood["opt_puts"] = tot_c, tot_p
    if opts:
        mood["most_puts"] = [s for s, o in sorted(opts.items(), key=lambda x: -x[1]["put_vol"])][:6]
        mood["most_calls"] = [s for s, o in sorted(opts.items(), key=lambda x: -x[1]["call_vol"])][:6]
        mood["unusual"] = sorted(([s, *u] for s, o in opts.items() for u in o["unusual"][:2]), key=lambda x: -x[5])[:10]
    if flow:
        mood["most_bought"] = [s for s, f in sorted(flow.items(), key=lambda x: -(x[1].get("buy_pct") or 50)) if (f.get("rel_vol") or 0) >= 0.8][:6]
        mood["most_sold"] = [s for s, f in sorted(flow.items(), key=lambda x: (x[1].get("buy_pct") or 50)) if (f.get("rel_vol") or 0) >= 0.8][:6]
        mood["heavy_vol"] = [s for s, f in sorted(flow.items(), key=lambda x: -(x[1].get("rel_vol") or 0))][:6]

    # ---------------------------------------------------------------- Focus
    earn = {}
    for e in C["cal"].get("earnings") or []:
        try:
            dd = (date.fromisoformat(e["date"]) - today).days
        except Exception:
            continue
        if dd >= 0 and (e["ticker"] not in earn or dd < earn[e["ticker"]]):
            earn[e["ticker"]] = dd
    fctx = {**C, "stocks": stocks_ctx, "flow": flow, "opts": opts, "mood": mood, "earn": earn}
    focus = build_focus(fctx, C["alerts"], C["live"])
    counts = {t: sum(g["tier"] == t for g in focus) for t in ("focus", "watch", "noise")}
    raw_n = sum(len(g["ids"]) for g in focus)
    if not args.demo:
        notify_focus(focus, C["names"], today.isoformat())
    keep = [g for g in focus if g["tier"] != "noise"][:40] + [g for g in focus if g["tier"] == "noise"][:60]
    for g in keep:
        g.pop("ids", None)

    res = {"generated_at": now.strftime("%Y-%m-%dT%H:%MZ"), "session": today.isoformat(), "progress": r(prog, 2),
           "demo": bool(args.demo), "mood": mood, "flow": flow, "options": opts, "options_at": options_at,
           "focus": {"min": FOCUS_MIN, "max": FOCUS_MAX, "watch": WATCH_MIN, "counts": counts, "alerts_in": raw_n,
                     "sessions": LOOKBACK, "items": keep}}
    write_json(out_dir / "flow.json", res)
    write_json(out_dir / "flow_hist.json", hist)
    print(f"Mood {mood.get('score')} {mood.get('label')} · {len(flow)} stocks · {len(opts)} with options · "
          f"focus {counts} from {raw_n} alerts → {out_dir}")


# ====================================================================== demo
def demo_data(syms, now):
    import numpy as np
    import pandas as pd
    rng = random.Random(7)
    idx_days = pd.bdate_range(end=now.date(), periods=260)
    def series(p0, vol):
        c = [p0]
        for _ in range(len(idx_days) - 1):
            c.append(c[-1] * (1 + rng.gauss(0.0003, vol)))
        c = np.array(c)
        o = c * (1 + np.array([rng.gauss(0, vol / 3) for _ in c]))
        h = np.maximum(o, c) * (1 + np.abs(np.array([rng.gauss(0, vol / 2) for _ in c])))
        l = np.minimum(o, c) * (1 - np.abs(np.array([rng.gauss(0, vol / 2) for _ in c])))
        v = np.array([abs(rng.gauss(1, .3)) * 1e7 for _ in c])
        return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=idx_days)
    daily = {s: series(rng.uniform(50, 600), 0.018) for s in syms}
    t0 = datetime(now.year, now.month, now.day, 14, 30, tzinfo=timezone.utc)
    bars = pd.date_range(t0, periods=78, freq="5min")
    intra = {}
    for s in syms:
        p = float(daily[s]["close"].iloc[-1])
        c = p * (1 + np.cumsum([rng.gauss(0, 0.002) for _ in bars]))
        o = np.r_[p, c[:-1]]
        intra[s] = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.001, "low": np.minimum(o, c) * 0.999,
                                 "close": c, "volume": [rng.uniform(5e4, 3e5) for _ in bars]}, index=bars)
    chains, spots = {}, {}
    today = now.astimezone(NY).date()
    for s in syms:
        spot = float(daily[s]["close"].iloc[-1]); spots[s] = spot
        rows = []
        for k in range(1, 6):
            exp = today + timedelta(days=7 * k - today.weekday() + 4 if k else 4)
            for j in range(-8, 9):
                strike = round(spot * (1 + j * 0.025))
                for t in "CP":
                    oi = rng.randint(0, 5000)
                    vol = rng.randint(0, 4000) if rng.random() < 0.7 else 0
                    rows.append({"exp": exp, "type": t, "strike": strike, "oi": oi, "vol": vol,
                                 "px": max(0.05, abs(rng.gauss(spot * 0.02, spot * 0.01))), "iv": rng.uniform(0.2, 0.6)})
        chains[s] = rows
    idx = {"SPY": series(700, 0.01), "^GSPC": series(7000, 0.01), "^VIX": series(28, 0.03), "^VIX3M": series(25, 0.02)}
    return daily, intra, chains, spots, idx


if __name__ == "__main__":
    main()
