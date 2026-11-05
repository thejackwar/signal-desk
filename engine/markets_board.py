"""
Terminal market boards: the world at a glance, for the Terminal page (docs/data/terminal/markets.json).

  wei     world stock indices and US index futures: last, day change, 1-month spark      (Yahoo)
  fx      the main currency pairs, EUR first since most users are in Italy                 (Yahoo)
  vol     VIX term structure: 9-day, 30-day, 3-month, 6-month                              (Yahoo)
  curve   US Treasury yield curve 1M-30Y today, a month ago and a year ago                 (FRED, daily)
  eu      Italy and Germany 10-year yields and the BTP-Bund spread (FRED, monthly OECD data)
  fed     Fed target range, effective rate, ECB deposit rate, and what fed funds futures
          imply for the next FOMC meetings (FedWatch-style maths on the 30-day futures)      (FRED + Yahoo)
  ctm     futures curves (next contracts) for oil, gas, gold, silver, copper, corn, wheat, soybeans (Yahoo)
  cot     CFTC Commitments of Traders: how big speculators are positioned, last 52 weeks   (CFTC, weekly)

Every section is fetched on its own: one source failing leaves the others (and the section's
previous copy, if there is one) in place.

  python engine/markets_board.py
"""
from __future__ import annotations

import csv
import io
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "terminal" / "markets.json"
UA = {"User-Agent": "signal-desk markets board (github.com/thejackwar/signal-desk)"}

WEI = [  # (Yahoo symbol, short name, region)
    ("^GSPC", "S&P 500", "US"), ("^NDX", "Nasdaq 100", "US"), ("^DJI", "Dow Jones", "US"), ("^RUT", "Russell 2000", "US"),
    ("ES=F", "S&P 500 fut.", "US futures"), ("NQ=F", "Nasdaq fut.", "US futures"), ("YM=F", "Dow fut.", "US futures"), ("RTY=F", "Russell fut.", "US futures"),
    ("FTSEMIB.MI", "FTSE MIB", "Europe"), ("^STOXX50E", "Euro Stoxx 50", "Europe"), ("^GDAXI", "DAX", "Europe"), ("^FCHI", "CAC 40", "Europe"),
    ("^FTSE", "FTSE 100", "Europe"), ("^IBEX", "IBEX 35", "Europe"), ("^SSMI", "SMI", "Europe"),
    ("^N225", "Nikkei 225", "Asia"), ("^HSI", "Hang Seng", "Asia"), ("000001.SS", "Shanghai", "Asia"), ("^KS11", "KOSPI", "Asia"), ("^BSESN", "Sensex", "Asia"),
    ("^BVSP", "Bovespa", "Americas"), ("^GSPTSE", "TSX", "Americas"),
]
FX = [
    ("EURUSD=X", "EUR/USD"), ("EURGBP=X", "EUR/GBP"), ("EURCHF=X", "EUR/CHF"), ("EURJPY=X", "EUR/JPY"),
    ("GBPUSD=X", "GBP/USD"), ("USDJPY=X", "USD/JPY"), ("USDCHF=X", "USD/CHF"), ("AUDUSD=X", "AUD/USD"),
    ("USDCAD=X", "USD/CAD"), ("USDCNY=X", "USD/CNY"), ("DX-Y.NYB", "Dollar index"), ("BTC-USD", "BTC/USD"),
]
VOL = [("^VIX9D", "9 days"), ("^VIX", "30 days"), ("^VIX3M", "3 months"), ("^VIX6M", "6 months")]
CURVE = [("DGS1MO", "1M", 1 / 12), ("DGS3MO", "3M", .25), ("DGS6MO", "6M", .5), ("DGS1", "1Y", 1), ("DGS2", "2Y", 2), ("DGS3", "3Y", 3),
         ("DGS5", "5Y", 5), ("DGS7", "7Y", 7), ("DGS10", "10Y", 10), ("DGS20", "20Y", 20), ("DGS30", "30Y", 30)]
# futures curves: Yahoo contract symbols are ROOT + month code + 2-digit year + exchange
MONTH = "FGHJKMNQUVXZ"
CTM = [  # root, exchange suffix, name, unit, months traded (all 12 unless listed), how many contracts
    ("CL", "NYM", "WTI crude oil", "$/barrel", MONTH, 12),
    ("BZ", "NYM", "Brent crude oil", "$/barrel", MONTH, 12),
    ("NG", "NYM", "Natural gas", "$/MMBtu", MONTH, 12),
    ("GC", "CMX", "Gold", "$/oz", "GJMQVZ", 7),
    ("SI", "CMX", "Silver", "$/oz", "HKNUZ", 6),
    ("HG", "CMX", "Copper", "$/lb", "HKNUZ", 6),
    ("ZC", "CBT", "Corn", "¢/bushel", "HKNUZ", 6),
    ("ZW", "CBT", "Wheat", "¢/bushel", "HKNUZ", 6),
    ("ZS", "CBT", "Soybeans", "¢/bushel", "FHKNQUX", 7),
]
# CFTC legacy futures-only report, by contract market code
COT = [
    ("067651", "WTI crude oil", "Commodities"), ("023651", "Natural gas", "Commodities"), ("088691", "Gold", "Commodities"),
    ("084691", "Silver", "Commodities"), ("085692", "Copper", "Commodities"), ("002602", "Corn", "Commodities"),
    ("001602", "Wheat", "Commodities"), ("005602", "Soybeans", "Commodities"),
    ("13874A", "S&P 500 e-mini", "Stock indices"), ("209742", "Nasdaq 100 e-mini", "Stock indices"), ("1170E1", "VIX futures", "Stock indices"),
    ("043602", "10-year T-note", "Rates"), ("020601", "US T-bond", "Rates"),
    ("099741", "Euro", "Currencies"), ("096742", "British pound", "Currencies"), ("097741", "Japanese yen", "Currencies"),
    ("133741", "Bitcoin", "Crypto"),
]


def r(x, n=4):
    try:
        x = float(x)
        return None if math.isnan(x) or math.isinf(x) else round(x, n)
    except (TypeError, ValueError):
        return None


def get(url: str, tries: int = 3) -> bytes:
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as f:
                return f.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def closes(symbols: list[str], period: str = "3mo") -> dict[str, pd.Series]:
    df = yf.download(symbols, period=period, interval="1d", auto_adjust=False, progress=False, group_by="ticker", threads=True)
    out = {}
    for s in symbols:
        try:
            c = (df[s] if len(symbols) > 1 else df)["Close"].dropna()
            if len(c):
                out[s] = c
        except Exception:
            pass
    return out


def board(rows, period="3mo"):
    C = closes([x[0] for x in rows], period)
    out = []
    for row in rows:
        s, name = row[0], row[1]
        c = C.get(s)
        if c is None or len(c) < 2:
            continue
        last, prev = float(c.iloc[-1]), float(c.iloc[-2])
        m = c[c.index >= c.index[-1] - pd.Timedelta(days=31)]
        y0 = c.iloc[0]
        item = {"s": s, "name": name, "last": r(last), "chg": r((last / prev - 1) * 100, 2), "date": c.index[-1].strftime("%Y-%m-%d"),
                "m1": r((last / float(m.iloc[0]) - 1) * 100, 2), "spark": [r(v) for v in m.tolist()]}
        if len(row) > 2:
            item["region"] = row[2]
        if period == "1y":
            item["y1"] = r((last / float(y0) - 1) * 100, 2)
        out.append(item)
    return out


def fred(series: str, start: str) -> pd.Series:
    raw = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}").decode()
    df = pd.read_csv(io.StringIO(raw))
    df.columns = ["date", "v"]
    df["v"] = pd.to_numeric(df["v"], errors="coerce")
    return df.dropna().set_index(pd.to_datetime(df.dropna()["date"]))["v"]


def curve():
    start = (date.today() - timedelta(days=400)).isoformat()
    S = {sid: fred(sid, start) for sid, _, _ in CURVE}
    last = max(s.index[-1] for s in S.values())

    def at(when):
        return [r(s[s.index <= when].iloc[-1], 3) if len(s[s.index <= when]) else None for s in S.values()]

    pts = [{"k": k, "y": y} for _, k, y in CURVE]
    now, m1, y1 = at(last), at(last - pd.Timedelta(days=30)), at(last - pd.Timedelta(days=365))
    two, ten, three_m = S["DGS2"], S["DGS10"], S["DGS3MO"]
    sp = (ten - two).dropna()
    sp3 = (ten - three_m).dropna()
    return {"date": last.strftime("%Y-%m-%d"), "points": pts, "now": now, "m1": m1, "y1": y1,
            "s2s10": r(sp.iloc[-1], 3), "s3m10": r(sp3.iloc[-1], 3),
            "s2s10_hist": [[d.strftime("%Y-%m-%d"), r(v, 3)] for d, v in sp.iloc[-260:].items()][::2]}


def eu_rates():
    start = (date.today() - timedelta(days=800)).isoformat()
    it, de = fred("IRLTLT01ITM156N", start), fred("IRLTLT01DEM156N", start)
    j = pd.concat([it, de], axis=1, keys=["it", "de"]).dropna()
    return {"source": "OECD via FRED, monthly averages", "rows": [[d.strftime("%Y-%m"), r(a, 3), r(b, 3), r((a - b) * 100, 0)] for d, a, b in zip(j.index, j["it"], j["de"])][-24:]}


def fed_odds(cal_path: Path):
    start = (date.today() - timedelta(days=60)).isoformat()
    up, lo, effr = fred("DFEDTARU", start), fred("DFEDTARL", start), fred("EFFR", start)
    ecb = fred("ECBDFR", start)
    out = {"upper": r(up.iloc[-1], 3), "lower": r(lo.iloc[-1], 3), "effr": r(effr.iloc[-1], 3), "ecb_deposit": r(ecb.iloc[-1], 3),
           "date": effr.index[-1].strftime("%Y-%m-%d"), "meetings": []}
    # FOMC decision days from the calendar the site already builds
    days = []
    try:
        cal = json.loads(cal_path.read_text())
        days = sorted({e["date"] for e in cal.get("economy", []) if "FOMC" in e.get("name", "") and "Rate" in e.get("name", "") and e["date"] >= date.today().isoformat()})
    except Exception:
        pass
    if not days:
        return out
    # 30-day fed funds futures, one per month from now to the month after the last meeting
    today = date.today()
    months, y, m = [], today.year, today.month
    last = date.fromisoformat(days[-1])
    end = (last.year + (last.month == 12), 1 if last.month == 12 else last.month + 1)
    while (y, m) <= end:
        months.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
        if len(months) > 14:
            break
    syms = {f"ZQ{MONTH[mm - 1]}{str(yy)[2:]}.CBT": (yy, mm) for yy, mm in months}
    C = closes(list(syms), "1mo")
    rate = {syms[s]: 100 - float(c.iloc[-1]) for s, c in C.items() if len(c)}
    if not rate:
        out["note"] = "Fed funds futures prices weren't available"
        return out
    meet = {(int(d[:4]), int(d[5:7])): int(d[8:10]) for d in days}
    cur = float(effr.iloc[-1])   # rate going into the first month
    mid = (out["upper"] + out["lower"]) / 2
    spread = cur - mid           # effective rate usually sits a little off the middle of the range
    for (yy, mm) in months:
        n = pd.Period(f"{yy}-{mm:02d}").days_in_month
        if (yy, mm) not in meet:
            if (yy, mm) in rate:
                cur = rate[(yy, mm)]
            continue
        d = meet[(yy, mm)]
        nxt = (yy + (mm == 12), 1 if mm == 12 else mm + 1)
        if (yy, mm) in rate and d < n - 5:
            after = (rate[(yy, mm)] * n - cur * (d - 1)) / (n - d + 1)
        elif nxt in rate and nxt not in meet:
            after = rate[nxt]
        elif (yy, mm) in rate:
            after = (rate[(yy, mm)] * n - cur * (d - 1)) / max(1, n - d + 1)
        else:
            break
        imp_mid = after - spread
        steps = (imp_mid - mid) / 0.25
        lo_s, hi_s = math.floor(steps), math.ceil(steps)
        p_hi = steps - lo_s if hi_s != lo_s else 1.0
        probs = {}
        for s_, p in ((lo_s, 1 - p_hi), (hi_s, p_hi)):
            if p > 0.005:
                a = round(out["lower"] + s_ * 0.25, 2)
                probs[f"{a:.2f}-{a + .25:.2f}"] = round(p * 100, 1)
        out["meetings"].append({"date": f"{yy}-{mm:02d}-{d:02d}", "implied": r(imp_mid, 3), "change_bp": r((imp_mid - mid) * 100, 1), "probs": probs})
        cur = after
    return out


def contract_syms(root, ex, months, n):
    today = date.today()
    y, m, out = today.year, today.month, []
    while len(out) < n + 2 and y <= today.year + 3:
        if MONTH[m - 1] in months:
            out.append((f"{root}{MONTH[m - 1]}{str(y)[2:]}.{ex}", f"{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][m - 1]} {str(y)[2:]}"))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def futures_curves():
    out = []
    plan = {root: contract_syms(root, ex, months, n) for root, ex, _, _, months, n in CTM}
    allsyms = [s for v in plan.values() for s, _ in v]
    C = closes(allsyms, "1mo")
    front = closes([f"{root}=F" for root, *_ in CTM], "5d")
    for root, ex, name, unit, months, n in CTM:
        pts = []
        for s, lbl in plan[root]:
            c = C.get(s)
            # skip expired or not-yet-trading contracts (no price in the last week)
            if c is None or not len(c) or (pd.Timestamp.now(tz=c.index.tz) - c.index[-1]).days > 7:
                continue
            pts.append({"c": lbl, "s": s, "p": r(c.iloc[-1])})
        pts = pts[:n]
        if len(pts) < 2:
            continue
        f, l = pts[0]["p"], pts[-1]["p"]
        shape = "contango" if l > f * 1.005 else "backwardation" if l < f * .995 else "flat"
        fr = front.get(f"{root}=F")
        out.append({"root": root, "name": name, "unit": unit, "points": pts, "shape": shape, "slope": r((l / f - 1) * 100, 2),
                    "front": r(fr.iloc[-1]) if fr is not None and len(fr) else f})
    return out


def cot():
    codes = ",".join(f"'{c}'" for c, _, _ in COT)
    since = (date.today() - timedelta(days=400)).isoformat()
    q = {"$where": f"cftc_contract_market_code in ({codes}) AND report_date_as_yyyy_mm_dd >= '{since}T00:00:00'",
         "$select": "cftc_contract_market_code,report_date_as_yyyy_mm_dd,open_interest_all,noncomm_positions_long_all,noncomm_positions_short_all,comm_positions_long_all,comm_positions_short_all",
         "$order": "report_date_as_yyyy_mm_dd", "$limit": "5000"}
    rows = json.loads(get("https://publicreporting.cftc.gov/resource/6dca-aqww.json?" + urllib.parse.urlencode(q)))
    by = {}
    for x in rows:
        by.setdefault(x["cftc_contract_market_code"], []).append(x)
    out = []
    for code, name, group in COT:
        xs = by.get(code, [])
        if len(xs) < 2:
            continue
        hist = []
        for x in xs:
            oi = float(x.get("open_interest_all") or 0) or 1
            net = float(x.get("noncomm_positions_long_all") or 0) - float(x.get("noncomm_positions_short_all") or 0)
            cnet = float(x.get("comm_positions_long_all") or 0) - float(x.get("comm_positions_short_all") or 0)
            hist.append((x["report_date_as_yyyy_mm_dd"][:10], net, cnet, oi))
        nets = [h[1] for h in hist]
        lo, hi = min(nets), max(nets)
        last = hist[-1]
        out.append({"code": code, "name": name, "group": group, "date": last[0], "net": int(last[1]), "comm_net": int(last[2]), "oi": int(last[3]),
                    "net_pct_oi": r(last[1] / last[3] * 100, 1), "chg_week": int(last[1] - hist[-2][1]),
                    "index_1y": r((last[1] - lo) / (hi - lo) * 100, 0) if hi > lo else None,   # 0 = most bearish of the year, 100 = most bullish
                    "hist": [[h[0], int(h[1])] for h in hist[-52:]]})
    return out


def main():
    try:
        prev = json.loads(OUT.read_text())
    except Exception:
        prev = {}
    out = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
    jobs = {
        "wei": lambda: board(WEI, "1y"),
        "fx": lambda: board(FX, "1y"),
        "vol": lambda: board(VOL, "3mo"),
        "curve": curve,
        "eu": eu_rates,
        "fed": lambda: fed_odds(ROOT / "docs" / "data" / "calendar.json"),
        "ctm": futures_curves,
        "cot": cot,
    }
    for k, fn in jobs.items():
        try:
            v = fn()
            if not v:
                raise ValueError("empty")
            out[k] = v
            print(f"{k}: ok")
        except Exception as e:
            print(f"{k}: FAILED {type(e).__name__}: {e}")
            if k in prev:
                out[k] = prev[k]
                out.setdefault("stale", []).append(k)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print("wrote", OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
