"""
Turns a strategy's alerts into daily positions and an account, with the universe and costs applied.

Trade rules are the app's own (engine/backtest.py): trend strategies hold until the opposite
alert, the others for 20 sessions or until a bearish alert, Buy the Dip until +10% or 60
sessions, and the trading-bot strategies run their own trades. Long only.

What's different from engine/backtest.py:
  - a trade is only allowed if the stock was in the universe on the alert day
  - every buy and sell pays `cost_bps` (spread + slippage + commission, per side)
  - optional one-day entry delay (`lag=1`): buy at the next close instead of the alert's close,
    a check that results don't depend on trading at a price you only know once it's printed
  - two ways to size: "split" (the app's account: money split equally across open positions,
    so one lone position gets 100%) and "capped" (at most 1/slots of the account per stock,
    the rest in cash), closer to how a real account would be run
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ENGINE = Path(__file__).resolve().parents[2] / "engine"
sys.path.insert(0, str(ENGINE))
from backtest import EXITS, HOLD_DAYS, TREND, own_exit_positions, own_trade_days  # noqa: E402


def _runs(held: np.ndarray):
    """(start, end) index pairs of consecutive held days."""
    d = np.diff(np.concatenate([[0], (held > 0).astype(int), [0]]))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1))


def positions(sid: str, res, df: pd.DataFrame, allowed: np.ndarray, lag: int = 0):
    """Daily (H, R) for one stock: H[d] = 1 if a position earns day d's return R[d].
    allowed[i]: may a trade be opened on an alert at row i (stock in the universe that day)."""
    c, hi, lo = df["close"].values, df["high"].values, df["low"].values
    n = len(c)
    R = np.full(n, np.nan)
    R[1:] = c[1:] / c[:-1] - 1
    H = np.zeros(n)

    if res.trades is not None:                   # trading-bot strategies: their own fills
        R2 = np.zeros(n)
        for tr in res.trades:
            if tr["side"] <= 0 or tr.get("in") is None:
                continue
            if not allowed[max(0, tr["in"] - 1)]:
                continue
            days, rets = own_trade_days(tr, c, n)
            for d, r in zip(days, rets):
                H[d], R2[d] = 1, R2[d] + r
        return H, R2

    sig = res.signals.values
    ex = EXITS.get(sid)
    fills = {}
    if ex:
        held, _, fills = own_exit_positions(sig, c, hi, lo, ex["hold"], ex["tp"])
    else:
        held, exit_at = np.zeros(n), -1
        for i in range(n):
            if sig[i] == -1:
                exit_at = -1
            elif sig[i] == 1:
                exit_at = n if sid in TREND else i + HOLD_DAYS
            if i < exit_at:
                held[i] = 1
    for a, b in _runs(held):                     # universe check on the alert day
        if not allowed[a]:
            held[a:b + 1] = 0
    if lag:
        held = np.concatenate([np.zeros(lag), held[:-lag]])
    else:
        for i, f in fills.items():               # a take-profit day earns up to the fill
            R[i] = f / c[i - 1] - 1
    H[1:] = held[:-1]
    return H, np.nan_to_num(R)


def account(H: pd.DataFrame, R: pd.DataFrame, cost_bps: float = 10, slots: int | None = None) -> pd.DataFrame:
    """Daily account returns. Columns: gross, net, cost, traded (share of the account bought or
    sold that day), k (open positions)."""
    k = H.sum(axis=1)
    if slots:
        w = H / np.maximum(k, slots).values[:, None]
    else:
        w = H.div(k.replace(0, np.nan), axis=0).fillna(0)
    gross = (w * R).sum(axis=1)
    # what's bought or sold: positions opened or closed, at their weight (no daily rebalancing)
    traded = (w - w.shift(1).fillna(0)).where((H != H.shift(1).fillna(0)), 0).abs().sum(axis=1)
    cost = traded * cost_bps / 1e4
    return pd.DataFrame({"gross": gross, "net": gross - cost, "cost": cost, "traded": traded, "k": k})


def equal_weight(R: pd.DataFrame, member: pd.DataFrame) -> pd.Series:
    """Holding every stock in the universe equally (rebalanced daily, no costs): the fair
    benchmark for a strategy that picks from the same stocks."""
    held = member.shift(1).fillna(False) & R.notna()
    return (R.where(held)).mean(axis=1).fillna(0)
