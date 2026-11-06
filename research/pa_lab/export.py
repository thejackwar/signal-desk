"""Prompt 5 (site version): build docs/data/palab/findings.json for the Price Action Lab page.

Reads only saved result files (never raw or sealed data). Every number shown is recorded in provenance.json
(source file, row, column, format, value, text shown); provenance_check.py then re-reads every value with an
independent reader and the export is refused if anything differs or a digit on the page has no source.
"""
from __future__ import annotations

import csv
import gzip
import json
import re
from pathlib import Path

from . import config as C
from .dials import SETUP_NAMES, SETUPS

PROV: list[dict] = []


def _rows(path: Path) -> list[dict]:
    op = gzip.open if path.suffix == ".gz" else open
    with op(path, "rt") as f:
        return list(csv.DictReader(f))


def num(file: str, match: dict, column: str, fmt: str) -> str:
    """Look up one value in a saved csv/json result and record where it came from."""
    p = C.RESULTS / file
    if p.suffix == ".json":
        obj = json.loads(p.read_text())
        for k in match["path"]:
            obj = obj[k]
        val = obj
    else:
        rows = [r for r in _rows(p) if all(str(r.get(k)) == str(v) for k, v in match.items())]
        if len(rows) != 1:
            raise ValueError(f"{file} {match}: {len(rows)} rows")
        val = rows[0][column]
    text = fmt.format(float(val)) if fmt else str(val)
    PROV.append({"file": file, "match": match, "column": column, "format": fmt, "value": val, "text": text})
    return text


def series(file: str, match: dict, key_col: str, val_col: str, keys: list[str]) -> list:
    """A chart series from a saved csv, recorded value by value."""
    out = []
    for k in keys:
        m = dict(match, **{key_col: k})
        t = num(file, m, val_col, "{:.4f}")
        out.append(float(t))
    return out


def behaviour() -> dict | None:
    p = C.RESULTS / "behavior" / "behavior_tests.json"
    if not p.exists():
        return None
    js = json.loads(p.read_text())
    rows = []
    for i, t in enumerate(js["tests"]):
        m = t["main"]
        if m is None:
            continue
        base = {"path": ["tests", i, "main"]}
        rows.append({"title": t["title"], "measure": t["measure"], "verdict": t["verdict"], "claim": t["claim_sign"],
                     "effect": float(num("behavior/behavior_tests.json", {"path": ["tests", i, "main", "effect"]}, "", "{:+.3f}")),
                     "lo": float(num("behavior/behavior_tests.json", {"path": ["tests", i, "main", "lo"]}, "", "{:+.3f}")),
                     "hi": float(num("behavior/behavior_tests.json", {"path": ["tests", i, "main", "hi"]}, "", "{:+.3f}")),
                     "n": num("behavior/behavior_tests.json", {"path": ["tests", i, "main", "n"]}, "", "{:,.0f}")})
    surv = sum(r["verdict"] == "survived" for r in rows)
    split = sum(r["verdict"] == "split" for r in rows)
    ec = js["engine_checks"]
    return {"id": "behaviour", "section": "Behaviour tests", "title": "Does price respect the lines?",
            "subtitle": "Each claim measured on QQQ against a matched random baseline, checked on SPY and both eras",
            "chart": {"type": "forest", "rows": rows},
            "stats": [{"label": "claims that survived", "value": f"{surv} of {len(rows)}"}, {"label": "split", "value": f"{split}"}],
            "conclusion": "A claim survives only when its interval excludes zero on QQQ, SPY agrees and both eras agree.",
            "engine": {"random_win": num("behavior/behavior_tests.json", {"path": ["engine_checks", "random_entries", "win_rate"]}, "", "{:.0%}"),
                       "random_behaves": ec["random_entries"]["behaves"],
                       "cheat_win": num("behavior/behavior_tests.json", {"path": ["engine_checks", "cheat", "win_rate"]}, "", "{:.0%}"),
                       "cheat_behaves": ec["cheat"]["behaves"]}}


def funnel() -> list[dict]:
    f = C.RESULTS / "funnel" / "funnel_summary.json"
    if not f.exists():
        return []
    out = []
    stages = [("graded", "Graded (40+ signals)"), ("q_s1", "30+ trades"), ("q_s2", "Profitable after costs"), ("q_s3", "Sharpe 0.5+"),
              ("q_s4", "Beats random twins"), ("candidate", "Both eras + holds on SPY")]
    vals = [int(num("funnel/funnel_summary.json", {"path": [k]}, "", "{:.0f}")) for k, _ in stages]
    out.append({"id": "funnel", "section": "The funnel", "title": "From every version to a short list",
                "subtitle": "Versions left after each stage on QQQ discovery data, 2022 to July 2024",
                "chart": {"type": "funnel", "labels": [l for _, l in stages], "values": vals},
                "stats": [{"label": "versions graded", "value": f"{vals[0]:,}"}, {"label": "final-test candidates", "value": f"{vals[-1]:,}"}],
                "conclusion": "Each stage is fixed before the first run; candidates are saved and hashed before the sealed test."})
    # setups as a whole in discovery
    w = "funnel/setups_whole.csv"
    if (C.RESULTS / w).exists():
        rows = []
        for s in SETUPS:
            try:
                sh = float(num(w, {"setup": s, "market": C.MAIN, "chart": "all"}, "sharpe", "{:.2f}"))
                py = num(w, {"setup": s, "market": C.MAIN, "chart": "all"}, "per_year", "{:+,.0f}")
                works = [r for r in _rows(C.RESULTS / w) if r["setup"] == s and r["market"] == C.MAIN and r["chart"] == "all"][0]["works"] == "True"
            except (ValueError, IndexError):
                continue
            rows.append({"setup": SETUP_NAMES[s], "sharpe": sh, "per_year": py, "works": works})
        out.append({"id": "setups_discovery", "section": "The funnel", "title": "Each setup as a whole",
                    "subtitle": "Equal-weight average of all its versions, one 100-share position each, QQQ discovery",
                    "chart": {"type": "bars", "rows": [{"label": r["setup"], "value": r["sharpe"], "note": "$" + r["per_year"] + "/yr", "ok": r["works"]} for r in rows],
                              "unit": "Sharpe"},
                    "stats": [{"label": "setups that worked in discovery", "value": f"{sum(r['works'] for r in rows)} of {len(rows)}"}],
                    "conclusion": "A setup works when its average made money per year, had a Sharpe of 0.5 or more, and made money in 2022 and in 2023-24."})
    r = "funnel/rule_effects.csv"
    if (C.RESULTS / r).exists():
        eff = []
        for row in _rows(C.RESULTS / r):
            if int(row["pairs"]) < 20:
                continue
            m = {"dial": row["dial"], "value": row["value"], "vs": row["vs"]}
            eff.append({"label": f"{row['dial'].replace('f_', 'filter: ')} {row['value']} vs {row['vs']}",
                        "value": float(num(r, m, "mean_sharpe_change", "{:+.2f}")),
                        "note": num(r, m, "share_better", "{:.0%}") + " better"})
        eff.sort(key=lambda e: e["value"])
        if eff:
            out.append({"id": "rule_effects", "section": "What each rule does", "title": "One rule changed, everything else equal",
                        "subtitle": "Average change in discovery Sharpe across matched pairs of usual-settings versions",
                        "chart": {"type": "bars", "rows": [dict(e, ok=e["value"] > 0) for e in eff], "unit": "Sharpe change"},
                        "stats": [], "conclusion": "Positive bars help on average; the share is how often the change helped in a matched pair."})
    wv = "funnel/winrate_vs_profit.csv"
    if (C.RESULTS / wv).exists():
        pts = [[round(float(x["q_win_rate"]), 4), round(float(x["q_per_year"]), 2)] for x in _rows(C.RESULTS / wv)]
        PROV.append({"file": wv, "match": {}, "column": "q_win_rate,q_per_year", "format": "series", "value": len(pts), "text": "scatter"})
        out.append({"id": "winrate", "section": "What each rule does", "title": "Win rate against profit",
                    "subtitle": "A sample of graded versions: a high win rate is not the same as making money",
                    "chart": {"type": "scatter", "points": pts, "x": "win rate", "y": "$ per year"}, "stats": [],
                    "conclusion": "Versions with high win rates often lose: small targets and wide stops win often and lose big."})
    nv = "funnel/named_versions.csv"
    if (C.RESULTS / nv).exists():
        rows = []
        for row in _rows(C.RESULTS / nv):
            m = {"name": row["name"]}
            rows.append({"name": row["name"], "setup": SETUP_NAMES[row["setup"]],
                         "trades": num(nv, m, "q_trades", "{:,.0f}"), "per_year": num(nv, m, "q_per_year", "{:+,.0f}"),
                         "sharpe": num(nv, m, "q_sharpe", "{:.2f}"), "win": num(nv, m, "q_win_rate", "{:.0%}"),
                         "spy": num(nv, m, "s_sharpe", "{:.2f}"), "candidate": row["candidate"] == "True"})
        out.append({"id": "named", "section": "Systems as taught", "title": "Six systems, built as they are taught",
                    "subtitle": "Discovery results on QQQ, with the SPY Sharpe as the check",
                    "chart": {"type": "table", "cols": ["System", "Setup", "Trades", "$/yr", "Sharpe", "Win rate", "SPY Sharpe"],
                              "rows": [[r["name"], r["setup"], r["trades"], r["per_year"], r["sharpe"], r["win"], r["spy"]] for r in rows],
                              "flags": [r["candidate"] for r in rows]},
                    "stats": [], "conclusion": "Lab defaults fill in anything the usual description leaves out."})
    return out


def final() -> list[dict]:
    f = C.RESULTS / "final" / "summary.json"
    if not f.exists():
        man = C.RESULTS / "sealed_manifest.json"
        last = json.loads(man.read_text()).get("last_session") if man.exists() else None
        return [{"id": "sealed", "section": "The final test", "title": "Still sealed",
                 "subtitle": "The last two years have not been read by any rule",
                 "chart": {"type": "none"}, "stats": [{"label": "sealed from", "value": C.FINAL_FROM}] + ([{"label": "to", "value": last}] if last else []),
                 "conclusion": "It opens once, after the candidates and working setups are saved and hashed."}]
    out = []
    rep = json.loads(f.read_text())
    stats = []
    for sym in C.SYMBOLS:
        stats.append({"label": f"{sym}: all versions held up", "value": num("final/summary.json", {"path": [sym, "held_share"]}, "", "{:.1%}")})
    cq = rep[C.MAIN]["candidates"]
    if cq:
        stats = [stats[0], {"label": f"{C.MAIN}: candidates held up",
                            "value": num("final/summary.json", {"path": [C.MAIN, "candidates_held"]}, "", "{:.0f}") + " of " +
                            num("final/summary.json", {"path": [C.MAIN, "candidates"]}, "", "{:.0f}")}]
    w = "final/setups_whole.csv"
    rows = []
    for s in SETUPS:
        try:
            q = float(num(w, {"setup": s, "market": C.MAIN, "chart": "all"}, "sharpe", "{:.2f}"))
            sp = float(num(w, {"setup": s, "market": C.CHECK, "chart": "all"}, "sharpe", "{:.2f}"))
        except ValueError:
            continue
        held = [r["held"] == "True" for r in _rows(C.RESULTS / w) if r["setup"] == s and r["chart"] == "all"]
        verdict = "survived" if all(held) else ("split" if any(held) else "didn't survive")
        rows.append({"label": SETUP_NAMES[s], "value": q, "note": f"SPY {sp:.2f}", "ok": verdict == "survived", "verdict": verdict})
    out.append({"id": "final", "section": "The final test", "title": "On data no rule had seen",
                "subtitle": "August 2024 onwards, opened once; Sharpe of each setup's equal-weight average",
                "chart": {"type": "bars", "rows": rows, "unit": "Sharpe", "verdicts": True}, "stats": stats,
                "conclusion": "A setup survived when its average made money with a Sharpe of 0.5 or more on both QQQ and SPY."})
    hd = "final/held_dials.csv"
    if (C.RESULTS / hd).exists():
        top = [r for r in _rows(C.RESULTS / hd) if float(r["share_held"]) >= 0.05][:10]
        out.append({"id": "held_dials", "section": "The final test", "title": "What the survivors have in common",
                    "subtitle": "Dial values most over-represented among versions that held up on QQQ",
                    "chart": {"type": "bars", "unit": "times as common", "rows": [
                        {"label": f"{r['dial']} = {r['value']}", "value": float(num(hd, {"dial": r["dial"], "value": r["value"]}, "lift", "{:.2f}")),
                         "note": num(hd, {"dial": r["dial"], "value": r["value"]}, "share_held", "{:.0%}") + " of survivors", "ok": True} for r in top]},
                    "stats": [], "conclusion": "Read this as a hint for further research, not as a new rule: it was found after the seal opened."})
    return out


def overview() -> dict:
    spec = C.RESULTS / "rulebook_spec.json"
    counts = C.RESULTS / "session_counts.json"
    stats = []
    if spec.exists():
        stats.append({"label": "valid versions of the rules", "value": num("rulebook_spec.json", {"path": ["valid_total"]}, "", "{:,.0f}")})
    if counts.exists():
        stats.append({"label": "QQQ discovery sessions", "value": num("session_counts.json", {"path": [C.MAIN, "discovery"]}, "", "{:.0f}")})
    return {"id": "overview", "section": "The lab", "title": "Ten price action setups, tested properly",
            "subtitle": "Every rule turned into exact dials, found on 2022 to July 2024, judged on two sealed years",
            "chart": {"type": "steps", "items": ["Dials", "Behaviour", "Funnel", "Sealed test"]}, "stats": stats,
            "conclusion": "QQQ stands in for Nasdaq futures and SPY for S&P futures; regular hours only, real costs on every trade."}


def build() -> dict:
    PROV.clear()
    findings = [overview()]
    b = behaviour()
    if b:
        findings.append(b)
    findings += funnel()
    findings += final()
    status = "final" if (C.RESULTS / "final" / "summary.json").exists() else ("funnel" if (C.RESULTS / "funnel" / "funnel_summary.json").exists()
                                                                              else ("behaviour" if b else "setup"))
    doc = {"status": status, "findings": findings}
    C.SITE.mkdir(parents=True, exist_ok=True)
    (C.SITE / "provenance.json").write_text(json.dumps(PROV, indent=0, default=str))
    from .provenance_check import check
    problems = check(C.RESULTS, PROV, doc)
    if problems:
        raise SystemExit("provenance check failed:\n" + "\n".join(problems[:30]))
    (C.SITE / "findings.json").write_text(json.dumps(doc, separators=(",", ":")))
    print(f"  {len(findings)} findings, {len(PROV)} sourced values, status {status}")
    return doc


if __name__ == "__main__":
    build()
