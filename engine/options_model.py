"""
Options model shared by the options backtest (options_backtest.py) and the paper trades
(options_live.py). docs/options-lib.js is the same model in JavaScript for the Options page;
tests/options/test_parity.py checks that the two agree.

Historical option prices cost a lot of money, so the backtest *models* them:
  - price and Greeks: Black-Scholes, rate R, no dividends, European exercise
  - implied volatility at the money: VIX for SPY; for a stock, its own recent volatility scaled by
    how much VIX was above or below SPY's recent volatility that day (the market's "fear premium")
  - skew: strikes below the price get a higher volatility (puts cost more), as in real markets
  - costs: half a bid/ask spread on every option bought or sold, plus a commission per contract
It is an approximation (no earnings jumps in the volatility, no early assignment, no dividends on
the option side), and the page says so.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

R = 0.04                  # risk-free rate used everywhere (a year)
COMMISSION = 0.65         # $ per contract per leg, each time a contract is opened or closed early
SPREAD_PCT = 0.025        # half the bid/ask spread as a share of the option price ...
SPREAD_MIN, SPREAD_MAX = 0.01, 0.15   # ... but at least 1 cent and at most 15 cents a share
# Calibrated on SPY's real option chain (Alpaca quotes, 2 Nov 2026, 46 days out):
ATM_VIX = 0.82            # at-the-money volatility is about 82% of VIX (VIX also prices the skew)
SKEW_INDEX, SKEW_STOCK = 0.30, 0.15   # below the price: volatility rises this share per standard deviation
CALL_LIN, CALL_QUAD = 0.46, 0.21      # above the price (index): it falls fast, then flattens (a "smirk")
MIN_DAYS = 21             # the next monthly expiry must be at least this many days away

# ------------------------------------------------------------------ strategies
# legs: t = "C" call / "P" put, s = +1 buy / -1 sell, d = target |delta| or "atm",
# exp = "near" (default) or "far" (the month after). stock = shares per contract held alongside.
# kind: "full" = uses the whole account (owns the stock or holds the cash for assignment),
#       "risk" = a fixed slice of the account is put at risk each month (its maximum loss).
STRATEGIES: dict[str, dict] = {
    "covered_call":     {"stock": 1, "kind": "full", "legs": [{"t": "C", "s": -1, "d": 0.30}]},
    "cash_secured_put": {"stock": 0, "kind": "full", "legs": [{"t": "P", "s": -1, "d": 0.30}]},
    "wheel":            {"stock": 0, "kind": "full", "legs": [{"t": "P", "s": -1, "d": 0.30}], "wheel": True},
    "protective_put":   {"stock": 1, "kind": "full", "legs": [{"t": "P", "s": 1, "d": 0.25}]},
    "collar":           {"stock": 1, "kind": "full", "legs": [{"t": "P", "s": 1, "d": 0.25}, {"t": "C", "s": -1, "d": 0.25}]},
    "bull_call_spread": {"stock": 0, "kind": "risk", "legs": [{"t": "C", "s": 1, "d": "atm"}, {"t": "C", "s": -1, "d": 0.25}]},
    "bear_put_spread":  {"stock": 0, "kind": "risk", "legs": [{"t": "P", "s": 1, "d": "atm"}, {"t": "P", "s": -1, "d": 0.25}]},
    "iron_condor":      {"stock": 0, "kind": "risk", "legs": [{"t": "P", "s": 1, "d": 0.08}, {"t": "P", "s": -1, "d": 0.20},
                                                              {"t": "C", "s": -1, "d": 0.20}, {"t": "C", "s": 1, "d": 0.08}]},
    "long_straddle":    {"stock": 0, "kind": "risk", "legs": [{"t": "C", "s": 1, "d": "atm"}, {"t": "P", "s": 1, "d": "atm"}]},
    "long_strangle":    {"stock": 0, "kind": "risk", "legs": [{"t": "C", "s": 1, "d": 0.25}, {"t": "P", "s": 1, "d": 0.25}]},
    "calendar":         {"stock": 0, "kind": "risk", "legs": [{"t": "C", "s": -1, "d": "atm"}, {"t": "C", "s": 1, "d": "atm", "exp": "far"}]},
}
ORDER = list(STRATEGIES)
# strategies made only of options: these can also be run with a take-profit / stop-loss
MANAGEABLE = {k for k, v in STRATEGIES.items() if not v["stock"] and not v.get("wheel")}


# ------------------------------------------------------------------ Black-Scholes
def ncdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2))


def npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs(S: float, K: float, T: float, iv: float, t: str, r: float = R) -> float:
    """Price of one option (per share). T in years. At or after expiry: what it's worth if exercised."""
    if T <= 1e-9 or iv <= 0:
        return max(S - K, 0.0) if t == "C" else max(K - S, 0.0)
    sq = iv * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / sq
    d2 = d1 - sq
    if t == "C":
        return S * ncdf(d1) - K * math.exp(-r * T) * ncdf(d2)
    return K * math.exp(-r * T) * ncdf(-d2) - S * ncdf(-d1)


def greeks(S: float, K: float, T: float, iv: float, t: str, r: float = R) -> dict:
    """delta, gamma, theta (per calendar day), vega (per 1 volatility point), per share."""
    if T <= 1e-9 or iv <= 0:
        itm = (S > K) if t == "C" else (S < K)
        return {"delta": (1.0 if t == "C" else -1.0) if itm else 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    sq = iv * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / sq
    d2 = d1 - sq
    pdf = npdf(d1)
    gamma = pdf / (S * sq)
    vega = S * pdf * math.sqrt(T) / 100
    if t == "C":
        delta = ncdf(d1)
        theta = (-S * pdf * iv / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * ncdf(d2)) / 365
    else:
        delta = ncdf(d1) - 1
        theta = (-S * pdf * iv / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * ncdf(-d2)) / 365
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def skew_iv(atm: float, S: float, K: float, T: float, slope: float) -> float:
    """Volatility for strike K: higher below the price (puts cost more), lower above it.
    x = how many standard deviations K is below the price. Stocks (slope SKEW_STOCK) get a flatter
    version of the index's shape on both sides."""
    T = max(T, 1 / 365)
    x = -math.log(K / S) / (atm * math.sqrt(T))          # + below the price, - above
    x = max(-2.5, min(2.5, x))
    if x > 0:
        k = 1 + slope * x
    else:
        k = max(0.6, 1 + slope / SKEW_INDEX * (CALL_LIN * x + CALL_QUAD * x * x))
    return max(0.05, atm * k)


def half_spread(price: float) -> float:
    return min(SPREAD_MAX, max(SPREAD_MIN, SPREAD_PCT * price))


# ------------------------------------------------------------------ strikes and expiries
def strike_step(S: float) -> float:
    if S < 25: return 0.5
    if S < 100: return 1.0
    if S < 250: return 2.5
    if S < 500: return 5.0
    return 10.0


def strike_grid(S: float, step: float | None = None) -> list[float]:
    step = step or strike_step(S)
    lo, hi = math.floor(S * 0.5 / step), math.ceil(S * 1.5 / step)
    return [round(i * step, 4) for i in range(max(lo, 1), hi + 1)]


def pick_strikes(legs: list[dict], S: float, T_of, iv_of, step: float | None = None) -> list[float]:
    """Strikes on the model's grid. T_of(leg) -> years to that leg's expiry; iv_of(K, T) -> volatility."""
    step = step or strike_step(S)
    def delta_of(leg, K):
        T = T_of(leg)
        return abs(greeks(S, K, T, iv_of(K, T), leg["t"])["delta"])
    return pick_strikes_by(legs, S, strike_grid(S, step), delta_of)


def pick_strikes_by(legs: list[dict], S: float, grid: list[float], delta_of) -> list[float]:
    """Strike of each leg: the one nearest the price for "atm", else the out-of-the-money strike whose
    |delta| is closest to the target. delta_of(leg, K) -> |delta| (None = no usable quote).
    Same-type, same-expiry legs that would share a strike (or cross) are pushed to the next strike
    further out, so a spread or a condor always has distinct strikes (an "atm" leg keeps its own).
    The same rule runs on real option chains (options_live.py, the Options page) and on the model."""
    grid = sorted(set(grid))
    atm_k = min(grid, key=lambda K: (abs(K - S), K))
    out = []
    for leg in legs:
        if leg["d"] == "atm":
            out.append(atm_k)
            continue
        best, bd = None, 9.0
        for K in grid:
            if (leg["t"] == "C" and K < S) or (leg["t"] == "P" and K > S):
                continue                                        # out of the money only
            dl = delta_of(leg, K)
            if dl is None:
                continue
            if abs(dl - leg["d"]) < bd - 1e-12:
                best, bd = K, abs(dl - leg["d"])
        out.append(best if best is not None else atm_k)
    for t in ("C", "P"):
        idx = [i for i, l in enumerate(legs) if l["t"] == t and l.get("exp", "near") == "near"]
        idx.sort(key=lambda i: -(0.5 if legs[i]["d"] == "atm" else legs[i]["d"]))
        for a, b in zip(idx, idx[1:]):
            if (t == "C" and out[b] <= out[a]) or (t == "P" and out[b] >= out[a]):
                nxt = [K for K in grid if K > out[a]] if t == "C" else [K for K in grid if K < out[a]][::-1]
                if nxt:
                    out[b] = nxt[0]
    return out


def third_friday(y: int, m: int) -> date:
    d = date(y, m, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def next_expiry(d: date, min_days: int = MIN_DAYS) -> date:
    """The first monthly expiry (third Friday) at least min_days after d."""
    y, m = d.year, d.month
    for _ in range(4):
        e = third_friday(y, m)
        if (e - d).days >= min_days:
            return e
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    raise ValueError("no expiry")


def far_expiry(near: date) -> date:
    y, m = (near.year + 1, 1) if near.month == 12 else (near.year, near.month + 1)
    return third_friday(y, m)
