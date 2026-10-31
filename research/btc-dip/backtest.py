"""BTC buy-the-dip backtest on hourly candles.

Signal: BTC falls >= DIP% below its highest price of the last LOOKBACK hours.
Entry:  a limit buy resting at (rolling high * (1 - DIP%)); it fills on the first hour whose
        low touches it (gap-downs fill at that hour's open, which is worse).
Exit:   after HOLD hours at that hour's close (time exit), optionally earlier at a take-profit
        or a stop-loss. One trade at a time; after an exit, a fresh dip must form from a
        new rolling high before re-entering.
Costs:  COST per side (fees + slippage). With leverage, perpetual funding is charged/credited
        for every 8h funding timestamp held, and a position is liquidated when the low
        falls past the maintenance margin.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

DATA = Path(__file__).parent / "data"


def load() -> tuple[pd.DataFrame, pd.Series]:
    px = pd.read_csv(DATA / "btc_1h.csv.gz", index_col=0)
    px.index = pd.to_datetime(px.index, utc=True, format="mixed")
    px = px[~px.index.duplicated()].sort_index()
    try:
        f = pd.read_csv(DATA / "funding.csv", index_col=0)["rate"]
        f.index = pd.to_datetime(f.index, utc=True, format="mixed").floor("h")
        f = f.groupby(level=0).sum()
    except FileNotFoundError:
        f = pd.Series(dtype=float)
    return px, f


def run(px: pd.DataFrame, dip=0.05, lookback=24, hold=24, tp=None, sl=None,
        lev=1.0, cost=0.001, funding: pd.Series | None = None, mmr=0.005,
        start=None, end=None, entry_mode="limit") -> pd.DataFrame:
    if start is not None or end is not None:
        px = px.loc[start:end]
    o, h, l, c = (px[k].to_numpy() for k in ("open", "high", "low", "close"))
    idx = px.index
    # rolling high of the PREVIOUS `lookback` hours (no look-ahead)
    rh = pd.Series(h, index=idx).rolling(lookback, min_periods=lookback).max().shift(1).to_numpy()
    fund = None
    if lev > 1 and funding is not None and len(funding):
        fund = funding.reindex(idx).fillna(0.0).to_numpy()   # rate stamped on the funding hour
    trades, i, n = [], lookback + 1, len(c)
    reset_after = -1
    while i < n - 1:
        if np.isnan(rh[i]) or i <= reset_after:
            i += 1
            continue
        trig = rh[i] * (1 - dip)
        if entry_mode == "close":                    # wait for an hourly CLOSE below the level, buy at that close
            if c[i] > trig:
                i += 1
                continue
            entry, e_i = c[i], i
        else:
            if l[i] > trig:
                i += 1
                continue
            entry = min(o[i], trig)                  # gap through the level -> fill at open
            e_i = i
        liq = entry * (1 - 1 / lev + mmr) if lev > 1 else -np.inf
        exit_px, reason, j = None, "time", e_i
        last = min(e_i + hold, n - 1)
        # the entry hour itself: only check stop/liquidation against what happened after the fill
        first = e_i + 1 if entry_mode == "close" else e_i
        for j in range(first, last + 1):
            lo = l[j] if j > e_i else min(l[j], entry)
            hi = h[j] if j > e_i else c[j]           # be conservative about the entry hour's high
            if lo <= liq:
                exit_px, reason = liq, "liquidated"
                break
            if sl is not None and lo <= entry * (1 - sl):
                exit_px, reason = min(o[j], entry * (1 - sl)) if j > e_i else entry * (1 - sl), "stop"
                break
            if tp is not None and hi >= entry * (1 + tp):
                exit_px, reason = max(o[j], entry * (1 + tp)) if j > e_i else entry * (1 + tp), "target"
                break
        if exit_px is None:
            j = last
            exit_px = c[j]
        gross = exit_px / entry - 1
        fund_cost = 0.0
        if fund is not None:
            fund_cost = fund[e_i + 1: j + 1].sum()       # long pays positive funding
        if reason == "liquidated":
            ret = -1.0                                     # margin gone
        else:
            ret = lev * (gross - fund_cost) - lev * cost * 2
            ret = max(ret, -1.0)
        trades.append(dict(entry_time=idx[e_i], exit_time=idx[j], entry=entry, exit=exit_px,
                           hours=j - e_i, gross=gross, ret=ret, reason=reason,
                           dip_from=rh[e_i]))
        reset_after = j + lookback                      # need a fresh rolling window before the next dip
        i = j + 1
    return pd.DataFrame(trades)


def stats(t: pd.DataFrame, years: float) -> dict:
    if t.empty:
        return dict(trades=0)
    eq = (1 + t["ret"]).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    total = eq.iloc[-1] - 1
    hours_in = t["hours"].sum()
    return dict(
        trades=len(t), per_year=len(t) / years,
        win=(t["ret"] > 0).mean(), avg=t["ret"].mean(), median=t["ret"].median(),
        total=total, cagr=(1 + total) ** (1 / years) - 1 if total > -1 else -1,
        maxdd=dd, worst=t["ret"].min(), best=t["ret"].max(),
        pf=t.loc[t.ret > 0, "ret"].sum() / max(1e-12, -t.loc[t.ret < 0, "ret"].sum()),
        liq=(t["reason"] == "liquidated").sum(), avg_hours=t["hours"].mean(),
        exposure_h=hours_in,
    )
