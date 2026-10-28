"""
How often is each stock / coin mentioned in the news?

For every company and coin the app tracks, counts the news articles that
mention it, using Google News search (news sites worldwide, English).

Google returns at most 100 articles per search, so the window narrows for
busy names: 24 hours -> 4 hours -> 1 hour (scaled up to a day and marked as
an estimate). Each run also stores that day's 24-hour count, so the
"vs a usual day" comparison builds up over the days.

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
PAUSE = 1.3                # seconds between searches, to stay polite
DAYS_LIST: list[str] = []

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


CAP = 100   # Google News returns at most 100 articles per search


def google(q: str, window: str) -> list[datetime] | None:
    from email.utils import parsedate_to_datetime
    for attempt in range(2):
        try:
            xml = _get("https://news.google.com/rss/search?" + urllib.parse.urlencode(
                {"q": f"{q} when:{window}", "hl": "en-US", "gl": "US", "ceid": "US:en"}))
            out = []
            for m in re.finditer(r"<pubDate>([^<]+)</pubDate>", xml):
                try:
                    out.append(parsedate_to_datetime(m.group(1)).astimezone(timezone.utc))
                except Exception:
                    pass
            return out
        except Exception as e:
            print(f"    google news retry ({e})")
            time.sleep(PAUSE * 4)
    return None


def count(q: str, days: list[str]) -> dict | None:
    """Daily article counts. Widest window first; narrow it while Google's 100-article cap is hit."""
    now = datetime.now(timezone.utc)
    for window, hours in (("1d", 24), ("4h", 4), ("1h", 1)):
        dates = google(q, window)
        time.sleep(PAUSE)
        if dates is None:
            return None
        if len(dates) < CAP * .95 or window == "1h":
            break
    per = {}
    for d in dates:
        k = d.strftime("%Y-%m-%d")
        per[k] = per.get(k, 0) + 1
    capped = len(dates) >= CAP * .95
    known_from = (now - timedelta(hours=hours)).strftime("%Y-%m-%d")
    last24 = sum(1 for d in dates if (now - d).total_seconds() <= 86400)
    if hours < 24:                                   # only a few hours seen: scale up to a day
        last24 = round(len(dates) * 24 / hours)
    return {"last24": last24, "window": window, "estimate": hours < 24 or capped}


def summarise(t: str, name: str, c: dict, hist: dict) -> dict:
    """Google's wide-window searches lean towards recent articles, so daily history only comes
    from this script's own runs: one 24-hour count stored per day."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    hist = {d: v for d, v in hist.items() if d >= (now - timedelta(days=DAYS)).strftime("%Y-%m-%d")}
    hist[today] = c["last24"]
    prev = [v for d, v in sorted(hist.items()) if d < today][-14:]
    usual = float(np.mean(prev)) if len(prev) >= 3 else None
    last7 = [hist[d] for d in DAYS_LIST[-7:] if d in hist]
    return {
        "ticker": t, "name": name, "series": [hist.get(d) for d in DAYS_LIST],
        "last24": c["last24"], "estimate": c["estimate"], "window": c["window"],
        "week": int(sum(last7)) if len(last7) == 7 else None,
        "usual": round(usual, 1) if usual is not None else None,
        "surge": round(c["last24"] / usual, 2) if usual and usual >= 1 else None,
        "days_known": len(hist),
        "history": hist,
    }


def run(market: str, demo: bool, previous: dict) -> dict:
    global DAYS_LIST
    names = json.loads((ENGINE / ("names.json" if market == "stocks" else "crypto_names.json")).read_text())
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=DAYS - 1 - i)).isoformat() for i in range(DAYS)]
    DAYS_LIST = days
    prev_hist = {x["ticker"]: x.get("history", {}) for x in previous.get("items", [])}
    rng = np.random.default_rng(5)
    items = []
    for t, (name, _) in names.items():
        if t in SKIP:
            continue
        if demo:
            base = rng.lognormal(3, 1.1)
            series = [int(x) for x in rng.poisson(base, DAYS)]
            if rng.random() < .15:
                series[-1] = int(series[-1] * rng.uniform(2, 5))
            prev_hist[t] = {d: v for d, v in zip(days[:-1], series[:-1])}
            c = {"last24": series[-1], "window": "1d", "estimate": False}
        else:
            c = count(query_for(t, name, market), days)
            if c is None:
                continue
        items.append(summarise(t, name, c, prev_hist.get(t, {})))
        x = items[-1]
        print(f"  {t:9s} {x['last24']:5d}{'~' if x['estimate'] else ' '} in 24h ({x['window']})  usual {x['usual']}  x{x['surge']}")
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"), "market": market, "demo": demo,
            "source": "Demo (simulated counts)" if demo else "Google News search", "days": days, "items": items}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--market", choices=["stocks", "crypto", "both"], default="both")
    args = ap.parse_args()
    for market in (["stocks", "crypto"] if args.market == "both" else [args.market]):
        print(market)
        out = ROOT / "docs" / "data" / ("mentions.json" if market == "stocks" else "crypto/mentions.json")
        try:
            previous = json.loads(out.read_text()) if out.exists() and not args.demo else {}
        except Exception:
            previous = {}
        res = run(market, args.demo, previous)
        if len(res["items"]) < 3:
            print("  too few results; keeping the previous file")
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, separators=(",", ":")))
        print(f"  -> {out.relative_to(ROOT)} ({len(res['items'])} names, {res['source']})")


if __name__ == "__main__":
    main()
