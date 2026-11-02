"""Fair value gap (FVG) study: BTC (1H, 4H, 1D) and S&P 100 stocks (1D).

Rules (defaults, all adjustable below):
  Detect   candles A, B, C. Bullish gap: low[C] > high[A]; zone = high[A] .. low[C].
  Filters  gap size >= MIN_GAP_ATR * ATR(14)
           B is a strong candle: range >= B_RANGE_MULT * average range of the 20 bars before it,
             and B closes in the top CLOSE_POS of its range
           trend: close[C] > EMA(200)                    (long only)
           volume (stocks only): volume[B] > 20-bar average
  Entry    limit order at the gap's near edge / 50% / far edge, valid EXPIRY bars after C
  Cancel   a bar closes below the gap's far edge before the order fills
  Stop     far edge - STOP_BUF_ATR * ATR
  Target   entry + TARGET_R * risk
  Limits   one position per symbol; when an order fills, other pending gaps are dropped
Fills: if a bar both reaches the stop and the target, the stop counts (conservative).
Stock data has no opens (lab.json), so gaps through a level fill at the level itself, except a
bar that opens beyond the stop is filled at that bar's midpoint as a stand-in for the open.
"""
from __future__ import annotations
import json, gzip, sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "results"

DEFAULT = dict(min_gap_atr=0.25, b_range_mult=1.5, close_pos=0.30, trend=True, volume=True,
               entry="mid", stop_at="gap", expiry=20, stop_buf_atr=0.1, target_r=2.0, max_hold=None)
COST = {"stocks": 0.001, "btc": 0.001}   # paid on each buy and each sell (commission + spread)


# ---------- data ----------
def load_btc() -> dict[str, pd.DataFrame]:
    df = pd.read_csv(ROOT / "research/btc-dip/data/btc_1h.csv.gz")
    df["time"] = pd.to_datetime(df["time"], utc=True, format="mixed")
    df = df.set_index("time").sort_index()
    df = df[~df.index.duplicated()]
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = {"1H": df}
    for tf, rule in [("4H", "4h"), ("1D", "1D")]:
        out[tf] = df.resample(rule).agg(agg).dropna()
    return out


def load_stocks() -> dict[str, pd.DataFrame]:
    d = json.loads((ROOT / "docs/data/lab.json").read_text())
    dates = pd.to_datetime(d["dates"])
    out = {}
    for T in d["tickers"]:
        s, miss = T["s"], set(T["miss"])
        n = len(T["c"])
        idx = [i for i in range(s, s + n + len(miss)) if i not in miss][:n]
        L = np.cumsum(np.array(T["c"], dtype=float))
        close = np.exp(L / 1e4)
        high = np.exp((L + np.array(T["h"], dtype=float)) / 1e4)
        low = np.exp((L + np.array(T["l"], dtype=float)) / 1e4)
        vol = np.array(T["v"], dtype=float)
        vol = np.where(vol > 0, np.exp(vol / d["v_scale"]), np.nan)
        out[T["t"]] = pd.DataFrame({"open": np.nan, "high": high, "low": low, "close": close,
                                    "volume": vol}, index=dates[idx])
    return out


def spy_close() -> pd.Series:
    d = json.loads((ROOT / "docs/data/lab.json").read_text())
    return pd.Series(np.exp(np.cumsum(np.array(d["spy"], dtype=float)) / 1e4),
                     index=pd.to_datetime(d["dates"]))


# ---------- strategy ----------
def find_trades(df: pd.DataFrame, p: dict, use_volume: bool) -> list[dict]:
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    v = df["volume"].to_numpy(float)
    n = len(c)
    tr = np.maximum(h - l, np.maximum(abs(h - np.roll(c, 1)), abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    rng = h - l
    avg_rng = pd.Series(rng).rolling(20).mean().shift(1).to_numpy()   # 20 bars before B
    ema = pd.Series(c).ewm(span=200, adjust=False).mean().to_numpy()
    avg_v = pd.Series(v).rolling(20).mean().shift(1).to_numpy()
    has_open = not np.isnan(o).all()

    trades, pending, pos = [], [], None
    start = 220
    for i in range(start, n):
        # 1) manage an open position on bar i
        if pos is not None:
            stop, tgt = pos["stop"], pos["target"]
            exit_px = why = None
            if l[i] <= stop:
                if has_open and o[i] < stop:
                    exit_px = o[i]
                elif h[i] < stop:                       # whole bar below the stop: gapped through
                    exit_px = (h[i] + l[i]) / 2 if not has_open else o[i]
                else:
                    exit_px = stop
                why = "stop"
            elif h[i] >= tgt:
                exit_px = max(tgt, o[i]) if has_open and o[i] > tgt else tgt
                why = "target"
            elif p["max_hold"] and i - pos["i_in"] >= p["max_hold"]:
                exit_px, why = c[i], "time"
            if exit_px is not None:
                trades.append({**pos, "i_out": i, "t_out": df.index[i], "exit": exit_px, "why": why})
                pos = None
            continue_after = pos is not None
        else:
            continue_after = False

        # 2) pending limit orders (only when flat)
        if pos is None and pending:
            still = []
            filled = None
            for g in pending:
                if i > g["exp"]:
                    continue
                if filled is None and l[i] <= g["entry"]:
                    if has_open:
                        px = min(g["entry"], o[i])
                    else:                               # no opens: a bar wholly below the limit fills at its high
                        px = min(g["entry"], h[i])
                    if px <= g["stop"]:                 # opened below the stop: skip the setup
                        continue
                    filled = (g, px)
                    continue
                if c[i] < g["zlo"]:                     # closed through the gap: invalid
                    continue
                still.append(g)
            pending = still
            if filled:
                g, px = filled
                risk = px - g["stop"]
                pos = {"i_in": i, "t_in": df.index[i], "entry": px, "stop": g["stop"],
                       "target": px + p["target_r"] * risk, "risk": risk, "t_gap": g["t_gap"],
                       "gap_atr": g["gap_atr"]}
                pending = []
                # same bar: stop first if touched after fill (conservative), target needs a later bar
                if l[i] <= pos["stop"]:
                    trades.append({**pos, "i_out": i, "t_out": df.index[i], "exit": pos["stop"], "why": "stop"})
                    pos = None
        if continue_after or pos is not None:
            continue

        # 3) new gap formed by A=i-2, B=i-1, C=i
        a, b = i - 2, i - 1
        zlo, zhi = h[a], l[i]
        if zhi <= zlo:
            continue
        gap = zhi - zlo
        if gap < p["min_gap_atr"] * atr[i]:
            continue
        if not (rng[b] >= p["b_range_mult"] * avg_rng[b]):
            continue
        if rng[b] <= 0 or (c[b] - l[b]) / rng[b] < 1 - p["close_pos"]:
            continue
        if p["trend"] and c[i] <= ema[i]:
            continue
        if use_volume and p["volume"] and not (v[b] > avg_v[b]):
            continue
        entry = {"near": zhi, "mid": (zlo + zhi) / 2, "far": zlo}[p["entry"]]
        stop = (l[a] if p.get("stop_at") == "A_low" else zlo) - p["stop_buf_atr"] * atr[i]
        pending.append({"entry": entry, "stop": stop, "zlo": zlo, "exp": i + p["expiry"],
                        "t_gap": df.index[i], "gap_atr": gap / atr[i]})
    return trades


def stats(trades: list[dict], cost: float, years: float) -> dict:
    if not trades:
        return {"n": 0}
    t = pd.DataFrame(trades)
    t["ret"] = t["exit"] / t["entry"] - 1 - 2 * cost
    t["R"] = (t["exit"] - t["entry"]) / t["risk"] - 2 * cost * t["entry"] / t["risk"]
    t["bars"] = t["i_out"] - t["i_in"]
    wins = t[t["ret"] > 0]["ret"].sum()
    loss = -t[t["ret"] <= 0]["ret"].sum()
    return {"n": len(t), "per_year": round(len(t) / years, 1),
            "win%": round(100 * (t["ret"] > 0).mean(), 1),
            "avg%": round(100 * t["ret"].mean(), 2),
            "avgR": round(t["R"].mean(), 2),
            "PF": round(wins / loss, 2) if loss > 0 else None,
            "bars": round(t["bars"].median(), 1),
            "eur1000": round(1000 * t["ret"].sum())}


# ---------- accounts ----------
ITALY_TAX = 0.26


def italian_tax(eq: pd.Series) -> pd.Series:
    """Tax 26% of each calendar year's net gain, losses carried forward 4 years (simplified)."""
    eq = eq.copy()
    years = eq.index.year
    out = eq.copy()
    paid_factor = 1.0
    carry = []           # (year, loss)
    prev_val = eq.iloc[0]
    for y in sorted(set(years)):
        seg = eq[years == y]
        gain = seg.iloc[-1] / prev_val - 1
        out[years == y] = out[years == y] * paid_factor
        carry = [(cy, cl) for cy, cl in carry if y - cy <= 4]
        if gain > 0:
            usable = sum(cl for _, cl in carry)
            taxable = max(0.0, gain - usable)
            used = gain - taxable
            new_carry = []
            for cy, cl in carry:
                take = min(cl, used); used -= take
                if cl - take > 1e-12:
                    new_carry.append((cy, cl - take))
            carry = new_carry
            tax = ITALY_TAX * taxable * prev_val * paid_factor
            end_val = seg.iloc[-1] * paid_factor - tax
            paid_factor = end_val / seg.iloc[-1]
            out.iloc[np.where(years == y)[0][-1]] = end_val
        else:
            carry.append((y, -gain))
        prev_val = seg.iloc[-1]
    return out


def btc_account(df: pd.DataFrame, trades: list[dict], cost: float) -> pd.Series:
    """All-in on each trade (one position at a time), cash earns nothing."""
    eq = pd.Series(1.0, index=df.index)
    val = 1.0
    c = df["close"].to_numpy()
    marks = np.ones(len(df))
    ti = {t["i_in"]: t for t in trades}
    i = 0
    n = len(df)
    while i < n:
        if i in ti:
            t = ti[i]
            base = val * (1 - cost)
            for j in range(i, t["i_out"]):
                marks[j] = base * c[j] / t["entry"]
            val = base * t["exit"] / t["entry"] * (1 - cost)
            marks[t["i_out"]] = val
            i = t["i_out"] + 1
            continue
        marks[i] = val
        i += 1
    return pd.Series(marks, index=df.index)


def stock_account(data: dict[str, pd.DataFrame], trades: dict[str, list[dict]], cost: float,
                  slots: int = 10) -> pd.Series:
    """EUR 100k account, up to `slots` positions of 1/slots of equity each, first come first served."""
    cal = pd.DatetimeIndex(sorted(set().union(*(df.index for df in data.values()))))
    closes = pd.DataFrame({t: df["close"] for t, df in data.items()}).reindex(cal).ffill()
    events = []
    for tk, ts in trades.items():
        for t in ts:
            events.append((t["t_in"], t["t_out"], tk, t["entry"], t["exit"]))
    events.sort()
    by_day = {}
    for e in events:
        by_day.setdefault(e[0], []).append(e)
    cash, openp = 1.0, []      # each: dict(tk, shares, out, exit)
    eq = []
    for d in cal:
        # exits first
        keep = []
        for p in openp:
            if p["out"] == d:
                cash += p["sh"] * p["exit"] * (1 - cost)
            else:
                keep.append(p)
        openp = keep
        mark = cash + sum(p["sh"] * closes.at[d, p["tk"]] for p in openp)
        for e in by_day.get(d, []):
            if len(openp) >= slots:
                break
            size = min(mark / slots, cash)
            if size <= 0:
                break
            sh = size * (1 - cost) / e[3]
            cash -= size
            if e[1] == d:      # in and out the same day
                cash += sh * e[4] * (1 - cost)
            else:
                openp.append({"tk": e[2], "sh": sh, "out": e[1], "exit": e[4]})
        eq.append(cash + sum(p["sh"] * closes.at[d, p["tk"]] for p in openp))
    return pd.Series(eq, index=cal)


def cagr(eq: pd.Series) -> float:
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    return (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1


def maxdd(eq: pd.Series) -> float:
    return float((eq / eq.cummax() - 1).min())


def summary(eq: pd.Series) -> dict:
    tx = italian_tax(eq)
    return {"x": round(eq.iloc[-1] / eq.iloc[0], 2), "cagr%": round(100 * cagr(eq), 1),
            "maxDD%": round(100 * maxdd(eq), 1), "after_tax_x": round(tx.iloc[-1] / tx.iloc[0], 2),
            "after_tax_cagr%": round(100 * cagr(tx), 1)}
