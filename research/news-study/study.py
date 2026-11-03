"""News study, step 2: does the news line up with price moves, and can you trade on it?

  python research/news-study/study.py --news DIR [--demo]

Reads DIR/alpaca-*.csv (fetch_alpaca.py) and DIR/gdelt.csv (fetch_gdelt.py), downloads daily prices
(Yahoo Finance) and writes research/news-study/results.json, research/news-study/README.md and
research/news-study/data/signals.csv.gz (daily signals per asset, for the Strategy Lab later).

Three signals per asset and trading day, all known at that day's close:
  mood        average tone of the day's headlines (FinBERT for Alpaca, GDELT's own tone for GDELT)
  vs usual    today's tone minus this asset's average of the last 120 trading days, in standard deviations
  burst       how much more news than usual (log of today's count vs the last 60 days), signed by the
              direction of the tone vs usual: a loud good-news day is +, a loud bad-news day is -

Two questions:
  1. Reaction: on the day of the news, does price move the same way? (explains, can't be traded)
  2. Prediction: after the close, does the signal say anything about the next 1, 3, 5, 10 days?
     Rank correlation (IC), top vs bottom fifth, and simple strategies after costs vs buy & hold,
     also split into 2019-2023 and 2024 on (nothing is tuned, so the later years are a fair check).
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
HORIZONS = (1, 3, 5, 10)
SPLIT = "2024-01-01"
TOP_K = 10                 # stocks bought each day by the cross-sectional strategies
MIN_N = 2                  # headlines needed for a stock to be picked on its mood
COST = {"stocks": 0.0005, "etf": 0.0003, "crypto": 0.0010}   # per side
GDELT_ASSET = {"oil": "USO", "gold": "GLD", "silver": "SLV", "bitcoin": "BTC-USD", "market": "SPY"}
LABEL = {"GLD": "Gold (GLD)", "SLV": "Silver (SLV)", "USO": "Oil (USO)", "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum",
         "SPY": "S&P 500 (SPY)"}
SIGNALS = ("mood", "vs_usual", "burst")
SIG_NAME = {"mood": "Mood", "vs_usual": "Mood vs usual", "burst": "News burst"}


# ------------------------------------------------------------------ data
def load_news(d: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    files = sorted(d.glob("alpaca-*.csv*"))
    a = pd.concat([pd.read_csv(f) for f in files]) if files else pd.DataFrame(columns=["group", "sym", "day", "n", "mood", "pos", "neg"])
    gf = sorted(d.glob("gdelt.csv*"))
    g = pd.read_csv(gf[0]) if gf else pd.DataFrame(columns=["theme", "day", "n", "tone"])
    return a, g


def get_prices(syms: list[str], start: str, demo: bool) -> pd.DataFrame:
    if demo:
        rng = np.random.default_rng(1)
        idx = pd.bdate_range(start, pd.Timestamp.today().normalize())
        cidx = pd.date_range(start, pd.Timestamp.today().normalize())
        out = {}
        for s in syms:
            ix = cidx if s.endswith("-USD") else idx
            out[s] = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, len(ix)))), ix)
        return pd.DataFrame(out)
    import yfinance as yf
    px = yf.download(syms, start=start, auto_adjust=True, progress=False, threads=True)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame(syms[0])
    px.index = pd.to_datetime(px.index).tz_localize(None).normalize()
    return px.sort_index()


def to_sessions(df: pd.DataFrame, close: pd.Series, lag: bool) -> pd.DataFrame:
    """Map news days to the trading day whose close comes after them (lag: strictly the next one),
    adding up weekends and holidays. Returns n and mood on every trading day (mood NaN without news)."""
    idx = close.dropna().index
    days = pd.to_datetime(df["day"]).values
    pos = idx.searchsorted(days, side="right" if lag else "left")
    ok = pos < len(idx)
    x = df.loc[ok].assign(session=idx[pos[ok]])
    x = x.assign(tot=x["mood"] * x["n"])
    s = x.groupby("session")[["n", "tot"]].sum()
    out = pd.DataFrame(index=idx)
    out["n"] = s["n"].reindex(idx).fillna(0)
    out["mood"] = (s["tot"] / s["n"]).reindex(idx)
    return out


def add_signals(p: pd.DataFrame, close: pd.Series) -> pd.DataFrame:
    p = p.copy()
    m = p["mood"]
    prev = m.shift(1).rolling(120, min_periods=20)
    sd = prev.std().replace(0, np.nan)
    p["vs_usual"] = (m - prev.mean()) / sd
    vol = np.log((p["n"] + 1) / (p["n"].shift(1).rolling(60, min_periods=40).mean() + 1))
    p["volume"] = vol
    direction = np.sign(p["vs_usual"]).where(p["n"] > 0)
    p["burst"] = vol.clip(lower=0) * direction
    # mood cut-offs for the single-asset strategy: this asset's own top/bottom fifth over the past year
    hist = m.shift(1).rolling(252, min_periods=60)
    p["mood_hi"], p["mood_lo"] = hist.quantile(0.8), hist.quantile(0.2)
    c = close.reindex(p.index)
    p["r0"] = c / c.shift(1) - 1
    for h in HORIZONS:
        p[f"f{h}"] = c.shift(-h) / c - 1
    p["ret"] = p["r0"]
    return p


# ---------------------------------------------------------------- stats
def spearman(a: pd.Series, b: pd.Series) -> float:
    ok = a.notna() & b.notna()
    if ok.sum() < 30:
        return float("nan")
    return float(a[ok].rank().corr(b[ok].rank()))


def panel_stats(df: pd.DataFrame, sig: str, cross: bool) -> dict:
    """df: long panel (date, sym) with the signal, r0 and forward returns."""
    d = df[df[sig].notna() & (df["n"] > 0 if sig != "volume" else True)]
    out = {"obs": int(len(d)), "reaction": round(spearman(d[sig], d["r0"]), 3), "fwd": {}}
    for h in HORIZONS:
        f = f"f{h}"
        x = d[d[f].notna()]
        if len(x) < 60:
            continue
        if cross:
            ic = x.groupby("date").apply(lambda g: spearman(g[sig], g[f]) if len(g) >= 10 else np.nan, include_groups=False).dropna()
            q = x.groupby("date")[sig].rank(pct=True)
            ic_mean, t = ic.mean(), ic.mean() / (ic.std() / math.sqrt(len(ic))) / math.sqrt(h) if len(ic) > 20 else np.nan
        else:
            ic_mean = spearman(x[sig], x[f])
            t = ic_mean * math.sqrt(len(x) / h)
            q = x[sig].rank(pct=True)
        top, bot = x.loc[q >= 0.8, f].mean(), x.loc[q <= 0.2, f].mean()
        early, late = x[x["date"] < SPLIT], x[x["date"] >= SPLIT]
        out["fwd"][h] = {"ic": round(float(ic_mean), 4), "t": round(float(t), 2),
                         "ic_2019_23": round(spearman(early[sig], early[f]), 4), "ic_2024_now": round(spearman(late[sig], late[f]), 4),
                         "top_pct": round(100 * top, 3), "bottom_pct": round(100 * bot, 3),
                         "all_pct": round(100 * x[f].mean(), 3),
                         "top_hit": round(100 * (x.loc[q >= 0.8, f] > 0).mean(), 1)}
    return out


def perf(r: pd.Series, per_year: int) -> dict:
    r = r.dropna()
    if len(r) < 60:
        return {}
    eq = (1 + r).cumprod()
    yrs = len(r) / per_year
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    sharpe = r.mean() / r.std() * math.sqrt(per_year) if r.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return {"cagr": round(100 * cagr, 2), "sharpe": round(sharpe, 2), "maxdd": round(100 * dd, 1)}


def periods(strat: pd.Series, bench: pd.Series, per_year: int, extra: dict | None = None) -> dict:
    out = {}
    for name, lo, hi in (("all", None, None), ("2019-2023", None, SPLIT), ("2024-now", SPLIT, None)):
        s, b = strat.loc[lo:hi], bench.loc[strat.index].loc[lo:hi]
        if hi:
            s, b = s[s.index < hi], b[b.index < hi]
        ps, pb = perf(s, per_year), perf(b, per_year)
        if ps and pb:
            out[name] = {"strategy": ps, "buy_hold": pb, "beats": ps["cagr"] > pb["cagr"]}
    if extra:
        out.update(extra)
    return out


def tranche_returns(picks: pd.DataFrame, rets: pd.DataFrame, h: int, cost: float, short: pd.DataFrame | None = None):
    """picks: bool frame (date x sym) of what is bought at each close. Each day's picks are held h days,
    so the account is h staggered slices (empty slice = cash). Returns daily account returns and exposure."""
    w = picks.astype(float)
    cnt = w.sum(axis=1)
    w = w.div(cnt.replace(0, np.nan), axis=0).fillna(0)
    if short is not None:
        ws = short.astype(float)
        ws = ws.div(ws.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
        w = w - ws
    pos = sum(w.shift(k) for k in range(1, h + 1)) / h
    gross = (pos * rets.fillna(0)).sum(axis=1)
    turnover = (pos - pos.shift(1)).abs().sum(axis=1)
    return gross - cost * turnover, pos.abs().sum(axis=1)


def stock_strategies(panel: dict[str, pd.DataFrame], bench_ew: pd.Series, cost: float) -> dict:
    sig = {s: pd.DataFrame({k: p[s] for k, p in panel.items()}) for s in SIGNALS + ("n", "ret", "volume")}
    rets = sig["ret"]
    enough = sig["n"] >= MIN_N
    out = {}
    for s in SIGNALS:
        x = sig[s].where(enough) if s != "burst" else sig[s].where(sig["volume"] >= math.log(2))
        rk = x.rank(axis=1, ascending=False)
        rk_low = x.rank(axis=1, ascending=True)
        top = (rk <= TOP_K) & (x > 0 if s != "mood" else True)
        bot = (rk_low <= TOP_K) & (x < 0 if s != "mood" else True)
        out[s] = {}
        for h in HORIZONS:
            lo, expo = tranche_returns(top, rets, h, cost)
            ls, _ = tranche_returns(top, rets, h, cost, short=bot)
            out[s][h] = {"long": periods(lo, bench_ew, 252, {"exposure_pct": round(100 * expo.mean(), 1)}),
                         "long_short": {k: v["strategy"] for k, v in periods(ls, bench_ew, 252).items()}}
    return out


def single_strategies(p: pd.DataFrame, per_year: int, cost: float) -> dict:
    rets = p[["ret"]]
    rules = {"mood": (p["mood"] > p["mood_hi"], p["mood"] < p["mood_lo"]),
             "vs_usual": (p["vs_usual"] >= 1, p["vs_usual"] <= -1),
             "burst": ((p["volume"] >= math.log(2)) & (p["vs_usual"] > 0), (p["volume"] >= math.log(2)) & (p["vs_usual"] < 0))}
    bench = p["ret"]
    out = {}
    for s, (up, down) in rules.items():
        out[s] = {}
        for h in HORIZONS:
            lo, expo = tranche_returns(up.to_frame("ret"), rets, h, cost)
            ls, _ = tranche_returns(up.to_frame("ret"), rets, h, cost, short=down.to_frame("ret"))
            avoid, _ = tranche_returns((~down).to_frame("ret"), rets, h, cost)   # hold, but step aside after bad news
            out[s][h] = {"long": periods(lo, bench, per_year, {"exposure_pct": round(100 * expo.mean(), 1),
                                                                  "signals": int(up.sum())}),
                         "long_short": {k: v["strategy"] for k, v in periods(ls, bench, per_year).items()},
                         "avoid_bad_news": periods(avoid, bench, per_year)}
    return out


# --------------------------------------------------------------- report
def fmt(x, nd=2, plus=True):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x:+.{nd}f}" if plus else f"{x:.{nd}f}"


def report(res: dict) -> str:
    L = ["# News-only strategy study", "",
         f"Data to {res['through']}. Headlines from Alpaca/Benzinga (S&P 100, top coins, gold/silver/oil ETFs) scored by "
         "FinBERT, plus GDELT's worldwide tone and article counts for oil, gold, silver, bitcoin and the stock market. "
         "Signals are only used from the close after the news. Costs included. Nothing tuned: fixed rules, "
         "so 2024-now is an honest second look.", "",
         "**How to read it.** *Reaction* = rank correlation between the day's news and that same day's move "
         "(news explaining the move — not tradable). *IC* = rank correlation between the signal and the *next* h days' "
         "return (tradable edge; 0.02+ is meaningful in finance, |t| > 2 is unlikely to be luck). "
         "*Top/Bottom* = average next-h-day return after the strongest fifth of signals vs the weakest fifth.", ""]
    for block in res["blocks"]:
        L += [f"## {block['title']}", ""]
        L += ["| Signal | Obs | Reaction | IC 1d (t) | IC 3d (t) | IC 5d (t) | IC 10d (t) | 5d top / bottom / all |",
              "|---|---|---|---|---|---|---|---|"]
        for s, st in block["stats"].items():
            f = st["fwd"]
            ic = [f"{fmt(f[h]['ic'], 3)} ({fmt(f[h]['t'], 1)})" if h in f else "–" for h in HORIZONS]
            tb = f"{fmt(f[5]['top_pct'])}% / {fmt(f[5]['bottom_pct'])}% / {fmt(f[5]['all_pct'])}%" if 5 in f else "–"
            L.append(f"| {SIG_NAME.get(s, 'News volume (unsigned)')} | {st['obs']:,} | {fmt(st['reaction'], 3)} | " + " | ".join(ic) + f" | {tb} |")
        L.append("")
        if block.get("strategies"):
            L += ["Strategy (long only) vs buy & hold, yearly growth after costs:", "",
                  "| Signal | Hold | All years | 2019-23 | 2024-now | Sharpe (vs B&H) | Time invested |", "|---|---|---|---|---|---|---|"]
            for s, byh in block["strategies"].items():
                for h, r in byh.items():
                    lo = r["long"]
                    if "all" not in lo:
                        continue
                    cell = lambda k: (f"{lo[k]['strategy']['cagr']:+.1f}% vs {lo[k]['buy_hold']['cagr']:+.1f}%"
                                      + (" ✓" if lo[k]["beats"] else "")) if k in lo else "–"
                    L.append(f"| {SIG_NAME[s]} | {h}d | {cell('all')} | {cell('2019-2023')} | {cell('2024-now')} | "
                             f"{lo['all']['strategy']['sharpe']:.2f} vs {lo['all']['buy_hold']['sharpe']:.2f} | {lo.get('exposure_pct', 0):.0f}% |")
            L.append("")
    L += ["## Verdict", ""] + [f"- {v}" for v in res["verdict"]] + [""]
    return "\n".join(L)


def verdict(res: dict) -> list[str]:
    out = []
    for b in res["blocks"]:
        # many tests are run, so a few |t| > 2 appear by luck: also ask for the same sign in both halves
        strong = [(s, h, f) for s, st in b["stats"].items() for h, f in st["fwd"].items()
                  if abs(f["t"]) >= 2 and f["ic_2019_23"] * f["ic_2024_now"] > 0]
        react = max(b["stats"].values(), key=lambda st: abs(st["reaction"] if st["reaction"] == st["reaction"] else 0))["reaction"]
        winners = []
        for s, byh in (b.get("strategies") or {}).items():
            for h, r in byh.items():
                lo = r["long"]
                if all(lo.get(k, {}).get("beats") for k in ("all", "2019-2023", "2024-now")):
                    winners.append(f"{SIG_NAME[s]} {h}d ({lo['all']['strategy']['cagr']:+.1f}% vs {lo['all']['buy_hold']['cagr']:+.1f}%)")
        line = f"**{b['title']}**: same-day reaction up to {react:+.3f}; "
        line += ("predictive (|t| ≥ 2, same sign before and after 2024): " + ", ".join(f"{SIG_NAME.get(s, 'volume')} {h}d (IC {f['ic']:+.3f})" for s, h, f in strong)
                 if strong else "no signal predicts the next days reliably (all |t| < 2)")
        line += "; beats buy & hold in both halves: " + (", ".join(winners) if winners else "none") + "."
        out.append(line)
    return out


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--news", type=Path, required=True)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    a, g = load_news(args.news)
    stocks = sorted(a.loc[a["group"] == "stocks", "sym"].unique())
    crypto = sorted(a.loc[a["group"] == "crypto", "sym"].unique())
    etfs = ["GLD", "SLV", "USO", "SPY"]
    px = get_prices(sorted(set(stocks + crypto + etfs + ["BTC-USD"])), "2018-06-01", args.demo)
    res = {"through": str(px.index.max().date()), "blocks": []}
    all_signals = []

    def build(df, sym, lag, group):
        p = add_signals(to_sessions(df, px[sym], lag), px[sym])
        all_signals.append(p[["n", "mood", "vs_usual", "volume", "burst"]].assign(sym=sym, source=group))
        return p

    def long_panel(panel):
        return pd.concat([p.assign(sym=k, date=p.index) for k, p in panel.items()], ignore_index=True)

    # stocks: cross-section of the S&P 100
    if stocks:
        panel = {t: build(a[(a["group"] == "stocks") & (a["sym"] == t)], t, False, "alpaca") for t in stocks if t in px}
        lp = long_panel(panel)
        lp = lp[lp["date"] >= "2019-06-01"]
        ew = pd.DataFrame({k: p["ret"] for k, p in panel.items()}).mean(axis=1)
        res["blocks"].append({"title": "Stocks (S&P 100, Benzinga headlines)", "key": "stocks",
                              "stats": {s: panel_stats(lp, s, True) for s in SIGNALS + ("volume",)},
                              "strategies": stock_strategies({k: p.loc["2019-06-01":] for k, p in panel.items()},
                                                             ew.loc["2019-06-01":], COST["stocks"])})
        print("stocks done", flush=True)

    # single assets: crypto and commodity ETFs from Alpaca, five themes from GDELT
    singles = []
    for c in crypto:
        singles.append((f"{LABEL.get(c, c)}, Benzinga", c, a[(a["group"] == "crypto") & (a["sym"] == c)], False, "crypto", "alpaca"))
    for e in ("GLD", "SLV", "USO"):
        singles.append((f"{LABEL[e]}, Benzinga", e, a[(a["group"] == "commodities") & (a["sym"] == e)], False, "etf", "alpaca"))
    for theme, sym in GDELT_ASSET.items():
        df = g[g["theme"] == theme].rename(columns={"tone": "mood"})
        singles.append((f"{LABEL[sym]}, GDELT world news", sym, df, sym != "BTC-USD", "crypto" if sym.endswith("-USD") else "etf", "gdelt"))
    crypto_pool = []
    for title, sym, df, lag, kind, src in singles:
        if df.empty or sym not in px:
            continue
        p = build(df, sym, lag, src).loc["2019-06-01":]
        if p["n"].sum() < 200:
            continue
        per_year = 365 if kind == "crypto" else 252
        d = p.assign(date=p.index, sym=sym)
        if src == "alpaca" and kind == "crypto":
            crypto_pool.append(d)
        if src == "alpaca" and kind == "crypto" and sym not in ("BTC-USD", "ETH-USD"):
            continue   # smaller coins only go into the pooled crypto stats
        res["blocks"].append({"title": title, "key": f"{src}:{sym}",
                              "stats": {s: panel_stats(d, s, False) for s in SIGNALS + ("volume",)},
                              "strategies": single_strategies(p, per_year, COST[kind])})
        print(title, "done", flush=True)
    if len(crypto_pool) > 2:
        lp = pd.concat(crypto_pool, ignore_index=True)
        res["blocks"].insert(1, {"title": f"Crypto, {len(crypto_pool)} coins pooled (Benzinga)", "key": "crypto-pool",
                                 "stats": {s: panel_stats(lp, s, False) for s in SIGNALS + ("volume",)}})
    res["verdict"] = verdict(res)
    out = HERE if not args.demo else args.news
    (out / "data").mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=1, default=float))
    (out / "README.md").write_text(report(res))
    sig = pd.concat(all_signals).round(4)
    sig.index.name = "date"
    with gzip.open(out / "data" / "signals.csv.gz", "wt") as f:
        sig.reset_index().to_csv(f, index=False)
    print("\n".join(res["verdict"]))


if __name__ == "__main__":
    main()
