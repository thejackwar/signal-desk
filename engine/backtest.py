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
import scan  # noqa: E402
from scan import MARKETS, fetch_demo, fetch_real, load_tickers  # noqa: E402
from strategies import BENCHMARK, DIP_HOLD, DIP_TP, REGISTRY, for_market  # noqa: E402
from bot_strategies import BOT_MONEY, BOT_RULES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "backtest.json"
HORIZONS = (5, 20, 60)
HOLD_DAYS = 20
TREND = {"ma200", "golden_cross", "donchian", "supertrend", "relative_strength"}   # hold until the opposite alert
# strategies with their own exit: a take profit (checked on each day's high, like the Strategy Lab's
# riskHit) and a time limit instead of HOLD_DAYS
EXITS = {"buy_the_dip": {"tp": DIP_TP, "hold": DIP_HOLD}}
START_CAPITAL = 10_000
TRADE_SIZE = 1_000      # "take every trade": each alert is a EUR 1,000 position
BENCH = "SPY"           # buy-and-hold comparison on the strategies page
BENCH_BY_MARKET = {"crypto": "BTC-USD"}   # crypto is measured against simply holding bitcoin
ACCOUNT = 100_000       # strategies page: each strategy run as a EUR 100,000 account vs SPY
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
                     "n": int(len(g)), "wins": int((g["pnl"] > 0).sum()), "win_rate": pct((g["pnl"] > 0).mean())}
                    for y, g in yr.groupby("y")]
    recent = closed.sort_values("out").tail(10).iloc[::-1]
    out["recent"] = [{"ticker": r["t"], "side": "long" if r["side"] > 0 else "short",
                      "in": f"{r['in']:%Y-%m-%d}", "out": f"{r['out']:%Y-%m-%d}", "days": int(r["days"]),
                      "ret": pct(r["ret"]), "pnl": round(float(r["ret"] * TRADE_SIZE))}
                     for r in recent.to_dict("records")]
    return out


def tp_fill(side: int, entry: float, high: float, low: float, tp: float):
    """Take-profit fill on one daily bar, same rule as riskHit() in docs/lab.js: a level touched
    inside the bar fills at the level; a bar entirely past it (a gap) fills at the middle of its range."""
    lv = entry * (1 + side * tp)
    if (high >= lv) if side > 0 else (low <= lv):
        return (high + low) / 2 if ((low > lv) if side > 0 else (high < lv)) else lv
    return None


def own_exit_positions(sig, c, hi, lo, hold: int, tp: float):
    """Long positions for a buy-only strategy with its own exit (EXITS), same loop as the long side of
    run() in docs/lab.js: buy at an alert's close, sell at the take profit (inside the day) or at the
    close `hold` sessions later. Alerts while a position is open are ignored, so the time limit
    counts from the buy. Returns the held flags, the alerts that opened a trade, and the
    take-profit days with their fill prices."""
    n = len(c)
    held, taken, fills = np.zeros(n), [], {}
    exit_at, entry = -1, 0.0
    for i in range(n):
        hit = False
        if i > 0 and held[i - 1]:
            f = tp_fill(1, entry, hi[i], lo[i], tp)
            if f is not None:
                exit_at, hit = -1, True
                fills[i] = f
        new = sig[i] == 1 and not (i > 0 and held[i - 1] and not hit and i < exit_at)
        if new:
            exit_at = i + hold
            taken.append(i)
        if i < exit_at:
            held[i] = 1
            if new or hit or i == 0 or not held[i - 1]:
                entry = c[i]
    return held, set(taken), fills


def weekly_account(pos: pd.DataFrame, rets: pd.DataFrame, weeks: pd.Series, held: pd.DataFrame | None = None) -> list[int]:
    """A EUR ACCOUNT account that follows the alerts: the money is split equally across every
    open position (+1 long, -1 short) and rebalanced daily; with nothing open it sits in cash at 0%.
    Value at the last session of every week. No costs, taxes, borrow fees or slippage.
    held: the position on each day, already aligned with that day's return (strategies with their
    own trades pass it, with rets = what each trade earned that day; pos is then ignored)."""
    if held is None:
        held = pos.shift(1).fillna(0).where(rets.notna(), 0)
    k = (held != 0).sum(axis=1)
    daily = ((rets.fillna(0) * held).sum(axis=1) / k.replace(0, np.nan)).fillna(0)
    eq = ACCOUNT * (1 + daily).cumprod()
    return [round(float(v)) for v in eq.reindex(weeks.values)]


def weekly_turnover(pos: pd.DataFrame, rets: pd.DataFrame, weeks: pd.Series, held: pd.DataFrame | None = None) -> list[float]:
    """How much of the weekly_account() account was bought or sold each week, as a fraction of it:
    opening or closing a position trades its share (1 / positions held), a flip from short to long
    trades it twice. Daily rebalancing between open positions isn't counted (a real account
    wouldn't do it). The app uses this to charge broker fees in its "after costs" view."""
    if held is None:
        held = pos.shift(1).fillna(0).where(rets.notna(), 0)
    k = (held != 0).sum(axis=1)
    denom = pd.concat([k, k.shift(1).fillna(0)], axis=1).max(axis=1).replace(0, np.nan)
    traded = ((held - held.shift(1).fillna(0)).abs().sum(axis=1) / denom).fillna(0)
    wk = traded.resample("W-FRI").sum().reindex(weeks.index, fill_value=0)
    return [round(float(x), 3) for x in wk]


def own_trade_days(t: dict, c: np.ndarray, n: int):
    """The days a bot strategy's trade is held and what it earns on each (bot_strategies.py trades:
    in at an open or a close, out at a stop, a close or an open). Returns (days, returns)."""
    a, pa = t["in"], t["in_px"]
    b = t["out"] if t.get("out") is not None else n - 1
    pb = t["out_px"] if t.get("out") is not None else c[n - 1]
    days, rets = [], []
    for d in range(a, b + 1):
        start = pa if d == a else c[d - 1]
        end = pb if d == b else c[d]
        days.append(d)
        rets.append(t["side"] * (end / start - 1))
    return days, rets


def bot_account(trades: list[dict], closes: pd.DataFrame, weeks: pd.Series, money: dict) -> list[int]:
    """A EUR ACCOUNT account run with the trading bot's own money rules (BOT_MONEY) on the strategy's
    trades: entries at their fill price, sized by risk (equity x risk / distance to the stop), at most
    `cap` of equity per position and 100% invested, at most `max_pos` positions and `max_new` new
    trades a day (lower rank first, then random, like the bot); risk None = everything in one trade.
    A trade that doesn't fit is skipped. Exits at the trade's own exit price. Weekly values."""
    C = closes.ffill().values
    cols = {t: k for k, t in enumerate(closes.columns)}
    n = len(closes)
    rng = np.random.default_rng(0)
    by_in = {}
    for tr in trades:
        by_in.setdefault(tr["g_in"], []).append(tr)
    cash, pos, curve = float(ACCOUNT), {}, np.full(n, np.nan)
    eq_prev = float(ACCOUNT)

    def val(p, px):
        return p["qty"] * (p["in_px"] + p["side"] * (px - p["in_px"]))
    for t in range(n):
        todays = by_in.get(t, [])
        if todays:
            order = sorted(todays, key=lambda x: (x["rank"], rng.random()))
            placed = 0
            for tr in order:
                if tr["t"] in pos or len(pos) >= money["max_pos"] or placed >= money["max_new"]:
                    continue
                invested = sum(val(p, C[t - 1, cols[p["t"]]] if t else p["in_px"]) for p in pos.values())
                room = eq_prev - invested
                size = min(eq_prev * money["cap"], room)
                if money["risk"] is not None:
                    dist = (tr["in_px"] - tr["stop"]) / tr["in_px"] if tr.get("stop") is not None else None
                    if not dist or dist <= 0:
                        continue
                    size = min(size, eq_prev * money["risk"] / dist)
                if size < 1:
                    continue
                pos[tr["t"]] = {**tr, "qty": size / tr["in_px"]}
                cash -= size
                placed += 1
        for k in [k for k, p in pos.items() if p["g_out"] == t]:
            p = pos.pop(k)
            cash += val(p, p["out_px"])
        curve[t] = cash + sum(val(p, C[t, cols[p["t"]]]) for p in pos.values())
        eq_prev = curve[t]
    eq = pd.Series(curve, index=closes.index)
    return [round(float(v)) for v in eq.reindex(weeks.values)]


def acct_stats(vals: list[int], dates) -> dict:
    """Per year, worst drop and final value of a weekly account."""
    s = pd.Series(vals, index=pd.DatetimeIndex(dates), dtype=float).dropna()
    return {"cagr": pct(cagr(s)), "maxdd": pct(max_drawdown(s)), "final": round(float(s.iloc[-1]))}


# What the Strategy Lab can't recompute in the browser, saved from this run for lab.json ("pre"):
#   chart patterns: their alerts per ticker, as signed (row + 1): +12 = bullish on row 11
#   trading-bot strategies: their trades per ticker, 7 numbers each:
#     alert row, entry row, entry price vs that row's close (log, basis points),
#     exit row (-1 still open), exit price vs that row's close (same), side, why (0 rule, 1 target, 2 stop)
LAB_SIGNALS = {"double_top_bottom", "head_shoulders", "triangle"}
LAB_PRE: dict[str, dict[str, list[int]]] = {}


def lab_trades(trades: list[dict], c: np.ndarray) -> list[int]:
    out = []
    bp = lambda px, i: int(round(np.log(px / c[i]) * 1e4))
    for tr in trades:
        if tr.get("in") is None:
            continue                          # alerted on the last close, bought tomorrow
        o = tr.get("out")
        why = str(tr.get("why") or "")
        code = 1 if "target" in why else 2 if "stop" in why else 0
        out += [int(tr["sig"]), int(tr["in"]), bp(tr["in_px"], tr["in"]),
                int(o) if o is not None else -1, bp(tr["out_px"], o) if o is not None else 0, int(tr["side"]), code]
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

    weeks = closes.index.to_series().resample("W-FRI").last().dropna()   # last session of each week
    out = {"_weeks": weeks}
    for sid, fn in REGISTRY.items():
        if not for_market(sid, scan.MARKET):
            continue
        print(f"  {sid}...", flush=True)
        rows, trades = [], []
        pos = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
        pos_ls = pos.copy()
        own = None   # strategies with their own trades: held flags and each day's return, long / long + short
        own_list = []   # ... and their trades on the shared calendar, for the bot-money-rules account
        ex = EXITS.get(sid)
        hold = ex["hold"] if ex else HOLD_DAYS
        tp = ex["tp"] if ex else 0
        srets = rets.copy() if tp else rets   # a take-profit day earns up to the fill, not the close
        for t, df in data.items():
            try:
                res = fn(df)
            except Exception as e:
                print(f"    ! {t}: {e}")
                continue
            c = df["close"].values
            hi, lo = df["high"].values, df["low"].values
            sig = res.signals.values
            n = len(df)
            idx = df.index
            if res.trades is not None:
                LAB_PRE.setdefault(sid, {})[t] = lab_trades(res.trades, c)
            elif sid in LAB_SIGNALS:
                LAB_PRE.setdefault(sid, {})[t] = [(int(i) + 1) * int(sig[i]) for i in np.flatnonzero(sig)]
            if res.trades is not None:
                if own is None:
                    own = {k: pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
                           for k in ("held_l", "ret_l", "held_a", "ret_a")}
                hl, rl, ha, ra = (np.zeros(n) for _ in range(4))
                g = closes.index.get_indexer(idx)
                for tr in res.trades:
                    if tr.get("in") is None:
                        continue                      # alerted on the last close, bought tomorrow
                    own_list.append({"t": t, "side": tr["side"], "g_in": int(g[tr["in"]]), "in_px": float(tr["in_px"]),
                                     "g_out": int(g[tr["out"]]) if tr.get("out") is not None else -1,
                                     "out_px": float(tr["out_px"]) if tr.get("out") is not None else None,
                                     "stop": tr.get("stop0"),
                                     "rank": tr.get("rank", 0 if t in ("BTC-USD",) else 1)})
                    closed = tr.get("out") is not None
                    j = tr["out"] if closed else n - 1
                    px = tr["out_px"] if closed else c[n - 1]
                    if j > tr["in"] or closed:
                        trades.append({"t": t, "side": tr["side"], "in": idx[tr["in"]], "out": idx[j],
                                       "days": max(1, j - tr["in"]), "ret": tr["side"] * (px / tr["in_px"] - 1),
                                       "closed": bool(closed)})
                    days, rr = own_trade_days(tr, c, n)
                    for d, r in zip(days, rr):
                        ha[d], ra[d] = tr["side"], ra[d] + r
                        if tr["side"] > 0:
                            hl[d], rl[d] = 1, rl[d] + r
                for k, v in (("held_l", hl), ("ret_l", rl), ("held_a", ha), ("ret_a", ra)):
                    own[k].loc[idx, t] = v
                for i in np.flatnonzero(sig):
                    row = {"t": t, "d": idx[i], "side": int(sig[i])}
                    for h in HORIZONS:
                        row[h] = c[i + h] / c[i] - 1 if i + h < n else np.nan
                    rows.append(row)
                continue
            if tp:
                held_own, taken, fills = own_exit_positions(sig, c, hi, lo, hold, tp)
            for i in np.flatnonzero(sig):
                side = int(sig[i])
                # every alert is a trade: in at the alert's close, out on the opposite alert
                # (trend strategies) or after HOLD_DAYS sessions / an opposite alert (the rest)
                opp = np.flatnonzero(sig[i + 1:] == -side)
                j = i + 1 + int(opp[0]) if len(opp) else n - 1
                if sid not in TREND:
                    j = min(j, i + hold)
                closed = j < n - 1 or (sid not in TREND and i + hold <= n - 1) or len(opp) > 0
                j = min(j, n - 1)
                px = c[j]
                if tp and i in taken:
                    for q in range(i + 1, j + 1):
                        f = tp_fill(side, c[i], hi[q], lo[q], tp)
                        if f is not None:
                            j, px, closed = q, f, True
                            break
                if j > i and not (tp and i not in taken):
                    trades.append({"t": t, "side": side, "in": idx[i], "out": idx[j], "days": j - i,
                                   "ret": side * (px / c[i] - 1), "closed": bool(closed)})
                row = {"t": t, "d": df.index[i], "side": int(sig[i])}
                for h in HORIZONS:
                    row[h] = c[i + h] / c[i] - 1 if i + h < n else np.nan
                rows.append(row)
            # positions for the equity curve (long-only, bullish alerts)
            if tp:
                held = held_own
                for i, f in fills.items():   # a take-profit day earns up to the fill, not the close
                    if np.isfinite(srets.at[idx[i], t]):
                        srets.at[idx[i], t] = f / c[i - 1] - 1
            else:
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
            # same, but a bearish alert also opens a short (the account view's "long + short")
            held_ls = np.zeros(n)
            side, exit_at = 0, -1
            for i in range(n):
                if sig[i] != 0:
                    side, exit_at = int(sig[i]), (n if sid in TREND else i + hold)
                if i < exit_at:
                    held_ls[i] = side
            if tp:
                held_ls = held   # strategies with their own exit only buy, so long + short is the long account
            pos_ls.loc[df.index, t] = held_ls

        sig_df = pd.DataFrame(rows)
        result = {"rule": BOT_RULES[sid] if own is not None else (f"Buy on an alert, sell at +{tp * 100:.0f}% or after {hold} sessions, whichever comes first"
                           if tp else
                           "Buy on a bullish alert, sell on the next bearish alert"
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

        if own is not None:
            # each held position earns what its trade made that day (fills at opens and stops included)
            held_prev = own["held_l"]
            k = held_prev.sum(axis=1)
            strat_daily = (own["ret_l"].sum(axis=1) / k.replace(0, np.nan)).fillna(0)
        else:
            held_prev = pos.shift(1).fillna(0)
            held_prev = held_prev.where(rets.notna(), 0)
            k = held_prev.sum(axis=1)
            strat_daily = ((srets.fillna(0) * held_prev).sum(axis=1) / k.replace(0, np.nan)).fillna(0)
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
        if own is not None:
            # held flags in absolute value for the split; the returns already carry the side
            # weekly_account multiplies each day's return by the side held (+1 / -1); the trade returns
            # already carry the side, so multiply them by it once more (side x side = 1)
            result["account"] = {"all": weekly_account(None, own["ret_a"] * own["held_a"], weeks, held=own["held_a"]),
                                 "long": weekly_account(None, own["ret_l"], weeks, held=own["held_l"])}
            result["turnover"] = {"all": weekly_turnover(None, None, weeks, held=own["held_a"]),
                                  "long": weekly_turnover(None, None, weeks, held=own["held_l"])}
            # the same trades with the trading bot's money rules, next to the equal split
            mine = [x for x in own_list if x["side"] > 0] if sid != "btc50_both" else own_list
            bot = bot_account(mine, closes, weeks, BOT_MONEY[sid])
            eq_key = "all" if sid == "btc50_both" else "long"
            result["bot_money"] = {"rules": BOT_MONEY[sid]["text"], "values": bot,
                                   "bot": acct_stats(bot, weeks.values),
                                   "equal": acct_stats(result["account"][eq_key], weeks.values)}
        else:
            result["account"] = {"all": weekly_account(pos_ls, srets, weeks), "long": weekly_account(pos, srets, weeks)}
            result["turnover"] = {"all": weekly_turnover(pos_ls, rets, weeks), "long": weekly_turnover(pos, rets, weeks)}
        result["trades"] = trade_summary(trades)
        out[sid] = result
    return out


def spy_close(demo: bool, bench: str = BENCH) -> pd.Series | None:
    if demo:
        return None
    try:
        return fetch_real([bench], period="max")[bench]["close"]
    except Exception as e:  # a missing benchmark must never sink the backtest
        print(f"  ! {bench}: {e}")
        return None


def spy_hold(weeks: pd.Series, c: pd.Series | None, bench: str = BENCH) -> dict | None:
    """A EUR ACCOUNT account that buys SPY and holds it, valued on the same weekly sessions as the
    strategy accounts (dividends reinvested: adjusted prices). Rebased to ACCOUNT at the first week."""
    if c is None:
        return None
    c = c.copy()
    if c.index.tz is not None:
        c.index = c.index.tz_localize(None)
    days = pd.DatetimeIndex(weeks.values)
    days = days.tz_localize(None) if days.tz is not None else days
    v = c.reindex(c.index.union(days)).ffill().reindex(days)   # crypto weeks can end on a weekend
    if v.isna().all():
        return None
    v = v.bfill()
    return {"ticker": bench, "account": [round(float(x)) for x in ACCOUNT * v / v.iloc[0]]}


def lab_prices(data: dict[str, pd.DataFrame], spy_c: pd.Series | None) -> dict:
    """Compact daily prices for the Strategy Lab, which reruns the indicator strategies in the
    browser with your own settings. Prices are stored as log-price steps in basis points
    (0.01%), so 15 years of the whole list stays small:
      close  -> first value round(ln(close) * 1e4), then the change from the day before
      high/low -> round((ln(high or low) - ln(close)) * 1e4) for the same day
    Each ticker runs on its own trading days; "s" is its first day in the shared calendar
    and "miss" lists shared-calendar days it has no price for (holidays, halts).
    Also: "v" volume per day as round(ln(volume) * v_scale), 0 = none (Volume breakout); "bench" and
    "bench_name" the series Relative strength compares against (SPY, Bitcoin or the commodity
    average), encoded like "spy"; "pre" the alerts and trades of the strategies the Lab can't
    recompute (LAB_PRE above)."""
    cal = pd.DatetimeIndex(sorted(set().union(*(df.index for df in data.values()))))
    pos = {d: i for i, d in enumerate(cal)}
    enc = lambda v: np.round(np.log(np.asarray(v, dtype=float)) * 1e4).astype(np.int64)
    out = {"dates": [f"{d:%Y-%m-%d}" for d in cal], "tickers": [], "v_scale": 1000}
    for t, df in data.items():
        ix = [pos[d] for d in df.index]
        s, e = ix[0], ix[-1]
        L = enc(df["close"].values)
        out["tickers"].append({
            "t": t, "s": s, "miss": sorted(set(range(s, e + 1)) - set(ix)),
            "c": [int(L[0])] + np.diff(L).tolist(),
            "h": (enc(df["high"].values) - L).tolist(),
            "l": (enc(df["low"].values) - L).tolist(),
            "v": [int(round(np.log(x) * 1000)) if x > 0 else 0 for x in df["volume"].fillna(0).astype(float).values]})
    if spy_c is not None:
        c = spy_c.copy()
        if c.index.tz is not None:
            c.index = c.index.tz_localize(None)
        days = cal.tz_localize(None) if cal.tz is not None else cal
        c = c.reindex(c.index.union(days)).ffill().reindex(days).bfill()
        if c.notna().all():
            L = enc(c.values)
            out["spy"] = [int(L[0])] + np.diff(L).tolist()
    b = BENCHMARK["close"]
    if b is not None:
        b = b.copy()
        if b.index.tz is not None:
            b.index = b.index.tz_localize(None)
        days = cal.tz_localize(None) if cal.tz is not None else cal
        b = b.reindex(b.index.union(days)).ffill().reindex(days).bfill()
        if b.notna().all():
            L = enc(b.values)
            out["bench"], out["bench_name"] = [int(L[0])] + np.diff(L).tolist(), BENCHMARK["name"]
    if LAB_PRE:
        out["pre"] = LAB_PRE
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--market", choices=list(MARKETS), default="stocks")
    args = ap.parse_args()
    scan.configure(args.market)
    out = scan.DATA / "backtest.json"
    data = fetch_demo(bars=3000, seed=11) if args.demo else fetch_real(load_tickers(), period="max" if args.market == "crypto" else "15y")
    data = {t: df for t, df in data.items() if len(df) >= 300}
    if not data:
        sys.exit("No price data downloaded; leaving the previous backtest untouched.")
    print(f"Backtesting {len(data)} tickers")
    scan.use_benchmark(data, args.demo, period="max" if args.market == "crypto" else "15y")
    strategies = run(data)
    weeks = strategies.pop("_weeks")
    start = min(df.index[0] for df in data.values())
    end = max(df.index[-1] for df in data.values())
    bench = BENCH_BY_MARKET.get(args.market, BENCH)
    spy_c = spy_close(args.demo, bench)
    spy = spy_hold(weeks, spy_c, bench)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "demo": args.demo,
        "market": args.market,
        "universe": "Demo tickers (simulated prices)" if args.demo else MARKETS[args.market]["bt_universe"],
        "tickers": len(data),
        "start": f"{start:%Y-%m-%d}", "end": f"{end:%Y-%m-%d}",
        "capital": START_CAPITAL,
        "trade_size": TRADE_SIZE,
        "account": {"size": ACCOUNT, "dates": [f"{d:%Y-%m-%d}" for d in weeks.values.astype("datetime64[D]").tolist()]},
        "strategies": strategies,
        "spy": spy,
    }, separators=(",", ":")))
    print(f"-> {out.relative_to(ROOT)} ({out.stat().st_size // 1024} KB)")
    lab = scan.DATA / "lab.json"
    lab.write_text(json.dumps(lab_prices(data, spy_c), separators=(",", ":")))
    print(f"-> {lab.relative_to(ROOT)} ({lab.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
