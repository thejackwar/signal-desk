"""
Earnings strategies research backtest (S&P 100).

Four strategies, each long and short:
  PRE  long : buy at the close before the report when the analysts' EPS estimate is ABOVE
              the EPS reported for the same quarter a year earlier (expected growth)
  PRE  short: short at that close when the estimate is BELOW the year-ago EPS
  POST long : buy after the report when it BEAT the estimate (reported EPS > estimate)
  POST short: short after the report when it MISSED the estimate

Also prints how often the price goes up/down after beats and misses (the "hit rates").

Data: Yahoo Finance via yfinance (earnings dates with estimate/reported EPS + daily prices).
Writes research/earnings_results.json and research/earnings_report.md
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research"
COST = 0.001          # 0.10% round trip (commission + slippage), taken off every trade
START = "2010-01-01"


def log(*a):
    print(*a, flush=True)


def tickers() -> list[str]:
    lines = (ROOT / "engine" / "tickers.txt").read_text().splitlines() if (ROOT / "engine" / "tickers.txt").exists() else []
    if not lines:
        import scan  # noqa
        return scan.load_tickers()
    return [x.strip() for x in lines if x.strip() and not x.startswith("#")]


# ------------------------------------------------------------------ data
def get_events(t: str) -> pd.DataFrame:
    for attempt in range(3):
        try:
            df = yf.Ticker(t).get_earnings_dates(limit=100)
            if df is not None and len(df):
                break
        except Exception as ex:  # noqa
            log("  retry", t, ex)
            time.sleep(3)
    else:
        return pd.DataFrame()
    df = df.rename(columns={"EPS Estimate": "est", "Reported EPS": "rep", "Surprise(%)": "surp"})
    df = df.dropna(subset=["est", "rep"]).sort_index()
    idx = df.index
    if idx.tz is not None:
        idx = idx.tz_convert("America/New_York")
    df["hour"] = idx.hour
    df["day"] = idx.tz_localize(None).normalize() if idx.tz is not None else idx.normalize()
    # same quarter a year earlier = 4 reports back
    df["yago"] = df["rep"].shift(4)
    return df.reset_index(drop=True)


def reaction_index(px: pd.DataFrame, day: pd.Timestamp, hour: int) -> int | None:
    """Index of the first session that trades on the news."""
    dates = px.index
    i = dates.searchsorted(day)          # first session on/after the report day
    if i >= len(dates) or i == 0:
        return None
    same_day = dates[i] == day
    if 0 < hour < 12:                     # before the open -> reacts that same day
        return i
    if hour >= 16:                        # after the close -> next session
        return i + 1 if same_day else i
    # unknown time (00:00): pick the session with the bigger gap
    if not same_day or i + 1 >= len(dates):
        return i
    g0 = abs(px["open"].iat[i] / px["close"].iat[i - 1] - 1)
    g1 = abs(px["open"].iat[i + 1] / px["close"].iat[i] - 1)
    return i if g0 >= g1 else i + 1


# ------------------------------------------------------------------ trade sim
def simulate(px, entry_i, entry_px, side, sl, tp, hold, first_bar_from_open):
    """side +1 long / -1 short. sl/tp as fractions (None = off). hold = max sessions.
    entry at entry_px during bar entry_i (open or close). Returns net return."""
    o, h, l, c = (px[k].values for k in ("open", "high", "low", "close"))
    n = len(c)
    last = min(entry_i + hold, n - 1)
    start = entry_i if first_bar_from_open else entry_i + 1
    if start > last:
        return None
    for j in range(start, last + 1):
        if side == 1:
            stop = entry_px * (1 - sl) if sl else None
            targ = entry_px * (1 + tp) if tp else None
            if j > entry_i:  # gaps only count on bars after entry
                if stop and o[j] <= stop:
                    return o[j] / entry_px - 1 - COST
                if targ and o[j] >= targ:
                    return o[j] / entry_px - 1 - COST
            if stop and l[j] <= stop:   # stop checked first = conservative
                return -sl - COST
            if targ and h[j] >= targ:
                return tp - COST
        else:
            stop = entry_px * (1 + sl) if sl else None
            targ = entry_px * (1 - tp) if tp else None
            if j > entry_i:
                if stop and o[j] >= stop:
                    return 1 - o[j] / entry_px - COST
                if targ and o[j] <= targ:
                    return 1 - o[j] / entry_px - COST
            if stop and h[j] >= stop:
                return -sl - COST
            if targ and l[j] <= targ:
                return tp - COST
    return side * (c[last] / entry_px - 1) - COST


def stats(r: list[float]) -> dict:
    a = np.array([x for x in r if x is not None and np.isfinite(x)])
    if len(a) == 0:
        return {"n": 0}
    wins, losses = a[a > 0].sum(), -a[a < 0].sum()
    return {"n": int(len(a)), "win_rate": round(float((a > 0).mean()) * 100, 1),
            "avg": round(float(a.mean()) * 100, 2), "median": round(float(np.median(a)) * 100, 2),
            "profit_factor": round(float(wins / losses), 2) if losses > 0 else None,
            "worst": round(float(a.min()) * 100, 1), "best": round(float(a.max()) * 100, 1)}


# ------------------------------------------------------------------ main
def main():
    OUT.mkdir(exist_ok=True)
    tks = tickers()
    log("tickers:", len(tks))
    raw = yf.download(tks + ["SPY"], start=START, interval="1d", group_by="ticker",
                      auto_adjust=True, progress=False, threads=True)
    prices = {}
    for t in tks + ["SPY"]:
        try:
            df = raw[t].rename(columns=str.lower)[["open", "high", "low", "close"]].dropna()
            df.index = pd.to_datetime(df.index).tz_localize(None) if getattr(df.index, "tz", None) else pd.to_datetime(df.index)
            if len(df) > 300:
                prices[t] = df
        except KeyError:
            pass
    spy = prices.pop("SPY")
    log("prices:", len(prices))

    rows = []
    for k, t in enumerate(tks):
        if t not in prices:
            continue
        ev = get_events(t)
        log(f"{k+1}/{len(tks)} {t}: {len(ev)} reports")
        px = prices[t]
        for _, e in ev.iterrows():
            if e["day"] < px.index[0] + pd.Timedelta(days=5):
                continue
            ri = reaction_index(px, e["day"], int(e["hour"]))
            if ri is None or ri < 1 or ri + 21 >= len(px):
                continue
            pre_c = px["close"].iat[ri - 1]
            r_o, r_c = px["open"].iat[ri], px["close"].iat[ri]
            def fwd(i, n): return px["close"].iat[i + n] / px["close"].iat[i] - 1
            d0, dr = px.index[ri - 1], px.index[ri]
            def spy_ret(a, b):
                try: return spy["close"].asof(b) / spy["close"].asof(a) - 1
                except Exception: return np.nan
            surp = (e["rep"] - e["est"]) / abs(e["est"]) * 100 if e["est"] else np.nan
            rows.append({
                "t": t, "date": dr.strftime("%Y-%m-%d"), "ri": ri,
                "est": e["est"], "rep": e["rep"], "yago": e["yago"], "surp": surp,
                "exp_up": (e["est"] > e["yago"]) if pd.notna(e["yago"]) else None,
                "beat": e["rep"] > e["est"], "miss": e["rep"] < e["est"],
                "gap": r_o / pre_c - 1, "react": r_c / pre_c - 1, "react_from_open": r_c / r_o - 1,
                "react_xs": r_c / pre_c - 1 - spy_ret(d0, dr),
                "d1": fwd(ri, 1), "d5": fwd(ri, 5), "d20": fwd(ri, 20),
                "d5_xs": fwd(ri, 5) - spy_ret(dr, px.index[ri + 5]),
                "d20_xs": fwd(ri, 20) - spy_ret(dr, px.index[ri + 20]),
            })
    ev = pd.DataFrame(rows)
    ev.to_csv(OUT / "earnings_events.csv", index=False)
    log("events:", len(ev), "from", ev["date"].min(), "to", ev["date"].max())

    # ---------------- hit rates
    def hit(sub, label):
        d = {"group": label, "n": len(sub)}
        for col, name in [("react", "earnings_day"), ("react_xs", "earnings_day_vs_spy"),
                          ("react_from_open", "open_to_close_day"), ("d1", "next_day_after"),
                          ("d5", "5d_after"), ("d20", "20d_after"), ("d20_xs", "20d_after_vs_spy")]:
            s = sub[col].dropna()
            d[name + "_up%"] = round(float((s > 0).mean()) * 100, 1) if len(s) else None
            d[name + "_avg%"] = round(float(s.mean()) * 100, 2) if len(s) else None
        return d

    groups = [("All reports", ev),
              ("Beat (reported > estimate)", ev[ev.beat]),
              ("Big beat (> 5% surprise)", ev[ev.surp > 5]),
              ("Miss (reported < estimate)", ev[ev.miss]),
              ("Big miss (< -5% surprise)", ev[ev.surp < -5]),
              ("Expected growth (est > year-ago)", ev[ev.exp_up == True]),   # noqa
              ("Expected decline (est < year-ago)", ev[ev.exp_up == False]),  # noqa
              ("Beat AND gapped up", ev[ev.beat & (ev.gap > 0)]),
              ("Miss AND gapped down", ev[ev.miss & (ev.gap < 0)])]
    hits = [hit(s, g) for g, s in groups]

    # ---------------- strategies
    sls = [None, 0.03, 0.05, 0.08]
    tps = [None, 0.05, 0.10, 0.15]
    holds = [1, 5, 10, 20]
    strat_defs = {
        # name: (filter, side, entry: 'pre_close' | 'react_open' | 'react_close')
        "PRE long  (est > year-ago)": (ev.exp_up == True, 1, "pre_close"),     # noqa
        "PRE short (est < year-ago)": (ev.exp_up == False, -1, "pre_close"),   # noqa
        "POST long  beat, buy at open": (ev.beat, 1, "react_open"),
        "POST long  beat, buy at close": (ev.beat, 1, "react_close"),
        "POST short miss, short at open": (ev.miss, -1, "react_open"),
        "POST short miss, short at close": (ev.miss, -1, "react_close"),
        "POST long  big beat + gap up, at close": (ev.beat & (ev.surp > 5) & (ev.gap > 0), 1, "react_close"),
        "POST short big miss + gap down, at close": (ev.miss & (ev.surp < -5) & (ev.gap < 0), -1, "react_close"),
        "BASELINE long every report (pre close)": (ev.t.notna(), 1, "pre_close"),
        "BASELINE long every report (react close)": (ev.t.notna(), 1, "react_close"),
    }
    results = []
    for name, (mask, side, entry) in strat_defs.items():
        sub = ev[mask.fillna(False).astype(bool)]
        for hold in holds:
            for sl in sls:
                for tp in tps:
                    rets, years = [], []
                    for _, e in sub.iterrows():
                        px = prices[e.t]
                        ri = int(e.ri)
                        if entry == "pre_close":
                            r = simulate(px, ri - 1, px["close"].iat[ri - 1], side, sl, tp, hold, False)
                        elif entry == "react_open":
                            r = simulate(px, ri, px["open"].iat[ri], side, sl, tp, hold, True)
                        else:
                            r = simulate(px, ri, px["close"].iat[ri], side, sl, tp, hold, False)
                        rets.append(r)
                        years.append(e.date[:4])
                    s = stats(rets)
                    s.update({"strategy": name, "hold_days": hold,
                              "stop_loss%": sl and sl * 100, "take_profit%": tp and tp * 100})
                    results.append(s)
        log("done", name, len(sub))
    res = pd.DataFrame(results)

    # by-year stability for each strategy's best setting
    yearly = {}
    for name, (mask, side, entry) in strat_defs.items():
        sr = res[(res.strategy == name) & (res.n >= 30)]
        if sr.empty:
            continue
        b = sr.sort_values("avg", ascending=False).iloc[0]
        sub = ev[mask.fillna(False).astype(bool)]
        per = {}
        for _, e in sub.iterrows():
            px = prices[e.t]; ri = int(e.ri)
            ep = {"pre_close": (ri - 1, px["close"].iat[ri - 1], False),
                  "react_open": (ri, px["open"].iat[ri], True),
                  "react_close": (ri, px["close"].iat[ri], False)}[entry]
            sl = b["stop_loss%"] / 100 if pd.notna(b["stop_loss%"]) else None
            tp = b["take_profit%"] / 100 if pd.notna(b["take_profit%"]) else None
            r = simulate(px, ep[0], ep[1], side, sl, tp, int(b.hold_days), ep[2])
            per.setdefault(e.date[:4], []).append(r)
        yearly[name] = {"setting": {"hold": int(b.hold_days), "sl": b["stop_loss%"], "tp": b["take_profit%"]},
                        "years": {y: stats(v) for y, v in sorted(per.items())}}

    out = {"generated": pd.Timestamp.utcnow().isoformat(), "tickers": len(prices), "events": len(ev),
           "first": ev["date"].min(), "last": ev["date"].max(), "cost_round_trip%": COST * 100,
           "hit_rates": hits, "strategies": json.loads(res.to_json(orient="records")), "yearly_best": yearly}
    (OUT / "earnings_results.json").write_text(json.dumps(out, indent=1, default=str))

    # markdown summary
    md = [f"# Earnings research — {len(prices)} S&P 100 stocks, {len(ev)} reports ({out['first']} → {out['last']})\n",
          "## Hit rates\n", pd.DataFrame(hits).to_markdown(index=False), "\n## Best 5 settings per strategy (by avg return, n ≥ 30)\n"]
    for name in strat_defs:
        sr = res[(res.strategy == name) & (res.n >= 30)].sort_values("avg", ascending=False).head(5)
        md += [f"### {name}\n", sr.drop(columns="strategy").to_markdown(index=False), ""]
    (OUT / "earnings_report.md").write_text("\n".join(md))
    log("written")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "engine"))
    main()
