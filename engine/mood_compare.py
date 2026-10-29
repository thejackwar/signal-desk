"""
News mood source test (29 Oct - 3 Nov 2026): Google News (the website) vs Alpaca/Benzinga.

  python engine/mood_compare.py GOOGLE.json ALPACA.json OUT_DIR     # save today's snapshot
  python engine/mood_compare.py --report OUT_DIR                     # summarise every day saved

Each run overwrites OUT_DIR/<UTC date>.json, so the last run of each day is kept. Only each
stock's mood, score and headline count are saved, never Alpaca's headline text (its terms don't
allow republishing it, and this repository is public).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SIDE = {"bullish": 1, "leaning_bullish": 1, "neutral": 0, "leaning_bearish": -1, "bearish": -1}


def snapshot(google: Path, alpaca: Path, out_dir: Path) -> None:
    G, A = json.loads(google.read_text()), json.loads(alpaca.read_text())
    keys = ("mood", "rel_mood", "score", "rel", "n")
    rows = {}
    for t in sorted(set(G["stocks"]) | set(A["stocks"])):
        g, a = G["stocks"].get(t), A["stocks"].get(t)
        rows[t] = {"g": [g[k] for k in keys] if g else None, "a": [a[k] for k in keys] if a else None}
    out = {"saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M+00:00"), "columns": list(keys),
           "baseline": {"g": G.get("baseline"), "a": A.get("baseline")},
           "headlines": {"g": len(G.get("items", [])), "a": len(A.get("items", []))}, "stocks": rows}
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{datetime.now(timezone.utc):%Y-%m-%d}.json"
    path.write_text(json.dumps(out, separators=(",", ":")))
    print(f"Saved {path} ({len(rows)} stocks)")


def report(out_dir: Path) -> None:
    for path in sorted(out_dir.glob("*.json")):
        d = json.loads(path.read_text())
        both = {t: r for t, r in d["stocks"].items() if r["g"] and r["a"] and r["g"][0] != "quiet" and r["a"][0] != "quiet"}
        cov_g = sum(1 for r in d["stocks"].values() if r["g"] and r["g"][0] != "quiet")
        cov_a = sum(1 for r in d["stocks"].values() if r["a"] and r["a"][0] != "quiet")
        for k, lab in ((0, "tone"), (1, "vs usual")):
            same = sum(SIDE[r["g"][k]] == SIDE[r["a"][k]] for r in both.values())
            opp = sum(SIDE[r["g"][k]] * SIDE[r["a"][k]] == -1 for r in both.values())
            print(f"{path.stem} {lab:9}: {len(both)} stocks rated by both; same side {same} ({same / max(1, len(both)):.0%}), "
                  f"opposite {opp}")
        print(f"           coverage: Google {cov_g}, Alpaca {cov_a}; headlines G {d['headlines']['g']} A {d['headlines']['a']}; "
              f"usual tone G {d['baseline']['g']} A {d['baseline']['a']}")


if __name__ == "__main__":
    if sys.argv[1] == "--report":
        report(Path(sys.argv[2]))
    else:
        snapshot(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
