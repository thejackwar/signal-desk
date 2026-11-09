"""
Today's market outlook for the Terminal: one box that reads everything the site already collects and
writes, in plain words, what the next US session is set up for (docs/data/terminal/outlook.json).

It is a rule-based read, not an AI guess and not a forecast: every sentence comes from a number on the
site, and the numbers are listed next to the text so anyone can check it.

  Inputs already on the site
    terminal/markets.json   US futures, Europe, Asia, VIX term structure, dollar, BTC, yield curve, Fed odds, oil
    terminal/flow.json      market mood gauge (breadth, put/call, gamma, dark pools, news tone) and Focus setups
    gex/latest.json         SPY gamma regime, gamma flip, call wall / put wall
    calendar.json           US economic releases, Fed meetings, earnings (who reports before/after the bell)
    crypto/market.json      crypto fear & greed, BTC funding
  New data fetched here (Yahoo, free; each one optional)
    S&P 500 + VIX daily history since 1990  -> "days like this" base rates and yesterday's high/low/close
    10-year yield, WTI oil, gold, copper       -> overnight moves of the macro drivers
    megacap and today's reporters pre-market  -> what the biggest stocks are doing before the open

The outlook made before the US open is logged (terminal/outlook_log.json) and graded against the
S&P 500's close, so the box shows how often its lean was right.

  python engine/outlook.py
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
OUT = DATA / "terminal" / "outlook.json"
LOG = DATA / "terminal" / "outlook_log.json"
ET = ZoneInfo("America/New_York")

MEGA = ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "BRK-B", "JPM", "LLY", "V"]
MACRO = [("^TNX", "10-year yield"), ("CL=F", "WTI oil"), ("GC=F", "Gold"), ("HG=F", "Copper")]
BIG_KINDS = ("CPI", "Jobs", "Fed", "PCE", "GDP", "PPI", "Retail", "ISM", "Unemployment")


def r(x, n=2):
    try:
        x = float(x)
        return None if math.isnan(x) or math.isinf(x) else round(x, n)
    except (TypeError, ValueError):
        return None


def load(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def sgn(x, a, b):
    """-2..2 from a change: beyond +-b counts double, beyond +-a counts once."""
    if x is None:
        return 0
    return 2 if x >= b else 1 if x >= a else -2 if x <= -b else -1 if x <= -a else 0


def pct(x, dp=1):
    return "–" if x is None else f"{'+' if x > 0 else '−' if x < 0 else ''}{abs(x):.{dp}f}%"


def session_date(now_et: datetime) -> date:
    """The US session this outlook is for: today until the 16:00 close, then the next weekday."""
    d = now_et.date()
    if now_et.hour >= 16 or d.weekday() >= 5:
        d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


# ====================================================================== new data (Yahoo, optional)
def fetch_history():
    """S&P 500 and VIX daily since 1990 (VIX3M since 2006). None when Yahoo can't be reached."""
    try:
        import yfinance as yf
        df = yf.download(["^GSPC", "^VIX", "^VIX3M"], start="1990-01-01", interval="1d", auto_adjust=False,
                         progress=False, group_by="ticker", threads=True)
        spx = df["^GSPC"][["Open", "High", "Low", "Close"]].dropna()
        vix = df["^VIX"]["Close"].dropna()
        v3 = df["^VIX3M"]["Close"].dropna()
        if len(spx) < 1000 or len(vix) < 1000:
            return None
        return spx, vix, v3
    except Exception as e:
        print("history: FAILED", type(e).__name__, e)
        return None


def fetch_last(symbols: list[str], prepost: bool = False) -> dict:
    """Last price vs the previous regular close, per symbol (pre-market included when prepost)."""
    out = {}
    try:
        import yfinance as yf
        daily = yf.download(symbols, period="10d", interval="1d", auto_adjust=False, progress=False, group_by="ticker", threads=True)
        intra = yf.download(symbols, period="2d", interval="5m", prepost=True, auto_adjust=False, progress=False,
                            group_by="ticker", threads=True) if prepost else None
        for s in symbols:
            try:
                d = (daily[s] if len(symbols) > 1 else daily)["Close"].dropna()
                if len(d) < 2:
                    continue
                last_day = d.index[-1].date()
                last, prev = float(d.iloc[-1]), float(d.iloc[-2])
                at = d.index[-1].strftime("%Y-%m-%d")
                if intra is not None:
                    i = (intra[s] if len(symbols) > 1 else intra)["Close"].dropna()
                    if len(i):
                        ts = i.index[-1].tz_convert(ET) if i.index.tz is not None else i.index[-1]
                        # a pre-market print today, before the daily bar for today exists
                        if ts.date() > last_day:
                            prev, last, at = float(d.iloc[-1]), float(i.iloc[-1]), ts.strftime("%Y-%m-%d %H:%M ET")
                out[s] = {"last": r(last, 4), "chg": r((last / prev - 1) * 100, 2), "at": at, "abs": r(last - prev, 4)}
            except Exception:
                pass
    except Exception as e:
        print("quotes: FAILED", type(e).__name__, e)
    return out


def base_rates(hist, vix_now: float | None, ts_now: float | None, weekday: int) -> dict | None:
    """What the S&P 500 did on the next session after days with a VIX like today's."""
    if not hist or vix_now is None:
        return None
    spx, vix, v3 = hist
    c = spx["Close"]
    nxt = (c.shift(-1) / c - 1) * 100            # the move of the following session
    rng = ((spx["High"] - spx["Low"]) / c.shift(1) * 100).shift(-1)
    df = nxt.to_frame("n").join(vix.rename("v"), how="inner").join(rng.rename("rg"))
    df = df.join(v3.rename("v3"), how="left").dropna(subset=["n", "v"])
    lo, hi = (0, 15) if vix_now < 15 else (15, 20) if vix_now < 20 else (20, 25) if vix_now < 25 else (25, 30) if vix_now < 30 else (30, 200)
    sel = df[(df.v >= lo) & (df.v < hi)]
    label = f"VIX between {lo} and {hi}" if hi < 200 else "VIX above 30"
    if ts_now is not None:
        inv = ts_now >= 1
        s2 = sel.dropna(subset=["v3"])
        s2 = s2[(s2.v / s2.v3 >= 1) == inv]
        if len(s2) >= 150:
            sel, label = s2, label + (" and the VIX curve inverted" if inv else " and the VIX curve normal")
    if len(sel) < 100:
        return None
    # the VIX-implied one-day move: VIX / sqrt(252), and how often the next day stayed inside it
    sig = df.v / math.sqrt(252)
    inside = (df.n.abs() <= sig).mean() * 100
    return {
        "label": label, "n": int(len(sel)), "since": str(df.index[0].date()),
        "up": r((sel.n > 0).mean() * 100, 0), "avg": r(sel.n.mean(), 2), "median_abs": r(sel.n.abs().median(), 2),
        "big": r((sel.n.abs() >= 1.5).mean() * 100, 0), "range": r(sel.rg.median(), 2),
        "all_up": r((df.n > 0).mean() * 100, 0), "all_abs": r(df.n.abs().median(), 2),
        "inside_1sd": r(inside, 0),
    }


# ====================================================================== the read
def build(now: datetime, hist, quotes: dict, prem: dict) -> dict:
    now_et = now.astimezone(ET)
    sess = session_date(now_et)
    sess_s = sess.isoformat()
    open_et = datetime.combine(sess, datetime.min.time(), ET).replace(hour=9, minute=30)
    phase = "pre" if now_et < open_et else "live" if now_et.hour < 16 else "post"

    MB = load(DATA / "terminal" / "markets.json", {}) or {}
    FL = load(DATA / "terminal" / "flow.json", {}) or {}
    GX0 = load(DATA / "gex" / "latest.json", {}) or {}
    GX = {**((GX0.get("underlyings") or {}).get("SPY") or {}), "updated": GX0.get("updated"), "oi_date": GX0.get("oi_date")}
    CAL = load(DATA / "calendar.json", {}) or {}
    CR = load(DATA / "crypto" / "market.json", {}) or {}
    ST = load(DATA / "stocks.json", {}) or {}
    q = {x["s"]: x for x in (MB.get("wei") or []) + (MB.get("fx") or []) + (MB.get("vol") or [])}
    M = FL.get("mood") or {}
    I = M.get("inputs") or {}

    def chg(s, fresh_days=4):
        x = q.get(s)
        if not x or x.get("chg") is None:
            return None
        try:   # only count markets that traded recently (a Friday close isn't "overnight" on Tuesday)
            if (sess - date.fromisoformat(x["date"][:10])).days > fresh_days:
                return None
        except Exception:
            pass
        return x["chg"]

    drivers, score, why = [], 0.0, []

    def drv(k, name, value, s, w, note, fmt=None):
        nonlocal score
        drivers.append({"k": k, "name": name, "value": value, "fmt": fmt or ("pct" if isinstance(value, (int, float)) else "txt"),
                        "effect": 0 if s == 0 else (1 if s > 0 else -1), "note": note})
        score += s * w

    # --- overnight: US futures, Europe, Asia
    es, nq = chg("ES=F", 3), chg("NQ=F", 3)
    fut = None if es is None and nq is None else ((es or 0) + (nq if nq is not None else es)) / 2
    if fut is not None:
        s = sgn(fut, 0.15, 0.6)
        drv("fut", "US futures", r(fut), s, 2.0, f"S&P {pct(es, 2)} · Nasdaq {pct(nq, 2)} vs the last close. "
            + ("The clearest hint of how the day opens." if phase == "pre" else "Live index futures."))
    eu = [chg(s, 1) for s in ("^STOXX50E", "^GDAXI", "FTSEMIB.MI")]
    eu = [x for x in eu if x is not None]
    if eu:
        e = sum(eu) / len(eu)
        drv("eu", "Europe", r(e), sgn(e, 0.3, 1.0), 0.8, f"Euro Stoxx 50 {pct(chg('^STOXX50E', 1), 2)}, DAX {pct(chg('^GDAXI', 1), 2)}, FTSE MIB {pct(chg('FTSEMIB.MI', 1), 2)}.")
    asia = [chg(s, 1) for s in ("^N225", "^HSI", "000001.SS", "^KS11")]
    asia = [x for x in asia if x is not None]
    if asia:
        a = sum(asia) / len(asia)
        drv("asia", "Asia", r(a), sgn(a, 0.4, 1.2), 0.5, f"Nikkei {pct(chg('^N225', 1), 2)}, Hang Seng {pct(chg('^HSI', 1), 2)}, Shanghai {pct(chg('000001.SS', 1), 2)}, KOSPI {pct(chg('^KS11', 1), 2)}.")

    # --- macro: dollar, 10-year yield, oil, gold, copper
    dxy = chg("DX-Y.NYB", 2)
    if dxy is not None:
        drv("dxy", "Dollar index", dxy, -sgn(dxy, 0.3, 0.7), 0.5, "A sharply stronger dollar usually weighs on US stocks (tighter money, weaker overseas earnings).", "pctn")
    tnx = quotes.get("^TNX")
    if tnx and tnx.get("abs") is not None:
        bp = r(tnx["abs"] * (100 if tnx["last"] < 20 else 10), 0)   # ^TNX in % (4.25; older feeds 42.5) -> basis points
        drv("tnx", "10-year yield", bp, -sgn(bp, 4, 9), 0.6, f"At {tnx['last']:.2f}%. Fast-rising yields hurt growth and tech stocks; falling yields help them.", "bp")
    oil = quotes.get("CL=F")
    if oil and oil.get("chg") is not None:
        drv("oil", "WTI oil", oil["chg"], -sgn(oil["chg"], 2.5, 5) if oil["chg"] > 0 else 0, 0.4, f"${oil['last']:.2f}. A big oil jump feeds inflation worries; energy stocks move with it.")
    gold = quotes.get("GC=F")
    if gold and gold.get("chg") is not None:
        drv("gold", "Gold", gold["chg"], -1 if gold["chg"] >= 1.5 else 0, 0.3, f"${gold['last']:.0f}. A strong gold bid often means investors are looking for safety.")
    cop = quotes.get("HG=F")
    if cop and cop.get("chg") is not None:
        drv("copper", "Copper", cop["chg"], sgn(cop["chg"], 1, 2.5), 0.3, "“Dr. Copper”: rising copper = traders expect more growth.")
    btc = chg("BTC-USD", 1)
    if btc is not None:
        drv("btc", "Bitcoin", btc, sgn(btc, 2, 5), 0.3, "Trades all weekend and night: a read on risk appetite while Wall Street is shut.")

    # --- backdrop: trend, breadth, mood, volatility, gamma (these shape the character more than the lean)
    trend = I.get("spy_vs200")
    if trend is not None:
        drv("trend", "S&P 500 vs 200-day", trend, 1 if trend > 0 else -1, 0.8, "Above the 200-day = long-term uptrend: dips have tended to get bought." if trend > 0 else "Below the 200-day = long-term downtrend: rallies have tended to get sold.")
    b200 = I.get("breadth200") or r(ST.get("breadth200"), 0)
    if b200 is not None:
        drv("b200", "Stocks above 200-day", b200, 1 if b200 >= 60 else -1 if b200 < 40 else 0, 0.4, "How many big US stocks are in their own uptrend.", "pctv")
    vix = (q.get("^VIX") or {}).get("last") or I.get("vix")
    v3 = (q.get("^VIX3M") or {}).get("last")
    v9 = (q.get("^VIX9D") or {}).get("last")
    ts = r(vix / v3, 3) if vix and v3 else I.get("vix_ts")
    if vix is not None:
        drv("vix", "VIX", vix, 1 if vix < 16 else -1 if vix >= 25 else 0, 0.4, "Below 16 calm, above 25 nervous, above 30 fear.", "num")
    if ts is not None:
        drv("vixts", "VIX curve", ts, -1 if ts >= 1 else 0, 0.5, "Inverted (above 1): traders pay more for protection this month than for later months. Usually only in sell-offs." if ts >= 1 else "Normal (below 1): no panic in the options market.", "num")
    reg = GX.get("regime") or I.get("gamma")
    if M.get("score") is not None:
        drv("mood", "Market mood", M["score"], 0, 0, f"{M.get('label')}: the site's 0–100 fear/greed gauge from {M.get('n')} readings. Extremes often mark turning points, so it sets the tone rather than the direction.", "of100")

    # --- the lean
    lean = "up" if score >= 2.5 else "down" if score <= -2.5 else "flat"

    # --- calendar: today's releases and reporters, and tomorrow's big ones
    eco_today = [e for e in CAL.get("economy") or [] if e.get("date") == sess_s]
    tmr = sess + timedelta(days=1 if sess.weekday() < 4 else 3)
    eco_next = [e for e in CAL.get("economy") or [] if e.get("date") == tmr.isoformat() and any(k in (e.get("kind") or "") for k in BIG_KINDS)]
    big_today = any(any(k in (e.get("kind") or "") for k in BIG_KINDS) for e in eco_today)
    fomc_today = any("Fed" in (e.get("kind") or "") for e in eco_today)
    ern = [e for e in CAL.get("earnings") or [] if e.get("date") == sess_s and (e.get("sp500") or e.get("sp100"))]
    ern.sort(key=lambda e: -(e.get("mcap") or 0))
    ern_pre = [e for e in ern if e.get("time") == "bmo"][:8]
    ern_post = [e for e in ern if e.get("time") == "amc"][:8]

    # --- character of the day
    vol_hi = (vix or 0) >= 25 or reg == "negative" or (ts or 0) >= 1
    vol_lo = (vix or 99) < 16 and reg == "positive"
    if fomc_today:
        character = "event"
    elif vol_hi:
        character = "wild"
    elif vol_lo:
        character = "calm"
    else:
        character = "normal"
    if big_today and character in ("normal", "calm"):
        character = "event"
    # a read is never more than "medium": the backdrop only tilts the odds, and wild or event days tilt them less
    conf = "medium" if abs(score) >= 5 and character not in ("wild", "event") else "low"

    # --- expected range from the VIX (one standard deviation for one day)
    spx = q.get("^GSPC") or {}
    spx_last = spx.get("last")
    ref = spx_last * (1 + (es or 0) / 100) if spx_last and phase == "pre" and es is not None else spx_last
    iv = v9 if v9 and phase == "pre" else vix
    rng = None
    if ref and iv:
        sd = iv / math.sqrt(252)
        rng = {"vix_used": "VIX9D" if iv == v9 else "VIX", "iv": r(iv, 1), "pct": r(sd, 2), "ref": r(ref, 0),
               "lo": r(ref * (1 - sd / 100), 0), "hi": r(ref * (1 + sd / 100), 0), "pts": r(ref * sd / 100, 0)}

    # --- key levels (SPY options), scaled to today's SPY price via the S&P 500
    levels = []
    spy_spot, spy_now = GX.get("spot"), None
    if spy_spot and ref:
        # the levels come from the last options snapshot: move SPY's price there to today's via the S&P 500
        spx_then = None
        if hist and GX.get("updated"):
            try:
                c0 = hist[0]["Close"]
                spx_then = float(c0[c0.index <= GX["updated"][:10]].iloc[-1])
            except Exception:
                spx_then = None
        spy_now = r(spy_spot * ref / (spx_then or spx_last), 2)
    if spy_spot and spy_now:
        for k, name, note in (("call_wall", "Call wall", "Biggest call position: often acts as a ceiling."),
                              ("flip", "Gamma flip", "Above it dealers calm moves down; below it they speed them up."),
                              ("put_wall", "Put wall", "Biggest put position: often acts as a floor; a break can speed up selling.")):
            v = GX.get(k)
            if v:
                levels.append({"name": "SPY " + name.lower(), "v": r(v, 2), "dist": r((v / spy_now - 1) * 100, 1), "note": note})
        levels.sort(key=lambda x: -x["v"])
    prev = None
    if hist:
        s = hist[0]
        last = s.iloc[-1]
        prev = {"date": str(s.index[-1].date()), "high": r(last["High"], 0), "low": r(last["Low"], 0), "close": r(last["Close"], 0)}

    # --- base rates
    br = base_rates(hist, vix, ts, sess.weekday())

    # --- pre-market movers among the biggest stocks and today's reporters
    pm = [{"t": t, "chg": v["chg"], "at": v["at"]} for t, v in prem.items() if v.get("chg") is not None and "ET" in str(v.get("at"))]
    pm.sort(key=lambda x: -abs(x["chg"]))

    # --- signals from the site
    foc = FL.get("focus") or {}
    fall = [x for x in foc.get("items") or [] if (x.get("score") or 0) >= (foc.get("min") or 75)]
    fitems = [{"t": x["t"], "side": x["side"], "strength": x.get("strength"), "score": x.get("score")} for x in fall[:6]]
    longs = sum(1 for x in fall if x.get("side") == 1)
    shorts = sum(1 for x in fall if x.get("side") == -1)

    # --- crypto
    fng = (CR.get("fng") or {})
    funding = next((d.get("funding") for d in CR.get("derivs") or [] if d.get("coin") == "BTC"), None)

    # ================================================================ the words
    day = sess.strftime("%A %-d %B")
    head = {
        ("up", "wild"): "Leaning higher, but expect big swings",
        ("up", "event"): "Leaning higher into a big data day",
        ("up", "calm"): "Leaning higher in a calm market",
        ("up", "normal"): "Leaning higher",
        ("down", "wild"): "Leaning lower, with big swings likely",
        ("down", "event"): "Leaning lower into a big data day",
        ("down", "calm"): "Leaning lower, but the market is calm",
        ("down", "normal"): "Leaning lower",
        ("flat", "wild"): "No clear direction, and a jumpy market",
        ("flat", "event"): "Waiting for the data",
        ("flat", "calm"): "Quiet: no clear direction",
        ("flat", "normal"): "No clear direction",
    }[(lean, character)]

    txt = []
    if fut is not None:
        when = "ahead of the open" if phase == "pre" else "today" if phase == "live" else "after the close"
        txt.append(f"US futures are {'up' if fut > 0.05 else 'down' if fut < -0.05 else 'flat'} {when} (S&P {pct(es, 2)}, Nasdaq {pct(nq, 2)})"
                   + (f", with Europe {'higher' if eu and sum(eu) > 0 else 'lower'}" if eu else "") + (f" and Asia {'mostly up' if asia and sum(asia) > 0 else 'mostly down'}." if asia else "."))
    pos = [d["name"] for d in drivers if d["effect"] > 0 and d["k"] != "fut"]
    neg = [d["name"] for d in drivers if d["effect"] < 0 and d["k"] != "fut"]
    if pos or neg:
        txt.append(" ".join(x for x in (("Helping: " + ", ".join(pos[:5]) + ".") if pos else "", ("Weighing: " + ", ".join(neg[:5]) + ".") if neg else "") if x))
    if character == "wild":
        bits = []
        if vix and vix >= 25: bits.append(f"the VIX is high at {vix:.1f}")
        if reg == "negative": bits.append("SPY options gamma is negative (dealers' hedging makes moves bigger)")
        if ts and ts >= 1: bits.append("the VIX curve is inverted")
        txt.append("Expect a jumpy session: " + ", ".join(bits) + ". Gaps and reversals are common in these conditions, so a small early move says little.")
    elif character == "calm":
        txt.append("Volatility is low and dealers are positioned to dampen moves: ranges tend to be narrow and breakouts often fail.")
    if eco_today:
        e0 = sorted(eco_today, key=lambda e: e.get("time") or "")
        txt.append("Today's data: " + "; ".join(f"{e.get('kind') or e.get('name')} at {e.get('time') or '?'} New York time" + (f" (previous {e['last']})" if e.get("last") is not None else "") for e in e0[:4])
                   + ". The first minutes after a release can whip both ways.")
    if eco_next:
        txt.append("Tomorrow brings " + ", ".join(e.get("kind") or e.get("name") for e in eco_next) + ", so traders may hold back today.")
    if ern_pre or ern_post:
        txt.append("Reporting: " + (("before the open " + ", ".join(e["ticker"] for e in ern_pre[:5])) if ern_pre else "")
                   + (("; " if ern_pre else "") + "after the close " + ", ".join(e["ticker"] for e in ern_post[:5]) if ern_post else "") + ".")
    if rng:
        txt.append(f"Options price a typical day of about ±{rng['pct']:.1f}% for the S&P 500 (about {rng['lo']:,.0f}–{rng['hi']:,.0f})"
                   + (f"; historically about {br['inside_1sd']:.0f}% of days stay inside that." if br and br.get("inside_1sd") is not None else "."))
    if br:
        txt.append(f"On past days with {br['label']} (since {br['since'][:4]}, {br['n']:,} days), the next session closed higher {br['up']:.0f}% of the time"
                   f" (all days: {br['all_up']:.0f}%), with a typical move of {br['median_abs']:.1f}% (all days: {br['all_abs']:.1f}%).")

    scen = []
    if levels and spy_now:
        above = [l for l in levels if l["v"] > spy_now]
        below = [l for l in levels if l["v"] <= spy_now]
        if below:
            b = below[0]
            scen.append({"side": -1, "text": f"If SPY breaks below {b['v']:.0f} ({b['name'].replace('SPY ', '')}), selling can speed up."})
        if above:
            a = above[-1]
            scen.append({"side": 1, "text": f"If SPY gets back above {a['v']:.0f} ({a['name'].replace('SPY ', '')}), moves tend to calm and dips get bought."
                         if "flip" in a["name"] else f"Above {a['v']:.0f} ({a['name'].replace('SPY ', '')}) is where rallies often stall."})
    if prev:
        scen.append({"side": 0, "text": f"Yesterday's S&P 500 range was {prev['low']:,.0f}–{prev['high']:,.0f}: a break of either end is the first real sign of direction."})

    tips = []
    if character in ("wild", "event"):
        tips.append("Size smaller than usual and use the “wrong if” level on every alert.")
    if big_today:
        tips.append("Avoid opening new trades just before the release time.")
    if lean != "flat" and fitems:
        same = longs if lean == "up" else shorts
        tips.append(f"{same} of the {len(fall)} Focus setups point the same way as the lean ({longs} long, {shorts} short).")
    if fng.get("value") is not None:
        tips.append(f"Crypto: fear & greed {fng['value']} ({fng.get('label', '')}), Bitcoin {pct(btc, 1)} in 24h" + (f", funding {funding:.3f}%" if funding is not None else "") + ".")

    out = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%MZ"), "session": sess_s, "day": day, "phase": phase,
        "lean": lean, "score": r(score, 1), "confidence": conf, "character": character, "headline": head,
        "text": txt, "drivers": drivers, "range": rng, "levels": levels, "prev": prev, "base": br,
        "scenarios": scen, "tips": tips, "spy_now": spy_now, "levels_from": GX.get("oi_date") or GX.get("updated"),
        "events": [{"time": e.get("time"), "kind": e.get("kind") or e.get("name"), "last": e.get("last"), "why": e.get("why")} for e in sorted(eco_today, key=lambda e: e.get("time") or "")],
        "events_next": [{"date": e.get("date"), "time": e.get("time"), "kind": e.get("kind") or e.get("name")} for e in eco_next],
        "earnings": {"pre": [{"t": e["ticker"], "name": e.get("name"), "eps": e.get("eps_est")} for e in ern_pre],
                     "post": [{"t": e["ticker"], "name": e.get("name"), "eps": e.get("eps_est")} for e in ern_post]},
        "premarket": pm[:10], "focus": fitems, "focus_sides": [longs, shorts],
        "crypto": {"fng": fng.get("value"), "fng_label": fng.get("label"), "btc": btc, "funding": funding},
        "inputs_at": {"markets": MB.get("generated_at"), "flow": FL.get("generated_at"), "gex": GX0.get("updated"),
                      "calendar": CAL.get("generated_at"), "crypto": CR.get("generated_at")},
    }
    return out


# ====================================================================== track record
def log_and_grade(out: dict, hist) -> dict:
    log = load(LOG, {}) or {}
    days = log.setdefault("days", {})
    s = out["session"]
    # keep the last read made before the open: that's the one that counts
    if out["phase"] == "pre" or s not in days:
        days[s] = {"lean": out["lean"], "score": out["score"], "character": out["character"], "made": out["generated_at"],
                   "pre": out["phase"] == "pre", "range_pct": (out.get("range") or {}).get("pct")}
    if hist:
        c = hist[0]["Close"]
        ch = (c / c.shift(1) - 1) * 100
        for d, e in days.items():
            if e.get("actual") is None:
                try:
                    v = ch.loc[str(d)]
                    v = float(v.iloc[0] if hasattr(v, "iloc") else v)
                    e["actual"] = r(v, 2)
                except Exception:
                    pass
    graded = [e for e in days.values() if e.get("actual") is not None and e.get("pre")]
    called = [e for e in graded if e["lean"] != "flat"]
    hit = sum(1 for e in called if (e["lean"] == "up") == (e["actual"] > 0))
    inside = [e for e in graded if e.get("range_pct")]
    log["summary"] = {"graded": len(graded), "called": len(called), "hit": hit, "hit_pct": r(hit / len(called) * 100, 0) if called else None,
                      "inside": sum(1 for e in inside if abs(e["actual"]) <= e["range_pct"]), "inside_n": len(inside)}
    LOG.write_text(json.dumps(log, separators=(",", ":")))
    recent = sorted(((d, e) for d, e in days.items() if e.get("actual") is not None and e.get("pre")), reverse=True)[:10]
    return {**log["summary"], "recent": [{"d": d, "lean": e["lean"], "actual": e["actual"]} for d, e in recent]}


def main():
    now = datetime.now(timezone.utc)
    hist = fetch_history()
    quotes = fetch_last([s for s, _ in MACRO])
    cal = load(DATA / "calendar.json", {}) or {}
    sess = session_date(now.astimezone(ET)).isoformat()
    reporters = [e["ticker"] for e in sorted((e for e in cal.get("earnings") or [] if e.get("date") == sess and e.get("time") == "bmo" and e.get("sp500")),
                                             key=lambda e: -(e.get("mcap") or 0))][:8]
    prem = fetch_last(list(dict.fromkeys(MEGA + reporters)), prepost=True)
    out = build(now, hist, quotes, prem)
    out["record"] = log_and_grade(out, hist)
    OUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    print("wrote", OUT, OUT.stat().st_size, "bytes ·", out["headline"], "· score", out["score"])


if __name__ == "__main__":
    main()
