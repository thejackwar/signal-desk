"""
Research run: every stock strategy, tested the honest way.

  python research/quant/run.py            # real prices (GitHub Actions: .github/workflows/research.yml)
  python research/quant/run.py --demo     # random prices, to test the pipeline offline

Writes research/quant/results/report.json and REPORT.md. Nothing on the website changes.

Questions it answers, per strategy:
  1. Survivorship: how much of the result came from testing on today's S&P 100?
     (point-in-time top 100 vs today's list)
  2. Costs: what's left after paying to trade? (0 / 10 / 25 basis points per side)
  3. Hindsight: does it survive buying one day later?
  4. Luck: does it beat simply holding the same stocks, risk-adjusted, in a bootstrap, after
     allowing for ~20 strategies tried (deflated Sharpe)?
  5. Out of sample: developed on 2011-2020, does it still work in 2021-2026, which wasn't used?
     And does "keep the strategies that did best" work when you can only see the past?
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import data as D  # noqa: E402
import engine as E  # noqa: E402
import metrics as M  # noqa: E402
import portfolio as PF  # noqa: E402
import universe as U  # noqa: E402
from strategies import REGISTRY, for_market, set_benchmark  # noqa: E402  (engine/ is on the path via engine.py)

OUT = HERE / "results"
START, SPLIT = "2011-01-01", "2021-01-01"     # results from START; dev period before SPLIT, test after
COSTS = (0, 10, 25)                            # basis points per side
BASE_COST = 10
SLOTS = 20                                     # "capped" sizing: at most 5% of the account per stock
LINEUP = json.loads((ROOT / "docs" / "data" / "lineup.json").read_text())["keep"]["stocks"]


def current_list() -> list[str]:
    lines = (ROOT / "engine" / "tickers.txt").read_text().splitlines()
    return [x.strip() for x in lines if x.strip() and not x.startswith("#")]


def matrices(data, cal):
    closes = pd.DataFrame({t: df["close"] for t, df in data.items()}).reindex(cal)
    vols = pd.DataFrame({t: df["volume"] for t, df in data.items()}).reindex(cal)
    return closes, vols


_W = {}


def _init(bench, sids):
    set_benchmark(bench, "SPY")
    _W["sids"] = sids


def _signals(item):
    """Every strategy on one stock (in a worker process; the deck strategies share a cache)."""
    t, df = item
    out = {}
    for sid in _W["sids"]:
        try:
            out[sid] = REGISTRY[sid](df)
        except Exception as e:  # noqa: BLE001
            print(f"    ! {sid} {t}: {e}", flush=True)
    return t, out


def all_signals(data, sids, bench):
    from multiprocessing import Pool, cpu_count
    res = {s: {} for s in sids}
    with Pool(max(1, cpu_count()), initializer=_init, initargs=(bench, sids)) as pool:
        for i, (t, out) in enumerate(pool.imap_unordered(_signals, data.items(), chunksize=2)):
            for s, r in out.items():
                res[s][t] = r
            if (i + 1) % 20 == 0:
                print(f"  signals: {i + 1}/{len(data)} stocks", flush=True)
    return res


def run_strategy(sid, res, data, cal, universes, lags=(0, 1)):
    """Daily account rows for every (universe, lag) combination."""
    out = {}
    for uname, umask in universes.items():
        for lag in lags:
            if lag and uname != "pit":
                continue
            H = pd.DataFrame(0.0, index=cal, columns=list(res))
            R = H.copy()
            for t, r in res.items():
                df = data[t]
                allowed = (umask[t].reindex(df.index).fillna(False).values if t in umask
                           else np.zeros(len(df), bool))
                if not allowed.any():
                    continue
                h, rr = E.positions(sid, r, df, allowed, lag=lag)
                g = cal.get_indexer(df.index)
                ok = g >= 0
                H.iloc[g[ok], H.columns.get_loc(t)] = h[ok]
                R.iloc[g[ok], R.columns.get_loc(t)] = rr[ok]
            for sizing, slots in (("split", None), ("capped", SLOTS)):
                if sizing == "capped" and (uname != "pit" or lag):
                    continue
                out[(uname, lag, sizing)] = E.account(H, R, BASE_COST, slots)
    return out


def period(s: pd.Series, which: str) -> pd.Series:
    s = s[s.index >= START]
    return s[s.index < SPLIT] if which == "dev" else s[s.index >= SPLIT] if which == "test" else s


def r4(x):
    if isinstance(x, dict):
        return {k: r4(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [r4(v) for v in x]
    return round(x, 4) if isinstance(x, float) else x


def walk_forward(nets: dict[str, pd.Series], ew: pd.Series, top: int = 5, train_years: int = 5):
    """Each January, keep the `top` strategies with the best Sharpe over the previous
    `train_years` (vs holding the universe; only those that beat it), and run them equally
    for the year. Repeats the app's "keep the winners" decision without hindsight."""
    df = pd.DataFrame(nets)
    years = sorted(set(df.index.year))
    first = pd.Timestamp(START).year + train_years
    rows, picks = [], {}
    for y in years:
        if y < first:
            continue
        tr = df[(df.index.year >= y - train_years) & (df.index.year < y)]
        b = ew.reindex(tr.index)
        sb = M.stats(b)["sharpe"]
        score = {c: (M.stats(tr[c]).get("sharpe") or -9) - sb for c in df}
        chosen = [c for c, s in sorted(score.items(), key=lambda kv: -kv[1]) if s > 0][:top]
        picks[y] = chosen
        yr = df[df.index.year == y]
        rows.append(yr[chosen].mean(axis=1) if chosen else pd.Series(0.0, index=yr.index))
    wf = pd.concat(rows) if rows else pd.Series(dtype=float)
    return wf, picks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--only", help="comma-separated strategy ids (default: every stock strategy)")
    args = ap.parse_args()

    today = current_list()
    if args.demo:
        names = U.all_tickers()[:160] + today[:40]
        names = sorted(set(names))
        data = D.demo(names + ["SPY"])
    else:
        names = sorted(set(U.all_tickers()) | set(today))
        print(f"Loading {len(names) + 1} tickers", flush=True)
        data = D.load(names + ["SPY", "^IRX"])
    spy = data.pop("SPY")
    irx = data.pop("^IRX")["close"] if "^IRX" in data else None
    cal = spy.index[spy.index >= "2009-01-01"]
    closes, vols = matrices(data, cal)
    rets = closes.pct_change(fill_method=None)

    pit = U.top_n(closes, vols, n=100)
    cur = pd.DataFrame(True, index=cal, columns=[t for t in today if t in closes]) & closes[[t for t in today if t in closes]].notna()
    cov = U.coverage(closes)
    ever = pit.columns[pit[pit.index >= START].any()]
    cand = sorted(set(ever) | set(cur.columns))
    data = {t: data[t] for t in cand if t in data}
    print(f"Point-in-time top 100: {len(ever)} different stocks since {START}; "
          f"S&P 500 price coverage {cov[cov.index >= START].mean():.0%}", flush=True)
    set_benchmark(spy["close"], "SPY")

    universes = {"pit": pit[[c for c in pit.columns if c in data]], "current": cur}
    ew = {"pit": E.equal_weight(rets[universes["pit"].columns], universes["pit"]),
          "current": E.equal_weight(rets[cur.columns], cur)}
    spy_r = spy["close"].pct_change().reindex(cal).fillna(0)

    sids = [s for s in REGISTRY if for_market(s, "stocks")]
    if args.only:
        sids = [s for s in sids if s in args.only.split(",")]
    sig = all_signals(data, sids, spy["close"])
    acc = {}
    for sid in sids:
        print(f"  {sid}", flush=True)
        acc[sid] = run_strategy(sid, sig.pop(sid), data, cal, universes)

    main_key = ("pit", 0, "split")
    nets = {s: acc[s][main_key]["net"] for s in sids}
    trial = [float(period(n, "full").mean() / period(n, "full").std()) for n in nets.values() if period(n, "full").std() > 0]

    report = {}
    for s in sids:
        a = acc[s]
        m = a[main_key]
        row = {"on_site": s in LINEUP}
        for p in ("full", "dev", "test"):
            idx = period(m["net"], p).index
            row[p] = M.stats(m["net"].reindex(idx), m["k"], m["traded"])
            row[p]["vs_hold"] = M.boot_vs(m["net"].reindex(idx), ew["pit"].reindex(idx)) if p != "dev" else None
        row["costs"] = {str(c): M.stats(period(m["gross"] - m["traded"] * c / 1e4, "full")).get("sharpe") for c in COSTS}
        row["lag1"] = M.stats(period(a[("pit", 1, "split")]["net"], "full"))
        row["capped"] = M.stats(period(a[("pit", 0, "capped")]["net"], "full"), a[("pit", 0, "capped")]["k"])
        cu = a[("current", 0, "split")]
        row["current_list"] = M.stats(period(cu["net"], "full"), cu["k"])
        row["dsr"] = M.deflated_sharpe(period(m["net"] - ew["pit"], "full"), trial)
        report[s] = row

    bench = {}
    for name, r in (("ew_pit", ew["pit"]), ("ew_current", ew["current"]), ("spy", spy_r)):
        bench[name] = {p: M.stats(period(r, p)) for p in ("full", "dev", "test")}

    # does a good 2011-2020 predict a good 2021-2026?
    dev_s = [report[s]["dev"].get("sharpe") or 0 for s in sids]
    test_s = [report[s]["test"].get("sharpe") or 0 for s in sids]
    rho = spearmanr(dev_s, test_s).statistic if len(sids) > 2 else None

    # the site's rule, applied on the development years only, then judged on the test years
    sb_dev = bench["ew_pit"]["dev"]["sharpe"]
    kept_dev = [s for s in sids if (report[s]["dev"].get("sharpe") or 0) > sb_dev]
    combo = {}
    for name, members in (("kept_on_dev", kept_dev), ("on_site_now", [s for s in sids if s in LINEUP]), ("all", sids)):
        if members:
            r = pd.DataFrame({s: nets[s] for s in members}).mean(axis=1)
            combo[name] = {"members": members, "test": M.stats(period(r, "test")),
                           "test_vs_hold": M.boot_vs(period(r, "test"), period(ew["pit"], "test"))}

    wf, picks = walk_forward({s: period(nets[s], "full") for s in sids}, period(ew["pit"], "full"))
    wf_ew = ew["pit"].reindex(wf.index)
    walk = {"stats": M.stats(wf), "hold_same_years": M.stats(wf_ew), "vs_hold": M.boot_vs(wf, wf_ew),
            "picks": {str(k): v for k, v in picks.items()}}

    corr = pd.DataFrame({s: period(nets[s], "full") for s in sids}).corr().round(2)

    # Phase 3: trend-filter portfolios with risk-based sizing
    print("  portfolios", flush=True)
    pf = PF.build(closes, rets, universes["pit"], spy["close"].reindex(cal), irx)
    hold = pf["hold_all"]["net"]
    act = {k: period(v["net"] - hold, "full") for k, v in pf.items() if k != "hold_all"}
    ptrial = trial + [float(a.mean() / a.std()) for a in act.values() if a.std() > 0]
    ports = {}
    for k, v in pf.items():
        row = {p: M.stats(period(v["net"], p), traded=v["traded"]) for p in ("full", "dev", "test")}
        row["invested"] = float(period(v["invested"], "full").mean())
        row["crash"] = PF.crash_stats(v["net"])
        if k != "hold_all":
            row["vs_hold"] = {p: M.boot_vs(period(v["net"], p), period(hold, p)) for p in ("full", "test")}
            row["dsr"] = M.deflated_sharpe(act[k], ptrial)
        ports[k] = row

    OUT.mkdir(exist_ok=True)
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "demo": args.demo,
        "settings": {"start": START, "split": SPLIT, "cost_bps": BASE_COST, "slots": SLOTS,
                     "universe": "point-in-time top 100 of the S&P 500 by trading value, quarterly"},
        "universe": {"stocks_ever": int(len(ever)), "sp500_coverage": float(cov[cov.index >= START].mean()),
                     "today_list_in_pit_2011": int(universes["pit"].loc[universes["pit"].index >= START].iloc[0]
                                                   .reindex(cur.columns, fill_value=False).sum())},
        "benchmarks": bench,
        "strategies": report,
        "dev_test_rank_corr": rho,
        "combos": combo,
        "walk_forward": walk,
        "correlation": corr.to_dict(),
        "portfolios": ports,
    }
    (OUT / "report.json").write_text(json.dumps(r4(doc), indent=1, default=float))
    (OUT / "REPORT.md").write_text(markdown(doc, sids))
    print(f"-> {OUT.relative_to(ROOT)}/report.json, REPORT.md")


def P(x, d=1):
    return "–" if x is None else f"{x * 100:.{d}f}%"


def F(x, d=2):
    return "–" if x is None else f"{x:.{d}f}"


def markdown(doc, sids) -> str:
    b, S = doc["benchmarks"], doc["strategies"]
    L = [f"# Research engine report ({doc['generated_at'][:10]}{', DEMO DATA' if doc['demo'] else ''})", "",
         f"Universe: {doc['settings']['universe']}. {doc['universe']['stocks_ever']} different stocks since "
         f"{doc['settings']['start'][:4]}; we have prices for {P(doc['universe']['sp500_coverage'], 0)} of S&P 500 "
         f"members over the period (the rest were delisted and Yahoo has no data). Of today's S&P 100, "
         f"{doc['universe']['today_list_in_pit_2011']} were in the 2011 top 100.", "",
         f"All strategy numbers are **after {doc['settings']['cost_bps']} bp per buy and per sell**, long only, "
         "money split equally across open positions (the app's account). Development years "
         f"{doc['settings']['start'][:4]}–2020, test years 2021–now (never used to pick anything).", "",
         "## Benchmarks", "", "| | CAGR | Sharpe | Worst drop | Sharpe dev | Sharpe test |", "|---|---|---|---|---|---|"]
    for k, lab in (("ew_pit", "Hold every stock in the universe (point in time)"),
                   ("ew_current", "Hold today's S&P 100 since 2011 (hindsight)"), ("spy", "SPY")):
        x = b[k]
        L.append(f"| {lab} | {P(x['full'].get('cagr'))} | {F(x['full'].get('sharpe'))} | {P(x['full'].get('maxdd'), 0)} "
                 f"| {F(x['dev'].get('sharpe'))} | {F(x['test'].get('sharpe'))} |")
    L += ["", "## Strategies", "",
          "Beats hold = probability its Sharpe is above holding the same stocks (bootstrap). "
          "Deflated = chance the edge over holding is real after ~20 strategies tried (0.95 is the usual bar).", "",
          "| Strategy | On site | CAGR | Sharpe | Dev | Test | Beats hold (full / test) | Deflated | Today's list CAGR | Buy 1 day later | Sharpe @0 / 10 / 25 bp | Capped 5% CAGR |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    order = sorted(sids, key=lambda s: -(S[s]["full"].get("sharpe") or -9))
    for s in order:
        x = S[s]
        vf, vt = x["full"].get("vs_hold") or {}, x["test"].get("vs_hold") or {}
        c = x["costs"]
        L.append(f"| {s} | {'yes' if x['on_site'] else ''} | {P(x['full'].get('cagr'))} | {F(x['full'].get('sharpe'))} "
                 f"| {F(x['dev'].get('sharpe'))} | {F(x['test'].get('sharpe'))} "
                 f"| {P(vf.get('p_better'), 0)} / {P(vt.get('p_better'), 0)} | {F(x['dsr'])} "
                 f"| {P(x['current_list'].get('cagr'))} | {F(x['lag1'].get('sharpe'))} "
                 f"| {F(c['0'])} / {F(c['10'])} / {F(c['25'])} | {P(x['capped'].get('cagr'))} |")
    L += ["", "## Does picking the winners work?", "",
          f"Rank correlation of Sharpe, dev years vs test years: **{F(doc['dev_test_rank_corr'])}** "
          "(1 = the best on 2011–2020 stayed best after; 0 = no link).", "",
          "| Portfolio of strategies (equal mix) | Test CAGR | Test Sharpe | Beats hold in test |", "|---|---|---|---|"]
    for k, lab in (("kept_on_dev", "Picked by the site's rule on 2011–2020 only"),
                   ("on_site_now", "Today's site line-up (picked with hindsight)"), ("all", "All strategies")):
        if k in doc["combos"]:
            x = doc["combos"][k]
            L.append(f"| {lab} ({len(x['members'])}) | {P(x['test'].get('cagr'))} | {F(x['test'].get('sharpe'))} "
                     f"| {P((x['test_vs_hold'] or {}).get('p_better'), 0)} |")
    w = doc["walk_forward"]
    L += ["", f"Walk-forward (each January keep the top 5 of the previous 5 years): CAGR {P(w['stats'].get('cagr'))}, "
          f"Sharpe {F(w['stats'].get('sharpe'))} vs holding the stocks {P(w['hold_same_years'].get('cagr'))}, "
          f"Sharpe {F(w['hold_same_years'].get('sharpe'))}; beats hold in {P((w['vs_hold'] or {}).get('p_better'), 0)} of bootstraps.", ""]
    pp = doc.get("portfolios") or {}
    if pp:
        L += ["## Phase 3: trend-filter portfolios", "",
              "Weekly rebalance, 10 bp on every change, idle money in 3-month T-bills. "
              "Compared with holding every stock equally under the same rules.", "",
              "| Portfolio | CAGR | Sharpe | Worst drop | Covid fall | 2022 | Invested | Dev | Test | Beats hold (full / test) | Deflated |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for k, x in pp.items():
            vh = x.get("vs_hold") or {}
            L.append(f"| {k} | {P(x['full'].get('cagr'))} | {F(x['full'].get('sharpe'))} | {P(x['full'].get('maxdd'), 0)} "
                     f"| {P(x['crash']['covid_2020'], 0)} | {P(x['crash']['year_2022'], 0)} | {P(x['invested'], 0)} "
                     f"| {F(x['dev'].get('sharpe'))} | {F(x['test'].get('sharpe'))} "
                     f"| {P((vh.get('full') or {}).get('p_better'), 0)} / {P((vh.get('test') or {}).get('p_better'), 0)} | {F(x.get('dsr'))} |")
        L.append("")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
