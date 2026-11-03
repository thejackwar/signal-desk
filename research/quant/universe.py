"""
Point-in-time stock universe: which stocks you could actually have picked on each date.

The app's backtest runs on today's S&P 100 (engine/tickers.txt) for 15 years. That list is
chosen with hindsight: it holds the companies that *became* the biggest (NVDA, AVGO, TSLA ...),
and leaves out the ones that shrank, were bought or went bust. Any strategy tested on it gets
a head start it would never have had live.

Here the universe is rebuilt for every quarter from the S&P 500 members *at that time*
(data/sp500_membership.csv, from github.com/fja05680/sp500, MIT licence), keeping the 100 with
the highest trading value (price x volume, median of the last 6 months). Trading value is a
stand-in for market cap, which free data doesn't give historically; it tracks it closely for
the largest US companies, and it only uses information known on that day.

Known gap: Yahoo Finance has no prices for most delisted tickers (mostly companies that were
taken over, e.g. Celgene, Monsanto, Time Warner). Those drop out of the universe early.
`coverage()` reports how much of the index is missing on each date.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
MEMBERSHIP = HERE / "data" / "sp500_membership.csv"

# Old ticker -> the Yahoo Finance symbol that carries its price history today.
# Only renames where the same company kept trading; takeovers are not mapped.
ALIASES = {
    "FB": "META", "UTX": "RTX", "ANTM": "ELV", "FISV": "FI", "BBT": "TFC", "BK": "BNY",
    "WLTW": "WTW", "ABC": "COR", "PKI": "RVTY", "FLT": "CPAY", "COG": "CTRA", "KORS": "CPRI",
    "TMK": "GL", "CTL": "LUMN", "HRS": "LHX", "NLOK": "GEN", "SYMC": "GEN", "ADS": "BFH",
    "MYL": "VTRS", "BLL": "BALL", "FBHS": "FBIN", "RE": "EG", "CDAY": "DAY", "VIAC": "PARA",
    "CBS": "PARA", "DWDP": "DD", "HCP": "DOC", "PEAK": "DOC", "DISCA": "WBD", "GPS": "GAP",
}


def yahoo(t: str) -> str:
    t = ALIASES.get(t, t)
    return t.replace(".", "-")


def membership(start: str = "2009-01-01") -> pd.DataFrame:
    """One row per (Yahoo ticker, membership interval) overlapping `start` onwards."""
    m = pd.read_csv(MEMBERSHIP, parse_dates=["start_date", "end_date"])
    m["end_date"] = m["end_date"].fillna(pd.Timestamp("2100-01-01"))
    m = m[m["end_date"] >= pd.Timestamp(start)].copy()
    m["ticker"] = m["ticker"].map(yahoo)
    return m.rename(columns={"start_date": "start", "end_date": "end"})[["ticker", "start", "end"]]


def all_tickers(start: str = "2009-01-01") -> list[str]:
    return sorted(membership(start)["ticker"].unique())


def member_mask(index: pd.DatetimeIndex, start: str = "2009-01-01") -> pd.DataFrame:
    """Boolean date x ticker: was it in the S&P 500 that day?"""
    m = membership(start)
    out = pd.DataFrame(False, index=index, columns=sorted(m["ticker"].unique()))
    for t, s, e in m.itertuples(index=False):
        out.loc[(index >= s) & (index < e), t] = True
    return out


def top_n(closes: pd.DataFrame, volumes: pd.DataFrame, n: int = 100, lookback: int = 126,
          freq: str = "QS", start: str = "2009-01-01") -> pd.DataFrame:
    """Boolean date x ticker: in the point-in-time top-n by trading value, rebalanced at the start
    of every quarter using only the data up to the day before."""
    mask = member_mask(closes.index, start).reindex(columns=closes.columns, fill_value=False)
    dv = (closes * volumes).rolling(lookback, min_periods=lookback // 2).median().shift(1)
    rebal = closes.index.to_series().groupby(closes.index.to_period(freq[0])).first()
    out = pd.DataFrame(False, index=closes.index, columns=closes.columns)
    for d in rebal:
        row = dv.loc[d].where(mask.loc[d]).dropna()
        out.loc[d, row.nlargest(n).index] = True
    keep = out.index.isin(rebal.values)
    return out.astype(float).where(pd.Series(keep, index=out.index), axis=0).ffill().fillna(0).astype(bool)


def coverage(closes: pd.DataFrame, start: str = "2009-01-01") -> pd.Series:
    """Share of the S&P 500 members on each date that we have a price for."""
    mask = member_mask(closes.index, start)
    have = closes.notna().reindex(columns=mask.columns, fill_value=False)
    return (mask & have).sum(axis=1) / mask.sum(axis=1)
