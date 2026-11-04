"""Max pain for SPY, QQQ, IWM and the S&P 100: the price at which the most option money expires worthless.

For each upcoming expiry (the next 60 days), and every candidate settlement price P (each listed strike):
    payout(P) = sum over calls  of  open interest x max(P - strike, 0) x 100
              + sum over puts   of  open interest x max(strike - P, 0) x 100
Max pain = the strike with the smallest payout: where option buyers, all together, would collect the least.

Open interest is the exchanges' count from the last session (Alpaca's options contracts list), so max pain only
changes once a day. Runs every weekday before the US open, right after the gamma levels
(.github/workflows/maxpain.yml).

Writes:
  docs/data/maxpain/index.json        one line per stock: price, next expiry, next monthly expiry, their max pain
  docs/data/maxpain/<SYM>.json        every expiry: max pain, open interest by strike and the payout curve
  docs/data/maxpain/history.csv       one row per day, stock and expiry: the max pain seen that morning
  docs/data/maxpain/scorecard.json    the honest test: once an expiry is over, did the price really close near
                                      max pain? (see score())

Free history of open interest per strike doesn't exist, so the test starts with the first day this runs.

    python engine/maxpain.py               # needs ALPACA_KEY_ID / ALPACA_SECRET_KEY
    python engine/maxpain.py --demo        # synthetic chains, no keys, writes to /tmp/maxpain-demo
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gex import DATA, TRADING, _get, _pages, spot_prices  # noqa: E402  (same Alpaca helpers as the gamma levels)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "maxpain"
NY = ZoneInfo("America/New_York")
ETFS = ["SPY", "QQQ", "IWM"]
DAYS_OUT = 60          # expiries up to this many days ahead
MAX_EXPIRIES = 6       # per stock, nearest first (SPY lists one every day), plus every monthly in the window
BAND = 0.5             # strikes within +-50% of the price (far strikes barely move max pain, but count them)


def tickers() -> list[str]:
    lines = (ROOT / "engine" / "tickers.txt").read_text().splitlines()
    syms = [s.strip().upper() for s in lines if s.strip() and not s.startswith("#")]
    return ETFS + [s for s in syms if s not in ETFS and s != "GOOG"]


def is_monthly(d: date) -> bool:
    """Third Friday of the month (or the Thursday before it when that Friday is a holiday)."""
    if d.weekday() == 4:
        return 15 <= d.day <= 21
    if d.weekday() == 3:
        return 15 <= (d + timedelta(days=1)).day <= 21
    return False


# ------------------------------------------------------------------ the maths
def max_pain(oi: dict[float, tuple[float, float]]) -> dict:
    """oi: {strike: (call open interest, put open interest)} for ONE expiry.
    Returns max pain, the payout curve [(strike, dollars)] and the open-interest totals."""
    ks = sorted(oi)
    curve = []
    for P in ks:
        pay = 0.0
        for K in ks:
            c, p = oi[K]
            if P > K:
                pay += c * (P - K)
            elif P < K:
                pay += p * (K - P)
        curve.append((P, pay * 100))
    mp = min(curve, key=lambda x: (x[1], x[0]))[0] if curve else None
    c_oi = sum(v[0] for v in oi.values())
    p_oi = sum(v[1] for v in oi.values())
    return {"max_pain": mp, "curve": curve, "call_oi": c_oi, "put_oi": p_oi}


def analyse(spot: float, contracts: list[tuple[date, str, float, float]], today: date) -> list[dict]:
    """contracts: [(expiry, "C"/"P", strike, open interest)] -> one dict per expiry, nearest first."""
    by_exp: dict[date, dict[float, list[float]]] = {}
    for e, t, K, n in contracts:
        if e < today or n <= 0:
            continue
        row = by_exp.setdefault(e, {}).setdefault(K, [0.0, 0.0])
        row[0 if t == "C" else 1] += n
    out = []
    keep = sorted(by_exp)[:MAX_EXPIRIES]
    keep += [e for e in sorted(by_exp) if e not in keep and is_monthly(e) and (e + timedelta(days=1)) not in by_exp]
    for e in sorted(keep):
        oi = {K: (v[0], v[1]) for K, v in by_exp[e].items()}
        if sum(a + b for a, b in oi.values()) < 50:          # almost no contracts open: meaningless
            continue
        r = max_pain(oi)
        mp = r["max_pain"]
        # keep the chart readable: strikes within 25% of the price
        near = [K for K in sorted(oi) if abs(K / spot - 1) <= 0.25] or sorted(oi)
        lo, hi = near[0], near[-1]
        out.append({
            "expiry": e.isoformat(), "dte": (e - today).days,
            # a Thursday only counts as the monthly when the Friday after it isn't listed (holiday)
            "monthly": is_monthly(e) and not (e.weekday() == 3 and (e + timedelta(days=1)) in by_exp),
            "max_pain": mp, "dist": round(mp / spot - 1, 4) if mp else None,
            "call_oi": int(r["call_oi"]), "put_oi": int(r["put_oi"]),
            "pc": round(r["put_oi"] / r["call_oi"], 2) if r["call_oi"] else None,
            "call_wall": max(oi, key=lambda K: oi[K][0]), "put_wall": max(oi, key=lambda K: oi[K][1]),
            "oi": [[K, int(oi[K][0]), int(oi[K][1])] for K in near],
            "curve": [[K, round(v)] for K, v in r["curve"] if lo <= K <= hi],
        })
    return out


# ------------------------------------------------------------------ Alpaca
def contracts_for(sym: str, spot: float, today: date) -> tuple[list, str | None]:
    params = {"underlying_symbols": sym.replace("-", "."), "status": "active", "limit": "10000",
              "expiration_date_gte": today.isoformat(),
              "expiration_date_lte": (today + timedelta(days=DAYS_OUT)).isoformat(),
              "strike_price_gte": f"{spot * (1 - BAND):.2f}", "strike_price_lte": f"{spot * (1 + BAND):.2f}"}
    last = None
    for base in TRADING:
        try:
            out, oi_date = [], None
            for page in _pages(f"{base}/v2/options/contracts", params, "option_contracts"):
                for c in page:
                    oi = float(c.get("open_interest") or 0)
                    if oi <= 0:
                        continue
                    out.append((date.fromisoformat(c["expiration_date"]), "C" if c["type"] == "call" else "P",
                                float(c["strike_price"]), oi))
                    oi_date = max(oi_date or "", c.get("open_interest_date") or "") or None
            return out, oi_date
        except urllib.error.HTTPError as e:
            last = e
    raise RuntimeError(f"options contracts for {sym}: {last}")


def closes_on(syms: list[str], days: list[date]) -> dict[tuple[str, str], float]:
    """{(symbol, YYYY-MM-DD): close} for the given days. Raw prices (strikes aren't split-adjusted)."""
    if not syms or not days:
        return {}
    out = {}
    a_syms = [s.replace("-", ".") for s in syms]
    for i in range(0, len(a_syms), 50):
        params = {"symbols": ",".join(a_syms[i:i + 50]), "timeframe": "1Day", "adjustment": "raw", "feed": "sip",
                  "start": min(days).isoformat(), "end": (max(days) + timedelta(days=1)).isoformat(), "limit": "10000"}
        try:
            for page in _pages(f"{DATA}/v2/stocks/bars", params, "bars", max_pages=20):
                for s, bars in (page or {}).items():
                    for b in bars:
                        out[(s.replace(".", "-"), b["t"][:10])] = float(b["c"])
        except urllib.error.HTTPError as e:
            print(f"  ! closes: HTTP {e.code}")
    return out


# ------------------------------------------------------------------ the honest test
def score(history: list[dict], closes: dict, today: date) -> dict:
    """For every expiry that is over: compare the max pain we published with where the price really closed.

    Two snapshots per (stock, expiry):
      - "week before": the morning 5-9 days before expiry (what someone would trade on)
      - "same day":    the morning of expiry day
    "Moved toward" = the closing price on expiry day ended closer to max pain than the price that morning.
    A coin would land near 50%; max pain is only useful if it is clearly above that.
    """
    rows = []
    by_key: dict[tuple[str, str], list[dict]] = {}
    for h in history:
        by_key.setdefault((h["symbol"], h["expiry"]), []).append(h)
    for (sym, exp), snaps in by_key.items():
        if date.fromisoformat(exp) >= today:
            continue
        close = closes.get((sym, exp))
        if close is None:
            continue
        for label, lo, hi in (("week", 5, 9), ("day", 0, 0)):
            pick = [s for s in snaps if lo <= int(s["dte"]) <= hi]
            if not pick:
                continue
            s = max(pick, key=lambda r: r["date"]) if label == "week" else pick[0]
            spot, mp = float(s["spot"]), float(s["max_pain"])
            d0, d1 = abs(spot - mp) / spot, abs(close - mp) / spot
            rows.append({"symbol": sym, "expiry": exp, "snap": label, "monthly": s["monthly"] == "1",
                         "spot": spot, "max_pain": mp, "close": close,
                         "dist_before": round(d0, 4), "dist_after": round(d1, 4),
                         "toward": d1 < d0, "within1": d1 <= 0.01})

    def summary(rs: list[dict]) -> dict | None:
        if not rs:
            return None
        n = len(rs)
        med = lambda xs: sorted(xs)[len(xs) // 2]
        return {"n": n, "toward": round(sum(r["toward"] for r in rs) / n, 3),
                "within1": round(sum(r["within1"] for r in rs) / n, 3),
                "med_before": med([r["dist_before"] for r in rs]), "med_after": med([r["dist_after"] for r in rs])}

    groups = {}
    for label in ("week", "day"):
        for scope, f in (("all", lambda r: True), ("monthly", lambda r: r["monthly"]),
                         ("etfs", lambda r: r["symbol"] in ETFS), ("stocks", lambda r: r["symbol"] not in ETFS)):
            groups[f"{label}_{scope}"] = summary([r for r in rows if r["snap"] == label and f(r)])
    rows.sort(key=lambda r: (r["expiry"], r["symbol"], r["snap"]), reverse=True)
    expiries = sorted({r["expiry"] for r in rows})
    return {"expiries_scored": len(expiries), "first": expiries[0] if expiries else None,
            "last": expiries[-1] if expiries else None, "groups": groups, "recent": rows[:300]}


# ------------------------------------------------------------------ demo chain (tests, no keys)
def demo_chain(S: float, today: date, seed: int) -> list:
    import random
    rnd = random.Random(seed)
    step = 1 if S < 200 else 5
    out = []
    fridays = [today + timedelta(days=d) for d in range(DAYS_OUT + 1) if (today + timedelta(days=d)).weekday() == 4]
    pin = round(S * rnd.uniform(0.96, 1.03) / step) * step
    for e in fridays:
        for K in range(int(S * 0.75) // step * step, int(S * 1.25), step):
            m = K / pin - 1
            base = 3000 * math.exp(-(m * 10) ** 2) * (3 if K % (step * 5) == 0 else 1) * (2.5 if is_monthly(e) else 1)
            out.append((e, "C", float(K), int(base * (1.7 if m > 0 else 0.5) * rnd.uniform(.6, 1.4))))
            out.append((e, "P", float(K), int(base * (1.7 if m < 0 else 0.5) * rnd.uniform(.6, 1.4))))
    return out


# ------------------------------------------------------------------ main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--only", help="comma-separated symbols (for a quick check)")
    a = ap.parse_args()
    out = Path("/tmp/maxpain-demo") if a.demo else OUT
    now = datetime.now(NY)
    today = now.date()
    syms = tickers()
    if a.only:
        syms = [s.strip().upper() for s in a.only.split(",")]

    if a.demo:
        import random
        rnd = random.Random(1)
        spots = {s: round(rnd.uniform(40, 700), 2) for s in syms}
        spots.update({"SPY": 681.4, "QQQ": 612.3, "IWM": 241.9})
        oi_date = (today - timedelta(days=1)).isoformat()
    else:
        if not os.environ.get("ALPACA_KEY_ID"):
            sys.exit("ALPACA_KEY_ID / ALPACA_SECRET_KEY missing")
        spots, oi_date = {}, None
        a_syms = [s.replace("-", ".") for s in syms]
        for i in range(0, len(a_syms), 50):
            got = spot_prices(a_syms[i:i + 50])
            spots.update({k.replace(".", "-"): v for k, v in got.items()})

    out.mkdir(parents=True, exist_ok=True)
    index, hist_rows = [], []
    for i, sym in enumerate(syms):
        S = spots.get(sym)
        if not S:
            print(f"  ! {sym}: no price, skipped")
            continue
        try:
            if a.demo:
                contracts = demo_chain(S, today, i)
            else:
                contracts, d = contracts_for(sym, S, today)
                oi_date = max(oi_date or "", d or "") or None
                time.sleep(0.25)                                  # stay well under Alpaca's 200 requests a minute
        except Exception as ex:
            print(f"  ! {sym}: {ex}")
            continue
        exps = analyse(S, contracts, today)
        if not exps:
            print(f"  ! {sym}: no expiry with open interest")
            continue
        (out / f"{sym}.json").write_text(json.dumps(
            {"symbol": sym, "spot": round(S, 2), "date": today.isoformat(), "oi_date": oi_date, "expiries": exps},
            separators=(",", ":")))
        nxt = exps[0]
        mon = next((e for e in exps if e["monthly"]), None)
        index.append({"symbol": sym, "spot": round(S, 2), "etf": sym in ETFS,
                      "next": {k: nxt[k] for k in ("expiry", "dte", "max_pain", "dist", "pc", "call_oi", "put_oi")},
                      "monthly": {k: mon[k] for k in ("expiry", "dte", "max_pain", "dist", "pc", "call_oi", "put_oi")} if mon else None})
        for e in exps:
            hist_rows.append({"date": today.isoformat(), "symbol": sym, "expiry": e["expiry"], "dte": e["dte"],
                              "monthly": int(e["monthly"]), "spot": round(S, 2), "max_pain": e["max_pain"],
                              "call_oi": e["call_oi"], "put_oi": e["put_oi"]})
        print(f"  {sym:6} {S:9.2f}  next {nxt['expiry']} max pain {nxt['max_pain']} ({nxt['dist']:+.1%})")

    if not index:
        sys.exit("no stock worked")
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    (out / "index.json").write_text(json.dumps(
        {"updated": stamp, "date": today.isoformat(), "oi_date": oi_date, "stocks": index}, separators=(",", ":")))

    # history: a re-run on the same day replaces that day's rows
    path = out / "history.csv"
    cols = ["date", "symbol", "expiry", "dte", "monthly", "spot", "max_pain", "call_oi", "put_oi"]
    rows = []
    if path.exists():
        with path.open() as f:
            rows = [r for r in csv.DictReader(f) if r["date"] != today.isoformat()]
    rows += hist_rows
    rows.sort(key=lambda r: (r["date"], r["symbol"], r["expiry"]))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    # score every expiry that's over (closing prices from Alpaca; none in demo mode)
    rows = [{k: str(v) for k, v in r.items()} for r in rows]
    past = sorted({date.fromisoformat(r["expiry"]) for r in rows if date.fromisoformat(r["expiry"]) < today})
    closes = {} if a.demo else closes_on(sorted({r["symbol"] for r in rows}), past)
    sc = score(rows, closes, today)
    sc["updated"] = stamp
    (out / "scorecard.json").write_text(json.dumps(sc, separators=(",", ":")))
    print(f"  {len(index)} stocks, {len(hist_rows)} expiries; scorecard: {sc['expiries_scored']} expiries scored. Wrote {out}")


if __name__ == "__main__":
    main()
