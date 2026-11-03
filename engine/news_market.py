"""
Stock-market news tone from GDELT, for the "News Contrarian" strategy (bot_strategies.news_contrarian).

  python engine/news_market.py                 # refresh the last few days from GDELT
  python engine/news_market.py --seed DIR      # rebuild from the news study's gdelt.csv(.gz)

GDELT scores the tone of the world's online news. The search is the news study's "market" theme:
articles mentioning "stock market" or "Wall Street", English sources. Kept per UTC day: the number of
articles and their average tone, in docs/data/news_market.json.

The rule (research/news-study/FINDINGS.md): a gloomy day is one whose tone is in the lowest fifth of
the past year. The study found the S&P 500 did better than usual in the days after those. A UTC day
ends at 19:00/20:00 New York time, after the US close, so a day's tone is only used from the next
session's close.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "news_market.json"
QUERY = '("stock market" OR "wall street") sourcelang:english'
REFRESH_DAYS = 10
GLOOMY_PCT = 0.2      # lowest fifth of the past year (252 sessions, at least 60)
HOLD = 5              # sessions held after the last gloomy day


def load() -> dict:
    try:
        return json.loads(OUT.read_text())
    except Exception:
        return {"days": {}}


def save(state: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "updated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "source": "GDELT 2.0 (worldwide online news), articles mentioning the stock market or Wall Street",
        "rule": f"Gloomy day: tone in the lowest fifth of the past year. The strategy buys at the next open "
                f"after the next session's close and holds {HOLD} sessions after the last gloomy day.",
        "days": dict(sorted(state["days"].items())),   # "YYYY-MM-DD": [articles, average tone]
    }, separators=(",", ":")))


def seed(src: Path) -> dict:
    days = {}
    for f in sorted(src.glob("gdelt.csv*")):
        opener = gzip.open if f.suffix == ".gz" else open
        with opener(f, "rt") as fh:
            for r in csv.DictReader(fh):
                if r["theme"] == "market":
                    days[r["day"]] = [int(r["n"]), round(float(r["tone"]), 4)]
    return {"days": days}


def refresh(state: dict) -> None:
    sys.path.insert(0, str(ROOT / "research" / "news-study"))
    import fetch_gdelt as G   # the study's own fetch (slow back-off: GDELT throttles cloud machines)
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=REFRESH_DAYS)
    tone = G.get(QUERY, "timelinetone", start, today)
    vol = G.get(QUERY, "timelinevolraw", start, today)
    done = today.isoformat()
    for d, t in tone.items():
        if d < done and d in vol:            # today isn't finished yet
            state["days"][d] = [int(vol[d]), round(t, 4)]
    print(f"GDELT market news: {len(tone)} days of tone since {start}")


# ----------------------------------------------- the signal (shared with the strategy)
def sessions(index: pd.DatetimeIndex, days: dict) -> pd.DataFrame:
    """Each news day counts from the first session strictly after it (weekend news adds up into
    Monday), exactly like the study's to_sessions(lag=True). Returns n, mood and gloomy per session."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out = pd.DataFrame(index=idx)
    out["n"], out["mood"] = 0.0, np.nan
    if not days:
        out["gloomy"] = False
        return out
    raw = pd.DataFrame.from_dict(days, orient="index", columns=["n", "mood"])
    raw.index = pd.to_datetime(raw.index)
    raw = raw[raw["n"] > 0]
    pos = idx.searchsorted(raw.index.values, side="right")
    ok = pos < len(idx)
    x = raw[ok].assign(session=idx[pos[ok]])
    x = x.assign(tot=x["mood"] * x["n"])
    s = x.groupby("session")[["n", "tot"]].sum()
    out["n"] = s["n"].reindex(idx).fillna(0)
    out["mood"] = (s["tot"] / s["n"]).reindex(idx)
    lo = out["mood"].shift(1).rolling(252, min_periods=60).quantile(GLOOMY_PCT)
    out["gloomy"] = (out["mood"] < lo).fillna(False)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=Path)
    args = ap.parse_args()
    if args.seed:
        state = seed(args.seed)
        print(f"seeded {len(state['days'])} days")
    else:
        state = load()
        refresh(state)
    save(state)
    idx = pd.bdate_range(min(state["days"]), datetime.now(timezone.utc).date())
    g = sessions(idx, state["days"])["gloomy"]
    print(f"{int(g.sum())} gloomy sessions, latest: {', '.join(f'{d:%Y-%m-%d}' for d in g.index[g.values][-3:]) or 'none'}")


if __name__ == "__main__":
    main()
