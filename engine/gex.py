"""Daily gamma exposure (GEX) levels for SPY, QQQ and IWM, and a saved snapshot for later research.

What it does, every weekday before the US open (.github/workflows/gex.yml):
  1. Open interest per contract from Alpaca's options contracts list (the exchanges' count from the last
     session), and implied volatility + last session's volume from Alpaca's option snapshots.
  2. Gamma per contract from Black-Scholes with its own IV, at today's price.
  3. GEX per strike in dollars per 1% move = gamma x open interest x 100 x price^2 x 1%.
     The usual convention: dealers are long the calls people sell them and short the puts people buy,
     so calls count +, puts count -. It's an estimate: nobody outside the dealers knows their real book.
  4. Levels:
       - gamma flip: the price where the total GEX crosses zero (the whole profile is recomputed at
         prices 10% below to 10% above). Above it = positive gamma (dealers damp moves, ranges),
         below it = negative gamma (dealers chase moves, bigger swings).
       - call wall: the strike with the most call GEX, usually a ceiling.
       - put wall: the strike with the most put GEX, usually a floor.
       - magnets: the 5 strikes with the largest net GEX within 5% of the price.
  5. Put/call: open interest ratio, and last session's volume and premium (volume x average price x 100).
     Alpaca's free feed has no buyer/seller side, so this is NOT "net premium" flow like paid tools show.

Writes:
  docs/data/gex/latest.json                  levels for the site (Trendlines page)
  docs/data/gex/history/YYYY-MM-DD.json      per-strike open interest, GEX and volume for every underlying
  docs/data/gex/history.csv                  one row per day and underlying: the levels, for backtests

Free history of open interest per strike doesn't exist, so these daily snapshots ARE the backtest data.

Fresher levels (Nov 2026):
  - Source: Cboe's free delayed-quotes file first (one request per underlying: every contract with
    open interest from the LAST session, implied volatility and today's volume, ~15 minutes delayed).
    Alpaca's contracts list lagged 2-3 sessions on open interest, so it's now the fallback.
  - --intraday: recomputed every 30 minutes while the US market is open (and once in the European
    morning) at the live price, with live IV. Only latest.json changes; the daily snapshot/CSV stay the
    pre-open ones, so the backtest data keeps one consistent time of day. The pre-open levels are kept
    in latest.json ("open") so the site can show how they moved.
  - Volume walls: the strikes where today's option volume carries the most gamma. Open interest only
    updates overnight; today's volume shows where new positions are going during the session
    (an estimate: volume can be opening or closing trades).

    python engine/gex.py              # pre-open run: levels + daily snapshot
    python engine/gex.py --intraday   # levels only, at the live price
    python engine/gex.py --demo       # synthetic chain, no keys, writes to /tmp/gex-demo
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "gex"
UNDERLYINGS = ["SPY", "QQQ", "IWM"]
DAYS_OUT = 45            # expiries up to this many days ahead
BAND = 0.12              # strikes within +-12% of the price
R = 0.04                 # risk-free rate for Black-Scholes
NY = ZoneInfo("America/New_York")
DATA = "https://data.alpaca.markets"
TRADING = ["https://paper-api.alpaca.markets", "https://api.alpaca.markets"]   # contracts list: either works


# ------------------------------------------------------------------ Black-Scholes gamma
def bs_gamma(S: float, K: float, T: float, iv: float) -> float:
    if T <= 0 or iv <= 0 or S <= 0:
        return 0.0
    sq = iv * math.sqrt(T)
    d1 = (math.log(S / K) + (R + 0.5 * iv * iv) * T) / sq
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (S * sq)


def years_to_expiry(exp: date, now: datetime) -> float:
    close = datetime(exp.year, exp.month, exp.day, 16, 0, tzinfo=NY)
    return max((close - now).total_seconds(), 3600) / (365.25 * 86400)    # at least an hour


def gex_dollars(gamma: float, oi: float, S: float) -> float:
    """Dollars of underlying dealers must trade for a 1% move."""
    return gamma * oi * 100 * S * S * 0.01


# ------------------------------------------------------------------ Alpaca
def _get(url: str, params: dict) -> dict:
    key, sec = os.environ["ALPACA_KEY_ID"], os.environ["ALPACA_SECRET_KEY"]
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                 headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
    return json.loads(urllib.request.urlopen(req, timeout=40).read())


def _pages(url: str, params: dict, field: str, max_pages: int = 60):
    params = dict(params)
    for _ in range(max_pages):
        js = _get(url, params)
        yield js.get(field) or ({} if field == "snapshots" else [])
        tok = js.get("next_page_token")
        if not tok:
            return
        params["page_token"] = tok


def spot_prices(symbols: list[str]) -> dict[str, float]:
    out = {}
    for feed in ("sip", "iex"):
        try:
            js = _get(f"{DATA}/v2/stocks/snapshots", {"symbols": ",".join(symbols), "feed": feed})
        except urllib.error.HTTPError as e:
            print(f"  ! stock snapshots ({feed}): HTTP {e.code}")
            continue
        for s, snap in js.items():
            p = (snap.get("latestTrade") or {}).get("p") or (snap.get("dailyBar") or {}).get("c") \
                or (snap.get("prevDailyBar") or {}).get("c")
            if p:
                out[s] = float(p)
        if len(out) == len(symbols):
            break
    return out


def open_interest(sym: str, lo: float, hi: float, today: date) -> tuple[dict, str | None]:
    """{occ symbol: (expiry, "C"/"P", strike, open interest)}, and the date of that open interest."""
    params = {"underlying_symbols": sym, "status": "active", "limit": "10000",
              "expiration_date_gte": today.isoformat(),
              "expiration_date_lte": (today + timedelta(days=DAYS_OUT)).isoformat(),
              "strike_price_gte": f"{lo:.0f}", "strike_price_lte": f"{hi:.0f}"}
    last = None
    for base in TRADING:
        try:
            out, oi_date = {}, None
            for page in _pages(f"{base}/v2/options/contracts", params, "option_contracts"):
                for c in page:
                    oi = float(c.get("open_interest") or 0)
                    if oi <= 0:
                        continue
                    out[c["symbol"]] = (date.fromisoformat(c["expiration_date"]), "C" if c["type"] == "call" else "P",
                                        float(c["strike_price"]), oi)
                    oi_date = max(oi_date or "", c.get("open_interest_date") or "") or None
            return out, oi_date
        except urllib.error.HTTPError as e:          # keys belong to the other host: try it
            last = e
    raise RuntimeError(f"options contracts for {sym}: {last}")


def snapshots(sym: str, lo: float, hi: float, today: date) -> dict:
    """{occ symbol: {"iv", "vol", "vwap"}} from the free indicative feed."""
    params = {"feed": "indicative", "limit": "1000",
              "expiration_date_gte": today.isoformat(),
              "expiration_date_lte": (today + timedelta(days=DAYS_OUT)).isoformat(),
              "strike_price_gte": f"{lo:.0f}", "strike_price_lte": f"{hi:.0f}"}
    out = {}
    for page in _pages(f"{DATA}/v1beta1/options/snapshots/{sym}", params, "snapshots"):
        for occ, s in page.items():
            bar = s.get("dailyBar") or s.get("prevDailyBar") or {}
            q = s.get("latestQuote") or {}
            mid = (float(q.get("bp") or 0) + float(q.get("ap") or 0)) / 2
            out[occ] = {"iv": s.get("impliedVolatility"), "vol": float(bar.get("v") or 0),
                        "px": float(bar.get("vw") or bar.get("c") or mid or 0)}
    return out


# ------------------------------------------------------------------ Cboe (free, no key)
CBOE = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"
OCC = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


def prev_session(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def cboe_chain(sym: str, today: date) -> tuple[float | None, list[dict], str | None, str | None]:
    """(delayed price, contracts within BAND and DAYS_OUT, date of the open interest, Cboe timestamp)."""
    req = urllib.request.Request(CBOE.format(sym), headers={
        "User-Agent": "Mozilla/5.0 (signal-desk gamma levels; github.com/thejackwar/signal-desk)", "Accept": "application/json"})
    js = json.loads(urllib.request.urlopen(req, timeout=40).read())
    d = js.get("data") or {}
    S = float(d.get("current_price") or d.get("close") or 0) or None
    ts = js.get("timestamp") or d.get("last_trade_time")
    try:
        stamp = datetime.fromisoformat(str(ts).replace("Z", "")).date()
    except Exception:
        stamp = today
    # open interest is the exchanges' count after the last finished session, published overnight
    oi_date = prev_session(stamp + timedelta(days=1)) if stamp.weekday() >= 5 else prev_session(stamp)
    out = []
    if not S:
        return None, out, None, ts
    lo, hi, last = S * (1 - BAND), S * (1 + BAND), today + timedelta(days=DAYS_OUT)
    for o in d.get("options") or []:
        m = OCC.match(str(o.get("option") or "").strip())
        if not m:
            continue
        exp = datetime.strptime(m.group(2), "%y%m%d").date()
        K = int(m.group(4)) / 1000
        oi = float(o.get("open_interest") or 0)
        if exp < today or exp > last or not lo <= K <= hi:
            continue
        iv = o.get("iv")
        try:
            iv = float(iv)
            iv = iv / 100 if iv > 5 else iv
        except (TypeError, ValueError):
            iv = None
        vol = float(o.get("volume") or 0)
        bid, ask = float(o.get("bid") or 0), float(o.get("ask") or 0)
        px = float(o.get("last_trade_price") or 0) or (bid + ask) / 2
        if oi <= 0 and vol <= 0:
            continue
        out.append({"exp": exp, "type": m.group(3), "strike": K, "oi": oi, "iv": iv, "vol": vol, "px": px})
    return S, out, oi_date.isoformat(), ts


# ------------------------------------------------------------------ the maths
def analyse(sym: str, S: float, contracts: list[dict], now: datetime) -> tuple[dict, dict]:
    """contracts: [{exp, type, strike, oi, iv, vol, px}] -> (levels for the site, per-strike detail)."""
    contracts = [c for c in contracts if datetime(c["exp"].year, c["exp"].month, c["exp"].day, 16, tzinfo=NY) > now]
    # an option with no IV (no quote) borrows the median IV of its expiry
    by_exp: dict[date, list[float]] = {}
    for c in contracts:
        if c["iv"] and 0.01 < c["iv"] < 5:
            by_exp.setdefault(c["exp"], []).append(c["iv"])
    med = {e: sorted(v)[len(v) // 2] for e, v in by_exp.items()}
    all_iv = sorted(x for v in by_exp.values() for x in v)
    fallback = all_iv[len(all_iv) // 2] if all_iv else 0.18
    for c in contracts:
        if not (c["iv"] and 0.01 < c["iv"] < 5):
            c["iv"] = med.get(c["exp"], fallback)
        c["T"] = years_to_expiry(c["exp"], now)

    strikes: dict[float, dict] = {}
    exp_tot: dict[str, float] = {}
    first_exp = min((c["exp"] for c in contracts), default=None)
    zero_dte = 0.0
    for c in contracts:
        g = gex_dollars(bs_gamma(S, c["strike"], c["T"], c["iv"]), c["oi"], S)
        sign = 1 if c["type"] == "C" else -1
        k = strikes.setdefault(c["strike"], {"c_oi": 0, "p_oi": 0, "c_gex": 0.0, "p_gex": 0.0, "c_vol": 0, "p_vol": 0, "c_vg": 0.0, "p_vg": 0.0})
        side = "c" if c["type"] == "C" else "p"
        k[f"{side}_oi"] += c["oi"]
        k[f"{side}_gex"] += sign * g
        k[f"{side}_vol"] += c["vol"]
        k[f"{side}_vg"] += sign * gex_dollars(bs_gamma(S, c["strike"], c["T"], c["iv"]), c["vol"], S) if c["vol"] else 0.0
        exp_tot[c["exp"].isoformat()] = exp_tot.get(c["exp"].isoformat(), 0) + sign * g
        if c["exp"] == first_exp:
            zero_dte += sign * g

    # gamma profile: total GEX if the price were somewhere else today
    grid = [S * (0.90 + 0.0025 * i) for i in range(81)]
    profile = []
    for x in grid:
        tot = 0.0
        for c in contracts:
            tot += (1 if c["type"] == "C" else -1) * gex_dollars(bs_gamma(x, c["strike"], c["T"], c["iv"]), c["oi"], x)
        profile.append((x, tot))
    flips = []
    for (x0, y0), (x1, y1) in zip(profile, profile[1:]):
        if y0 == 0 or (y0 < 0) != (y1 < 0):
            flips.append(x0 + (x1 - x0) * (0 - y0) / (y1 - y0) if y1 != y0 else x0)
    flip = min(flips, key=lambda f: abs(f - S)) if flips else None

    total = sum(k["c_gex"] + k["p_gex"] for k in strikes.values())
    near = {K: v for K, v in strikes.items() if abs(K / S - 1) <= 0.05}
    strikes_oi = {K: v for K, v in strikes.items() if v["c_oi"] or v["p_oi"]}
    call_wall = max(strikes_oi, key=lambda K: strikes_oi[K]["c_gex"]) if strikes_oi else None
    put_wall = min(strikes_oi, key=lambda K: strikes_oi[K]["p_gex"]) if strikes_oi else None
    # today's volume: where new positions are going during the session (OI only updates overnight)
    has_vol = sum(v["c_vol"] + v["p_vol"] for v in strikes.values()) > 0
    vol_call_wall = max(strikes, key=lambda K: strikes[K]["c_vg"]) if has_vol else None
    vol_put_wall = min(strikes, key=lambda K: strikes[K]["p_vg"]) if has_vol else None
    magnets = sorted(near, key=lambda K: -abs(near[K]["c_gex"] + near[K]["p_gex"]))[:5]
    c_oi = sum(k["c_oi"] for k in strikes.values()); p_oi = sum(k["p_oi"] for k in strikes.values())
    c_vol = sum(c["vol"] for c in contracts if c["type"] == "C"); p_vol = sum(c["vol"] for c in contracts if c["type"] == "P")
    c_prem = sum(c["vol"] * c["px"] * 100 for c in contracts if c["type"] == "C")
    p_prem = sum(c["vol"] * c["px"] * 100 for c in contracts if c["type"] == "P")

    r2 = lambda v: round(v, 2)
    levels = {
        "spot": r2(S),
        "flip": r2(flip) if flip else None,
        "regime": None if flip is None else ("positive" if S >= flip else "negative"),
        "total_gex": round(total), "next_expiry_gex": round(zero_dte),
        "next_expiry": first_exp.isoformat() if first_exp else None,
        "call_wall": call_wall, "put_wall": put_wall,
        "vol_call_wall": vol_call_wall, "vol_put_wall": vol_put_wall,
        "magnets": [{"strike": K, "gex": round(near[K]["c_gex"] + near[K]["p_gex"])} for K in sorted(magnets)],
        "put_call_oi": r2(p_oi / c_oi) if c_oi else None,
        "put_call_volume": r2(p_vol / c_vol) if c_vol else None,
        "call_premium": round(c_prem), "put_premium": round(p_prem),
        "contracts": len(contracts),
        # for the bar chart on the site: strikes within 5%
        "strikes": [[K, round(near[K]["c_gex"]), round(near[K]["p_gex"])] for K in sorted(near)],
        "profile": [[r2(x), round(y)] for x, y in profile[::2]],
    }
    detail = {
        "spot": r2(S), "flip": levels["flip"], "call_wall": call_wall, "put_wall": put_wall,
        "by_expiry": {e: round(v) for e, v in sorted(exp_tot.items())},
        "columns": ["strike", "call_oi", "put_oi", "call_gex", "put_gex", "call_vol", "put_vol"],
        "strikes": [[K, int(v["c_oi"]), int(v["p_oi"]), round(v["c_gex"]), round(v["p_gex"]), int(v["c_vol"]), int(v["p_vol"])]
                    for K, v in sorted(strikes.items())],
    }
    return levels, detail


# ------------------------------------------------------------------ demo chain (tests, no keys)
def demo_chain(sym: str, S: float, today: date, seed: int) -> list[dict]:
    import random
    rnd = random.Random(seed)
    out = []
    step = 1 if S < 300 else 5 if S > 1500 else 1
    exps = [today + timedelta(days=d) for d in range(0, DAYS_OUT + 1) if (today + timedelta(days=d)).weekday() < 5][:20]
    for e in exps:
        for K in range(int(S * (1 - BAND)), int(S * (1 + BAND)), step):
            m = K / S - 1
            for t in "CP":
                base = 4000 * math.exp(-(m * 12) ** 2)
                if K % 10 == 0:
                    base *= 3
                bias = (1.6 if m > 0 else 0.6) if t == "C" else (1.8 if m < 0 else 0.5)
                oi = int(base * bias * rnd.uniform(0.5, 1.5))
                if oi > 0:
                    out.append({"exp": e, "type": t, "strike": float(K), "oi": oi, "iv": 0.16 - m * 0.5,
                                "vol": oi * rnd.uniform(0.05, 0.4), "px": max(0.05, abs(m) * S * 0.2)})
    return out


# ------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--intraday", action="store_true", help="levels at the live price only; no daily snapshot")
    a = ap.parse_args()
    out = Path("/tmp/gex-demo") if a.demo else OUT
    now = datetime.now(NY)
    today = now.date()

    keys = bool(os.environ.get("ALPACA_KEY_ID"))
    if a.demo:
        spots, oi_date = {"SPY": 681.4, "QQQ": 612.3, "IWM": 241.9}, (today - timedelta(days=1)).isoformat()
    else:
        spots, oi_date = {}, None
        if keys:
            try:
                spots = spot_prices(UNDERLYINGS)          # live price (Cboe's is 15 minutes delayed)
            except Exception as ex:
                print(f"  ! Alpaca prices: {ex}")

    site, hist, sources = {}, {}, {}
    for i, sym in enumerate(UNDERLYINGS):
        S = spots.get(sym)
        contracts = None
        if not a.demo:
            try:                                      # 1) Cboe: open interest from the last session
                cS, cc, cd, cts = cboe_chain(sym, today)
                if cc and sum(c["oi"] for c in cc) > 0:
                    S = S or cS
                    contracts, sources[sym] = cc, "cboe"
                    oi_date = max(oi_date or "", cd or "") or None
                    print(f"  {sym} {S:.2f}: Cboe, {len(cc)} contracts, open interest of {cd} (Cboe time {cts})")
                else:
                    print(f"  ! {sym}: Cboe chain empty")
            except Exception as ex:
                print(f"  ! {sym}: Cboe failed ({type(ex).__name__}: {ex}), trying Alpaca")
        if not S:
            print(f"  ! {sym}: no price, skipped")
            continue
        lo, hi = S * (1 - BAND), S * (1 + BAND)
        try:
            if a.demo:
                contracts = demo_chain(sym, S, today, i)
            elif contracts is None:                   # 2) Alpaca: the old way (open interest lags 2-3 sessions)
                if not keys:
                    raise RuntimeError("no Alpaca keys for the fallback")
                sources[sym] = "alpaca"
                oi, d = open_interest(sym, lo, hi, today)
                oi_date = max(oi_date or "", d or "") or None
                snaps = snapshots(sym, lo, hi, today)
                contracts = [{"exp": e, "type": t, "strike": K, "oi": n,
                              "iv": (snaps.get(occ) or {}).get("iv"),
                              "vol": (snaps.get(occ) or {}).get("vol", 0), "px": (snaps.get(occ) or {}).get("px", 0)}
                             for occ, (e, t, K, n) in oi.items()]
                print(f"  {sym} {S:.2f}: {len(oi)} contracts with open interest, {len(snaps)} snapshots")
        except Exception as ex:
            print(f"  ! {sym}: {ex}")
            continue
        if not contracts:
            print(f"  ! {sym}: empty chain")
            continue
        levels, detail = analyse(sym, S, contracts, now)
        site[sym], hist[sym] = levels, detail
        print(f"  {sym}: flip {levels['flip']} ({levels['regime']}), call wall {levels['call_wall']}, "
              f"put wall {levels['put_wall']}, total {levels['total_gex'] / 1e9:+.2f}bn per 1%")

    if not site:
        sys.exit("no underlying worked")
    out.mkdir(parents=True, exist_ok=True)
    (out / "history").mkdir(exist_ok=True)
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    # keep the pre-open levels next to the live ones, so the site can show how they moved today
    try:
        prev = json.loads((out / "latest.json").read_text())
    except Exception:
        prev = {}
    for sym, L in site.items():
        P = (prev.get("underlyings") or {}).get(sym) or {}
        if a.intraday and (P.get("open") or {}).get("date") == today.isoformat():
            L["open"] = P["open"]
        elif not a.intraday:
            L["open"] = {"date": today.isoformat(), **{k: L.get(k) for k in ("spot", "flip", "call_wall", "put_wall", "regime")}}
        L["source"] = sources.get(sym, "demo" if a.demo else None)
    (out / "latest.json").write_text(json.dumps(
        {"updated": stamp, "oi_date": oi_date, "intraday": a.intraday,
         "source": "Cboe delayed quotes" if "cboe" in sources.values() else "Alpaca", "underlyings": site}, separators=(",", ":")))
    if a.intraday:
        print(f"  wrote {out / 'latest.json'} (intraday: no daily snapshot)")
        return
    (out / "history" / f"{today.isoformat()}.json").write_text(json.dumps(
        {"date": today.isoformat(), "updated": stamp, "oi_date": oi_date, "underlyings": hist}, separators=(",", ":")))

    # one row per day and underlying; a re-run on the same day replaces that day's rows
    path = out / "history.csv"
    cols = ["date", "symbol", "oi_date", "spot", "flip", "regime", "call_wall", "put_wall", "total_gex",
            "next_expiry_gex", "put_call_oi", "put_call_volume", "call_premium", "put_premium", "magnets"]
    rows = []
    if path.exists():
        with path.open() as f:
            rows = [r for r in csv.DictReader(f) if r["date"] != today.isoformat()]
    for sym, L in site.items():
        rows.append({"date": today.isoformat(), "symbol": sym, "oi_date": oi_date or "",
                     **{c: L.get(c) for c in cols[3:-1]},
                     "magnets": " ".join(str(m["strike"]) for m in L["magnets"])})
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["date"], r["symbol"])))
    print(f"  wrote {out}")


if __name__ == "__main__":
    main()
