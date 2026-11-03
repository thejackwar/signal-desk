"""News study, step 1a: one year of Alpaca/Benzinga headlines, scored by FinBERT, summed per asset and day.

  ALPACA_KEY_ID=... ALPACA_SECRET_KEY=... python research/news-study/fetch_alpaca.py --year 2021 --out DIR
  python research/news-study/fetch_alpaca.py --year 2021 --out DIR --demo     # made-up headlines, no internet

Three groups, fetched month by month (oldest first):
  stocks       the S&P 100 (engine/tickers.txt)
  crypto       the biggest coins (Alpaca tags them BTCUSD, ETHUSD, ...)
  commodities  news tagged with the gold, silver and oil ETFs (GLD, SLV, USO)

Each headline gets the same tone as the website's News mood: FinBERT (positive minus negative, -1..+1)
with the price-target fix from engine/sentiment.py. Roundups that tag more than 6 symbols are skipped.

Which day a headline counts for (so nothing is known before it happened):
  stocks and ETFs: New York time; anything from 15:50 on counts for the next session
  crypto: the UTC day (the daily candle closes at 00:00 UTC)
The study maps weekend and holiday dates to the next trading day.

Writes DIR/alpaca-<year>.csv: group, sym (Yahoo symbol), day, n, mood (mean tone), pos, neg.
Alpaca's terms don't allow republishing its headlines, so only these daily counts are kept.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
from sentiment import KeywordScorer, adjust  # noqa: E402

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
NY = ZoneInfo("America/New_York")
CUTOFF = (15, 50)        # NY time: from here on a headline counts for the next session
MAX_SYMBOLS = 6
BULL = 0.35
CRYPTO = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "LTC", "AVAX", "DOT", "BCH", "XLM"]
COMMOD = {"GLD": "GLD", "IAU": "GLD", "SLV": "SLV", "USO": "USO"}   # IAU news counts as gold too


def groups() -> dict[str, dict[str, str]]:
    """group -> {alpaca symbol: yahoo symbol}"""
    lines = (ROOT / "engine" / "tickers.txt").read_text().splitlines()
    stocks = [x.strip() for x in lines if x.strip() and not x.startswith("#")]
    return {
        "stocks": {t.replace("-", "."): t for t in stocks},
        "crypto": {f"{c}USD": f"{c}-USD" for c in CRYPTO},
        "commodities": dict(COMMOD),
    }


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(params: dict) -> dict:
    req = urllib.request.Request(NEWS_URL + "?" + urllib.parse.urlencode(params), headers={
        "APCA-API-KEY-ID": os.environ["ALPACA_KEY_ID"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]})
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 7:
                time.sleep(min(60, 4 * 2 ** attempt))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < 7:
                time.sleep(10)
                continue
            raise
    raise RuntimeError("unreachable")


def fetch_month(syms: dict[str, str], start: datetime, end: datetime) -> list[dict]:
    out, token = [], None
    while True:
        p = {"symbols": ",".join(syms), "start": iso(start), "end": iso(end), "limit": 50,
             "sort": "asc", "include_content": "false"}
        if token:
            p["page_token"] = token
        data = get(p)
        for n in data.get("news", []):
            tags = n.get("symbols") or []
            mine = sorted({syms[s] for s in tags if s in syms})
            title = (n.get("headline") or "").strip()
            if mine and title and len(tags) <= MAX_SYMBOLS:
                out.append({"t": n["created_at"], "title": title, "syms": mine})
        token = data.get("next_page_token")
        if not token:
            return out
        time.sleep(0.32)   # free plan: 200 requests a minute


def fetch_demo(syms: dict[str, str], start: datetime, end: datetime) -> list[dict]:
    rng = random.Random(start.toordinal())
    words = ["beats estimates", "misses estimates", "upgraded", "downgraded", "announces buyback",
             "faces lawsuit", "surges", "plunges", "reports quarterly results", "names new CFO"]
    out, ys = [], sorted(set(syms.values()))
    t = start
    while t < end:
        t += timedelta(minutes=rng.randint(20, 300))
        s = rng.choice(ys)
        out.append({"t": iso(t), "title": f"{s} {rng.choice(words)}", "syms": [s]})
    return out


class FastFinBert:
    """FinBERT on CPU, short headlines only (64 tokens), big batches."""
    def __init__(self):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        torch.set_num_threads(os.cpu_count() or 4)
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained("ProsusAI/finbert")
        self.model = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert").eval()
        lab = {v.lower(): k for k, v in self.model.config.id2label.items()}
        self.pos, self.neg = lab["positive"], lab["negative"]

    def __call__(self, titles: list[str]) -> list[float]:
        out = []
        with self.torch.inference_mode():
            for i in range(0, len(titles), 128):
                enc = self.tok(titles[i:i + 128], padding=True, truncation=True, max_length=64, return_tensors="pt")
                p = self.model(**enc).logits.softmax(-1)
                out += (p[:, self.pos] - p[:, self.neg]).tolist()
        return out


def day_of(created: str, group: str) -> str:
    t = datetime.fromisoformat(created.replace("Z", "+00:00"))
    if group == "crypto":
        return t.astimezone(timezone.utc).date().isoformat()
    ny = t.astimezone(NY)
    d = ny.date()
    if (ny.hour, ny.minute) >= CUTOFF:
        d += timedelta(days=1)
    return d.isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    score = KeywordScorer() if args.demo else FastFinBert()
    fetch = fetch_demo if args.demo else fetch_month
    now = datetime.now(timezone.utc)
    rows = []
    for group, syms in groups().items():
        agg = defaultdict(lambda: [0, 0.0, 0, 0])
        for m in range(1, 13):
            start = datetime(args.year, m, 1, tzinfo=timezone.utc)
            end = datetime(args.year + (m == 12), m % 12 + 1, 1, tzinfo=timezone.utc)
            if start >= now:
                break
            t0 = time.time()
            items = fetch(syms, start, min(end, now))
            titles = sorted({x["title"] for x in items})
            tone = dict(zip(titles, score(titles)))
            for x in items:
                s = adjust(x["title"], tone[x["title"]])
                d = day_of(x["t"], group)
                for y in x["syms"]:
                    a = agg[(y, d)]
                    a[0] += 1
                    a[1] += s
                    a[2] += s >= BULL
                    a[3] += s <= -BULL
            print(f"{group} {args.year}-{m:02d}: {len(items)} headlines ({len(titles)} distinct) in {time.time() - t0:.0f}s",
                  flush=True)
        rows += [(group, y, d, n, round(tot / n, 4), p, q) for (y, d), (n, tot, p, q) in sorted(agg.items())]
    with open(args.out / f"alpaca-{args.year}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group", "sym", "day", "n", "mood", "pos", "neg"])
        w.writerows(rows)
    print(f"wrote {len(rows)} asset-days")


if __name__ == "__main__":
    main()
