"""News study, step 1b: daily tone and article counts from GDELT (worldwide online news, free).

  python research/news-study/fetch_gdelt.py --out DIR [--start 2019]
  python research/news-study/fetch_gdelt.py --out DIR --demo        # made-up series, no internet

GDELT's DOC API scores the tone of every article it finds (negative = gloomy, positive = upbeat)
and counts how many matched a search. One search per theme, English sources only:
  oil      crude oil / oil prices / OPEC         -> CL=F, USO
  gold     gold price(s)                          -> GC=F, GLD
  silver   silver price(s)                        -> SI=F, SLV
  bitcoin  bitcoin                                -> BTC-USD
  market   stock market / Wall Street             -> SPY
Days are UTC days. The study only uses a day's GDELT numbers from the next trading close on, since a
UTC day ends at 19:00/20:00 New York time, after the US close.

Writes DIR/gdelt.csv: theme, day, n (articles), tone (average tone).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

URL = "https://api.gdeltproject.org/api/v2/doc/doc"
THEMES = {
    "oil": '("crude oil" OR "oil price" OR "oil prices" OR OPEC) sourcelang:english',
    "gold": '("gold price" OR "gold prices" OR "price of gold") sourcelang:english',
    "silver": '("silver price" OR "silver prices" OR "price of silver") sourcelang:english',
    "bitcoin": "bitcoin sourcelang:english",
    "market": '("stock market" OR "wall street") sourcelang:english',
}
PAUSE = 6   # GDELT asks for at most one request every 5 seconds


def get(query: str, mode: str, start: date, end: date) -> dict[str, float]:
    p = {"query": query, "mode": mode, "format": "json", "TIMELINESMOOTH": 0,
         "startdatetime": start.strftime("%Y%m%d000000"), "enddatetime": end.strftime("%Y%m%d235959")}
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(p), headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(6):
        time.sleep(PAUSE)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read().decode("utf-8", "replace")
            data = json.loads(raw)
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print(f"  retry {mode} {start}: {str(e)[:80]}", flush=True)
            time.sleep(PAUSE * (attempt + 2))
    else:
        return {}
    vals: dict[str, list[float]] = {}
    for series in data.get("timeline", [])[:1]:   # volraw also has a "total articles" series: skip it
        for pt in series.get("data", []):
            d = datetime.strptime(pt["date"][:8], "%Y%m%d").date().isoformat()
            vals.setdefault(d, []).append(float(pt["value"]))
    # if GDELT answers in finer steps than a day: counts add up, tone averages
    return {d: (sum(v) if mode == "timelinevolraw" else sum(v) / len(v)) for d, v in vals.items()}


def demo(theme: str, start: date, end: date):
    rng = random.Random(theme)
    d, out = start, []
    while d <= end:
        out.append((theme, d.isoformat(), int(800 + 300 * rng.random()), round(-1 + math.sin(d.toordinal() / 9) + rng.gauss(0, .6), 3)))
        d += timedelta(days=1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--start", type=int, default=2019)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date()
    rows = []
    for theme, q in THEMES.items():
        if args.demo:
            rows += demo(theme, date(args.start, 1, 1), today)
            continue
        for y in range(args.start, today.year + 1):
            # half-years keep each answer at one point per day
            for a, b in ((date(y, 1, 1), date(y, 6, 30)), (date(y, 7, 1), date(y, 12, 31))):
                if a > today:
                    continue
                b = min(b, today)
                tone = get(q, "timelinetone", a, b)
                vol = get(q, "timelinevolraw", a, b)
                rows += [(theme, d, int(vol.get(d, 0)), round(t, 4)) for d, t in sorted(tone.items())]
                print(f"{theme} {a}..{b}: {len(tone)} days of tone, {len(vol)} of counts", flush=True)
    with open(args.out / "gdelt.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["theme", "day", "n", "tone"])
        w.writerows(rows)
    print(f"wrote {len(rows)} theme-days")


if __name__ == "__main__":
    main()
