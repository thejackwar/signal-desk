"""
Builds the market calendar: upcoming earnings, IPOs and US economic releases.

  python engine/events.py           # real data
  python engine/events.py --demo    # made-up sample, no internet

Writes docs/data/calendar.json. Window: two weeks back (so the month view shows
what was just reported, with the result) to about ten weeks ahead.

Sources, each one optional (if one fails the others still fill the file, and if
everything fails the old file is kept):
  earnings   Yahoo Finance earnings calendar (US companies above MIN_CAP),
             then each S&P 100 stock's own Yahoo page for its next date and the
             analysts' EPS / revenue estimates; Nasdaq's calendar as a fallback
  IPOs       Nasdaq IPO calendar (expected, priced, filed) plus Yahoo's
  economy    Yahoo economic calendar (US, main releases only) plus the Fed's
             published FOMC meeting dates
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
ENGINE = Path(__file__).parent
OUT = ROOT / "docs" / "data" / "calendar.json"
NY = ZoneInfo("America/New_York")
BACK_DAYS, AHEAD_DAYS = 14, 90
MIN_CAP = 2e9                    # earnings from companies worth less than $2B are left out
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
      "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}

# FOMC decision days (second day of each meeting), from federalreserve.gov.
# Add next year's dates when the Fed publishes them.
FOMC = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]

# Economic releases worth showing, and a short line on why they matter.
MACRO = [
    (r"\bCPI\b|Consumer Price", "CPI inflation", "Consumer prices: the inflation number the Fed watches most closely."),
    (r"\bPPI\b|Producer Price", "PPI", "Prices businesses pay; often leads consumer inflation."),
    (r"Core PCE|\bPCE\b", "PCE inflation", "The Fed's preferred inflation gauge."),
    (r"Non.?farm|Nonfarm Payrolls", "Jobs report", "Jobs added last month; one of the biggest market movers."),
    (r"Unemployment Rate", "Unemployment rate", "Share of the workforce without a job."),
    (r"\bGDP\b", "GDP", "Growth of the whole US economy."),
    (r"Retail Sales", "Retail sales", "How much consumers spent in shops and online."),
    (r"ISM.*Manufacturing", "ISM manufacturing", "Survey of factory managers; above 50 means growth."),
    (r"ISM.*(Services|Non-Manufacturing)", "ISM services", "Survey of service businesses; above 50 means growth."),
    (r"JOLTS|Job Openings", "Job openings", "Open positions across the US (JOLTS)."),
    (r"Michigan.*Sentiment|Consumer Sentiment", "Consumer sentiment", "How confident households feel (University of Michigan)."),
    (r"Fed.*(Rate|Interest)|Interest Rate Decision|FOMC", "Fed rate decision", "The Federal Reserve sets interest rates."),
]
REGIONAL_FED = r"\b(Dallas|Cleveland|Richmond|Philly|Philadelphia|Kansas City|KC|NY|New York|Chicago|Atlanta|St\.? Louis) Fed\b"
# when a release has several figures (CPI month-on-month, year-on-year, core...), the one shown first
HEADLINE = {"CPI inflation": r"^CPI YY", "PPI": r"^PPI Final Demand YY", "PCE inflation": r"^PCE Price Index YY", "Jobs report": r"^Non.?Farm Payrolls$",
            "GDP": r"^GDP (Advance|Prelim|Final|Second|Third)$", "Retail sales": r"^Retail Sales MM$", "ISM manufacturing": r"Manufacturing PMI$",
            "ISM services": r"(Services|N-Mfg) PMI$", "Consumer sentiment": r"Sentiment", "Fed rate decision": r"Fed Funds"}
# Yahoo's IPO calendar also lists new ETFs, funds and the extra unit / right / warrant lines of SPACs
NOT_IPO = r"\bETFs?\b|\bFunds?\b|Portfolio|SPDR|\bUnits?$|\bRights$|\bWarrants?$|Preferred|(?<!Investment) Trust( [IVX]+)?$"
SPAC = r"Acquisition (Corp|Company|Co\b|Ltd|Inc)|\bSPAC\b|Treasury Corp"


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def num(v):
    """'$1,234.5' / '1.2B' / 3.4 / None -> float or None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and math.isnan(v)) or v == 0 else float(v)
    s = str(v).strip().replace(",", "").replace("$", "")
    if s in {"", "N/A", "NA", "--", "-"}:
        return None
    mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(s[-1:].upper())
    try:
        x = float(s[:-1] if mult else s) * (mult or 1)
        return x if x else None
    except ValueError:
        return None


def txt(v):
    """Text from a table cell, or None for blanks (pandas gives NaN for empty cells)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v).strip()
    return None if s.lower() in {"", "nan", "none", "n/a", "na", "--"} else s


def clean(o):
    """Drop empty values everywhere, so the file is valid JSON (no NaN) and small."""
    if isinstance(o, dict):
        return {k: c for k, v in o.items() if (c := clean(v)) is not None}
    if isinstance(o, list):
        return [clean(v) for v in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    return o


def short_name(n: str) -> str:
    n = re.sub(r",? (Inc\.?,? )?(Class [AB] )?(Common Stock|Common Shares|Ordinary Shares|Common stock|American Depositary Shares)$", "", n.strip())
    return n


def r2(x):
    return None if x is None else round(x, 2)


def ny_date(ts) -> str:
    """A UTC timestamp from Yahoo -> the New York calendar day."""
    import pandas as pd
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.tz_convert(NY).date().isoformat()


TIMING = {"BMO": "bmo", "AMC": "amc", "TNS": "tns", "TAS": "tns", "TBD": "tns"}


# ---------------------------------------------------------------- earnings
def yahoo_earnings(start: date, end: date) -> list[dict]:
    import yfinance as yf
    cal = yf.Calendars()
    out, d = [], start
    while d <= end:
        e = min(d + timedelta(days=6), end)
        for page in range(25):
            df = cal.get_earnings_calendar(market_cap=MIN_CAP, filter_most_active=False, start=d.isoformat(),
                                           end=(e + timedelta(days=1)).isoformat(), limit=100, offset=page * 100, force=True)
            if df is None or df.empty:
                break
            for sym, r in df.iterrows():
                when = r.get("Event Start Date")
                if when is None or str(when) == "NaT":
                    continue
                out.append({"ticker": str(sym).replace(".", "-"), "name": txt(r.get("Company")), "date": ny_date(when),
                            "time": TIMING.get(str(r.get("Timing") or "").upper(), "tns"),
                            "mcap": num(r.get("Marketcap")), "eps_est": r2(num(r.get("EPS Estimate"))),
                            "eps": r2(num(r.get("Reported EPS"))), "surprise": r2(num(r.get("Surprise(%)"))),
                            "event": txt(r.get("Event Name"))})
            if len(df) < 100:
                break
            time.sleep(.4)
        d = e + timedelta(days=1)
    return out


def nasdaq_earnings(start: date, end: date) -> list[dict]:
    import requests
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            try:
                j = requests.get("https://api.nasdaq.com/api/calendar/earnings", params={"date": d.isoformat()}, headers=UA, timeout=20).json()
                for r in (j.get("data") or {}).get("rows") or []:
                    cap = num(r.get("marketCap"))
                    if not cap or cap < MIN_CAP:
                        continue
                    t = str(r.get("time") or "")
                    out.append({"ticker": r["symbol"].replace(".", "-").replace("/", "-"), "name": r.get("name"), "date": d.isoformat(),
                                "time": "bmo" if "pre" in t else "amc" if "after" in t else "tns", "mcap": cap,
                                "eps_est": r2(num(r.get("epsForecast"))), "quarter": r.get("fiscalQuarterEnding")})
            except Exception as ex:                       # one bad day shouldn't stop the rest
                log("  nasdaq earnings", d, ex)
            time.sleep(.3)
        d += timedelta(days=1)
    return out


def sp100_dates(tickers: list[str], names: dict) -> dict[str, dict]:
    """Each S&P 100 stock's next earnings date and the analysts' estimates, from its own Yahoo page."""
    import yfinance as yf
    out = {}
    for t in tickers:
        try:
            c = yf.Ticker(t).calendar or {}
            ds = [x for x in (c.get("Earnings Date") or []) if x]
            if not ds:
                continue
            ds = sorted(x if isinstance(x, date) else x.date() for x in ds)
            out[t] = {"ticker": t, "name": names.get(t, [t])[0], "date": ds[0].isoformat(), "confirmed": len(ds) == 1,
                      "eps_est": r2(num(c.get("Earnings Average"))), "eps_lo": r2(num(c.get("Earnings Low"))),
                      "eps_hi": r2(num(c.get("Earnings High"))), "rev_est": num(c.get("Revenue Average"))}
        except Exception as ex:
            log("  calendar", t, ex)
        time.sleep(.15)
    return out


def earnings(start: date, end: date, tickers: list[str], names: dict) -> tuple[list[dict], list[str]]:
    srcs, rows = [], []
    try:
        rows = yahoo_earnings(start, end)
        if rows:
            srcs.append("Yahoo Finance earnings calendar")
        log(f"yahoo earnings: {len(rows)}")
    except Exception as ex:
        log("yahoo earnings failed:", ex)
    if not rows:
        try:
            rows = nasdaq_earnings(start, end)
            if rows:
                srcs.append("Nasdaq earnings calendar")
            log(f"nasdaq earnings: {len(rows)}")
        except Exception as ex:
            log("nasdaq earnings failed:", ex)

    # one entry per company and day (Yahoo lists some twice: date + call)
    by = {}
    for r in rows:
        k = (r["ticker"], r["date"])
        old = by.get(k)
        if not old or (old["time"] == "tns" and r["time"] != "tns") or (old.get("eps") is None and r.get("eps") is not None):
            by[k] = {**(old or {}), **{k2: v for k2, v in r.items() if v is not None}}
    sp = set(tickers)
    try:
        own = sp100_dates(tickers, names)
        if own:
            srcs.append("Yahoo Finance stock pages")
        log(f"S&P 100 own dates: {len(own)}")
    except Exception as ex:
        log("S&P 100 dates failed:", ex)
        own = {}
    for t, o in own.items():
        if not (start.isoformat() <= o["date"] <= end.isoformat()):
            continue
        hit = [k for k in by if k[0] == t and abs((date.fromisoformat(k[1]) - date.fromisoformat(o["date"])).days) <= 3]
        if hit:                          # same report: keep the calendar's day and time, add the estimates
            e = by[hit[0]]
            for f in ("eps_lo", "eps_hi", "rev_est", "confirmed"):
                if o.get(f) is not None:
                    e[f] = o[f]
            if e.get("eps_est") is None:
                e["eps_est"] = o["eps_est"]
        elif not any(k[0] == t and k[1] >= date.today().isoformat() for k in by):
            by[(t, o["date"])] = {**o, "time": "tns"}

    out = []
    for e in by.values():
        e["sp100"] = e["ticker"] in sp
        if e["sp100"]:
            e["name"] = names.get(e["ticker"], [e.get("name")])[0]
        e.pop("event", None)
        out.append({k: v for k, v in e.items() if v is not None})
    out.sort(key=lambda e: (e["date"], -(e.get("mcap") or 0)))
    return out, srcs


# --------------------------------------------------------------------- IPOs
def nasdaq_ipos(months: list[str]) -> list[dict]:
    import requests
    out = []
    for m in months:
        j = requests.get("https://api.nasdaq.com/api/ipo/calendar", params={"date": m}, headers=UA, timeout=20).json()
        d = j.get("data") or {}
        up = ((d.get("upcoming") or {}).get("upcomingTable") or {}).get("rows") or []
        for r in up:
            out.append(ipo_row(r, "expected", r.get("expectedPriceDate")))
        for r in (d.get("priced") or {}).get("rows") or []:
            out.append(ipo_row(r, "priced", r.get("pricedDate")))
        for r in (d.get("filed") or {}).get("rows") or []:
            out.append(ipo_row(r, "filed", None, filed=r.get("filedDate")))
        time.sleep(.4)
    return [x for x in out if x]


def us_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s).strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def ipo_row(r, status, when, filed=None):
    name = txt(r.get("companyName"))
    if not name:
        return None
    px = str(r.get("proposedSharePrice") or "").replace("$", "")
    lo = hi = None
    if "-" in px:
        a, b = px.split("-", 1); lo, hi = num(a), num(b)
    else:
        lo = hi = num(px)
    return {"ticker": txt(r.get("proposedTickerSymbol")), "name": name, "status": status,
            "date": us_date(when), "filed": us_date(filed), "exchange": r.get("proposedExchange"),
            "price_lo": lo, "price_hi": hi, "price": lo if status == "priced" and lo == hi else None,
            "shares": num(r.get("sharesOffered")), "size": num(r.get("dollarValueOfSharesOffered"))}


def yahoo_ipos(start: date, end: date) -> list[dict]:
    import yfinance as yf
    df = yf.Calendars().get_ipo_info_calendar(start=start.isoformat(), end=end.isoformat(), limit=100, force=True)
    out = []
    if df is None or df.empty:
        return out
    for sym, r in df.iterrows():
        deal = str(r.get("Deal Type") or "").lower()
        if "withdraw" in deal:
            continue
        when = r.get("Date")
        ok = when is not None and str(when) != "NaT"
        lo, hi, px = num(r.get("Price From")), num(r.get("Price To")), num(r.get("Price"))
        sh = num(r.get("Shares"))
        if not txt(r.get("Company")):
            continue
        out.append({"ticker": txt(sym), "name": txt(r.get("Company")),
                    "status": "priced" if "priced" in deal or (px and ok and ny_date(when) < date.today().isoformat()) else "expected" if ok else "filed",
                    "date": ny_date(when) if ok else None, "exchange": txt(r.get("Exchange")),
                    "price_lo": lo, "price_hi": hi, "price": px, "shares": sh,
                    "size": (sh * (px or hi or lo)) if sh and (px or hi or lo) else None})
    return out


def ipos(start: date, end: date) -> tuple[list[dict], list[str]]:
    srcs, rows = [], []
    months, d = [], start.replace(day=1)
    while d <= end:
        months.append(d.strftime("%Y-%m"))
        d = (d + timedelta(days=32)).replace(day=1)
    for name, fn in (("Nasdaq IPO calendar", lambda: nasdaq_ipos(months)), ("Yahoo Finance IPO calendar", lambda: yahoo_ipos(start, end))):
        try:
            got = fn()
            log(f"{name}: {len(got)}")
            if got:
                srcs.append(name)
            for x in got:
                if re.search(NOT_IPO, x["name"], re.I):
                    continue
                x["name"] = short_name(x["name"])
                if re.search(SPAC, x["name"], re.I):
                    x["spac"] = True
                key = re.sub(r"[^a-z0-9]", "", x["name"].lower())
                if any(key == re.sub(r"[^a-z0-9]", "", y["name"].lower()) or (x["ticker"] and x["ticker"] == y["ticker"]) for y in rows):
                    continue
                rows.append(x)
        except Exception as ex:
            log(f"{name} failed:", ex)
    s, e = start.isoformat(), end.isoformat()
    keep = [{k: v for k, v in x.items() if v is not None} for x in rows if (x.get("date") and s <= x["date"] <= e) or x["status"] == "filed"]
    dated = sorted([x for x in keep if x.get("date")], key=lambda x: (x["date"], -(x.get("size") or 0)))
    filed = sorted([x for x in keep if not x.get("date")], key=lambda x: -(x.get("size") or 0))[:12]
    return dated + filed, srcs


# ------------------------------------------------------------------ economy
def economy(start: date, end: date) -> tuple[list[dict], list[str]]:
    srcs, out = [], []
    try:
        import yfinance as yf
        cal, d, rows = yf.Calendars(), start, []
        while d <= end:
            e = min(d + timedelta(days=13), end)
            for page in range(10):
                df = cal.get_economic_events_calendar(start=d.isoformat(), end=(e + timedelta(days=1)).isoformat(), limit=100, offset=page * 100, force=True)
                if df is None or df.empty:
                    break
                rows += [(str(ev), r) for ev, r in df.iterrows()]
                if len(df) < 100:
                    break
                time.sleep(.4)
            d = e + timedelta(days=1)
        seen = set()
        for ev, r in rows:
            if str(r.get("Region") or "").upper() not in {"US", "USA", "UNITED STATES"}:
                continue
            hit = None if re.search(REGIONAL_FED, ev, re.I) else next(((lbl, why) for rx, lbl, why in MACRO if re.search(rx, ev, re.I)), None)
            when = r.get("Event Time")
            if not hit or when is None or str(when) == "NaT":
                continue
            import pandas as pd
            t = pd.Timestamp(when); t = t.tz_localize("UTC") if t.tzinfo is None else t
            k = (ev, t.isoformat())
            if k in seen:
                continue
            seen.add(k)
            out.append({"date": ny_date(t), "time": t.tz_convert(NY).strftime("%H:%M"), "utc": t.tz_convert("UTC").strftime("%Y-%m-%dT%H:%MZ"),
                        "name": ev.strip(" *"), "kind": hit[0], "why": hit[1], "period": txt(r.get("For")),
                        "expected": r2(num(r.get("Expected"))), "last": r2(num(r.get("Last"))), "actual": r2(num(r.get("Actual")))})
        # one entry per release and day: the headline figure, with the others as parts
        merged = {}
        for x in out:
            k = (x["date"], x["kind"])
            if k not in merged:
                merged[k] = x
                continue
            m, rx = merged[k], HEADLINE.get(x["kind"])
            if rx and re.search(rx, x["name"], re.I) and not re.search(rx, m["name"], re.I):
                x["parts"] = m.pop("parts", []) + [m]
                merged[k] = x
            else:
                m.setdefault("parts", []).append(x)
        out = []
        for m in merged.values():
            m["parts"] = [{f: p.get(f) for f in ("name", "actual", "expected", "last")} for p in m.get("parts", [])][:5]
            out.append(m)
        log(f"yahoo economy: {len(rows)} releases, {len(out)} kept")
        if out:
            srcs.append("Yahoo Finance economic calendar")
    except Exception as ex:
        log("yahoo economy failed:", ex)
    s, e = start.isoformat(), end.isoformat()
    for d in FOMC:
        if s <= d <= e and not any(x["date"] == d and x["kind"] == "Fed rate decision" for x in out):
            out.append({"date": d, "time": "14:00", "utc": None, "name": "FOMC rate decision", "kind": "Fed rate decision",
                        "why": "The Federal Reserve sets interest rates; the chair's press conference follows at 14:30 New York time."})
    srcs.append("Federal Reserve (FOMC dates)")
    out.sort(key=lambda x: (x["date"], x.get("time") or ""))
    return out, srcs


# --------------------------------------------------------------------- demo
def demo(start: date, end: date, tickers: list[str], names: dict) -> dict:
    rnd = random.Random(7)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    wd = [d for d in days if d.weekday() < 5]
    today = date.today().isoformat()
    ev = []
    for t in rnd.sample(tickers, min(70, len(tickers))):
        d = rnd.choice(wd[:45]).isoformat()
        est = round(rnd.uniform(.3, 6), 2)
        e = {"ticker": t, "name": names.get(t, [t])[0], "date": d, "time": rnd.choice(["bmo", "amc", "amc", "tns"]),
             "mcap": rnd.uniform(5e10, 3e12), "eps_est": est, "rev_est": rnd.uniform(5e9, 9e10), "sp100": True, "confirmed": rnd.random() > .3}
        if d < today:
            e["eps"] = round(est * rnd.uniform(.85, 1.15), 2); e["surprise"] = round((e["eps"] / est - 1) * 100, 2)
        ev.append(e)
    for i in range(40):
        d = rnd.choice(wd[:50]).isoformat()
        ev.append({"ticker": f"DMO{i}", "name": f"Demo Company {i}", "date": d, "time": rnd.choice(["bmo", "amc"]),
                   "mcap": rnd.uniform(2e9, 4e10), "eps_est": round(rnd.uniform(.1, 2), 2), "sp100": False})
    ev.sort(key=lambda e: (e["date"], -e["mcap"]))
    ip = [{"ticker": f"NEW{i}", "name": f"Sample Startup {i}", "status": "expected", "date": rnd.choice(wd[10:40]).isoformat(),
           "exchange": "NASDAQ Global", "price_lo": 14 + i, "price_hi": 16 + i, "shares": 1e7 * (i + 1), "size": 1.6e8 * (i + 1)} for i in range(6)]
    ip.sort(key=lambda x: x["date"])
    ip += [{"name": "Filed Co", "status": "filed", "filed": wd[3].isoformat(), "exchange": "NYSE", "size": 1e8}]
    eco = [{"date": d, "time": "14:00", "name": "FOMC rate decision", "kind": "Fed rate decision", "why": "The Federal Reserve sets interest rates."}
           for d in FOMC if start.isoformat() <= d <= end.isoformat()]
    eco += [{"date": wd[min(i * 7 + 3, len(wd) - 1)].isoformat(), "time": "08:30", "name": "CPI YoY", "kind": "CPI inflation",
             "why": "Consumer prices.", "expected": 2.8, "last": 2.9} for i in range(3)]
    eco.sort(key=lambda x: x["date"])
    return {"earnings": ev, "ipos": ip, "economy": eco, "sources": ["demo"], "demo": True}


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    today = datetime.now(NY).date()
    start, end = today - timedelta(days=BACK_DAYS), today + timedelta(days=AHEAD_DAYS)
    tickers = [ln.split("#")[0].strip() for ln in (ENGINE / "tickers.txt").read_text().splitlines()]
    tickers = [t for t in tickers if t]
    names = json.loads((ENGINE / "names.json").read_text())

    if a.demo:
        data = demo(start, end, tickers, names)
    else:
        e, s1 = earnings(start, end, tickers, names)
        i, s2 = ipos(start, end)
        m, s3 = economy(start, end)
        if not e and not i and OUT.exists():
            log("No earnings and no IPOs from any source: keeping the previous calendar.")
            return
        data = {"earnings": e, "ipos": i, "economy": m, "sources": s1 + s2 + s3}

    data = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window": {"start": start.isoformat(), "end": end.isoformat()}, "min_cap": MIN_CAP, **data}
    for e in data["earnings"]:
        if e.get("mcap"):
            e["mcap"] = round(e["mcap"])
        if e.get("rev_est"):
            e["rev_est"] = round(e["rev_est"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = clean(data)
    OUT.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    log(f"wrote {OUT.relative_to(ROOT)}: {len(data['earnings'])} earnings, {len(data['ipos'])} IPOs, {len(data['economy'])} economy")


if __name__ == "__main__":
    main()
