"""
Strategy screen: the 87 new price-only stock strategies (screen_strategies.py), tested in two
steps decided before seeing any result.

  1. Screen on 2021-now: every strategy vs holding the same stocks (the point-in-time top 100,
     equal weight), after 10 bp per side.
  2. Confirm on 2018-2020: the 15 best of the screen are run on years that played no part in
     picking them. A strategy that only won 2021-now by luck usually fails here.

Finalists: Sharpe above holding's in 2021-now, ranked by how often they beat holding in a
block bootstrap (then by the Sharpe gap). Verdict on 2018-2020: Confirmed (beats holding in
>= 70% of bootstraps and a higher Sharpe), Failed (< 40%), otherwise Mixed.

Also reported: Sharpe at 25 bp, buying one day later, turnover, and the deflated Sharpe with
all 87 counted as tries.

  python research/quant/screen.py           # real prices (GitHub Actions: strategy-screen.yml)
  python research/quant/screen.py --demo    # random prices, to test the pipeline offline
  python research/quant/screen.py --only mom_12_1,lowvol_252

Writes research/quant/results/screen/: REPORT.md, screen.json, nets.parquet (daily returns
after costs, for adding winners to the site later). Nothing on the website changes.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import data as D  # noqa: E402
import engine as E  # noqa: E402
import metrics as M  # noqa: E402
import universe as U  # noqa: E402
import screen_strategies as S  # noqa: E402
import screen_improve as I  # noqa: E402

OUT = HERE / "results" / "screen"
WARM = "2011-01-01"
SCREEN = ("2021-01-01", "2100-01-01")
CONFIRM = ("2018-01-01", "2020-12-31")
COST = 10
FINALISTS = 15
DEV = ("2018-01-01", "2100-01-01")          # improve: years already seen, used to pick
HOLDOUT = ("2011-01-01", "2017-12-31")      # improve: never looked at, judged once


def cut(s: pd.Series, p) -> pd.Series:
    return s[(s.index >= p[0]) & (s.index <= p[1])]


def evaluate(x: S.Ctx, out, cost=COST) -> dict:
    """Daily account from target weights (held from the next session), T-bills on idle cash."""
    R = x.R.fillna(0)
    lag1 = None
    if isinstance(out, dict):
        r = out["r"].reindex(index=x.idx, columns=x.C.columns).fillna(0)
        if "w_same_day" in out:
            Wd = out["w_same_day"].reindex(index=x.idx, columns=x.C.columns).fillna(0)
        else:
            Wd = out["w"].reindex(index=x.idx, columns=x.C.columns).fillna(0).shift(1).fillna(0)
        gross = (Wd * r).sum(axis=1)
        traded = out["traded"].reindex(x.idx).fillna(0)
        inv = Wd.sum(axis=1)
    else:
        W = out.reindex(index=x.idx, columns=x.C.columns).fillna(0)
        Wd = W.shift(1).fillna(0)
        gross = (Wd * R).sum(axis=1)
        traded = (Wd - Wd.shift(1).fillna(0)).abs().sum(axis=1)
        inv = Wd.sum(axis=1)
        W2 = W.shift(2).fillna(0)                   # buy one day later
        lag1 = (W2 * R).sum(axis=1) + (1 - W2.sum(axis=1)).clip(lower=0) * x.cash \
            - (W2 - W2.shift(1).fillna(0)).abs().sum(axis=1) * cost / 1e4
    gross = gross + (1 - inv).clip(lower=0) * x.cash
    return {"gross": gross, "net": gross - traded * cost / 1e4, "traded": traded, "invested": inv, "lag1": lag1,
            "intraday": out.get("intraday") if isinstance(out, dict) else None}


def period_stats(a: dict, ew: pd.Series, p) -> dict:
    net = cut(a["net"], p)
    if len(net) < 60:
        return {}
    st = M.stats(net, traded=a["traded"])
    st["invested"] = float((cut(a["invested"], p) > 1e-9).mean())
    st["exposure"] = float(cut(a["invested"], p).mean())
    st["vs_hold"] = M.boot_vs(net, cut(ew, p))
    st["sharpe_25bp"] = M.stats(cut(a["gross"] - a["traded"] * 25 / 1e4, p)).get("sharpe")
    st["sharpe_0bp"] = M.stats(cut(a["gross"], p)).get("sharpe")
    st["sharpe_lag1"] = M.stats(cut(a["lag1"], p)).get("sharpe") if a["lag1"] is not None else None
    return st


def lineup_positions(data, cal, pit, spy):
    """Daily held (0/1) of each on-site stock strategy, for site_lineup_vote."""
    import run as RUN
    sids = [s for s in RUN.LINEUP if s in RUN.REGISTRY and RUN.for_market(s, "stocks") and s != "topdown"]
    print(f"  line-up strategies for the vote: {sids}", flush=True)
    sig = RUN.all_signals(data, sids, spy)
    held = {}
    for sid in sids:
        H = pd.DataFrame(0.0, index=cal, columns=list(data))
        for t, r in sig[sid].items():
            df = data[t]
            allowed = pit[t].reindex(df.index).fillna(False).values if t in pit else np.zeros(len(df), bool)
            if not allowed.any():
                continue
            h, _ = E.positions(sid, r, df, allowed)
            g = cal.get_indexer(df.index)
            ok = g >= 0
            # E.positions: h[d] = 1 if day d's return is earned -> the decision was at close d-1
            H.iloc[g[ok], H.columns.get_loc(t)] = h[ok]
        held[sid] = H.shift(-1).fillna(0)
    return held


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--set", default="screen", choices=["screen", "improve"])
    args = ap.parse_args()
    t0 = time.time()

    today = [s.strip() for s in (ROOT / "engine" / "tickers.txt").read_text().splitlines() if s.strip() and not s.startswith("#")]
    if args.demo:
        names = sorted(set(U.all_tickers()[:160] + today[:40]))
        data = D.demo(names + ["SPY"])
        irx = None
    else:
        names = sorted(set(U.all_tickers()) | set(today))
        print(f"Loading {len(names) + 1} tickers", flush=True)
        data = D.load(names + ["SPY", "^IRX"])
        irx = data.pop("^IRX")["close"] if "^IRX" in data else None
    spy = data.pop("SPY")
    cal = spy.index[spy.index >= "2009-01-01"]

    def mat(col):
        return pd.DataFrame({t: df[col] for t, df in data.items()}).reindex(cal)
    C, V = mat("close"), mat("volume")
    pit = U.top_n(C, V, n=100)
    ever = [c for c in pit.columns if pit[c][pit.index >= "2010-01-01"].any()]
    data = {t: data[t] for t in ever if t in data}
    C, V, O, H, L = (mat(k) for k in ("close", "volume", "open", "high", "low"))
    pit = pit[list(data)]
    cash = ((irx.reindex(cal).ffill().fillna(0) / 100) / 252) if irx is not None else pd.Series(0.0, index=cal)
    x = S.Ctx(O, H, L, C, V, pit, spy["close"], cash)
    print(f"{len(data)} stocks ever in the top 100; {len(cal)} days; loaded in {time.time() - t0:.0f}s", flush=True)

    ew = E.equal_weight(x.R[pit.columns], pit)
    spy_r = x.spy_r

    REG = S.REG if args.set == "screen" else I.REG
    sids = list(REG)
    if args.only:
        sids = [s for s in sids if s in args.only.split(",")]
    if "site_lineup_vote" in sids:
        try:
            x._c["lineup_held"] = lineup_positions(data, cal, pit, spy["close"])
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    acc, errors = {}, {}
    for k, sid in enumerate(sids):
        t1 = time.time()
        try:
            out = REG[sid][1](x)
            acc[sid] = evaluate(x, out)
            print(f"  [{k + 1}/{len(sids)}] {sid}  {time.time() - t1:.0f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            errors[sid] = f"{type(e).__name__}: {e}"
            print(f"  [{k + 1}/{len(sids)}] {sid}  ERROR {errors[sid]}", flush=True)
            traceback.print_exc()

    if args.set == "improve":
        return improve_report(acc, ew, spy_r, x, args, t0, len(data))

    rows = {}
    act = {s: cut(a["net"] - ew, SCREEN) for s, a in acc.items()}
    trial = [float(v.mean() / v.std()) for v in act.values() if v.std() > 0]
    for s, a in acc.items():
        rows[s] = {"family": S.REG[s][0], "intraday": a["intraday"],
                   "screen": period_stats(a, ew, SCREEN), "confirm": period_stats(a, ew, CONFIRM),
                   "dsr_screen": M.deflated_sharpe(act[s], trial)}
    bench = {n: {"screen": M.stats(cut(r, SCREEN)), "confirm": M.stats(cut(r, CONFIRM))}
             for n, r in (("hold_same_stocks", ew), ("spy", spy_r))}

    hs = bench["hold_same_stocks"]["screen"]["sharpe"]
    hc = bench["hold_same_stocks"]["confirm"]["sharpe"]
    cand = [s for s, r in rows.items() if (r["screen"].get("sharpe") or -9) > hs]
    cand.sort(key=lambda s: (-(rows[s]["screen"].get("vs_hold") or {}).get("p_better", 0),
                             -(rows[s]["screen"]["sharpe"] - hs)))
    finalists = cand[:FINALISTS]
    for s in rows:
        c = rows[s]["confirm"]
        p = (c.get("vs_hold") or {}).get("p_better")
        if s not in finalists:
            v = None
        elif p is None:
            v = "No data"
        elif p >= 0.70 and (c.get("sharpe") or -9) > hc:
            v = "Confirmed"
        elif p < 0.40:
            v = "Failed"
        else:
            v = "Mixed"
        rows[s]["verdict"] = v

    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "demo": args.demo,
            "screen": SCREEN[0] + " to now", "confirm": f"{CONFIRM[0]} to {CONFIRM[1]}", "cost_bps": COST,
            "tried": len(acc), "stocks": len(data), "runtime_min": round((time.time() - t0) / 60, 1)}
    rep = {"meta": meta, "benchmarks": bench, "finalists": finalists, "strategies": rows, "errors": errors}
    (OUT / "screen.json").write_text(json.dumps(r4(rep), indent=1))
    pd.DataFrame({**{s: a["net"] for s, a in acc.items()}, "_hold": ew, "_spy": spy_r})[lambda d: d.index >= WARM].astype("float32").to_parquet(OUT / "nets.parquet")
    (OUT / "REPORT.md").write_text(markdown(rep))
    print(f"Done in {(time.time() - t0) / 60:.1f} min. Finalists: {finalists}", flush=True)


def improve_report(acc, ew, spy_r, x, args, t0, nstocks):
    """Pick the best variant per strategy on DEV, judge it once on HOLDOUT."""
    out_dir = HERE / "results" / "improve"
    out_dir.mkdir(parents=True, exist_ok=True)
    act = {s: (a["net"] - ew)[lambda r: r.index >= WARM] for s, a in acc.items()}
    prev = OUT / "nets.parquet"
    if prev.exists():                                   # the 87 screen strategies count as tries too
        nets = pd.read_parquet(prev)
        for c in nets.columns:
            if not c.startswith("_") and c not in act:
                act[c] = (nets[c].astype(float) - ew.reindex(nets.index)).dropna()
    trial = [float(v.mean() / v.std()) for v in act.values() if v.std() > 0]
    rows = {}
    for s, a in acc.items():
        rows[s] = {"group": I.REG[s][0], "base": s in I.BASE.values(),
                   "dev": period_stats(a, ew, DEV), "holdout": period_stats(a, ew, HOLDOUT),
                   "dsr_full": M.deflated_sharpe(act[s], trial)}
    hd, hh = M.stats(cut(ew, DEV))["sharpe"], M.stats(cut(ew, HOLDOUT))["sharpe"]
    picks = {}
    for g in I.BASE:
        mem = [s for s in rows if rows[s]["group"] == g]
        best = max(mem, key=lambda s: (rows[s]["dev"].get("sharpe") or -9))
        h = rows[best]["holdout"]
        p = (h.get("vs_hold") or {}).get("p_better")
        verdict = "No data" if p is None else "Holds up" if p >= 0.70 and (h.get("sharpe") or -9) > hh else "Fails" if p < 0.40 else "Mixed"
        picks[g] = {"pick": best, "verdict": verdict}
    bench = {n: {"dev": M.stats(cut(r, DEV)), "holdout": M.stats(cut(r, HOLDOUT))}
             for n, r in (("hold_same_stocks", ew), ("spy", spy_r))}
    meta = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "demo": args.demo,
            "dev": DEV[0] + " to now", "holdout": f"{HOLDOUT[0]} to {HOLDOUT[1]}", "tried_total": len(trial),
            "stocks": nstocks, "runtime_min": round((time.time() - t0) / 60, 1)}
    rep = r4({"meta": meta, "benchmarks": bench, "picks": picks, "strategies": rows})
    (out_dir / "improve.json").write_text(json.dumps(rep, indent=1))
    pd.DataFrame({s: a["net"] for s, a in acc.items()})[lambda d: d.index >= WARM].astype("float32").to_parquet(out_dir / "nets.parquet")

    L = ["# Improving residual momentum and the high-volume premium", "",
         f"Generated {meta['generated']}{' (DEMO prices, not real)' if args.demo else ''}. Same universe and 10 bp costs as the screen. "
         "The ten variants and the rule for picking were fixed before the run (see screen_improve.py).", "",
         f"**Step 1, pick on {meta['dev']}** (years already seen): the best Sharpe minus holding's per strategy.", "",
         "| Strategy | Variant | CAGR | Sharpe | vs hold | Worst drop | Beats hold | Turnover/yr | Sharpe @25bp |",
         "|---|---|---|---|---|---|---|---|---|"]
    for g in I.BASE:
        for s in [k for k in rows if rows[k]["group"] == g]:
            d = rows[s]["dev"]
            tag = " (base)" if rows[s]["base"] else ""
            star = " ★" if picks[g]["pick"] == s else ""
            L.append(f"| {g} | `{s}`{tag}{star} | {P(d.get('cagr'))} | {F(d.get('sharpe'))} | {F((d.get('sharpe') or 0) - hd)} | "
                     f"{P(d.get('maxdd'), 0)} | {P((d.get('vs_hold') or {}).get('p_better'), 0)} | {F(d.get('turnover'), 1)} | {F(d.get('sharpe_25bp'))} |")
    L += ["", f"Holding the same stocks: Sharpe {F(hd)}. ★ = picked.", "",
          f"## Step 2, the one-shot check on {meta['holdout']} (never looked at before)", "",
          "| Strategy | Picked | CAGR | Sharpe | Holding's Sharpe | Beats hold | Base, same years (Sharpe) | Verdict |",
          "|---|---|---|---|---|---|---|---|"]
    for g, pk in picks.items():
        h, b = rows[pk["pick"]]["holdout"], rows[I.BASE[g]]["holdout"]
        L.append(f"| {g} | `{pk['pick']}` | {P(h.get('cagr'))} | {F(h.get('sharpe'))} | {F(hh)} | "
                 f"{P((h.get('vs_hold') or {}).get('p_better'), 0)} | {F(b.get('sharpe'))} | **{pk['verdict']}** |")
    L += ["", "Holds up = beats holding in at least 70% of bootstraps on these years, with a higher Sharpe. "
          "Residual momentum needs 3 years of history, so its check starts in 2012.", "",
          f"Deflated Sharpe over 2011–now, counting all {len(trial)} strategies tried: " +
          ", ".join(f"`{pk['pick']}` {F(rows[pk['pick']]['dsr_full'])}" for pk in picks.values()) + " (0.95 is the usual bar)."]
    (out_dir / "REPORT.md").write_text("\n".join(L) + "\n")
    print(f"Done in {(time.time() - t0) / 60:.1f} min. Picks: {picks}", flush=True)


def r4(v):
    if isinstance(v, dict):
        return {k: r4(w) for k, w in v.items()}
    if isinstance(v, list):
        return [r4(w) for w in v]
    if isinstance(v, (float, np.floating)):
        return None if not np.isfinite(v) else round(float(v), 4)
    return v


def P(v, d=1):
    return "–" if v is None else f"{v * 100:.{d}f}%"


def F(v, d=2):
    return "–" if v is None else f"{v:.{d}f}"


def markdown(rep) -> str:
    m, b, rows = rep["meta"], rep["benchmarks"], rep["strategies"]
    hs, hc = b["hold_same_stocks"]["screen"], b["hold_same_stocks"]["confirm"]
    L = [f"# Strategy screen: {m['tried']} new stock strategies", "",
         f"Generated {m['generated']}{' (DEMO prices, not real)' if m['demo'] else ''}. "
         f"Universe: the point-in-time top 100 ({m['stocks']} different stocks). Costs {m['cost_bps']} bp per side. "
         "Idle money earns T-bills. Nothing on the website changes.", "",
         "**How it works (fixed before the run):** every strategy is screened on "
         f"{m['screen']}. Those with a higher Sharpe than holding the same stocks are ranked by how often they beat "
         f"holding in a block bootstrap; the best {FINALISTS} are then run on {m['confirm']}, years never used to pick them. "
         "Confirmed = beats holding in at least 70% of bootstraps there, with a higher Sharpe. Failed = under 40%.", "",
         "| Benchmark | Screen CAGR | Screen Sharpe | Screen worst drop | 2018–2020 CAGR | 2018–2020 Sharpe |",
         "|---|---|---|---|---|---|"]
    for n, lab in (("hold_same_stocks", "Hold the same stocks (equal weight)"), ("spy", "SPY")):
        s, c = b[n]["screen"], b[n]["confirm"]
        L.append(f"| {lab} | {P(s.get('cagr'))} | {F(s.get('sharpe'))} | {P(s.get('maxdd'))} | {P(c.get('cagr'))} | {F(c.get('sharpe'))} |")

    L += ["", f"## Finalists ({len(rep['finalists'])}) and the 2018–2020 check", "",
          "| # | Strategy | Family | Screen CAGR | Screen Sharpe | Beats hold | 2018–2020 CAGR | 2018–2020 Sharpe | Beats hold | Verdict |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for i, s in enumerate(rep["finalists"], 1):
        r = rows[s]
        a, c = r["screen"], r["confirm"]
        L.append(f"| {i} | `{s}` | {r['family']} | {P(a.get('cagr'))} | {F(a.get('sharpe'))} | "
                 f"{P((a.get('vs_hold') or {}).get('p_better'), 0)} | {P(c.get('cagr'))} | {F(c.get('sharpe'))} | "
                 f"{P((c.get('vs_hold') or {}).get('p_better'), 0)} | **{r['verdict']}** |")
    L += ["", f"Holding the same stocks: Sharpe {F(hs.get('sharpe'))} in the screen years, {F(hc.get('sharpe'))} in 2018–2020.", "",
          "## Every strategy, screen years", "",
          "Sorted by Sharpe minus holding's. Deflated = chance the edge over holding is real after "
          f"{m['tried']} tries (0.95 is the usual bar). Exposure = average share of money in stocks.", "",
          "| Strategy | Family | CAGR | Sharpe | vs hold | Worst drop | Beats hold | Sharpe @25bp | Buy 1 day later | Turnover/yr | Exposure | Deflated |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    order = sorted(rows, key=lambda s: -((rows[s]["screen"].get("sharpe") or -9)))
    for s in order:
        r, a = rows[s], rows[s]["screen"]
        tag = " ⓘ" if r["intraday"] else ""
        d = (a.get("sharpe") or 0) - (hs.get("sharpe") or 0) if a.get("sharpe") is not None else None
        L.append(f"| `{s}`{tag} | {r['family']} | {P(a.get('cagr'))} | {F(a.get('sharpe'))} | {'+' if d and d > 0 else ''}{F(d)} | "
                 f"{P(a.get('maxdd'), 0)} | {P((a.get('vs_hold') or {}).get('p_better'), 0)} | {F(a.get('sharpe_25bp'))} | "
                 f"{F(a.get('sharpe_lag1'))} | {F(a.get('turnover'), 1)} | {P(a.get('exposure'), 0)} | {F(r.get('dsr_screen'))} |")
    L += ["", "ⓘ trades inside the day (open/close), so 'buy 1 day later' doesn't apply."]
    if rep["errors"]:
        L += ["", "## Errors", ""] + [f"- `{k}`: {v}" for k, v in rep["errors"].items()]
    L += ["", "## Not in this run", "",
          "19 more ideas need data the engine doesn't have yet (sectors, fundamentals, VIX). They come in a second batch."]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
