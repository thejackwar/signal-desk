"""
Pairs trading (statistical arbitrage) on the S&P 100.

Two stocks from the same sector whose prices move together in the long run
("cointegrated") are traded against each other when their gap stretches too far:
buy the one that fell behind, short the one that ran ahead, close when the gap
closes. The page is docs/pairs.html.

What it writes (docs/data/pairs/):
  pairs.json   today's pairs (picked on the last two years), their z-score history,
               the prices behind them (for the page's what-if settings), and the
               out-of-sample walk-forward backtest vs SPY.
  grid.json    the walk-forward again with other settings (--grid, weekly)
  live.json    paper trades from the go-live day, €1,000 a trade. Closed trades are
               never changed or removed (same rule as data/live_trades.json).

The walk-forward is the honest test: every six months the pairs are picked using
only the two years before, then traded for the next six months without peeking.

Prices: the Strategy Lab's files (docs/data/lab.json + lab_recent.json), Yahoo Finance if missing.

Run:  python engine/pairs.py          (today's pairs, walk-forward, paper trades)
      python engine/pairs.py --grid   (also the settings grid, grid.json: slow, weekly)
      python engine/pairs.py --demo   (made-up prices, for testing offline)
"""

from __future__ import annotations

import itertools
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.tsa.stattools import coint

ENGINE = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE))
from universe import U  # noqa: E402
import board_series as BS  # noqa: E402

OUT = U.path("pairs")      # UNIVERSE=sp500: docs/data/sp500/pairs/, from that list's Strategy Lab price files

# ---- rules (the page shows these, and lets visitors change the first five) ----
RULES = {
    "entry_z": 2.0,       # open when the gap is this many standard deviations from normal
    "exit_z": 0.5,        # close when it's back within this
    "stop_z": 3.5,        # close if it stretches this far: the link may be broken
    "max_days": 30,       # close after this many sessions whatever happens
    "z_window": 60,       # sessions used for "normal" (rolling mean and spread of the gap)
    "formation_days": 504,  # two years to pick pairs
    "trading_days": 126,    # then trade them for six months
    "max_pairs": 10,
    "max_per_stock": 2,
    "p_max": 0.05,        # cointegration test: keep pairs this unlikely to be chance
    "hl_min": 2, "hl_max": 40,  # mean-reversion half-life, sessions
    "cost_bps": 10,       # cost per leg per trade side, in basis points (commission + spread)
}
TRADE_EUR = 1000
SAME_COMPANY = [{"GOOG", "GOOGL"}, {"BRK-A", "BRK-B"}]   # two share classes of one company, not a pair
HISTORY = "10y"


# ------------------------------------------------------------------ data
def load_universe() -> tuple[list[str], dict[str, str]]:
    names = json.loads(U.names_file.read_text())
    tickers = U.tickers()
    sectors = {t: names.get(t, ["", "Other"])[1] for t in tickers}
    return tickers, sectors


def _decode_lab(path: Path) -> pd.DataFrame:
    """Closes from a Strategy Lab price file (format in engine/backtest.py, lab_prices)."""
    d = json.loads(path.read_text())
    dates = pd.to_datetime(d["dates"])
    cols = {}
    for t in d["tickers"]:
        L = np.cumsum(np.asarray(t["c"], dtype=np.int64))
        miss = set(t.get("miss", []))
        ix = [i for i in range(t["s"], t["s"] + len(L) + len(miss)) if i not in miss][:len(L)]
        cols[t["t"]] = pd.Series(np.exp(L / 1e4), index=dates[ix])
    if "spy" in d:
        cols["SPY"] = pd.Series(np.exp(np.cumsum(np.asarray(d["spy"], dtype=np.int64)) / 1e4), index=dates)
    return pd.DataFrame(cols).sort_index()


def lab_ohl_ratios() -> dict[str, pd.DataFrame] | None:
    """Each day's open, high and low as a ratio of its close, from the Lab price files (the recent
    one wins on shared days), so the page can draw the pair's prices as candles."""
    data = U.data
    out = {k: {} for k in ("o", "h", "l")}
    for path in (data / "lab.json", data / "lab_recent.json"):
        if not path.exists():
            continue
        d = json.loads(path.read_text())
        dates = pd.to_datetime(d["dates"])
        for t in d["tickers"]:
            if not all(len(t.get(k) or []) == len(t["c"]) for k in out):
                continue
            miss = set(t.get("miss", []))
            ix = [i for i in range(t["s"], t["s"] + len(t["c"]) + len(miss)) if i not in miss][:len(t["c"])]
            for k in out:
                s = pd.Series(np.exp(np.asarray(t[k], dtype=float) / 1e4), index=dates[ix])
                old = out[k].get(t["t"])
                out[k][t["t"]] = s if old is None else s.combine_first(old)
    if not out["o"]:
        return None
    return {k: pd.DataFrame(v) for k, v in out.items()}


def fetch_lab() -> pd.DataFrame | None:
    """15 years from docs/data/lab.json (weekly), topped up with docs/data/lab_recent.json
    (rewritten after every daily scan). No download needed."""
    data = U.data
    if not (data / "lab.json").exists():
        return None
    long = _decode_lab(data / "lab.json")
    recent = data / "lab_recent.json"
    if recent.exists():
        new = _decode_lab(recent)
        new = new[new.index > long.index[-1]]
        long = pd.concat([long, new.reindex(columns=long.columns)])
    return long


def fetch_real(tickers: list[str]) -> pd.DataFrame:
    import yfinance as yf
    raw = yf.download(tickers + ["SPY"], period=HISTORY, interval="1d",
                      auto_adjust=True, progress=False, threads=True)["Close"]
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    return raw.dropna(how="all")


def fetch_demo(seed: int = 3) -> tuple[pd.DataFrame, dict[str, str]]:
    """Made-up market: a few sector factors, some pairs genuinely tied together."""
    rng = np.random.default_rng(seed)
    n = 252 * 6
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    mkt = np.cumsum(rng.normal(0.0004, 0.01, n))
    cols, sectors = {}, {}
    for s in range(4):
        sector = f"Sector {s + 1}"
        base = mkt + np.cumsum(rng.normal(0, 0.008, n))
        for k in range(5):
            t = f"S{s + 1}{chr(65 + k)}"
            if k < 2:   # first two of each sector share a mean-reverting link
                noise = np.zeros(n)
                for i in range(1, n):
                    noise[i] = 0.93 * noise[i - 1] + rng.normal(0, 0.008)
                lp = base * rng.uniform(0.7, 1.2) + noise
            else:
                lp = base + np.cumsum(rng.normal(0, 0.012, n))
            cols[t] = 50 * np.exp(lp)
            sectors[t] = sector
    cols["SPY"] = 400 * np.exp(mkt)
    return pd.DataFrame(cols, index=idx), sectors


# ------------------------------------------------------------------ stats
def hedge(la: np.ndarray, lb: np.ndarray) -> tuple[float, float]:
    """log A = alpha + beta * log B, by least squares."""
    res = sm.OLS(la, sm.add_constant(lb)).fit()
    return float(res.params[0]), float(res.params[1])


def half_life(spread: np.ndarray) -> float:
    lag, delta = spread[:-1], np.diff(spread)
    lam = sm.OLS(delta, sm.add_constant(lag)).fit().params[1]
    return float(-np.log(2) / lam) if lam < 0 else float("inf")


def select_pairs(logp: pd.DataFrame, sectors: dict[str, str], rules=RULES) -> list[dict]:
    """Pick the best cointegrated same-sector pairs on this window (no future data)."""
    cands = []
    by_sector: dict[str, list[str]] = {}
    for t in logp.columns:
        if t != "SPY" and logp[t].notna().all():
            by_sector.setdefault(sectors.get(t, "Other"), []).append(t)
    for sector, ts in by_sector.items():
        for a, b in itertools.combinations(sorted(ts), 2):
            if {a, b} in SAME_COMPANY:
                continue
            la, lb = logp[a].values, logp[b].values
            if np.corrcoef(np.diff(la), np.diff(lb))[0, 1] < 0.5:
                continue   # cheap pre-filter: pairs that don't even move together day to day
            p = float(coint(la, lb)[1])
            if p >= rules["p_max"]:
                continue
            alpha, beta = hedge(la, lb)
            if not 0.2 < beta < 5:
                continue
            hl = half_life(la - beta * lb)
            if not rules["hl_min"] <= hl <= rules["hl_max"]:
                continue
            cands.append({"a": a, "b": b, "sector": sector, "p": p, "alpha": alpha,
                          "beta": beta, "half_life": hl})
    cands.sort(key=lambda c: c["p"])
    picked, used = [], {}
    for c in cands:
        if used.get(c["a"], 0) >= rules["max_per_stock"] or used.get(c["b"], 0) >= rules["max_per_stock"]:
            continue
        picked.append(c)
        used[c["a"]] = used.get(c["a"], 0) + 1
        used[c["b"]] = used.get(c["b"], 0) + 1
        if len(picked) >= rules["max_pairs"]:
            break
    return picked


def zscore(spread: pd.Series, window: int) -> pd.Series:
    m = spread.rolling(window).mean()
    s = spread.rolling(window).std()
    return (spread - m) / s


def simulate(z: pd.Series, ra: pd.Series, rb: pd.Series, beta: float, rules=RULES,
             force_close_at_end: bool = True) -> tuple[pd.Series, list[dict]]:
    """Daily returns of one pair (as a fraction of the money in it) and its trades.
    Acts on the previous session's z-score, so there's no peeking.
    Position +1 = long A / short B, -1 = short A / long B; legs sized 1/(1+β) and β/(1+β)."""
    wa, wb = 1 / (1 + abs(beta)), abs(beta) / (1 + abs(beta))
    cost = rules["cost_bps"] / 1e4
    idx = z.index
    pnl = np.zeros(len(idx))
    trades, state, held, entry_i, acc = [], 0, 0, None, 0.0
    zv, rav, rbv = z.values, ra.values, rb.values
    for i in range(1, len(idx)):
        if state:
            day = state * (wa * rav[i] - wb * rbv[i])
            pnl[i] += day
            acc = (1 + acc) * (1 + day) - 1
            held += 1
        zp = zv[i - 1]
        last = i == len(idx) - 1
        if state:
            why = None
            if abs(zv[i]) < rules["exit_z"]:
                why = "back to normal"
            elif abs(zv[i]) > rules["stop_z"]:
                why = "stop"
            elif held >= rules["max_days"]:
                why = "time"
            elif last and force_close_at_end:
                why = "period end"
            if why:
                pnl[i] -= 2 * cost
                acc -= 2 * cost
                trades.append({"open": str(idx[entry_i].date()), "close": str(idx[i].date()),
                               "side": "long A / short B" if state > 0 else "short A / long B",
                               "days": held, "ret": round(acc, 5), "why": why})
                state = 0
        elif not np.isnan(zp) and not last:
            if zp > rules["entry_z"] and zp < rules["stop_z"]:
                state = -1
            elif zp < -rules["entry_z"] and zp > -rules["stop_z"]:
                state = 1
            if state:
                # signal seen at yesterday's close, filled at today's close: one session late on purpose
                pnl[i] -= 2 * cost   # both legs opened
                held, entry_i, acc = 0, i, -2 * cost
    return pd.Series(pnl, index=idx), trades


def perf(daily: pd.Series) -> dict:
    eq = (1 + daily).cumprod()
    years = len(daily) / 252
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else 0
    sd = daily.std()
    return {
        "total": round(float(eq.iloc[-1] - 1), 4),
        "cagr": round(float(cagr), 4),
        "sharpe": round(float(daily.mean() / sd * np.sqrt(252)), 2) if sd > 0 else 0,
        "max_dd": round(float((eq / eq.cummax() - 1).min()), 4),
        "vol": round(float(sd * np.sqrt(252)), 4),
    }


# ------------------------------------------------------------------ walk-forward
def walk_forward(prices: pd.DataFrame, sectors: dict[str, str], rules=RULES) -> dict:
    logp = np.log(prices)
    rets = logp.diff()
    F, T, W = rules["formation_days"], rules["trading_days"], rules["z_window"]
    days = logp.index
    port = pd.Series(0.0, index=days)
    in_test = pd.Series(False, index=days)
    all_trades, periods = [], []
    start = F
    while start + 20 < len(days):
        form = logp.iloc[start - F:start].dropna(axis=1)
        picks = select_pairs(form, sectors, rules)
        end = min(start + T, len(days))
        seg = days[start:end]
        in_test.loc[seg] = True
        period_ret = []
        for c in picks:
            a, b = c["a"], c["b"]
            spread = (logp[a] - c["beta"] * logp[b]).iloc[start - W:end]
            z = zscore(spread, W).loc[seg]
            ra = np.expm1(rets[a]).loc[seg].fillna(0)
            rb = np.expm1(rets[b]).loc[seg].fillna(0)
            daily, trades = simulate(z, ra, rb, c["beta"], rules)
            port.loc[seg] += daily / max(len(picks), 1)   # equal money in each pair, idle cash earns 0
            for t in trades:
                t.update({"a": a, "b": b})
            all_trades += trades
            period_ret.append(float((1 + daily).prod() - 1))
        periods.append({"from": str(seg[0].date()), "to": str(seg[-1].date()),
                        "pairs": [f"{c['a']}/{c['b']}" for c in picks],
                        "ret": round(float(np.mean(period_ret)) if period_ret else 0.0, 4)})
        start = end
    port = port[in_test]
    spy = np.expm1(rets["SPY"]).reindex(port.index).fillna(0)
    wins = [t for t in all_trades if t["ret"] > 0]

    def curve(r: pd.Series) -> list:
        eq = 100000 * (1 + r).cumprod()
        step = max(1, len(eq) // 260)          # about weekly points: keeps the file small
        pts = eq.iloc[::step]
        if pts.index[-1] != eq.index[-1]:
            pts = pd.concat([pts, eq.iloc[-1:]])
        return [[str(d.date()), round(float(v))] for d, v in pts.items()]

    exits = pd.Series([t["why"] for t in all_trades]).value_counts().to_dict() if all_trades else {}
    return {
        "from": str(port.index[0].date()), "to": str(port.index[-1].date()),
        "strategy": perf(port), "spy": perf(spy),
        "corr_spy": round(float(port.corr(spy)), 2),
        "trades": len(all_trades),
        "win_rate": round(len(wins) / len(all_trades), 3) if all_trades else 0,
        "avg_trade": round(float(np.mean([t["ret"] for t in all_trades])), 4) if all_trades else 0,
        "avg_days": round(float(np.mean([t["days"] for t in all_trades])), 1) if all_trades else 0,
        "exits": exits,
        "periods": periods,
        "curve": curve(port), "spy_curve": curve(spy),
        # the Strategy Board's tiles (engine/board_series.py); older history is weekly prices, so weekly steps
        "board": BS.pack([[str(d.date()), float(v)] for d, v in port.items() if d >= pd.Timestamp("2020-12-01")]),
        "recent_trades": sorted(all_trades, key=lambda t: t["close"])[-40:][::-1],
    }


# ------------------------------------------------------------------ today
def signal_for(z_now: float, rules=RULES) -> str:
    if abs(z_now) > rules["stop_z"]:
        return "stretched"          # past the stop: don't open, the link may be breaking
    if z_now > rules["entry_z"]:
        return "short_a"
    if z_now < -rules["entry_z"]:
        return "long_a"
    if abs(z_now) > rules["entry_z"] * 0.75:
        return "near"
    return "none"


def today(prices: pd.DataFrame, sectors: dict[str, str], rules=RULES, ohl: dict | None = None) -> list[dict]:
    logp = np.log(prices)
    F, W = rules["formation_days"], rules["z_window"]
    form = logp.iloc[-F:].dropna(axis=1)
    picks = select_pairs(form, sectors, rules)
    out = []
    for c in picks:
        a, b = c["a"], c["b"]
        spread = logp[a] - c["beta"] * logp[b]
        z = zscore(spread, W)
        tail = prices[[a, b]].iloc[-F:]
        z_now = float(z.iloc[-1])
        out.append({
            **{k: c[k] for k in ("a", "b", "sector")},
            "p": round(c["p"], 4), "beta": round(c["beta"], 3), "alpha": round(c["alpha"], 4),
            "half_life": round(c["half_life"], 1),
            "z": round(z_now, 2), "signal": signal_for(z_now, rules),
            "dates": [str(d.date()) for d in tail.index],
            "pa": [round(float(x), 2) for x in tail[a].values],
            "pb": [round(float(x), 2) for x in tail[b].values],
            **candles(tail, a, b, ohl),
        })
    return out


# ------------------------------------------------------------------ paper trades
def candles(tail: pd.DataFrame, a: str, b: str, ohl: dict | None) -> dict:
    """Opens, highs and lows for both legs, same units as pa/pb ("oa", "ha", "la", "ob", ...).
    A day with no Lab bar gets a flat candle at its close."""
    if not ohl or any(t not in ohl["o"] for t in (a, b)):
        return {}
    out = {}
    for t, s in ((a, "a"), (b, "b")):
        for k in ("o", "h", "l"):
            r = ohl[k][t].reindex(tail.index).fillna(1.0)
            out[k + s] = [round(float(x), 2) for x in (tail[t] * r).values]
    return out


def update_live(prices: pd.DataFrame, pairs: list[dict], rules=RULES) -> dict:
    """Open/close paper trades on the latest session. Closed trades stay exactly as written."""
    path = OUT / "live.json"
    live = json.loads(path.read_text()) if path.exists() else {
        "since": str(prices.index[-1].date()), "trade_eur": TRADE_EUR, "open": [], "closed": []}
    last_day = str(prices.index[-1].date())
    if live.get("last_day") == last_day:
        return live
    logp = np.log(prices)
    cost = rules["cost_bps"] / 1e4
    still_open = []
    for t in live["open"]:
        a, b = t["a"], t["b"]
        z = zscore(logp[a] - t["beta"] * logp[b], rules["z_window"])
        z_now = float(z.iloc[-1])
        pa, pb = float(prices[a].iloc[-1]), float(prices[b].iloc[-1])
        wa, wb = 1 / (1 + abs(t["beta"])), abs(t["beta"]) / (1 + abs(t["beta"]))
        gross = t["dir"] * (wa * (pa / t["pa0"] - 1) - wb * (pb / t["pb0"] - 1))
        held = int(((prices.index > pd.Timestamp(t["opened"])).sum()))
        t.update({"z": round(z_now, 2), "pa": round(pa, 2), "pb": round(pb, 2), "days": held,
                  "pnl": round(TRADE_EUR * (gross - 2 * cost), 2)})
        why = ("back to normal" if abs(z_now) < rules["exit_z"] else
               "stop" if abs(z_now) > rules["stop_z"] else
               "time" if held >= rules["max_days"] else None)
        if why:
            t.update({"closed": last_day, "why": why, "pnl": round(TRADE_EUR * (gross - 4 * cost), 2)})
            live["closed"].append(t)
        else:
            still_open.append(t)
    live["open"] = still_open
    busy = {(t["a"], t["b"]) for t in still_open}
    for p in pairs:
        if p["signal"] not in ("long_a", "short_a") or (p["a"], p["b"]) in busy:
            continue
        live["open"].append({
            "a": p["a"], "b": p["b"], "beta": p["beta"], "dir": 1 if p["signal"] == "long_a" else -1,
            "opened": last_day, "z0": p["z"], "z": p["z"],
            "pa0": p["pa"][-1], "pb0": p["pb"][-1], "pa": p["pa"][-1], "pb": p["pb"][-1],
            "days": 0, "pnl": round(-TRADE_EUR * 2 * cost, 2)})
    live["last_day"] = last_day
    closed = live["closed"]
    live["summary"] = {
        "closed": len(closed), "open": len(live["open"]),
        "pnl_closed": round(sum(t["pnl"] for t in closed), 2),
        "pnl_open": round(sum(t["pnl"] for t in live["open"]), 2),
        "win_rate": round(sum(t["pnl"] > 0 for t in closed) / len(closed), 3) if closed else None,
    }
    return live


# ------------------------------------------------------------------ settings grid
GRID = {"formation_days": [252, 504], "trading_days": [63, 126], "entry_z": [1.5, 2.0, 2.5]}


def _grid_one(args):
    prices, sectors, cfg = args
    wf = walk_forward(prices, sectors, {**RULES, **cfg})
    s = wf["strategy"]
    return {"formation": cfg["formation_days"], "trading": cfg["trading_days"], "entry": cfg["entry_z"],
            "cagr": s["cagr"], "sharpe": s["sharpe"], "max_dd": s["max_dd"], "trades": wf["trades"],
            "win": wf["win_rate"], "corr": wf["corr_spy"]}


def run_grid(prices: pd.DataFrame, sectors: dict[str, str]) -> dict:
    """The same walk-forward with other settings, so one lucky setting can't hide (weekly)."""
    from multiprocessing import Pool
    keys = list(GRID)
    cfgs = [dict(zip(keys, v)) for v in itertools.product(*(GRID[k] for k in keys))]
    with Pool() as pool:
        res = pool.map(_grid_one, [(prices, sectors, c) for c in cfgs])
    return {"updated": str(date.today()), "results": res}


# ------------------------------------------------------------------ main
def main(demo: bool = False, grid: bool = False) -> None:
    if demo:
        prices, sectors = fetch_demo()
    else:
        tickers, sectors = load_universe()
        prices = fetch_lab()
        if prices is None or "SPY" not in prices:
            prices = fetch_real(tickers)
        prices = prices[[t for t in prices.columns if t in tickers or t == "SPY"]]
    # keep stocks with a clean full history for the latest window; older gaps are handled per period
    prices = prices.ffill(limit=3)
    OUT.mkdir(parents=True, exist_ok=True)

    wf = walk_forward(prices, sectors)
    pairs = today(prices, sectors, ohl=None if demo else lab_ohl_ratios())
    data = {
        "updated": str(date.today()), "last_day": str(prices.index[-1].date()),
        "demo": demo, "rules": RULES, "pairs": pairs, "walk_forward": wf,
    }
    (OUT / "pairs.json").write_text(json.dumps(data, separators=(",", ":")))
    if grid:
        (OUT / "grid.json").write_text(json.dumps(run_grid(prices, sectors), indent=1))
    if not demo:
        (OUT / "live.json").write_text(json.dumps(update_live(prices, pairs), indent=1))

    s, b = wf["strategy"], wf["spy"]
    print(f"Walk-forward {wf['from']} → {wf['to']}: pairs {s['cagr']:.1%}/yr, Sharpe {s['sharpe']}, "
          f"max DD {s['max_dd']:.1%} | SPY {b['cagr']:.1%}/yr, Sharpe {b['sharpe']}, max DD {b['max_dd']:.1%} | "
          f"{wf['trades']} trades, win rate {wf['win_rate']:.0%}")
    for p in pairs:
        print(f"  {p['a']:>5}/{p['b']:<5} {p['sector']:<24} p={p['p']:.3f} β={p['beta']:.2f} "
              f"HL={p['half_life']:>4}d z={p['z']:+.2f} {p['signal']}")


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore", message=".*colinear.*")
    main(demo="--demo" in sys.argv, grid="--grid" in sys.argv)
