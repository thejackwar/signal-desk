"""
Backtest: how did each strategy's alerts play out in the past?

Runs every strategy over ~15 years of daily prices for the whole stock
list and writes docs/data/backtest.json, which the app shows on each
strategy page.

Two views per strategy:

1. Signal statistics. For every past alert: did the price move the
   expected way 5, 20 and 60 sessions later, and by how much? Compared
   with a "random day" baseline (the same stocks on any day), so you can
   see whether the signal adds anything.

2. Equity curve. A simple long-only portfolio that follows the bullish
   alerts, compared with holding all the stocks equally:
     - trend strategies hold until the strategy's own bearish alert
     - the others hold for 20 sessions (or until a bearish alert)
     - money is split equally across open positions; idle money earns 0%
   No trading costs, taxes or slippage.

  python engine/backtest.py            # real data (GitHub Actions)
  python engine/backtest.py --demo     # simulated prices, for testing

Informational only. Past results don't predict future ones.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from scan import fetch_demo, fetch_real, load_tickers  # noqa: E402
from strategies import REGISTRY  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "backtest.json"
HORIZONS = (5, 20, 60)
HOLD_DAYS = 20
TREND = {"ma200", "golden_cross", "donchian"}   # hold until the opposite alert
START_CAPITAL = 10_000
TRADE_SIZE = 1_000      # "take every trade": each alert is a EUR 1,000 position
BINS = np.arange(-0.20, 0.2001, 0.025)        # histogram of 20-day results


def pct(x):
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, 2)


def max_drawdown(eq: pd.Series) -> float:
    return float((eq / eq.cummax() - 1).min())


def cagr(eq: pd.Series) -> float:
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    return float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1) if years > 0 else 0.0


def trade_stats(t: pd.DataFrame) -> dict:
    if not len(t):
        return {"n": 0}
    pnl = t["ret"] * TRADE_SIZE
    w, l = pnl[pnl > 0], pnl[pnl <= 0]
    return {
        "n": int(len(t)), "wins": int(len(w)), "losses": int(len(l)),
        "win_rate": pct(len(w) / len(t)),
        "total": round(float(pnl.sum())),
        "won": round(float(w.sum())), "lost": round(float(l.sum())),
        "avg_win": round(float(w.mean()), 1) if len(w) else 0,
        "avg_loss": round(float(l.mean()), 1) if len(l) else 0,
        "avg": round(float(pnl.mean()), 1),
        "profit_factor": round(float(w.sum() / -l.sum()), 2) if l.sum() < 0 else None,
        "avg_days": round(float(t["days"].mean()), 1),
    }


def trade_summary(trades: list[dict]) -> dict:
    """Every alert taken as a EUR TRADE_SIZE trade: totals, a cumulative profit curve and recent trades."""
    df = pd.DataFrame(trades)
    if not len(df):
        return {"size": TRADE_SIZE, "all": {"n": 0}, "long": {"n": 0}, "short": {"n": 0}}
    closed = df[df["closed"]]
    out = {"size": TRADE_SIZE, "open": int((~df["closed"]).sum())}
    for key, part in (("all", closed), ("long", closed[closed["side"] > 0]), ("short", closed[closed["side"] < 0])):
        out[key] = trade_stats(part)
    # cumulative profit by exit week, for all / long / short
    c = closed.assign(pnl=closed["ret"] * TRADE_SIZE, wk=closed["out"].dt.to_period("W-FRI").dt.end_time.dt.normalize())
    weeks = pd.date_range(c["wk"].min(), c["wk"].max(), freq="W-FRI")
    curve = {"dates": [f"{d:%Y-%m-%d}" for d in weeks]}
    for key, part in (("all", c), ("long", c[c["side"] > 0]), ("short", c[c["side"] < 0])):
        wk = part.groupby("wk")["pnl"].sum().reindex(weeks, fill_value=0).cumsum()
        curve[key] = [round(float(v)) for v in wk]
    out["curve"] = curve
    yr = c.assign(y=c["out"].dt.year)
    out["years"] = [{"year": int(y), "all": round(float(g["pnl"].sum())),
                     "long": round(float(g.loc[g["side"] > 0, "pnl"].sum())),
                     "short": round(float(g.loc[g["side"] < 0, "pnl"].sum())),
                     "n": int(len(g)), "win_rate": pct((g["pnl"] > 0).mean())}
                    for y, g in yr.groupby("y")]
    recent = closed.sort_values("out").tail(10).iloc[::-1]
    out["recent"] = [{"ticker": r["t"], "side": "long" if r["side"] > 0 else "short",
                      "in": f"{r['in']:%Y-%m-%d}", "out": f"{r['out']:%Y-%m-%d}", "days": int(r["days"]),
                      "ret": pct(r["ret"]), "pnl": round(float(r["ret"] * TRADE_SIZE))}
                     for r in recent.to_dict("records")]
    return out


def run(data: dict[str, pd.DataFrame]) -> dict:
    closes = pd.DataFrame({t: df["close"] for t, df in data.items()}).sort_index()
    rets = closes.pct_change()
    bench_daily = rets.mean(axis=1, skipna=True).fillna(0)

    # baseline: what any random day in these stocks did
    base = {}
    for h in HORIZONS:
        fwd = (closes.shift(-h) / closes - 1).stack()
        base[h] = {"avg": fwd.mean(), "up": (fwd > 0).mean()}

    out = {}
    for sid, fn in REGISTRY.items():
        print(f"  {sid}...", flush=True)
        rows, trades = [], []
        pos = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
        for t, df in data.items():
            try:
                res = fn(df)
            except Exception as e:
                print(f"    ! {t}: {e}")
                continue
            c = df["close"].values
            sig = res.signals.values
            n = len(df)
            idx = df.index
            for i in np.flatnonzero(sig):
                side = int(sig[i])
                # every alert is a trade: in at the alert's close, out on the opposite alert
                # (trend strategies) or after HOLD_DAYS sessions / an opposite alert (the rest)
                opp = np.flatnonzero(sig[i + 1:] == -side)
                j = i + 1 + int(opp[0]) if len(opp) else n - 1
                if sid not in TREND:
                    j = min(j, i + HOLD_DAYS)
                closed = j < n - 1 or (sid not in TREND and i + HOLD_DAYS <= n - 1) or len(opp) > 0
                j = min(j, n - 1)
                if j > i:
                    trades.append({"t": t, "side": side, "in": idx[i], "out": idx[j], "days": j - i,
                                   "ret": side * (c[j] / c[i] - 1), "closed": bool(closed)})
                row = {"t": t, "d": df.index[i], "side": int(sig[i])}
                for h in HORIZONS:
                    row[h] = c[i + h] / c[i] - 1 if i + h < n else np.nan
                rows.append(row)
            # positions for the equity curve (long-only, bullish alerts)
            held = np.zeros(n)
            exit_at = -1
            for i in range(n):
                if sig[i] == -1:
                    exit_at = -1
                elif sig[i] == 1:
                    exit_at = n if sid in TREND else i + HOLD_DAYS
                if i < exit_at:
                    held[i] = 1  # held at this close -> earns the next day's return
            pos.loc[df.index, t] = held

        sig_df = pd.DataFrame(rows)
        result = {"rule": ("Buy on a bullish alert, sell on the next bearish alert"
                           if sid in TREND else
                           f"Buy on a bullish alert, sell after {HOLD_DAYS} sessions or on a bearish alert")}
        best, worst = [], []
        for side, label in ((1, "bullish"), (-1, "bearish")):
            s = sig_df[sig_df["side"] == side] if len(sig_df) else sig_df
            stats = {"n": int(len(s))}
            for h in HORIZONS:
                r = (s[h] * side).dropna() if len(s) else pd.Series(dtype=float)
                b_avg = base[h]["avg"] * side
                b_hit = base[h]["up"] if side > 0 else 1 - base[h]["up"]
                stats[str(h)] = {"hit": pct(r.gt(0).mean()) if len(r) else None,
                                 "avg": pct(r.mean()) if len(r) else None,
                                 "med": pct(r.median()) if len(r) else None,
                                 "base_hit": pct(b_hit), "base_avg": pct(b_avg), "n": int(len(r))}
            r20 = (s[20] * side).dropna() if len(s) else pd.Series(dtype=float)
            counts = np.histogram(np.clip(r20, BINS[0] + 1e-9, BINS[-1] - 1e-9), BINS)[0] if len(r20) else []
            stats["hist"] = {"edges": [round(x * 100, 1) for x in BINS], "counts": [int(x) for x in counts]}
            result[label] = stats
            if len(s):
                ranked = s.assign(r=s[20] * side).dropna(subset=["r"]).sort_values("r")
                fmt = lambda x: {"ticker": x["t"], "date": f"{x['d']:%Y-%m-%d}", "side": label, "ret": pct(x[20])}
                best += [fmt(x) for x in ranked.tail(3).to_dict("records")]
                worst += [fmt(x) for x in ranked.head(3).to_dict("records")]
        result["best"] = sorted(best, key=lambda x: -abs(x["ret"]))[:4]
        result["worst"] = sorted(worst, key=lambda x: abs(x["ret"]) * -1)[:4]

        held_prev = pos.shift(1).fillna(0)
        held_prev = held_prev.where(rets.notna(), 0)
        k = held_prev.sum(axis=1)
        strat_daily = ((rets.fillna(0) * held_prev).sum(axis=1) / k.replace(0, np.nan)).fillna(0)
        eq = START_CAPITAL * (1 + strat_daily).cumprod()
        bench = START_CAPITAL * (1 + bench_daily).cumprod()
        wk_eq, wk_b = eq.resample("W-FRI").last().dropna(), bench.resample("W-FRI").last().dropna()
        result["equity"] = {
            "dates": [f"{d:%Y-%m-%d}" for d in wk_eq.index],
            "strategy": [round(float(v)) for v in wk_eq],
            "benchmark": [round(float(v)) for v in wk_b.reindex(wk_eq.index).ffill()],
            "cagr": pct(cagr(eq)), "bench_cagr": pct(cagr(bench)),
            "maxdd": pct(max_drawdown(eq)), "bench_maxdd": pct(max_drawdown(bench)),
            "final": round(float(eq.iloc[-1])), "bench_final": round(float(bench.iloc[-1])),
            "invested": pct((k > 0).mean()),
            "avg_positions": round(float(k[k > 0].mean()), 1) if (k > 0).any() else 0,
        }
        result["trades"] = trade_summary(trades)
        out[sid] = result
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    data = fetch_demo(bars=3000, seed=11) if args.demo else fetch_real(load_tickers(), period="15y")
    data = {t: df for t, df in data.items() if len(df) >= 300}
    if not data:
        sys.exit("No price data downloaded; leaving the previous backtest untouched.")
    print(f"Backtesting {len(data)} tickers")
    strategies = run(data)
    start = min(df.index[0] for df in data.values())
    end = max(df.index[-1] for df in data.values())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "demo": args.demo,
        "universe": "Demo tickers (simulated prices)" if args.demo else "Today's S&P 100",
        "tickers": len(data),
        "start": f"{start:%Y-%m-%d}", "end": f"{end:%Y-%m-%d}",
        "capital": START_CAPITAL,
        "trade_size": TRADE_SIZE,
        "strategies": strategies,
    }, separators=(",", ":")))
    print(f"-> {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
