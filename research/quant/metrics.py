"""
Performance numbers and luck tests for daily return series.

  stats()            yearly growth, volatility, Sharpe, Sortino, worst drop, time invested
  boot_vs()          block bootstrap: how often does the strategy's Sharpe beat the benchmark's
                     when the past is reshuffled in 1-month blocks? (90% range + probability)
  deflated_sharpe()  Bailey & Lopez de Prado (2014): the chance a Sharpe ratio is real after
                     allowing for how many strategies were tried and for fat tails
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, norm, skew

DAYS = 252


def stats(r: pd.Series, k: pd.Series | None = None, traded: pd.Series | None = None) -> dict:
    r = r.dropna()
    if len(r) < 20:
        return {}
    eq = (1 + r).cumprod()
    years = len(r) / DAYS
    sd = r.std()
    dsd = np.sqrt((np.minimum(r, 0) ** 2).mean())
    out = {
        "cagr": float(eq.iloc[-1] ** (1 / years) - 1),
        "vol": float(sd * np.sqrt(DAYS)),
        "sharpe": float(r.mean() / sd * np.sqrt(DAYS)) if sd > 0 else None,
        "sortino": float(r.mean() / dsd * np.sqrt(DAYS)) if dsd > 0 else None,
        "maxdd": float((eq / eq.cummax() - 1).min()),
    }
    if k is not None:
        k = k.reindex(r.index)
        out["invested"] = float((k > 0).mean())
        out["avg_positions"] = float(k[k > 0].mean()) if (k > 0).any() else 0.0
    if traded is not None:
        out["turnover"] = float(traded.reindex(r.index).sum() / years)   # times the account is traded a year
    return out


def _sharpe(r: np.ndarray) -> np.ndarray:
    sd = r.std(axis=-1)
    return np.where(sd > 0, r.mean(axis=-1) / np.where(sd > 0, sd, 1) * np.sqrt(DAYS), 0.0)


def boot_vs(r: pd.Series, b: pd.Series, reps: int = 2000, block: int = 21, seed: int = 0) -> dict:
    """Stationary-ish block bootstrap of the Sharpe difference (strategy - benchmark), resampling
    the same days for both so their correlation is kept."""
    df = pd.concat([r, b], axis=1).dropna().values
    n = len(df)
    if n < block * 4:
        return {}
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(n / block))
    starts = rng.integers(0, n - block, size=(reps, nb))
    idx = (starts[:, :, None] + np.arange(block)).reshape(reps, -1)[:, :n]
    s = df[idx]                                   # reps x n x 2
    diff = _sharpe(s[:, :, 0]) - _sharpe(s[:, :, 1])
    return {"diff": float(_sharpe(df[:, 0]) - _sharpe(df[:, 1])),
            "lo": float(np.percentile(diff, 5)), "hi": float(np.percentile(diff, 95)),
            "p_better": float((diff > 0).mean())}


def deflated_sharpe(r: pd.Series, trial_sharpes: list[float]) -> float | None:
    """Probability the true Sharpe (per day, not annualised) is above what the best of
    len(trial_sharpes) worthless strategies would show by luck. trial_sharpes: daily Sharpes of
    every strategy tried. > 0.95 is the usual bar."""
    r = r.dropna()
    N, T = len(trial_sharpes), len(r)
    if N < 2 or T < 60 or r.std() == 0:
        return None
    sr = r.mean() / r.std()
    v = np.var(trial_sharpes, ddof=1)
    g = 0.5772156649
    sr0 = np.sqrt(v) * ((1 - g) * norm.ppf(1 - 1 / N) + g * norm.ppf(1 - 1 / (N * np.e)))
    sk, ku = skew(r), kurtosis(r, fisher=False)
    den = np.sqrt(max(1e-12, 1 - sk * sr + (ku - 1) / 4 * sr ** 2))
    return float(norm.cdf((sr - sr0) * np.sqrt(T - 1) / den))
