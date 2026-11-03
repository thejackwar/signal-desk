"""
Evidence grades for the site, from the research report (research/quant/results/report.json).

Writes the stock line-up and an "evidence" block into docs/data/lineup.json. Crypto and
commodities keep their line-ups until the research engine covers them.

Grades (fixed in advance, judged against holding the same stocks, after costs):
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
REPORT = ROOT / "research" / "quant" / "results" / "report.json"
LINEUP = ROOT / "docs" / "data" / "lineup.json"
ALWAYS = ["topdown"]
LABELS = {"A": "Proven edge", "B": "Promising", "C": "Unproven", "D": "Trails holding"}
RULE = ("Stocks: every strategy is retested each month against simply holding the same stocks, on the "
        "stocks that were actually among the 100 biggest at the time (not today's list), after trading costs, "
        "with 2021 onwards kept aside as a test the strategies were never picked on. Each gets an evidence "
        "grade: Proven edge, Promising, Unproven or Trails holding. Strategies that trail holding go to the "
        "Vault. Crypto and commodities: picked on the older backtest until the new tests cover them.")


def grade(s: dict) -> str:
    full = (s["full"].get("vs_hold") or {}).get("p_better") or 0
    test = (s["test"].get("vs_hold") or {}).get("p_better") or 0
    if (s.get("dsr") or 0) >= 0.95 and full >= 0.95:
        return "A"
    if full >= 0.70 and test >= 0.60:
        return "B"
    if full < 0.10 and test < 0.20:
        return "D"
    return "C"


def main():
    rep = json.loads(REPORT.read_text())
    if rep.get("demo"):
        raise SystemExit("Demo report: not touching the site's line-up.")
    hold = rep["benchmarks"]["ew_pit"]["full"]
    ev = {}
    for sid, s in rep["strategies"].items():
        g = grade(s)
        ev[sid] = {"grade": g, "label": LABELS[g],
                   "beats_hold": s["full"]["vs_hold"]["p_better"], "beats_hold_test": s["test"]["vs_hold"]["p_better"],
                   "deflated": s.get("dsr"), "cagr": s["full"].get("cagr"), "sharpe": s["full"].get("sharpe")}
    order = sorted(ev, key=lambda k: (ev[k]["grade"], -(ev[k]["beats_hold"] or 0)))
    lu = json.loads(LINEUP.read_text())
    lu["updated"] = rep["generated_at"][:10]
    lu["rule"] = RULE
    lu["keep"]["stocks"] = [k for k in order if ev[k]["grade"] != "D"] + ALWAYS
    lu["evidence"] = {"stocks": {"updated": rep["generated_at"][:10], "labels": LABELS,
                                 "hold": {"cagr": hold.get("cagr"), "sharpe": hold.get("sharpe")},
                                 "strategies": {k: ev[k] for k in order}}}
    LINEUP.write_text(json.dumps(lu, indent=2) + "\n")
    for k in order:
        print(f"  {ev[k]['grade']}  {k:20s} beats holding {ev[k]['beats_hold']:.0%} / test {ev[k]['beats_hold_test']:.0%}")
    print(f"-> {LINEUP.relative_to(ROOT)}: {len(lu['keep']['stocks'])} stock strategies on the site")


if __name__ == "__main__":
    main()
