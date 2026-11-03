"""
Evidence grades for the site, from the research report (research/quant/results/report.json).

Writes each market's line-up (stocks, crypto) and an "evidence" block into docs/data/lineup.json.
Commodities keep their line-up until the research engine covers them.

Grades (fixed in advance, judged against holding (the same stocks / bitcoin), after costs):
  A  Proven edge       deflated Sharpe >= 0.95 and beats holding in >= 95% of bootstraps
  B  Promising         beats holding in >= 70% of bootstraps overall and >= 60% in the test years
  C  Unproven          neither clearly better nor clearly worse than holding
  D  Trails holding    beats holding in < 10% of bootstraps overall and < 20% in the test years

On the site: A, B and C. In the Vault: D. Top-Down Analysis is a charting tool, not an alert
rule the engine can backtest, so it stays.

  python research/quant/grades.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORTS = {"stocks": ROOT / "research" / "quant" / "results" / "report.json",
           "crypto": ROOT / "research" / "quant" / "results" / "crypto" / "report.json"}
VS = {"stocks": "holding the same stocks", "crypto": "holding bitcoin"}
# which years decide the grade and the line-up (the site's default view): stocks all of them
# (2011 on, with 2021 on as a check); crypto 2021 on, because earlier crypto prices were tiny and
# illiquid. The other period is stored too and can be picked on the site.
JUDGE = {"stocks": "full", "crypto": "test"}
PERIODS = {"stocks": [("full", "2011–now"), ("test", "2021–now")],
           "crypto": [("test", "2021–now"), ("full", "2015–now")]}   # first = the default
LINEUP = ROOT / "docs" / "data" / "lineup.json"
ALWAYS = ["topdown"]
LABELS = {"A": "Proven edge", "B": "Promising", "C": "Unproven", "D": "Trails holding"}
RULE = ("Every strategy is retested each month against simply holding: for stocks, holding the same stocks, "
        "using the ones that were actually among the 100 biggest at the time (not today's list); for crypto, "
        "holding bitcoin, tested on bitcoin and ether and judged on 2021 onwards only. After trading costs, "
        "with 2021 onwards also the stock test the strategies were never picked on. Each gets an evidence grade: Proven edge, Promising, "
        "Unproven or Trails holding. Strategies that trail holding go to the Vault. Commodities: picked on "
        "the older backtest until the new tests cover them.")


def grade(s: dict, judge: str = "full") -> str:
    full = (s["full"].get("vs_hold") or {}).get("p_better") or 0
    test = (s["test"].get("vs_hold") or {}).get("p_better") or 0
    if judge == "test":   # 2021 on only
        if (s.get("dsr_test", s.get("dsr")) or 0) >= 0.95 and test >= 0.95:
            return "A"
        return "B" if test >= 0.70 else "D" if test < 0.20 else "C"
    if (s.get("dsr") or 0) >= 0.95 and full >= 0.95:
        return "A"
    if full >= 0.70 and test >= 0.60:
        return "B"
    if full < 0.10 and test < 0.20:
        return "D"
    return "C"


def main():
    for market, path in REPORTS.items():
        if path.exists():
            update(market, json.loads(path.read_text()))


def update(market: str, rep: dict):
    if rep.get("demo"):
        print(f"{market}: demo report, line-up left alone")
        return
    j = JUDGE[market]
    hk = "btc" if market == "crypto" else "ew_pit"
    hold = rep["benchmarks"][hk][j]
    ev = {}
    for sid, s in rep["strategies"].items():
        g = grade(s, j)
        by = {}
        for k, _ in PERIODS[market]:
            gk = grade(s, k)   # "full" uses the stock rule (all years + the 2021-on check), "test" 2021 on only
            by[k] = {"grade": gk, "label": LABELS[gk], "beats_hold": s[k]["vs_hold"]["p_better"],
                     "cagr": s[k].get("cagr"), "maxdd": s[k].get("maxdd")}
        ev[sid] = {"grade": g, "label": LABELS[g],
                   "beats_hold": s[j]["vs_hold"]["p_better"], "beats_hold_test": s["test"]["vs_hold"]["p_better"],
                   "deflated": s.get("dsr_test", s.get("dsr")) if j == "test" else s.get("dsr"),
                   "cagr": s[j].get("cagr"), "sharpe": s[j].get("sharpe"), "maxdd": s[j].get("maxdd"), "by": by}
    order = sorted(ev, key=lambda k: (ev[k]["grade"], -(ev[k]["beats_hold"] or 0)))
    lu = json.loads(LINEUP.read_text())
    lu["updated"] = rep["generated_at"][:10]
    lu["rule"] = RULE
    lu["keep"][market] = [k for k in order if ev[k]["grade"] != "D"] + ALWAYS
    lu.setdefault("evidence", {})[market] = {"updated": rep["generated_at"][:10], "labels": LABELS, "vs": VS[market],
                                             "period": PERIODS[market][0][1], "judged_on": j,
                                             "periods": [{"key": k, "label": lab} for k, lab in PERIODS[market]],
                                             "hold": {"cagr": hold.get("cagr"), "sharpe": hold.get("sharpe"), "maxdd": hold.get("maxdd")},
                                             "hold_by": {k: {"cagr": rep["benchmarks"][hk][k].get("cagr"), "maxdd": rep["benchmarks"][hk][k].get("maxdd")}
                                                         for k, _ in PERIODS[market]},
                                             "strategies": {k: ev[k] for k in order}}
    LINEUP.write_text(json.dumps(lu, indent=2) + "\n")
    for k in order:
        print(f"  {ev[k]['grade']}  {k:20s} beats holding {ev[k]['beats_hold']:.0%} / test {ev[k]['beats_hold_test']:.0%}")
    print(f"-> {LINEUP.relative_to(ROOT)}: {len(lu['keep'][market])} {market} strategies on the site")


if __name__ == "__main__":
    main()
