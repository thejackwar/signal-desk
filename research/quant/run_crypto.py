"""
Research run for crypto: every crypto strategy against simply holding bitcoin.

  python research/quant/run_crypto.py          # real prices (GitHub Actions)
  python research/quant/run_crypto.py --demo   # random prices, offline

Same tests as run.py, adapted to crypto:
  - Universe: bitcoin and ether only. There's no free point-in-time list of the biggest coins,
    and today's top 20 is pure hindsight (most coins that were top 20 in 2017 are gone or tiny).
    The app's current coin list is run too, as the hindsight comparison.
  - Benchmark: holding bitcoin, as on the site.
  - Costs: 20 bp per buy and per sell (exchange fee + spread); Sharpe also at 0 and 50 bp.
  - 365 trading days a year. Development years 2015–2020, test years 2021–now.

Writes research/quant/results/crypto/report.json and REPORT.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from scipy.stats import spearmanr

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run as R  # noqa: E402  (also puts engine/ on the path)
import data as D  # noqa: E402
import metrics as M  # noqa: E402
import scan  # noqa: E402
from strategies import REGISTRY, for_market, set_benchmark  # noqa: E402

R.START, R.SPLIT, R.BASE_COST = "2015-01-01", "2021-01-01", 20
M.DAYS = 365
COSTS = (0, 20, 50)
CORE = ["BTC-USD", "ETH-USD"]
OUT = HERE / "results" / "crypto"
LINEUP = json.loads((R.ROOT / "docs" / "data" / "lineup.json").read_text())["keep"]["crypto"]


def coins() -> list[str]:
    lines = (R.ROOT / "engine" / "crypto.txt").read_text().splitlines()
    return [x.strip() for x in lines if x.strip() and not x.startswith("#")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    scan.configure("crypto")
    names = coins()
    if args.demo:
        data = D.demo(names, bars=4000)
        data = {t: df.set_axis(pd.date_range(end="2026-10-30", periods=len(df), freq="D")) for t, df in data.items()}
    else:
        data = D.load(names, start="2014-01-01")
    btc = data["BTC-USD"]
    cal = btc.index
    closes = pd.DataFrame({t: df["close"] for t, df in data.items()}).reindex(cal)
    rets = closes.pct_change(fill_method=None)
    have = closes.notna()
    core = have[[c for c in CORE if c in have]]
    universes = {"pit": core, "current": have}
    hold = rets["BTC-USD"].fillna(0)
    ew = {"core": R.E.equal_weight(rets[core.columns], core), "current": R.E.equal_weight(rets, have)}
    set_benchmark(btc["close"], "Bitcoin")

    sids = [s for s in REGISTRY if for_market(s, "crypto")]
    sig = R.all_signals(data, sids, btc["close"])
    acc = {s: R.run_strategy(s, sig.pop(s), data, cal, universes) for s in sids}
    key = ("pit", 0, "split")
    nets = {s: acc[s][key]["net"] for s in sids}
    P = R.period
    trial = [float(P(n, "full").mean() / P(n, "full").std()) for n in nets.values() if P(n, "full").std() > 0]

    report = {}
    for s in sids:
        a, m = acc[s], acc[s][key]
        row = {"on_site": s in LINEUP}
        for p in ("full", "dev", "test"):
            idx = P(m["net"], p).index
            row[p] = M.stats(m["net"].reindex(idx), m["k"], m["traded"])
            row[p]["vs_hold"] = M.boot_vs(m["net"].reindex(idx), hold.reindex(idx)) if p != "dev" else None
        row["costs"] = {str(c): M.stats(P(m["gross"] - m["traded"] * c / 1e4, "full")).get("sharpe") for c in COSTS}
        row["lag1"] = M.stats(P(a[("pit", 1, "split")]["net"], "full"))
        cu = a[("current", 0, "split")]
        row["current_list"] = M.stats(P(cu["net"], "full"), cu["k"])
        row["dsr"] = M.deflated_sharpe(P(m["net"] - hold, "full"), trial)
        report[s] = row

    bench = {n: {p: M.stats(P(r, p)) for p in ("full", "dev", "test")}
             for n, r in (("btc", hold), ("ew_core", ew["core"]), ("ew_current", ew["current"]))}
    dev_s = [report[s]["dev"].get("sharpe") or 0 for s in sids]
    test_s = [report[s]["test"].get("sharpe") or 0 for s in sids]
    wf, picks = R.walk_forward({s: P(nets[s], "full") for s in sids}, P(hold, "full"))
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "demo": args.demo, "market": "crypto",
        "settings": {"start": R.START, "split": R.SPLIT, "cost_bps": R.BASE_COST,
                     "universe": "bitcoin and ether (no free point-in-time coin list)"},
        "hold": bench["btc"]["full"], "hold_name": "holding bitcoin",
        "benchmarks": bench, "strategies": report,
        "dev_test_rank_corr": spearmanr(dev_s, test_s).statistic if len(sids) > 2 else None,
        "walk_forward": {"stats": M.stats(wf), "hold_same_years": M.stats(hold.reindex(wf.index)),
                         "vs_hold": M.boot_vs(wf, hold.reindex(wf.index)), "picks": {str(k): v for k, v in picks.items()}},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(R.r4(doc), indent=1, default=float))
    (OUT / "REPORT.md").write_text(markdown(doc, sids))
    print(f"-> {OUT.relative_to(R.ROOT)}/report.json, REPORT.md")


def markdown(doc, sids) -> str:
    P, F, b, S = R.P, R.F, doc["benchmarks"], doc["strategies"]
    L = [f"# Crypto research report ({doc['generated_at'][:10]}{', DEMO DATA' if doc['demo'] else ''})", "",
         f"Universe: {doc['settings']['universe']}. After **{doc['settings']['cost_bps']} bp per buy and per sell**, long only. "
         "Benchmark: holding bitcoin. Development years 2015–2020, test years 2021–now.", "",
         "| Benchmark | CAGR | Sharpe | Worst drop | Sharpe dev | Sharpe test |", "|---|---|---|---|---|---|"]
    for k, lab in (("btc", "Hold bitcoin"), ("ew_core", "Hold bitcoin + ether equally"), ("ew_current", "Hold today's coin list (hindsight)")):
        x = b[k]
        L.append(f"| {lab} | {P(x['full'].get('cagr'))} | {F(x['full'].get('sharpe'))} | {P(x['full'].get('maxdd'), 0)} | {F(x['dev'].get('sharpe'))} | {F(x['test'].get('sharpe'))} |")
    L += ["", "| Strategy | On site | CAGR | Sharpe | Worst drop | Dev | Test | Beats BTC (full / test) | Deflated | Today's coins CAGR | Buy 1 day later | Sharpe @0 / 20 / 50 bp |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in sorted(sids, key=lambda s: -(S[s]["full"].get("sharpe") or -9)):
        x = S[s]
        vf, vt, c = x["full"].get("vs_hold") or {}, x["test"].get("vs_hold") or {}, x["costs"]
        L.append(f"| {s} | {'yes' if x['on_site'] else ''} | {P(x['full'].get('cagr'))} | {F(x['full'].get('sharpe'))} | {P(x['full'].get('maxdd'), 0)} "
                 f"| {F(x['dev'].get('sharpe'))} | {F(x['test'].get('sharpe'))} | {P(vf.get('p_better'), 0)} / {P(vt.get('p_better'), 0)} "
                 f"| {F(x['dsr'])} | {P(x['current_list'].get('cagr'))} | {F(x['lag1'].get('sharpe'))} | {F(c['0'])} / {F(c['20'])} / {F(c['50'])} |")
    w = doc["walk_forward"]
    L += ["", f"Rank correlation of Sharpe, dev vs test years: **{F(doc['dev_test_rank_corr'])}**.", "",
          f"Walk-forward (each January keep the top 5 of the previous 5 years): CAGR {P(w['stats'].get('cagr'))}, Sharpe {F(w['stats'].get('sharpe'))} "
          f"vs holding bitcoin {P(w['hold_same_years'].get('cagr'))}, Sharpe {F(w['hold_same_years'].get('sharpe'))}; "
          f"beats it in {P((w['vs_hold'] or {}).get('p_better'), 0)} of bootstraps.", ""]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
