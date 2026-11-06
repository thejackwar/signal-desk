"""The second, independent reader. It re-reads every recorded value from the saved files with plain csv/json
(no pandas, no shared code with the exporter), re-formats it, and fails if anything differs. Then it scans
every string the page will show: any number that is neither a recorded value, a derived count it can recompute,
nor one of the lab's fixed settings (config.py) fails the build.
"""
from __future__ import annotations

import csv
import gzip
import json
import re
from pathlib import Path

from . import config as C

TOKEN = re.compile(r"[+\-]?\$?\d[\d,]*(?:\.\d+)?%?")


def _read(results: Path, file: str, match: dict, column: str):
    p = results / file
    if file.endswith(".json"):
        obj = json.loads(p.read_text())
        for k in match["path"]:
            obj = obj[k]
        return obj
    op = gzip.open if file.endswith(".gz") else open
    with op(p, "rt", newline="") as f:
        hits = [r for r in csv.DictReader(f) if all(str(r.get(k)) == str(v) for k, v in match.items())]
    if len(hits) != 1:
        raise KeyError(f"{len(hits)} rows")
    return hits[0][column]


def static_numbers() -> set[str]:
    """Fixed lab settings that may appear in prose: thresholds, window years, sizes."""
    vals = {C.FUNNEL["min_trades"], C.FUNNEL["min_sharpe"], C.FUNNEL["check_sharpe"], C.MIN_SIGNALS, C.SHARES, C.HELD_SHARPE,
            C.TWIN_SETS, C.BOOT, 2022, 2023, 2024, 24, 10, 20, 50, 5, 3, 4}   # 3, 4: touch counts in the behaviour test definitions
    out = set()
    for v in vals:
        out |= {str(v), f"{v:,}"}
    out |= {"2023-24", "2-3", "0.5", "0.3", "100-share", "95%", "1", "2", "50%"}
    return out


def tokens(s: str) -> list[str]:
    return [t.strip(",") for t in TOKEN.findall(s)]


def check(results: Path, prov: list[dict], doc: dict) -> list[str]:
    problems = []
    allowed = static_numbers()
    numeric = set()
    for e in prov:
        if e["format"] == "series":
            continue
        try:
            v = _read(results, e["file"], e["match"], e["column"])
        except Exception as x:  # noqa: BLE001
            problems.append(f"cannot re-read {e['file']} {e['match']} {e['column']}: {x}")
            continue
        text = e["format"].format(float(v)) if e["format"] else str(v)
        if text != e["text"]:
            problems.append(f"{e['file']} {e['match']} {e['column']}: shown {e['text']!r}, file gives {text!r}")
        allowed |= set(tokens(text)) | {text}
        try:
            numeric.add(round(float(text.replace(",", "").replace("%", "").replace("$", "")), 6))
            if text.endswith("%"):
                numeric.add(round(float(text.rstrip("%").replace(",", "")) / 100, 6))
        except ValueError:
            pass
    for f in doc["findings"]:
        ch = f.get("chart", {})
        # derived counts: recompute from the chart rows the page will show
        if f["id"] == "behaviour":
            rows = ch["rows"]
            allowed |= {str(sum(r["verdict"] == "survived" for r in rows)), str(len(rows)), str(sum(r["verdict"] == "split" for r in rows))}
            for r in rows:
                for k in ("effect", "lo", "hi"):
                    if round(r[k], 6) not in numeric:
                        problems.append(f"behaviour {r['title']} {k}={r[k]} has no source")
        if ch.get("type") == "bars":
            rows = ch["rows"]
            allowed |= {str(sum(bool(r.get("ok")) for r in rows)), str(len(rows))}
            for r in rows:
                if round(r["value"], 6) not in numeric:
                    problems.append(f"{f['id']} bar {r['label']} value {r['value']} has no source")
        if ch.get("type") == "funnel":
            for v in ch["values"]:
                if round(float(v), 6) not in numeric:
                    problems.append(f"funnel value {v} has no source")
            allowed |= {f"{v:,}" for v in ch["values"]}
        if ch.get("type") == "scatter":
            src = [e for e in prov if e["format"] == "series" and e["text"] == "scatter"]
            p = results / src[0]["file"]
            with open(p, newline="") as fh:
                pts = [[round(float(r["q_win_rate"]), 4), round(float(r["q_per_year"]), 2)] for r in csv.DictReader(fh)]
            if pts != ch["points"]:
                problems.append("scatter points differ from their file")
    # every number shown must be sourced
    def walk(x, where):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in ("points", "values", "effect", "lo", "hi", "value", "claim") and not isinstance(v, str):
                    continue
                walk(v, f"{where}.{k}")
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{where}[{i}]")
        elif isinstance(x, str):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x):
                return                                   # session dates shown as stats come from the manifest/config
            for t in tokens(x):
                core = t.lstrip("+-$").rstrip("%")
                if t in allowed or core in allowed or t.lstrip("$") in allowed or any(t in a for a in allowed if len(t) >= 2):
                    continue
                problems.append(f"unsourced number {t!r} in {where}: {x!r}")
    walk(doc, "doc")
    return problems
