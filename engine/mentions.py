"""
How often is each stock / coin mentioned in the news?

For every company and coin the app tracks, counts the online news articles
that mention it, per day, for the last 30 days.

Source: the GDELT Project (gdeltproject.org), a free database that monitors
news sites worldwide. Its DOC API returns article counts over time for a
search, with no cap. If GDELT doesn't answer, Google News search is used as a
fallback (it returns at most 100 articles per search, so very busy names hit
that ceiling).

  python engine/mentions.py                    # both markets
  python engine/mentions.py --market crypto
  python engine/mentions.py --demo             # simulated counts, no internet

Writes docs/data/mentions.json and docs/data/crypto/mentions.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from news import EXTRA_ALIASES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ENGINE = Path(__file__).parent
DAYS = 30
GDELT_PAUSE = 5.5          # GDELT asks for at most one request every 5 seconds

# what to search for when the plain company name is also an everyday word
STOCK_QUERY = {
    "AAPL": '"Apple" (iPhone OR shares OR stock OR Nasdaq)', "TGT": '"Target Corp" OR "Target stores" OR "Target shares"',
    "V": '"Visa Inc" OR "Visa shares" OR "Visa stock"', "MA": '"Mastercard"', "META": '"Meta Platforms" OR "Facebook parent"',
    "ORCL": '"Oracle" (shares OR stock OR cloud OR Ellison)', "SO": '"Southern Company" OR "Southern Co"',
    "F": '"Ford Motor" OR "Ford shares" OR "Ford stock"', "GOOGL": '"Alphabet" OR "Google"', "BRK-B": '"Berkshire Hathaway"',
    "C": '"Citigroup"', "T": '"AT&T"', "LIN": '"Linde"', "CAT": '"Caterpillar"', "DE": '"Deere"', "LOW": "\"Lowe's\"",
    "NOW": '"ServiceNow"', "BK": '"BNY Mellon" OR "Bank of New York Mellon"', "MO": '"Altria"', "PM": '"Philip Morris"',
    "GE": '"GE Aerospace" OR "General Electric"', "GD": '"General Dynamics"', "MS": '"Morgan Stanley"', "GS": '"Goldman Sachs"',
    "COF": '"Capital One"', "SPG": '"Simon Property"', "UPS": '"UPS" (shipping OR delivery OR shares OR stock)',
    "AMT": '"American Tower"', "DUK": '"Duke Energy"', "NEE": '"NextEra"', "USB": '"U.S. Bancorp" OR "US Bancorp"',
    "MET": '"MetLife"', "AIG": '"AIG"', "AMD": '"AMD" OR "Advanced Micro Devices"', "IBM": '"IBM"', "MMM": '"3M"',
}
SKIP = {"GOOG"}            # same company as GOOGL
CRYPTO_QUERY = {
    "BTC-USD": '"Bitcoin"', "ETH-USD": '"Ethereum"',
    "BCH-USD": '"Bitcoin Cash"', "ETC-USD": '"Ethereum Classic"',
}
CRYPTO_CONTEXT = "(crypto OR cryptocurrency OR token OR blockchain OR coin)"


def _get(url: str, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (InvestmentCenterSignals)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def query_for(t: str, name: str, market: str) -> str:
    if market == "crypto":
        if t in CRYPTO_QUERY:
            return CRYPTO_QUERY[t]
        return f'"{name}" {CRYPTO_CONTEXT}'
    if t in STOCK_QUERY:
        return STOCK_QUERY[t]
    return f'"{EXTRA_ALIASES.get(t, [name])[0]}"'


def gdelt_daily(q: str, days: list[str]) -> list[int] | None:
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode({
        "query": f"{q} sourcelang:english", "mode": "timelinevolraw", "timespan": f"{DAYS}d",
        "format": "json", "timelinesmooth": 0}))
    for attempt in range(3):
        try:
            txt = _get(url)
            if not txt.strip().startswith("{"):
                raise ValueError(txt[:80])
            data = json.loads(txt)
            series = (data.get("timeline") or [{}])[0].get("data", [])
            per_day = {}
            for p in series:
                d = p["date"][:8]
                per_day[d] = per_day.get(d, 0) + int(p.get("value", 0))
            return [per_day.get(d.replace("-", ""), 0) for d in days]
        except Exception as e:
            print(f"    gdelt retry {attempt + 1}: {str(e)[:90]}")
            time.sleep(GDELT_PAUSE * (attempt + 2))
    return None


def google_daily(q: str, days: list[str]) -> list[int] | None:
    from email.utils import parsedate_to_datetime
    try:
        xml = _get("https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": f"{q} when:30d", "hl": "en-US", "gl": "US", "ceid": "US:en"}))
    except Exception as e:
        print(f"    google news failed: {e}")
        return None
    counts = dict.fromkeys(days, 0)
    for m in re.finditer(r"<pubDate>([^<]+)</pubDate>", xml):
        try:
            d = parsedate_to_datetime(m.group(1)).astimezone(timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            continue
        if d in counts:
            counts[d] += 1
    return [counts[d] for d in days]


def summarise(t: str, name: str, series: list[int]) -> dict:
    s = np.array(series, dtype=float)
    full = s[:-1]                                     # today's bucket is still filling
    d1 = float(full[-1]) if len(full) else 0.0
    d7 = float(full[-7:].sum())
    usual = float(full[:-1].mean()) if len(full) > 1 else 0.0
    usual7 = float(full[-8:-1].mean()) if len(full) > 8 else usual
    return {
        "ticker": t, "name": name, "series": [int(x) for x in s],
        "yesterday": int(d1), "today": int(s[-1]), "week": int(d7),
        "usual": round(usual, 1),
        "surge": round(d1 / usual7, 2) if usual7 >= 1 else (round(d1, 2) if d1 else 0),
    }


def run(market: str, demo: bool) -> dict:
    names = json.loads((ENGINE / ("names.json" if market == "stocks" else "crypto_names.json")).read_text())
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=DAYS - 1 - i)).isoformat() for i in range(DAYS)]
    rng = np.random.default_rng(5)
    items, used = [], {"GDELT": 0, "Google News": 0}
    for t, (name, _) in names.items():
        if t in SKIP:
            continue
        q = query_for(t, name, market)
        if demo:
            base = rng.lognormal(3.5, 1.2)
            series = list(rng.poisson(base * (1 + .3 * np.sin(np.arange(DAYS) / 4))))
            if rng.random() < .15:
                series[-2] = int(series[-2] * rng.uniform(2, 5))
        else:
            series = gdelt_daily(q, days)
            time.sleep(GDELT_PAUSE)
            if series is None:
                series = google_daily(q, days)
                if series is None:
                    continue
                used["Google News"] += 1
            else:
                used["GDELT"] += 1
        items.append(summarise(t, name, series))
        print(f"  {t:9s} {items[-1]['yesterday']:6d} yesterday  x{items[-1]['surge']}")
    src = "Demo (simulated counts)" if demo else " + ".join(f"{k} ({v})" for k, v in used.items() if v)
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"), "market": market,
            "demo": demo, "source": src, "days": days, "items": items}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--market", choices=["stocks", "crypto", "both"], default="both")
    args = ap.parse_args()
    for market in (["stocks", "crypto"] if args.market == "both" else [args.market]):
        print(market)
        res = run(market, args.demo)
        if len(res["items"]) < 3:
            print("  too few results; keeping the previous file")
            continue
        out = ROOT / "docs" / "data" / ("mentions.json" if market == "stocks" else "crypto/mentions.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, separators=(",", ":")))
        print(f"  -> {out.relative_to(ROOT)} ({len(res['items'])} names, {res['source']})")


if __name__ == "__main__":
    main()
