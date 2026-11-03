"""
Options backtest (modeled prices): every options strategy of the Options page, rolled every month
since 2010 on SPY and on each S&P 100 stock, written to docs/data/options/backtest.json.

Each month, on the day of the monthly expiry (third Friday), the strategy opens a new position
expiring the next month (28 or 35 days later) and holds it to expiry. The "managed" version of the
options-only strategies also closes early: credit trades at 50% of the premium kept or a loss of
twice the premium, debit trades at +100% or -50%. Prices come from engine/options_model.py
(Black-Scholes with VIX-based volatility, skew and trading costs): see that file for the details.

Account: $10,000 (any currency: it's all in % terms). Strategies that own the stock or hold the
cash for an assignment use the whole account each month; spreads, condors, straddles, strangles
and calendars put RISK (5%) of the account at risk each month, their maximum loss.

  python engine/options_backtest.py          # real prices (GitHub Actions, yfinance)
  python engine/options_backtest.py --demo   # simulated prices, for testing
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import options_model as M  # noqa: E402

ENGINE = Path(__file__).parent
OUT = ENGINE.parent / "docs" / "data" / "options" / "backtest.json"
START = "2010-01-01"
RISK = 0.05               # share of the account at risk each month for the defined-risk strategies
ACCOUNT = 10_000
TAX, STAMP, CARRY = 0.26, 0.002, 4   # Italian: 26% on each year's net gain, 0.2%/yr stamp duty, losses kept 4 years
KEEP_TRADES = 36          # SPY: the latest trades of each strategy shown on the page


# ------------------------------------------------------------------ data
def load_real(tickers: list[str]):
    import yfinance as yf
    want = sorted(set(tickers) | {"SPY", "^VIX"})
    raw = yf.download(want, start="2009-01-01", interval="1d", group_by="ticker",
                      auto_adjust=True, progress=False, threads=True)
    closes = {}
    for t in want:
        try:
            s = raw[t]["Close"].dropna()
        except KeyError:
            continue
        if len(s) > 120:
            s.index = pd.DatetimeIndex(s.index).tz_localize(None)
            closes[t] = s
    vix = closes.pop("^VIX")
    return closes, vix


def load_demo(n: int = 12, seed: int = 3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2009-01-02", pd.Timestamp.today().normalize())
    N = len(idx)
    # market: volatility that wanders (mean-reverting), so VIX moves around like the real one
    lv = np.zeros(N); lv[0] = math.log(0.18)
    for k in range(1, N):
        lv[k] = lv[k - 1] + 0.03 * (math.log(0.17) - lv[k - 1]) + rng.normal(0, 0.06)
    vol = np.exp(lv)
    mret = rng.normal(0.0004, 1, N) * vol / math.sqrt(252)
    spy = pd.Series(100 * np.exp(np.cumsum(mret)), idx)
    vix = pd.Series(100 * vol * rng.uniform(1.05, 1.25, N), idx)
    closes = {"SPY": spy}
    for i in range(n):
        beta, idio = rng.uniform(0.7, 1.4), rng.uniform(0.12, 0.35)
        r = beta * mret + rng.normal(0.0002, idio / math.sqrt(252), N)
        start = int(rng.integers(0, N // 3)) if i % 4 == 3 else 0          # a few "listed later"
        s = pd.Series(rng.uniform(20, 600) * np.exp(np.cumsum(r)), idx).iloc[start:]
        closes[f"DM{i:02d}"] = s
    return closes, vix


def atm_vol(close: pd.Series, vix: pd.Series, spy: pd.Series, is_index: bool) -> pd.Series:
    """At-the-money implied volatility used for each day (see options_model.py)."""
    v = (vix / 100).reindex(close.index).ffill()
    if is_index:
        return v
    def rv(s, n):
        return np.log(s).diff().rolling(n).std() * math.sqrt(252)
    spy_rv = rv(spy, 20).reindex(close.index).ffill()
    ratio = (v / spy_rv).clip(0.8, 2.0)
    own = 0.5 * rv(close, 20) + 0.5 * rv(close, 60)
    return (own * ratio).clip(0.12, 1.5)


# ------------------------------------------------------------------ one strategy on one series
class Series_:
    def __init__(self, close: pd.Series, atm: pd.Series, slope: float):
        self.dates = [d.date() for d in close.index]
        self.S = close.values.astype(float)
        self.atm = atm.values.astype(float)
        self.slope = slope
        self.ord = np.array([d.toordinal() for d in self.dates])

    def idx_on_or_before(self, d: date) -> int:
        return int(np.searchsorted(self.ord, d.toordinal(), side="right")) - 1

    def T(self, k: int, exp: date, near_exp: date) -> float:
        if exp == near_exp and k == self.idx_on_or_before(near_exp):
            return 0.0                                     # settlement day (a holiday Friday settles the day before)
        return max((exp - self.dates[k]).days, 0) / 365

    def iv(self, k: int, K: float, T: float) -> float:
        return M.skew_iv(self.atm[k], self.S[k], K, T, self.slope)


def price_legs(ser: Series_, k: int, legs, strikes, exps, near):
    out = []
    for leg, K, e in zip(legs, strikes, exps):
        T = ser.T(k, e, near)
        out.append(M.bs(ser.S[k], K, T, ser.iv(k, K, T), leg["t"]))
    return out


def max_loss_at_expiry(legs, strikes, premium_paid: float) -> float:
    """Biggest possible loss per share of a same-expiry option position (premium_paid = net debit,
    negative for a credit). Checked at every strike, at zero and far above the top strike."""
    pts = sorted(set([0.0] + list(strikes) + [max(strikes) * 4]))
    worst = math.inf
    for P in pts:
        v = sum(l["s"] * (max(P - K, 0) if l["t"] == "C" else max(K - P, 0)) for l, K in zip(legs, strikes))
        worst = min(worst, v)
    return premium_paid - worst


def run(sid: str, ser: Series_, managed: bool = False, first: int = 0) -> list[dict]:
    st = M.STRATEGIES[sid]
    if st.get("wheel"):
        return run_wheel(ser, first)
    legs, cycles = st["legs"], []
    i = first
    while i < len(ser.S) and not np.isfinite(ser.atm[i]):
        i += 1
    while i < len(ser.S):
        d0 = ser.dates[i]
        near = M.next_expiry(d0)
        j = ser.idx_on_or_before(near)
        if near > ser.dates[-1] or j <= i:
            break                                           # this month isn't over yet
        far = M.far_expiry(near)
        exps = [far if l.get("exp") == "far" else near for l in legs]
        S0 = ser.S[i]
        strikes = M.pick_strikes(legs, S0, lambda l: ser.T(i, far if l.get("exp") == "far" else near, near),
                                 lambda K, T: ser.iv(i, K, T))
        mids = price_legs(ser, i, legs, strikes, exps, near)
        fills = [p + l["s"] * M.half_spread(p) for p, l in zip(mids, legs)]       # buy at the ask, sell at the bid
        fills = [max(f, 0.0) for f in fills]
        debit = sum(l["s"] * f for l, f in zip(legs, fills))                     # per share; < 0 = credit
        costs = M.COMMISSION * len(legs)
        if st["stock"]:
            capital = S0 * 100 + max(debit, 0) * 100
        elif sid == "cash_secured_put":
            capital = strikes[0] * 100
        elif any(l.get("exp") == "far" for l in legs):
            capital = max(debit, 0.01) * 100 + costs
        else:
            capital = max_loss_at_expiry(legs, strikes, debit) * 100 + costs
        if capital <= 0:
            i = j; continue
        exit_k, how = j, "expiry"
        if managed:
            for k in range(i + 1, j):
                val = sum(l["s"] * p for l, p in zip(legs, price_legs(ser, k, legs, strikes, exps, near)))
                pnl_ps = val - debit                                             # per share, at mid prices
                if debit < 0:                                                    # credit trade
                    if pnl_ps >= 0.5 * -debit: exit_k, how = k, "target"; break
                    if pnl_ps <= -2 * -debit: exit_k, how = k, "stop"; break
                else:
                    if pnl_ps >= 1.0 * debit: exit_k, how = k, "target"; break
                    if pnl_ps <= -0.5 * debit: exit_k, how = k, "stop"; break
        S1 = ser.S[exit_k]
        exit_mid = price_legs(ser, exit_k, legs, strikes, exps, near)
        exit_val = 0.0
        for l, p, e in zip(legs, exit_mid, exps):
            settles = (exit_k == j and e == near)
            if settles:
                exit_val += l["s"] * p                                           # cash value at expiry, no cost
            else:
                exit_val += l["s"] * max(p - l["s"] * M.half_spread(p), 0.0)      # sell longs at bid, buy back shorts at ask
                costs += M.COMMISSION
        pnl = (exit_val - debit) * 100 - costs + st["stock"] * (S1 - S0) * 100
        cycles.append({"open": d0.isoformat(), "close": ser.dates[exit_k].isoformat(), "expiry": near.isoformat(),
                       "S0": round(S0, 2), "S1": round(S1, 2), "strikes": strikes, "premium": round(-debit, 3),
                       "pnl": round(pnl, 2), "capital": round(capital, 2), "ret": pnl / capital, "how": how})
        i = j
    return cycles


def run_wheel(ser: Series_, first: int = 0) -> list[dict]:
    """Sell 30-delta puts until assigned, then 30-delta calls until the shares are called away, and
    repeat. Contracts scale with the account (fractions allowed). (Some traders never sell a call
    below the price they paid; in a falling stock that can mean months with almost no premium, so
    the backtest keeps the same 30-delta rule on both sides.)"""
    i = first
    while i < len(ser.S) and not np.isfinite(ser.atm[i]):
        i += 1
    if i >= len(ser.S):
        return []
    cash, shares, cycles = None, 0.0, []
    while i < len(ser.S):
        d0 = ser.dates[i]
        near = M.next_expiry(d0)
        j = ser.idx_on_or_before(near)
        if near > ser.dates[-1] or j <= i:
            break
        S0 = ser.S[i]
        T = ser.T(i, near, near)
        if shares == 0:
            leg = {"t": "P", "s": -1, "d": 0.30}
            K = M.pick_strikes([leg], S0, lambda l: T, lambda K, T: ser.iv(i, K, T))[0]
            if cash is None:
                cash = K * 100                                                     # one contract to start
            n = cash / (K * 100)
        else:
            leg = {"t": "C", "s": -1, "d": 0.30}
            K = M.pick_strikes([leg], S0, lambda l: T, lambda K, T: ser.iv(i, K, T))[0]
            n = shares / 100
        eq0 = cash + shares * S0
        mid = M.bs(S0, K, T, ser.iv(i, K, T), leg["t"])
        prem = max(mid - M.half_spread(mid), 0.0)
        cash += prem * 100 * n - M.COMMISSION * math.ceil(n - 1e-9)
        S1, how = ser.S[j], "expiry"
        if leg["t"] == "P" and S1 < K:
            shares, how = 100 * n, "assigned"
            cash -= K * 100 * n
        elif leg["t"] == "C" and S1 > K:
            cash += K * shares
            shares, how = 0.0, "called away"
        eq1 = cash + shares * S1
        cycles.append({"open": d0.isoformat(), "close": ser.dates[j].isoformat(), "expiry": near.isoformat(),
                       "S0": round(S0, 2), "S1": round(S1, 2), "strikes": [K], "leg": leg["t"], "premium": round(prem, 3),
                       "pnl": round(eq1 - eq0, 2), "capital": round(eq0, 2), "ret": eq1 / eq0 - 1, "how": how})
        i = j
    return cycles


# ------------------------------------------------------------------ accounts and statistics
def account(rets: list[float], kind: str) -> list[float]:
    eq = [float(ACCOUNT)]
    for r in rets:
        eq.append(eq[-1] * (1 + (r if kind == "full" else RISK * r)))
        eq[-1] = max(eq[-1], 0.0)
    return eq


def after_tax(eq: list[float], dates: list[str]) -> list[float]:
    """Same rules as afterTax in docs/lab.js, on month-end values: each calendar year's net gain is
    taxed (losses carried forward 4 years) and 0.2% a year of stamp duty, pro rata."""
    out, losses = [eq[0]], []
    v = y_start = eq[0]
    def pay(gain, year):
        if gain < 0:
            losses.append([year, -gain]); return 0.0
        for L in losses:
            if year - L[0] <= CARRY and L[1] > 0 and gain > 0:
                u = min(gain, L[1]); L[1] -= u; gain -= u
        return gain * TAX
    for g in range(1, len(eq)):
        if dates[g][:4] != dates[g - 1][:4]:
            t = pay(v - y_start, int(dates[g - 1][:4])); v -= t; y_start = v
        v *= eq[g] / eq[g - 1] if eq[g - 1] > 0 else 0.0
        days = (date.fromisoformat(dates[g]) - date.fromisoformat(dates[g - 1])).days
        v -= v * STAMP * days / 365
        out.append(v)
    out[-1] = v - pay(v - y_start, int(dates[-1][:4]))
    return out


def stats(eq: list[float], dates: list[str], rets: list[float]) -> dict:
    yrs = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days / 365.25
    def cagr(e):
        return ((e[-1] / e[0]) ** (1 / yrs) - 1) * 100 if yrs > 0.2 and e[-1] > 0 else (-100.0 if e[-1] <= 0 else None)
    peak, dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1 if peak > 0 else 0)
    wins = [r for r in rets if r > 0]
    return {"cagr": rnd(cagr(eq)), "cagr_tax": rnd(cagr(after_tax(eq, dates))), "maxdd": rnd(dd * 100),
            "win": rnd(100 * len(wins) / len(rets)) if rets else None, "trades": len(rets),
            "avg": rnd(100 * float(np.mean(rets))) if rets else None,
            "best": rnd(100 * max(rets)) if rets else None, "worst": rnd(100 * min(rets)) if rets else None,
            "final": round(eq[-1]), "years": round(yrs, 1)}


def rnd(x, n=2):
    return None if x is None or not np.isfinite(x) else round(float(x), n)


def bench(close: pd.Series, dates: list[str]) -> list[float]:
    s = close.copy(); s.index = [d.date().isoformat() for d in s.index]
    vals = [float(s[:d].iloc[-1]) for d in dates]
    return [ACCOUNT * v / vals[0] for v in vals]


# ------------------------------------------------------------------ main
def main(demo: bool = False, out: Path = OUT) -> None:
    tickers = [x.strip() for x in (ENGINE / "tickers.txt").read_text().splitlines() if x.strip() and not x.startswith("#")]
    closes, vix = load_demo() if demo else load_real(tickers)
    spy = closes["SPY"]
    start = pd.Timestamp(START)
    result = {"as_of": max(s.index[-1] for s in closes.values()).date().isoformat(), "demo": demo,
              "start": START, "account": ACCOUNT, "risk": RISK,
              "model": {"rate": M.R, "commission": M.COMMISSION, "spread_pct": M.SPREAD_PCT,
                        "spread_min": M.SPREAD_MIN, "spread_max": M.SPREAD_MAX, "skew_index": M.SKEW_INDEX,
                        "skew_stock": M.SKEW_STOCK, "tax": TAX, "stamp": STAMP},
              "strategies": {sid: {"kind": st["kind"], "manageable": sid in M.MANAGEABLE} for sid, st in M.STRATEGIES.items()}}

    def series_for(t):
        c = closes[t]
        c = c[c.index >= start - pd.Timedelta(days=120)]
        atm = atm_vol(c, vix, spy, t == "SPY")
        first = int(np.searchsorted(c.index.values, np.datetime64(start)))
        return Series_(c, atm, M.SKEW_INDEX if t == "SPY" else M.SKEW_STOCK), first

    # ---- SPY: full detail
    ser, first = series_for("SPY")
    spy_out = {"strat": {}}
    for sid, st in M.STRATEGIES.items():
        block = {}
        for variant in (["hold", "managed"] if sid in M.MANAGEABLE else ["hold"]):
            cyc = run(sid, ser, managed=(variant == "managed"), first=first)
            dates = [cyc[0]["open"]] + [c["expiry"] for c in cyc]
            rets = [c["ret"] for c in cyc]
            eq = account(rets, st["kind"])
            block[variant] = {"eq": [round(v) for v in eq], "stats": stats(eq, dates, rets),
                              "trades": [{k: v for k, v in c.items() if k != "ret"} | {"ret": rnd(100 * c["ret"])} for c in cyc[-KEEP_TRADES:]]}
            spy_out.setdefault("dates", dates)
        spy_out["strat"][sid] = block
        print(f"  SPY {sid:18s} " + "  ".join(f"{v}: {b['stats']['cagr']}%/yr dd {b['stats']['maxdd']}% win {b['stats']['win']}%" for v, b in block.items()))
    bh = bench(spy, spy_out["dates"])
    spy_out["bench"] = [round(v) for v in bh]
    spy_out["bench_stats"] = stats(bh, spy_out["dates"], [])
    result["spy"] = spy_out

    # ---- S&P 100: every stock, then the equal-weight average of each month's returns
    names = [t for t in closes if t != "SPY"]
    per: dict[str, dict[str, dict[str, dict[str, float]]]] = {sid: {"hold": {}, "managed": {}} for sid in M.STRATEGIES}
    table: dict[str, list] = {sid: [] for sid in M.STRATEGIES}
    bh_month: dict[str, list[float]] = {}
    for t in names:
        ser, first = series_for(t)
        if len(ser.S) - first < 60:
            continue
        for sid, st in M.STRATEGIES.items():
            for variant in (["hold", "managed"] if sid in M.MANAGEABLE else ["hold"]):
                cyc = run(sid, ser, managed=(variant == "managed"), first=first)
                if not cyc:
                    continue
                for c in cyc:
                    per[sid][variant].setdefault(c["expiry"], {})[t] = c["ret"]
                if variant == "hold":
                    dates = [cyc[0]["open"]] + [c["expiry"] for c in cyc]
                    rets = [c["ret"] for c in cyc]
                    s = stats(account(rets, st["kind"]), dates, rets)
                    b = stats(bench(closes[t], dates), dates, [])
                    table[sid].append([t, s["cagr"], s["maxdd"], s["win"], b["cagr"], s["trades"]])
                    if sid == "covered_call":                    # stock's own month-by-month return, for the average
                        for c in cyc:
                            bh_month.setdefault(c["expiry"], []).append(c["S1"] / c["S0"] - 1)
        print(f"  {t} done")
    months = sorted(bh_month)
    sp = {"strat": {}, "count": len(table["covered_call"])}
    if months:
        first_open = min(c for c in [spy_out["dates"][0]])
        dates = [first_open] + months
        for sid, st in M.STRATEGIES.items():
            block = {}
            for variant, by_exp in per[sid].items():
                if not by_exp:
                    continue
                rets = [float(np.mean(list(by_exp[m].values()))) if m in by_exp else 0.0 for m in months]
                eq = account(rets, st["kind"])
                block[variant] = {"eq": [round(v) for v in eq], "stats": stats(eq, dates, rets)}
            block["tickers"] = sorted(table[sid], key=lambda r: -(r[1] if r[1] is not None else -1e9))
            sp["strat"][sid] = block
        bh = account([float(np.mean(bh_month[m])) for m in months], "full")
        sp["dates"], sp["bench"], sp["bench_stats"] = dates, [round(v) for v in bh], stats(bh, dates, [])
    result["sp100"] = sp

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size // 1024} kB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    main(a.demo, a.out)
