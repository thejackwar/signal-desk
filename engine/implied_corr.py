"""Implied correlation surface for the S&P 500: how much the options market expects its biggest stocks
to move together, by strike (delta) and by maturity. Page: docs/correlation.html.

The idea (the same one behind Cboe's COR1M / COR3M indices):
  index variance = sum_i w_i^2 s_i^2 + sum_{i!=j} w_i w_j s_i s_j rho
Put in the index's implied vol (SPY options) and each stock's implied vol (its own options), and solve
for the one average correlation rho that makes it add up:
  rho = (s_I^2 - sum w_i^2 s_i^2) / ((sum w_i s_i)^2 - sum w_i^2 s_i^2)

What it does, every weekday near the US close (.github/workflows/correlation.yml):
  1. Basket: the 50 biggest S&P 100 stocks by market cap (Yahoo), weights rescaled to add to 100%
     (Cboe does the same with the 50 biggest S&P 500 stocks). GOOG is left out: GOOGL stands for Alphabet.
  2. Options: implied vols from Alpaca's free option snapshots (indicative feed) for SPY and every stock,
     on the monthly expiries around 1, 2, 3 and 6 months. Out-of-the-money options only.
  3. Smile by delta: each option's Black-Scholes call delta from its own IV, then the IV at
     10/25-delta puts, at-the-money (50 delta), and 25/10-delta calls. The index and the stocks are
     compared at the SAME delta (the usual way of building a correlation skew).
  4. Constant maturities: 30, 60, 91 and 182 calendar days, interpolating total variance (IV^2 x T)
     between the two expiries either side.
  5. Correlation in each cell (5 deltas x 4 maturities). A stock with no usable quote in a cell is
     dropped from that cell and the weights rescaled; the cell is left empty if less than 80% of the
     basket weight is left.
  6. Realized correlation with the same formula and weights, from daily closes over the last 1 and 3
     months, and its rolling 1-month history over the past year (the comparison for the implied one).

Writes:
  docs/data/corr/latest.json                 everything the page shows
  docs/data/corr/history.csv                 one row a day (free history of implied vols doesn't
                                             exist, so the page's implied history is what we collect)
  docs/data/corr/history/YYYY-MM-DD.json     the day's full surface and every stock's vols

    python engine/implied_corr.py            # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/implied_corr.py --demo     # synthetic vols and prices, no keys or network, writes to /tmp/corr-demo
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "corr"
INDEX = "SPY"
N_STOCKS = 50
SKIP = {"GOOG"}                          # second Alphabet share class: GOOGL already counts the company
TENORS = [30, 60, 91, 182]               # calendar days
# call deltas, from the downside to the upside (a 25-delta put has a call delta of 0.75)
DELTAS = [0.90, 0.75, 0.50, 0.25, 0.10]
DELTA_LABELS = ["10Δ put", "25Δ put", "At the money", "25Δ call", "10Δ call"]
MIN_COVER = 0.80                         # share of basket weight a cell needs
R = 0.04
NY = ZoneInfo("America/New_York")
DATA = "https://data.alpaca.markets"


# ------------------------------------------------------------------ maths
def ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def call_delta(S: float, K: float, T: float, iv: float) -> float:
    d1 = (math.log(S / K) + (R + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    return ncdf(d1)


def implied_corr(index_vol: float, vols: list[float], weights: list[float]) -> float | None:
    """Average correlation that makes the basket's variance equal the index's. Weights are rescaled to 1."""
    tw = sum(weights)
    if tw <= 0 or not vols:
        return None
    w = [x / tw for x in weights]
    a = sum(wi * si for wi, si in zip(w, vols)) ** 2           # (sum w s)^2: the variance if rho = 1
    b = sum((wi * si) ** 2 for wi, si in zip(w, vols))         # sum w^2 s^2: the variance if rho = 0
    if a - b <= 0:
        return None
    return (index_vol ** 2 - b) / (a - b)


def smile_at(points: list[tuple[float, float]], target: float) -> float | None:
    """points: [(call delta, iv)] -> iv at target delta, linear in delta. A little flat extrapolation
    (0.04 of delta) is allowed at the wings, no more."""
    pts = sorted(points)
    if len(pts) < 2:
        return None
    if target <= pts[0][0]:
        return pts[0][1] if pts[0][0] - target <= 0.04 else None
    if target >= pts[-1][0]:
        return pts[-1][1] if target - pts[-1][0] <= 0.04 else None
    for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
        if d0 <= target <= d1:
            return v0 if d1 == d0 else v0 + (v1 - v0) * (target - d0) / (d1 - d0)
    return None


def clean_smile(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop quotes far off their neighbours (stale or crossed indicative quotes). Median of 5 filter."""
    pts = sorted(points)
    if len(pts) < 5:
        return pts
    out = []
    for i, (d, v) in enumerate(pts):
        nb = sorted(x[1] for x in pts[max(0, i - 2): i + 3])
        med = nb[len(nb) // 2]
        if abs(v / med - 1) <= 0.35:
            out.append((d, v))
    return out


def years_to(exp: date, now: datetime) -> float:
    close = datetime(exp.year, exp.month, exp.day, 16, 0, tzinfo=NY)
    return max((close - now).total_seconds(), 3600) / (365.25 * 86400)


def tenor_vol(by_exp: dict[date, float], days: int, today: date) -> float | None:
    """IVs on actual expiries -> IV at a constant maturity, linear in total variance."""
    pts = sorted(((e - today).days, v) for e, v in by_exp.items() if v and (e - today).days > 0)
    if not pts:
        return None
    lo = [p for p in pts if p[0] <= days]
    hi = [p for p in pts if p[0] >= days]
    if lo and hi:
        (t0, v0), (t1, v1) = lo[-1], hi[0]
        if t1 == t0:
            return v0
        w = (v0 * v0 * t0 + (v1 * v1 * t1 - v0 * v0 * t0) * (days - t0) / (t1 - t0)) / days
        return math.sqrt(w) if w > 0 else None
    near = (lo or hi)[-1 if lo else 0]
    return near[1] if abs(near[0] - days) <= 12 else None     # small flat extrapolation only


def third_friday(y: int, m: int) -> date:
    d = date(y, m, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def target_expiries(today: date) -> list[date]:
    """The monthly expiries either side of each maturity (every optionable stock has these)."""
    months = []
    y, m = today.year, today.month
    for _ in range(9):
        f = third_friday(y, m)
        if (f - today).days >= 5:
            months.append(f)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    need = set()
    for t in TENORS:
        lo = [e for e in months if (e - today).days <= t]
        hi = [e for e in months if (e - today).days >= t]
        if lo:
            need.add(lo[-1])
        if hi:
            need.add(hi[0])
    return sorted(need)


# ------------------------------------------------------------------ Alpaca
def _get(url: str, params: dict) -> dict:
    key, sec = os.environ["ALPACA_KEY_ID"], os.environ["ALPACA_SECRET_KEY"]
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                 headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
    for attempt in range(5):
        try:
            time.sleep(0.32)                                    # free plan: 200 requests a minute
            return json.loads(urllib.request.urlopen(req, timeout=40).read())
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(3 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Alpaca kept failing: {url}")


def spot_prices(symbols: list[str]) -> dict[str, float]:
    out = {}
    for i in range(0, len(symbols), 50):
        chunk = symbols[i:i + 50]
        for feed in ("sip", "iex"):
            try:
                js = _get(f"{DATA}/v2/stocks/snapshots", {"symbols": ",".join(chunk), "feed": feed})
            except urllib.error.HTTPError as e:
                print(f"  ! stock snapshots ({feed}): HTTP {e.code}")
                continue
            for s, snap in js.items():
                p = (snap.get("latestTrade") or {}).get("p") or (snap.get("dailyBar") or {}).get("c") \
                    or (snap.get("prevDailyBar") or {}).get("c")
                if p:
                    out[s] = float(p)
            if all(s in out for s in chunk):
                break
    return out


def option_quotes(sym: str, S: float, exp: date) -> list[dict]:
    """[{type, strike, iv}] on one expiry (or the nearest within 3 days: a holiday moves the expiry)."""
    band = 0.40 if (exp - date.today()).days < 120 else 0.55
    base = {"feed": "indicative", "limit": "1000",
            "strike_price_gte": f"{S * (1 - band):.2f}", "strike_price_lte": f"{S * (1 + band):.2f}"}
    for params in ({"expiration_date": exp.isoformat()},
                   {"expiration_date_gte": (exp - timedelta(days=3)).isoformat(),
                    "expiration_date_lte": (exp + timedelta(days=3)).isoformat()}):
        out, p = [], {**base, **params}
        for _ in range(20):
            js = _get(f"{DATA}/v1beta1/options/snapshots/{sym}", p)
            for occ, s in (js.get("snapshots") or {}).items():
                iv = s.get("impliedVolatility")
                q = s.get("latestQuote") or {}
                if not iv or not (0.02 < iv < 4) or float(q.get("ap") or 0) <= 0:
                    continue
                e, t, k = parse_occ(occ)
                out.append({"exp": e, "type": t, "strike": k, "iv": float(iv)})
            tok = js.get("next_page_token")
            if not tok:
                break
            p["page_token"] = tok
        if out:
            # several expiries in the window: keep the one nearest the monthly
            best = min({o["exp"] for o in out}, key=lambda e: abs((e - exp).days))
            return [o for o in out if o["exp"] == best]
    return []


def parse_occ(occ: str) -> tuple[date, str, float]:
    tail = occ[-15:]                                           # YYMMDD + C/P + 8-digit strike
    return (date(2000 + int(tail[:2]), int(tail[2:4]), int(tail[4:6])), tail[6], int(tail[7:]) / 1000)


# ------------------------------------------------------------------ Yahoo (caps and closes)
def market_caps(tickers: list[str]) -> dict[str, float]:
    import yfinance as yf
    caps = {}
    for t in tickers:
        try:
            c = yf.Ticker(t).fast_info.get("market_cap") or yf.Ticker(t).fast_info.get("marketCap")
            if c:
                caps[t] = float(c)
        except Exception as ex:                                # noqa: BLE001
            print(f"  ! market cap {t}: {ex}")
    return caps


def closes(tickers: list[str]):
    import yfinance as yf
    df = yf.download(tickers, period="15mo", interval="1d", auto_adjust=True, progress=False, threads=True)["Close"]
    return df.dropna(how="all")


# ------------------------------------------------------------------ the surface
def smile_by_delta(quotes: list[dict], S: float, now: datetime) -> dict[date, list[float | None]]:
    """{expiry: [iv at each target delta]} from out-of-the-money options."""
    by_exp: dict[date, list[tuple[float, float]]] = {}
    for q in quotes:
        otm = (q["type"] == "P" and q["strike"] < S) or (q["type"] == "C" and q["strike"] >= S)
        if not otm:
            continue
        T = years_to(q["exp"], now)
        by_exp.setdefault(q["exp"], []).append((call_delta(S, q["strike"], T, q["iv"]), q["iv"]))
    return {e: [smile_at(clean_smile(p), d) for d in DELTAS] for e, p in by_exp.items()}


def surface_for(smiles: dict[date, list], today: date) -> list[list[float | None]]:
    """[tenor][delta] -> iv at constant maturity."""
    return [[tenor_vol({e: s[j] for e, s in smiles.items() if s[j]}, t, today) for j in range(len(DELTAS))]
            for t in TENORS]


def correlation_surface(index_surf, stock_surf: dict[str, list], weights: dict[str, float]):
    rho, cover, basket = [], [], []
    tw = sum(weights.values())
    for ti in range(len(TENORS)):
        r_row, c_row, b_row = [], [], []
        for di in range(len(DELTAS)):
            sI = index_surf[ti][di]
            names = [t for t in weights if stock_surf.get(t) and stock_surf[t][ti][di]]
            cov = sum(weights[t] for t in names) / tw if tw else 0
            if sI is None or cov < MIN_COVER:
                r_row.append(None); c_row.append(round(cov, 3)); b_row.append(None)
                continue
            vols = [stock_surf[t][ti][di] for t in names]
            ws = [weights[t] for t in names]
            r = implied_corr(sI, vols, ws)
            r_row.append(None if r is None else round(r, 4))
            c_row.append(round(cov, 3))
            b_row.append(round(sum(w * v for w, v in zip(ws, vols)) / sum(ws), 4))
        rho.append(r_row); cover.append(c_row); basket.append(b_row)
    return rho, cover, basket


def realized(px, weights: dict[str, float], days: int, end: int | None = None) -> float | None:
    """Realized correlation with the same formula: index = SPY's realized vol."""
    import numpy as np
    rets = np.log(px / px.shift(1))
    if end is not None:
        rets = rets.iloc[: end + 1]
    win = rets.iloc[-days:]
    if len(win) < days * 0.8 or INDEX not in win:
        return None
    sI = win[INDEX].std() * math.sqrt(252)
    names = [t for t in weights if t in win and win[t].notna().sum() >= days * 0.8]
    if sum(weights[t] for t in names) / sum(weights.values()) < MIN_COVER:
        return None
    vols = [win[t].std() * math.sqrt(252) for t in names]
    r = implied_corr(sI, vols, [weights[t] for t in names])
    return None if r is None or not math.isfinite(r) else round(r, 4)


def realized_series(px, weights, days: int = 21, keep: int = 260) -> list[list]:
    out = []
    n = len(px)
    for end in range(max(days + 1, n - keep), n):
        r = realized(px, weights, days, end)
        if r is not None:
            out.append([px.index[end].strftime("%Y-%m-%d"), r])
    return out


def cboe_history() -> dict[str, list]:
    """Cboe's own 1- and 3-month implied correlation indices for comparison. Optional: skipped if
    Cboe's file isn't reachable."""
    out = {}
    for name in ("COR1M", "COR3M"):
        url = f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (signal-desk)"})
            rows = list(csv.reader(urllib.request.urlopen(req, timeout=30).read().decode().splitlines()))
            pts = []
            for r in rows[1:]:
                try:
                    d = datetime.strptime(r[0], "%m/%d/%Y").date()
                except ValueError:
                    try:
                        d = date.fromisoformat(r[0])
                    except ValueError:
                        continue
                v = float(r[-1])
                pts.append([d.isoformat(), round(v / 100, 4)])
            if pts:
                out[name] = pts[-260:]
        except Exception as ex:                                # noqa: BLE001
            print(f"  (Cboe {name} not available: {ex})")
    return out


# ------------------------------------------------------------------ demo data (tests, no keys)
def demo_inputs(tickers: list[str], today: date):
    import random
    import numpy as np
    import pandas as pd
    rnd = random.Random(7)
    caps = {t: 3e12 * (0.82 ** i) * rnd.uniform(0.8, 1.2) for i, t in enumerate(tickers)}
    spots = {t: rnd.uniform(60, 600) for t in tickers}
    spots[INDEX] = 680.0
    # one market factor + noise, correlation around 0.25-0.3
    n = 330
    rng = np.random.default_rng(3)
    mkt = rng.normal(0, 0.009, n)
    data = {}
    for t in tickers:
        beta = rnd.uniform(0.7, 1.4)
        data[t] = 100 * np.exp(np.cumsum(beta * mkt + rng.normal(0, 0.014, n)))
    w = np.array([caps[t] for t in tickers]); w /= w.sum()
    idx_ret = sum(w[i] * np.diff(np.log(data[t]), prepend=np.log(data[t][0])) for i, t in enumerate(tickers))
    data[INDEX] = 600 * np.exp(np.cumsum(idx_ret))
    px = pd.DataFrame(data, index=pd.bdate_range(end=pd.Timestamp(today), periods=n))
    quotes = {}
    now = datetime.combine(today, datetime.min.time(), tzinfo=NY).replace(hour=15)
    for t in [INDEX] + tickers:
        S = spots[t]
        atm = 0.155 if t == INDEX else rnd.uniform(0.21, 0.34)
        skew = 0.75 if t == INDEX else 0.25
        rows = []
        for e in target_expiries(today):
            T = years_to(e, now)
            term = 1 + (0.06 if t == INDEX else 0.02) * math.sqrt(T)
            for k in range(-55, 56):
                K = round(S * (1 + k / 100), 2)
                m = math.log(K / S) / math.sqrt(max(T, 0.02))
                iv = max(0.05, atm * term * (1 - skew * m * 0.35 + 0.12 * m * m))
                rows.append({"exp": e, "type": "P" if K < S else "C", "strike": K, "iv": iv})
        quotes[t] = rows
    return caps, spots, quotes, px


# ------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    out = Path("/tmp/corr-demo") if args.demo else OUT
    (out / "history").mkdir(parents=True, exist_ok=True)

    universe = [ln.strip() for ln in (ROOT / "engine" / "tickers.txt").read_text().splitlines()
                if ln.strip() and not ln.startswith("#") and ln.strip() not in SKIP]
    now = datetime.now(NY)
    today = now.date()

    if args.demo:
        caps, spots, demo_q, px = demo_inputs(universe, today)
    else:
        print(f"Market caps for {len(universe)} stocks…")
        caps = market_caps(universe)
    basket = sorted(caps, key=lambda t: -caps[t])[:N_STOCKS]
    weights = {t: caps[t] for t in basket}
    print("Basket:", ", ".join(basket))

    alp = lambda t: t.replace("-", ".")                       # Yahoo BRK-B = Alpaca BRK.B
    if not args.demo:
        spots_a = spot_prices([INDEX] + [alp(t) for t in basket])
        spots = {t: spots_a.get(alp(t)) for t in [INDEX] + basket}
        px = closes([INDEX] + basket)

    exps = target_expiries(today)
    print("Expiries:", ", ".join(e.isoformat() for e in exps))

    surfaces: dict[str, list] = {}
    for t in [INDEX] + basket:
        S = spots.get(t)
        if not S:
            print(f"  ! {t}: no price")
            continue
        if args.demo:
            quotes = demo_q[t]
        else:
            quotes = []
            for e in exps:
                try:
                    quotes += option_quotes(alp(t), S, e)
                except Exception as ex:                        # noqa: BLE001
                    print(f"  ! {t} {e}: {ex}")
        surf = surface_for(smile_by_delta(quotes, S, now), today)
        filled = sum(v is not None for row in surf for v in row)
        print(f"  {t:6s} {len(quotes):5d} quotes, {filled}/{len(TENORS) * len(DELTAS)} cells")
        surfaces[t] = surf

    if INDEX not in surfaces:
        sys.exit("No SPY options: nothing to compute")
    idx = surfaces[INDEX]
    stock_s = {t: surfaces[t] for t in basket if t in surfaces}
    rho, cover, basket_iv = correlation_surface(idx, stock_s, weights)

    tw = sum(weights.values())
    atm = DELTAS.index(0.50)
    r1, r3 = realized(px, weights, 21), realized(px, weights, 63)
    rs = realized_series(px, weights)
    cboe = {} if args.demo else cboe_history()

    # which stocks matter most at 1 month, at the money: weight x vol (its share of the "rho = 1" variance)
    contrib = []
    for t in basket:
        s = stock_s.get(t)
        v = s[0][atm] if s else None
        contrib.append({"t": t, "w": round(weights[t] / tw, 4), "iv": None if v is None else round(v, 4),
                        "put25": None if not s or s[0][1] is None else round(s[0][1], 4)})

    r2 = lambda v: None if v is None else round(v, 4)
    head = {
        "rho_1m": rho[0][atm], "rho_3m": rho[2][atm],
        "realized_1m": r1, "realized_3m": r3,
        "premium_1m": None if rho[0][atm] is None or r1 is None else round(rho[0][atm] - r1, 4),
        "skew_1m": None if rho[0][1] is None or rho[0][3] is None else round(rho[0][1] - rho[0][3], 4),
        "spy_iv_1m": r2(idx[0][atm]), "basket_iv_1m": basket_iv[0][atm],
    }
    latest = {
        "asof": now.strftime("%Y-%m-%d %H:%M") + " New York",
        "date": today.isoformat(),
        "index": INDEX, "spot": round(spots[INDEX], 2), "demo": args.demo,
        "n_stocks": len(stock_s), "basket_weight_note": "weights = market caps of the 50, rescaled to 100%",
        "tenors": TENORS, "deltas": DELTAS, "delta_labels": DELTA_LABELS,
        "surface": rho, "coverage": cover,
        "index_iv": [[r2(v) for v in row] for row in idx], "basket_iv": basket_iv,
        "head": head, "stocks": contrib, "expiries": [e.isoformat() for e in exps],
        "realized_series": rs, "cboe": cboe,
    }

    # history: one row a day (re-runs on the same day replace it)
    hist_path = out / "history.csv"
    cols = ["date", "rho_1m", "rho_2m", "rho_3m", "rho_6m", "rho_1m_p25", "rho_1m_c25", "rho_1m_p10",
            "rho_3m_p25", "rho_3m_c25", "realized_1m", "realized_3m", "spy_iv_1m", "basket_iv_1m", "n_stocks"]
    rows = []
    if hist_path.exists():
        with hist_path.open() as f:
            rows = [r for r in csv.DictReader(f) if r["date"] != today.isoformat()]
    rows.append({"date": today.isoformat(), "rho_1m": rho[0][atm], "rho_2m": rho[1][atm], "rho_3m": rho[2][atm],
                 "rho_6m": rho[3][atm], "rho_1m_p25": rho[0][1], "rho_1m_c25": rho[0][3], "rho_1m_p10": rho[0][0],
                 "rho_3m_p25": rho[2][1], "rho_3m_c25": rho[2][3], "realized_1m": r1, "realized_3m": r3,
                 "spy_iv_1m": head["spy_iv_1m"], "basket_iv_1m": head["basket_iv_1m"], "n_stocks": len(stock_s)})
    rows.sort(key=lambda r: r["date"])
    with hist_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) in (None, "None") else r.get(c)) for c in cols})
    latest["history"] = [{c: (r[c] if c == "date" else (float(r[c]) if r.get(c) not in ("", None, "None") else None))
                          for c in cols if c != "n_stocks"} for r in rows][-400:]

    (out / "latest.json").write_text(json.dumps(latest, separators=(",", ":")))
    snap = {"date": today.isoformat(), "spot": latest["spot"], "weights": {t: round(weights[t] / tw, 5) for t in basket},
            "index": idx, "stocks": stock_s, "surface": rho, "coverage": cover, "tenors": TENORS, "deltas": DELTAS}
    (out / "history" / f"{today.isoformat()}.json").write_text(json.dumps(snap, separators=(",", ":")))

    print("\nImplied correlation (rows = 1M, 2M, 3M, 6M; columns = " + ", ".join(DELTA_LABELS) + ")")
    for t, row in zip(TENORS, rho):
        print(f"  {t:3d}d  " + "  ".join("  –  " if v is None else f"{v:5.2f}" for v in row))
    print(f"Realized 1M {r1}, 3M {r3}. Wrote {out}")


if __name__ == "__main__":
    main()
