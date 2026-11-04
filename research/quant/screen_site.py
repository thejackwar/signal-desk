"""
Builds docs/data/research.json for the site's Research page (docs/research.html) from the
strategy screen (results/screen/) and, when present, the improvement tests (results/improve/).

Each strategy gets a plain-English description (data/screen_descriptions.json), its numbers for
both test periods, a monthly growth curve from 2018 against holding the same stocks, and the
reason it passed or failed, worked out from its numbers by fixed rules (below).

  python research/quant/screen_site.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCREEN = HERE / "results" / "screen"
IMPROVE = HERE / "results" / "improve"
DESC = json.loads((HERE / "data" / "screen_descriptions.json").read_text())
OUT = ROOT / "docs" / "data" / "research.json"
CURVE_FROM = "2018-01-01"      # the site shows the two test periods only


def pct(v):
    return f"{v * 100:.1f}%" if v is not None else "–"


def why(r, hs, hc, hold_cagr):
    """(outcome, reason) from the numbers. Rules fixed in advance, same for every strategy."""
    s, c = r["screen"], r["confirm"]
    sh, s0, cg = s.get("sharpe"), s.get("sharpe_0bp"), s.get("cagr")
    expo, turn = s.get("exposure") or 0, s.get("turnover") or 0
    beats_c = (c.get("vs_hold") or {}).get("p_better")
    low_expo = expo < 0.6 and cg is not None and cg < hold_cagr - 0.03
    expo_note = (f" It was in the market only {expo * 100:.0f}% of the time, so it earned {pct(cg)} a year vs "
                 f"{pct(hold_cagr)} for holding: a high Sharpe isn't the same as a high return.") if low_expo else ""
    v = r.get("verdict")
    if v == "Confirmed":
        return "passed", (f"Beat holding the same stocks in 2021–now and again in 2018–2020, years that weren't used to pick it "
                          f"(it beat holding in {beats_c * 100:.0f}% of the resampled tests there).") + expo_note
    if v == "Mixed":
        return "mixed", ("Beat holding in 2021–now, but in 2018–2020 it was only about even with holding: "
                         "not clearly better, not clearly worse.") + expo_note
    if v == "Failed":
        return "failed_check", (f"Beat holding in 2021–now, but lost to it in 2018–2020 (Sharpe {c.get('sharpe', 0):.2f} vs "
                                f"{hc:.2f}). The win didn't repeat, so it was probably luck or one kind of market.") + expo_note
    if r.get("intraday") and sh is not None and sh < hs:
        return "costs" if (s0 or -9) > hs else "worse", (
            f"Trades in and out every day ({r['intraday']}), so it pays costs constantly. "
            + (f"Before costs it would have beaten holding (Sharpe {s0:.2f} vs {hs:.2f}); after them it didn't." if (s0 or -9) > hs
               else f"Even before costs it didn't beat holding (Sharpe {s0:.2f} vs {hs:.2f})."))
    if s0 is not None and s0 > hs + 0.05 and (sh or -9) <= hs:
        return "costs", (f"Before trading costs it beat holding (Sharpe {s0:.2f} vs {hs:.2f}), but it trades its whole account "
                         f"about {turn:.0f} times a year and the costs ate the edge (Sharpe {sh:.2f} after them).")
    if low_expo:
        return "out_of_market", (f"Smaller drops, but it was in the market only {expo * 100:.0f}% of the time and earned "
                                 f"{pct(cg)} a year vs {pct(hold_cagr)} for holding. Sitting out also meant missing the gains.")
    if sh is not None and abs(sh - hs) <= 0.08:
        return "no_edge", (f"Almost exactly the same as holding (Sharpe {sh:.2f} vs {hs:.2f}). "
                           "No real edge, just extra trading.")
    hold_dd = r.get("_hold_dd")
    if sh is not None and cg is not None and cg > hold_cagr + 0.005:
        return "riskier", (f"Made more than holding ({pct(cg)} vs {pct(hold_cagr)} a year), but with much bigger swings "
                           f"(worst drop {pct(s.get('maxdd'))} vs {pct(hold_dd)}). Per unit of risk it did worse "
                           f"(Sharpe {sh:.2f} vs {hs:.2f}).")
    return "worse", (f"Did worse than simply holding the same stocks: Sharpe {sh:.2f} vs {hs:.2f}, "
                     f"{pct(cg)} a year vs {pct(hold_cagr)}." if sh is not None else "No usable result.")


def slim(st):
    if not st:
        return {}
    return {"cagr": st.get("cagr"), "sharpe": st.get("sharpe"), "maxdd": st.get("maxdd"),
            "beats": (st.get("vs_hold") or {}).get("p_better"), "turnover": st.get("turnover"),
            "exposure": st.get("exposure"), "sharpe0": st.get("sharpe_0bp"), "sharpe25": st.get("sharpe_25bp")}


def monthly_curve(r: pd.Series) -> list:
    r = r[r.index >= CURVE_FROM].astype(float).fillna(0)
    eq = (1 + r).cumprod()
    m = eq.groupby(eq.index.to_period("M")).last()
    return [round(float(v) * 100, 1) for v in m.values]


def main():
    rep = json.loads((SCREEN / "screen.json").read_text())
    nets = pd.read_parquet(SCREEN / "nets.parquet")
    b = rep["benchmarks"]
    hs, hc = b["hold_same_stocks"]["screen"]["sharpe"], b["hold_same_stocks"]["confirm"]["sharpe"]
    hold_cagr = b["hold_same_stocks"]["screen"]["cagr"]
    months = [str(p) for p in nets.index[nets.index >= CURVE_FROM].to_period("M").unique()]
    out = []
    for sid, r in rep["strategies"].items():
        name, what, idea = DESC.get(sid, [sid, "", ""])
        r["_hold_dd"] = b["hold_same_stocks"]["screen"]["maxdd"]
        outcome, reason = why(r, hs, hc, hold_cagr)
        out.append({"id": sid, "name": name, "what": what, "idea": idea, "family": r["family"], "intraday": bool(r.get("intraday")),
                    "outcome": outcome, "reason": reason, "screen": slim(r["screen"]), "confirm": slim(r["confirm"]),
                    "curve": monthly_curve(nets[sid]) if sid in nets else []})
    site = {"generated": rep["meta"]["generated"], "tried": rep["meta"]["tried"], "cost_bps": rep["meta"]["cost_bps"],
            "screen": "2021–now", "confirm": "2018–2020", "months": months,
            "benchmarks": {k: {"screen": slim(v["screen"]), "confirm": slim(v["confirm"])} for k, v in b.items()},
            "hold_curve": monthly_curve(nets["_hold"]) if "_hold" in nets else [],
            "spy_curve": monthly_curve(nets["_spy"]) if "_spy" in nets else [],
            "strategies": out}

    if (IMPROVE / "improve.json").exists():                 # the improvement tests, when they've run
        im = json.loads((IMPROVE / "improve.json").read_text())
        inets = pd.read_parquet(IMPROVE / "nets.parquet")
        groups = []
        for g, pk in im["picks"].items():
            mem = [s for s, v in im["strategies"].items() if v["group"] == g]
            groups.append({"name": g, "pick": pk["pick"], "verdict": pk["verdict"],
                           "variants": [{"id": s, "base": im["strategies"][s]["base"], "dev": slim(im["strategies"][s]["dev"]),
                                         "curve": monthly_curve(inets[s]) if s in inets else []} for s in mem],
                           "holdout": slim(im["strategies"][pk["pick"]]["holdout"]),
                           "dsr": im["strategies"][pk["pick"]].get("dsr_full")})
        site["improve"] = {"generated": im["meta"]["generated"], "dev": "2018–now", "holdout": "2011–2017",
                           "hold_holdout": slim(im["benchmarks"]["hold_same_stocks"]["holdout"]),
                           "hold_dev": slim(im["benchmarks"]["hold_same_stocks"]["dev"]), "groups": groups}

    OUT.write_text(json.dumps(site, separators=(",", ":")))
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(out)} strategies, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
