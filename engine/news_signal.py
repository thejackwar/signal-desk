"""
Bitcoin news days, for the "News step-aside" strategy (bot_strategies.btc_news_step_aside).

  ALPACA_KEY_ID=... ALPACA_SECRET_KEY=... python engine/news_signal.py   # refresh the last few days
  python engine/news_signal.py --seed DIR                                # rebuild from the news study's files
  python engine/news_signal.py --demo                                    # made-up days, no internet

Keeps docs/data/crypto/news_signal.json: for every UTC day, how many Bitcoin headlines Alpaca's news
feed (Benzinga) had and their average tone, scored by FinBERT exactly like the news study
(research/news-study/fetch_alpaca.py): positive minus negative, price-target headlines from the numbers,
roundups tagging more than 6 symbols left out. Only these two numbers per day are kept: Alpaca's terms
don't allow republishing its headlines.

Each run rescores the last REFRESH_DAYS days from scratch (late headlines, a missed run), so the file
never double counts. The news job runs this every hour; the crypto scan (00:20 UTC) reads it.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "crypto" / "news_signal.json"
REFRESH_DAYS = 3

# The rule, fixed in the news study (research/news-study/README.md): a "loud bad-news day" has at
# least twice the usual number of headlines (the 60 days before) and a tone below the usual (the
# average of the 120 days before). After one, stay out of Bitcoin for STEP_ASIDE days.
LOUD = math.log(2)
STEP_ASIDE = 5


def load() -> dict:
    try:
        return json.loads(OUT.read_text())
    except Exception:
        return {"days": {}}


def save(state: dict) -> None:
    days = dict(sorted(state["days"].items()))
    state = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "source": state.get("source", "Alpaca news (Benzinga), Bitcoin headlines, scored by FinBERT"),
        "rule": f"Loud bad-news day: at least twice the usual headlines (60 days before) and a tone below the usual "
                f"(120 days before). The strategy stays out of Bitcoin for {STEP_ASIDE} days after one.",
        "days": days,     # "YYYY-MM-DD": [headlines, average tone]
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(state, separators=(",", ":")))


def seed(src: Path) -> dict:
    days = {}
    for f in sorted(src.glob("alpaca-*.csv*")):
        opener = gzip.open if f.suffix == ".gz" else open
        with opener(f, "rt") as fh:
            for r in csv.DictReader(fh):
                if r["group"] == "crypto" and r["sym"] == "BTC-USD":
                    days[r["day"]] = [int(r["n"]), round(float(r["mood"]), 4)]
    return {"days": days}


def refresh(state: dict, demo: bool) -> None:
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=REFRESH_DAYS - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if demo:
        rng = random.Random(7)
        d = datetime(2019, 1, 1, tzinfo=timezone.utc)
        while d <= now:
            n = rng.choice([0, 1, 2, 3, 4, 6, 9, 15])
            state["days"][f"{d:%Y-%m-%d}"] = [n, round(rng.uniform(-0.5, 0.5), 4)] if n else [0, 0.0]
            d += timedelta(days=1)
        return
    sys.path.insert(0, str(ROOT / "research" / "news-study"))
    sys.path.insert(0, str(Path(__file__).parent))
    from fetch_alpaca import FastFinBert, day_of, fetch_month   # the study's own fetch and scoring
    from sentiment import adjust
    items = fetch_month({"BTCUSD": "BTC-USD"}, start, now)
    titles = sorted({x["title"] for x in items})
    tone = dict(zip(titles, FastFinBert()(titles))) if titles else {}
    agg = defaultdict(lambda: [0, 0.0])
    for x in items:
        a = agg[day_of(x["t"], "crypto")]
        a[0] += 1
        a[1] += adjust(x["title"], tone[x["title"]])
    d = start
    while d <= now:
        k = f"{d:%Y-%m-%d}"
        n, tot = agg.get(k, [0, 0.0])
        state["days"][k] = [n, round(tot / n, 4) if n else 0.0]
        d += timedelta(days=1)
    print(f"Bitcoin headlines since {start:%Y-%m-%d}: {len(items)} -> " +
          ", ".join(f"{k} {v[0]}" for k, v in sorted(state["days"].items())[-REFRESH_DAYS:]))


# --------------------------------------------------- the signal (shared with the strategy)
def bad_days(index: pd.DatetimeIndex, days: dict) -> np.ndarray:
    """True on each day of `index` that was a loud bad-news day (the same maths as the news study's
    add_signals: tone vs the 120 days before, count vs the 60 days before; days without news are 0
    headlines and no tone). Days before the file starts are never bad."""
    idx = pd.DatetimeIndex(index).tz_localize(None) if getattr(index, "tz", None) else pd.DatetimeIndex(index)
    if not days:
        return np.zeros(len(idx), bool)
    raw = pd.DataFrame.from_dict(days, orient="index", columns=["n", "mood"])
    raw.index = pd.to_datetime(raw.index)
    raw = raw.reindex(idx)
    known = raw["n"].notna().values
    n = raw["n"].fillna(0)
    m = raw["mood"].where(n > 0)
    prev = m.shift(1).rolling(120, min_periods=20)
    vs = (m - prev.mean()) / prev.std().replace(0, np.nan)
    vol = np.log((n + 1) / (n.shift(1).rolling(60, min_periods=40).mean() + 1))
    return ((vol >= LOUD) & (vs < 0)).fillna(False).values & known


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=Path)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    if args.seed:
        state = seed(args.seed)
        print(f"seeded {len(state['days'])} days from {args.seed}")
    else:
        if not args.demo and not (os.environ.get("ALPACA_KEY_ID") and os.environ.get("ALPACA_SECRET_KEY")):
            sys.exit("ALPACA_KEY_ID / ALPACA_SECRET_KEY not set; leaving the file as it is")
        state = load()
        refresh(state, args.demo)
    save(state)
    idx = pd.date_range(min(state["days"]), max(state["days"]))   # every day: no news is 0 headlines
    bad = bad_days(idx, state["days"])
    last = [f"{d:%Y-%m-%d}" for d in idx[bad][-3:]]
    print(f"{int(bad.sum())} loud bad-news days, latest: {', '.join(last) or 'none'}")


if __name__ == "__main__":
    main()
