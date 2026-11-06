"""
Company data for the Terminal: the "DES/FA/ANR/EE/RV/HDS/DVD/MOST" side of a Bloomberg terminal,
for every S&P 100 stock (or S&P 500 with UNIVERSE=sp500, which also refreshes the S&P 100 summary), from Yahoo Finance.

Writes
  docs/data/company.json        one row per stock for the screener (EQS), comparables (RV) and
                                most active (MOST): valuation, quality, growth, dividend, short
                                interest, ownership, analyst target and rating, volume stats
  docs/data/company/<T>.json    the detail for the security panel:
                                  profile         what the company does, employees, website
                                  fa              last 5 quarters and 4 years: revenue, gross profit,
                                                  operating income, net income, EPS; debt, cash, equity, FCF
                                  anr             recommendation trend, price targets, last upgrades/downgrades
                                  ee              EPS and revenue estimates, EPS trend, last 4 surprises
                                  hds             top institutional and fund holders
                                  ins             latest insider buys and sells
                                  si              short interest
                                  dvd             dividend per year and the last payments

A stock that fails to download keeps its previous file. Information only.

  python engine/company.py              # every stock in engine/tickers.txt
  python engine/company.py AAPL MSFT    # just these
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))
from universe import U  # noqa: E402

DATA = ROOT / "docs" / "data"          # company/<T>.json is shared: one file per stock, whichever list it's in
SUM = U.path("company.json")           # the screener's summary: docs/data/ or docs/data/sp500/
DIR = DATA / "company"


def clean(v, n=4):
    if v is None:
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        v = float(v)
        return None if math.isnan(v) or math.isinf(v) else round(v, n)
    if isinstance(v, (int, str, bool)):
        return v
    return str(v)


def recs(df, limit=None, cols=None):
    """DataFrame -> list of dicts with clean values (index kept as 'date' when it's a date)."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    d = df.copy()
    if cols:
        d = d[[c for c in cols if c in d.columns]]
    if isinstance(d.index, pd.DatetimeIndex):
        d = d.reset_index().rename(columns={d.index.name or "index": "date"})
    out = [{str(k): clean(v) for k, v in row.items()} for row in d.to_dict("records")]
    return out[:limit] if limit else out


def statement(df, rows, n):
    """Income/balance/cash flow statement (rows = items, columns = periods) -> {periods, items}."""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    d = df.iloc[:, :n]
    out = {"periods": [c.strftime("%Y-%m-%d") if hasattr(c, "strftime") else str(c) for c in d.columns]}
    for key, names in rows.items():
        for nm in names:
            if nm in d.index:
                out[key] = [clean(v, 0) if abs(v or 0) > 1000 else clean(v, 3) for v in d.loc[nm].tolist()]
                break
    return out


INCOME = {"revenue": ["Total Revenue", "Operating Revenue"], "gross": ["Gross Profit"], "operating": ["Operating Income"],
          "net": ["Net Income", "Net Income Common Stockholders"], "eps": ["Diluted EPS", "Basic EPS"]}
BALANCE = {"debt": ["Total Debt"], "cash": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
           "equity": ["Stockholders Equity", "Common Stock Equity"]}
CASH = {"fcf": ["Free Cash Flow"], "buybacks": ["Repurchase Of Capital Stock"], "dividends": ["Cash Dividends Paid", "Common Stock Dividend Paid"]}


def one(t: str) -> tuple[dict, dict]:
    tk = yf.Ticker(t)
    info = tk.info or {}
    if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None and info.get("marketCap") is None):
        raise ValueError("no info")
    g = info.get
    px = g("currentPrice") or g("regularMarketPrice") or g("previousClose")
    det: dict = {"ticker": t, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    det["profile"] = {"name": g("longName") or g("shortName"), "sector": g("sector"), "industry": g("industry"), "employees": g("fullTimeEmployees"),
                      "website": g("website"), "country": g("country"), "summary": (g("longBusinessSummary") or "")[:900]}

    def safe(fn):
        try:
            return fn()
        except Exception as e:
            print(f"  {t}: {fn.__name__ if hasattr(fn, '__name__') else 'part'} skipped ({type(e).__name__})")
            return None

    det["fa"] = {
        "q": safe(lambda: statement(tk.quarterly_income_stmt, INCOME, 5)),
        "y": safe(lambda: statement(tk.income_stmt, INCOME, 4)),
        "bs": safe(lambda: statement(tk.balance_sheet, BALANCE, 2)),
        "cf": safe(lambda: statement(tk.cashflow, CASH, 4)),
    }
    ratios = {k: clean(g(k)) for k in ["trailingPE", "forwardPE", "priceToBook", "trailingPegRatio", "enterpriseToEbitda", "priceToSalesTrailing12Months",
                                         "profitMargins", "operatingMargins", "grossMargins", "returnOnEquity", "returnOnAssets", "revenueGrowth",
                                         "earningsGrowth", "debtToEquity", "currentRatio", "beta", "marketCap", "enterpriseValue", "freeCashflow",
                                         "totalRevenue", "totalCash", "totalDebt", "sharesOutstanding", "floatShares"]}
    det["fa"]["ratios"] = ratios

    rt = safe(lambda: recs(tk.recommendations, 4))
    ud = safe(lambda: recs(tk.upgrades_downgrades.sort_index(ascending=False) if tk.upgrades_downgrades is not None else None, 15))
    det["anr"] = {"mean": clean(g("recommendationMean"), 2), "key": g("recommendationKey"), "n": g("numberOfAnalystOpinions"),
                  "target": {"mean": clean(g("targetMeanPrice"), 2), "median": clean(g("targetMedianPrice"), 2), "high": clean(g("targetHighPrice"), 2), "low": clean(g("targetLowPrice"), 2)},
                  "trend": rt or [], "changes": ud or []}

    def est(df):
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return []
        d = df.reset_index().rename(columns={"index": "period"})
        return [{str(k): clean(v) for k, v in row.items()} for row in d.to_dict("records")]
    det["ee"] = {"eps": safe(lambda: est(tk.earnings_estimate)) or [], "rev": safe(lambda: est(tk.revenue_estimate)) or [],
                 "trend": safe(lambda: est(tk.eps_trend)) or [], "history": safe(lambda: recs(tk.earnings_history, 4)) or []}

    det["hds"] = {"insiders_pct": clean(g("heldPercentInsiders")), "inst_pct": clean(g("heldPercentInstitutions")),
                  "inst": safe(lambda: recs(tk.institutional_holders, 10)) or [], "funds": safe(lambda: recs(tk.mutualfund_holders, 6)) or []}
    det["ins"] = safe(lambda: recs(tk.insider_transactions, 15, ["Start Date", "Insider", "Position", "Transaction", "Text", "Shares", "Value", "Ownership"])) or []
    det["si"] = {"shares": g("sharesShort"), "prior": g("sharesShortPriorMonth"), "pct_float": clean(g("shortPercentOfFloat")), "days": clean(g("shortRatio"), 2),
                 "date": datetime.fromtimestamp(g("dateShortInterest"), timezone.utc).strftime("%Y-%m-%d") if g("dateShortInterest") else None}

    dv = safe(lambda: tk.dividends)
    dvd = {"rate": clean(g("dividendRate"), 3), "yield": clean((g("dividendRate") or 0) / px * 100, 2) if px and g("dividendRate") else None,
           "payout": clean(g("payoutRatio")), "avg5y": clean(g("fiveYearAvgDividendYield"), 2),
           "ex": datetime.fromtimestamp(g("exDividendDate"), timezone.utc).strftime("%Y-%m-%d") if g("exDividendDate") else None, "years": [], "last": []}
    if dv is not None and len(dv):
        dv.index = pd.to_datetime(dv.index).tz_localize(None)
        yr = dv.groupby(dv.index.year).sum()
        dvd["years"] = [[int(y), clean(v, 3)] for y, v in yr.items()][-11:]
        dvd["last"] = [[d.strftime("%Y-%m-%d"), clean(v, 4)] for d, v in dv.iloc[-8:][::-1].items()]
    det["dvd"] = dvd

    tgt = g("targetMeanPrice")
    row = {"name": det["profile"]["name"], "sector": g("sector"), "industry": g("industry"), "price": clean(px, 2),
           "mcap": g("marketCap"), "pe": clean(g("trailingPE"), 2), "fpe": clean(g("forwardPE"), 2), "pb": clean(g("priceToBook"), 2),
           "peg": clean(g("trailingPegRatio"), 2), "ev_ebitda": clean(g("enterpriseToEbitda"), 2), "ps": clean(g("priceToSalesTrailing12Months"), 2),
           "margin": clean(g("profitMargins")), "op_margin": clean(g("operatingMargins")), "roe": clean(g("returnOnEquity")),
           "rev_g": clean(g("revenueGrowth")), "eps_g": clean(g("earningsGrowth")), "de": clean(g("debtToEquity"), 1), "beta": clean(g("beta"), 2),
           "div_y": dvd["yield"], "payout": clean(g("payoutRatio")), "ex_div": dvd["ex"],
           "short_pct": clean(g("shortPercentOfFloat")), "short_days": clean(g("shortRatio"), 2),
           "ins_pct": clean(g("heldPercentInsiders")), "inst_pct": clean(g("heldPercentInstitutions")),
           "target": clean(tgt, 2), "upside": clean((tgt / px - 1) * 100, 1) if tgt and px else None,
           "rec": clean(g("recommendationMean"), 2), "rec_key": g("recommendationKey"), "n_an": g("numberOfAnalystOpinions")}
    return row, det


def activity(tickers: list[str]) -> dict:
    """Volume and price action from a year of daily bars: relative volume, gap, new highs/lows."""
    df = yf.download(tickers, period="1y", interval="1d", auto_adjust=False, progress=False, group_by="ticker", threads=True)
    out = {}
    for t in tickers:
        try:
            d = df[t].dropna()
            if len(d) < 30:
                continue
            c, v, o, h, l = d["Close"], d["Volume"], d["Open"], d["High"], d["Low"]
            avg20 = float(v.iloc[-21:-1].mean())
            out[t] = {"vol": int(v.iloc[-1]), "avg_vol": int(avg20), "rel_vol": clean(v.iloc[-1] / avg20, 2) if avg20 else None,
                      "dollar_vol": clean(v.iloc[-1] * c.iloc[-1], 0), "gap": clean((o.iloc[-1] / c.iloc[-2] - 1) * 100, 2),
                      "chg5d": clean((c.iloc[-1] / c.iloc[-6] - 1) * 100, 2), "range_pct": clean((h.iloc[-1] / l.iloc[-1] - 1) * 100, 2),
                      "new_hi": bool(h.iloc[-1] >= h.iloc[:-1].max()), "new_lo": bool(l.iloc[-1] <= l.iloc[:-1].min()),
                      "date": d.index[-1].strftime("%Y-%m-%d")}
        except Exception:
            pass
    return out


def main():
    # UNIVERSE=sp500 covers every S&P 100 stock too, and writes both summaries (see the end)
    tickers = sys.argv[1:] or U.tickers()
    try:
        prev = json.loads(SUM.read_text())
    except Exception:
        prev = {"stocks": {}}
    DIR.mkdir(parents=True, exist_ok=True)
    rows, ok, fail = dict(prev.get("stocks", {})), 0, []
    for i, t in enumerate(tickers):
        try:
            row, det = one(t)
            rows[t] = {**rows.get(t, {}), **row}
            (DIR / f"{t}.json").write_text(json.dumps(det, separators=(",", ":")))
            ok += 1
        except Exception as e:
            fail.append(t)
            print(f"{t}: FAILED {type(e).__name__}: {e}")
        time.sleep(0.4)
        if (i + 1) % 20 == 0:
            print(f"{i + 1}/{len(tickers)}")
    try:
        act = activity(tickers)
        for t, a in act.items():
            rows.setdefault(t, {})["act"] = a
        print("activity:", len(act))
    except Exception as e:
        print("activity FAILED", e)
    keep = set(tickers) if not sys.argv[1:] else set(rows)
    out = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "source": "Yahoo Finance",
           "stocks": {t: v for t, v in sorted(rows.items()) if t in keep}}
    SUM.parent.mkdir(parents=True, exist_ok=True)
    SUM.write_text(json.dumps(out, separators=(",", ":")))
    if U.is500 and not sys.argv[1:]:
        small = set((ROOT / "engine" / "tickers.txt").read_text().split())
        (DATA / "company.json").write_text(json.dumps({**out, "stocks": {t: v for t, v in out["stocks"].items() if t in small}},
                                                      separators=(",", ":")))
    print(f"done: {ok} ok, {len(fail)} failed {fail}")
    if not ok and not sys.argv[1:]:
        sys.exit(1)


if __name__ == "__main__":
    main()
