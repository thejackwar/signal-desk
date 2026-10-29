"""
News mood: is the latest news on each S&P 100 stock bullish or bearish?

  ALPACA_KEY_ID=... ALPACA_SECRET_KEY=... python engine/sentiment.py
  python engine/sentiment.py --demo        # made-up headlines and a simple stand-in scorer, no internet

1. Headlines tagged with each stock come from Alpaca's news feed (Benzinga), the same Alpaca
   account the Top-Down alerts use (secrets ALPACA_KEY_ID / ALPACA_SECRET_KEY).
2. Each new headline is scored by FinBERT (ProsusAI/finbert), a free language model trained on
   financial news: probability positive minus probability negative, from -1 to +1.
   Scores are kept in the output file, so each run only scores headlines it hasn't seen.
3. Each stock's mood is the weighted average of its headlines over the last 7 days: newer
   headlines count more (half-life 2 days), roundups that tag many companies count less, and a
   headline that doesn't name the company itself (it was only tagged) counts a quarter, since
   its tone is usually about someone else.
4. "vs usual": company news is mostly upbeat, so each stock's score is also published minus the
   usual tone (the same weighted average over every headline in the window, all stocks together).

Writes docs/data/sentiment.json. Information only: headline tone, not a forecast.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = Path(__file__).parent
OUT = ROOT / "docs" / "data" / "sentiment.json"
NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
MODEL = "ProsusAI/finbert"

WINDOW_DAYS = 7          # headlines older than this are dropped
HALF_LIFE_H = 48         # a 2-day-old headline counts half as much as a new one
MAX_SYMBOLS = 6          # headlines tagging more companies than this are market roundups: skipped
MIN_HEADLINES = 3        # fewer than this in the window -> "not enough news"
HEADLINE_CUT = 0.35      # a headline scoring above +0.35 is bullish, below -0.35 bearish
MOOD_CUTS = (0.10, 0.35) # stock score: leaning beyond 0.10, clear beyond 0.35
MAX_PAGES = 80
NOT_NAMED = 0.25         # weight of a headline that is tagged with the stock but doesn't name it


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_tickers() -> list[str]:
    lines = (ENGINE / "tickers.txt").read_text().splitlines()
    return [x.strip() for x in lines if x.strip() and not x.startswith("#")]


NAMES = json.loads((ENGINE / "names.json").read_text())


def alpaca_symbol(t: str) -> str:
    return t.replace("-", ".")   # BRK-B (Yahoo) -> BRK.B (Alpaca)


# ------------------------------------------------------------------ fetch
def fetch_alpaca(tickers: list[str], start: datetime, end: datetime) -> list[dict]:
    key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
    by_alp = {alpaca_symbol(t): t for t in tickers}
    out, token, pages = [], None, 0
    while pages < MAX_PAGES:
        params = {"symbols": ",".join(by_alp), "start": iso(start), "end": iso(end), "limit": 50,
                  "sort": "desc", "include_content": "false"}
        if token:
            params["page_token"] = token
        req = urllib.request.Request(NEWS_URL + "?" + urllib.parse.urlencode(params),
                                     headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:   # rate limited: wait and retry
                    time.sleep(3 * (attempt + 1))
                    continue
                raise
        for n in data.get("news", []):
            syms = n.get("symbols") or []
            mine = sorted({by_alp[s] for s in syms if s in by_alp})
            title = (n.get("headline") or "").strip()
            if not mine or not title:
                continue
            out.append({"id": str(n["id"]), "t": iso(parse(n["created_at"])), "title": title,
                        "url": n.get("url") or "", "src": (n.get("source") or "").title() or "Benzinga",
                        "syms": mine, "nsym": len(syms)})
        pages += 1
        token = data.get("next_page_token")
        if not token:
            break
    return out


def fetch_demo(tickers: list[str], start: datetime, end: datetime, seed: int = 4) -> list[dict]:
    rng = random.Random(seed)
    good = ["{} beats estimates and raises full-year guidance", "{} upgraded to Buy at a major broker",
            "{} shares surge after record quarterly revenue", "{} wins large new contract"]
    bad = ["{} misses earnings estimates, cuts outlook", "{} downgraded on weak demand",
           "{} shares plunge after regulator opens probe", "{} faces lawsuit over product recall"]
    flat = ["{} to present at industry conference", "{} announces date of quarterly results",
            "{} names new chief financial officer"]
    out, span = [], (end - start).total_seconds()
    for k, t in enumerate(tickers):
        lean = rng.choice([-1, -1, 0, 0, 0, 1, 1, 1])
        for j in range(rng.randint(0, 9)):
            pool = rng.choice([good] * (2 + lean) + [bad] * (2 - lean) + [flat] * 2)
            when = start + timedelta(seconds=rng.random() * span)
            extra = [x for x in rng.sample(tickers, rng.choice([0, 0, 0, 1, 2]))]
            syms = sorted({t, *extra})
            name = NAMES.get(t, [t])[0] if rng.random() < .7 else t
            out.append({"id": f"demo{k}-{j}", "t": iso(when), "title": rng.choice(pool).format(name), "url": "",
                        "src": "Demo", "syms": syms, "nsym": len(syms)})
    return out


# ------------------------------------------------------------------ score
class FinBert:
    """FinBERT on CPU. Scores headlines in batches: P(positive) - P(negative)."""
    def __init__(self):
        from transformers import pipeline
        self.pipe = pipeline("text-classification", model=MODEL, top_k=None, truncation=True)

    def __call__(self, titles: list[str]) -> list[float]:
        out = []
        for i in range(0, len(titles), 32):
            for res in self.pipe(titles[i:i + 32]):
                p = {r["label"].lower(): r["score"] for r in res}
                out.append(p.get("positive", 0) - p.get("negative", 0))
        return out


class KeywordScorer:
    """Stand-in for --demo and the tests (no model download): counts a few finance words."""
    POS = ("beat", "beats", "raise", "raises", "upgrade", "upgraded", "surge", "surges", "record", "wins", "jumps", "soars")
    NEG = ("miss", "misses", "cut", "cuts", "downgrade", "downgraded", "plunge", "plunges", "probe", "lawsuit", "recall", "falls")

    def __call__(self, titles: list[str]) -> list[float]:
        out = []
        for t in titles:
            w = [x.strip(",.;:!?'\"").lower() for x in t.split()]
            p, n = sum(x in self.POS for x in w), sum(x in self.NEG for x in w)
            out.append(0.0 if p == n else math.copysign(min(0.95, 0.55 + 0.2 * abs(p - n)), p - n))
        return out


# ------------------------------------------------- analyst price targets
# FinBERT reads "Adjusts Price Target on X to $230 From $240" as positive whether the target went
# up or down (about 1 headline in 5 is one of these), so those are scored from the numbers:
# a cut is bearish, a raise bullish, more so the bigger the change; a tiny change stays neutral.
PT = re.compile(r"price target", re.I)
PT_FROM = re.compile(r"to \$?([\d,]+(?:\.\d+)?)\s+from \$?([\d,]+(?:\.\d+)?)", re.I)
PT_UP = re.compile(r"\b(raises?|lifts?|boosts?|increases?|hikes?|ups)\b[^.]*price target", re.I)
PT_DOWN = re.compile(r"\b(lowers?|cuts?|reduces?|trims?|slashes?|decreases?)\b[^.]*price target", re.I)


def adjust(title: str, finbert: float) -> float:
    """Final tone of a headline: FinBERT's score, except for analyst price-target changes."""
    if not PT.search(title):
        return finbert
    m = PT_FROM.search(title)
    if m:
        new, old = (float(g.replace(",", "")) for g in m.groups())
        if old > 0 and new != old:
            ch = new / old - 1
            return round(math.copysign(min(0.8, 0.3 + 3 * abs(ch)), ch), 3)   # 5% raise -> +0.45, 10% cut -> -0.6
        return 0.0
    if PT_UP.search(title):
        return 0.5
    if PT_DOWN.search(title):
        return -0.5
    return round(max(-0.25, min(0.25, finbert)), 3)   # "maintains", "adjusts" with no numbers: about neutral


# -------------------------------------------------------------- aggregate
def label(s: float) -> str:
    return "bull" if s >= HEADLINE_CUT else "bear" if s <= -HEADLINE_CUT else "neu"


def weight(item: dict, t: str, now: datetime) -> float:
    age_h = max(0.0, (now - parse(item["t"])).total_seconds() / 3600)
    n = item.get("nsym", len(item["syms"]))
    named = is_named(item, t)
    return 0.5 ** (age_h / HALF_LIFE_H) * (1.0 if n <= 1 else 0.7 if n <= 3 else 0.4) * (1.0 if named else NOT_NAMED)


def is_named(item: dict, t: str) -> bool:
    """Is the headline about this company? Named in it, or the only company Alpaca tagged."""
    return t in item.get("about", item["syms"]) or item.get("nsym", len(item["syms"])) == 1


def named_in(items: list[dict]) -> None:
    """item["about"]: the stocks the headline names (same name matching as the Headlines page)."""
    sys.path.insert(0, str(ENGINE))
    import news
    ms = news.matchers("stocks")
    for x in items:
        x["about"] = [t for t in news.tag(x["title"], ms) if t in x["syms"]]


def mood_of(score: float) -> str:
    lean, clear = MOOD_CUTS
    return ("bullish" if score >= clear else "leaning_bullish" if score >= lean else
            "bearish" if score <= -clear else "leaning_bearish" if score <= -lean else "neutral")


def baseline(items: list[dict], now: datetime) -> float:
    """The usual tone: every headline in the window once, newer counting more (same half-life)."""
    ws = [0.5 ** (max(0.0, (now - parse(x["t"])).total_seconds() / 3600) / HALF_LIFE_H) for x in items]
    return sum(w * x["s"] for w, x in zip(ws, items)) / sum(ws) if items else 0.0


def aggregate(items: list[dict], tickers: list[str], now: datetime, base: float = 0.0) -> dict:
    """Per stock: the weighted mood over every tagged headline, but the counts, the drivers and
    the "enough news" test use only headlines that name the company."""
    stocks = {}
    for t in tickers:
        mine = [x for x in items if t in x["syms"]]
        named = [x for x in mine if is_named(x, t)]
        if not mine:
            continue
        ws = [weight(x, t, now) for x in mine]
        score = sum(w * x["s"] for w, x in zip(ws, mine)) / sum(ws)
        labs = [label(x["s"]) for x in named]
        m = mood_of(score) if len(named) >= MIN_HEADLINES else "quiet"
        sign = 1 if m.endswith("bullish") else -1 if m.endswith("bearish") else 0
        pool = named or mine
        ranked = sorted(pool, key=lambda x: -(weight(x, t, now) * (x["s"] * sign if sign else 1)))
        stocks[t] = {
            "mood": m, "score": round(score, 3), "n": len(named), "tagged": len(mine),
            # compared with the usual tone of all S&P 100 news in the window ("vs usual" in the app)
            "rel": round(score - base, 3), "rel_mood": mood_of(score - base) if m != "quiet" else "quiet",
            "bull": labs.count("bull"), "bear": labs.count("bear"), "neu": labs.count("neu"),
            "n48": sum(1 for x in named if (now - parse(x["t"])).total_seconds() <= 48 * 3600),
            "top": [x["id"] for x in ranked[:3]],
            "latest": max(x["t"] for x in pool),
        }
    return stocks


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    out_path = Path(args.out)
    tickers = load_tickers()
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=WINDOW_DAYS)

    if not args.demo and not (os.environ.get("ALPACA_KEY_ID") and os.environ.get("ALPACA_SECRET_KEY")):
        print("Skipped: add the ALPACA_KEY_ID and ALPACA_SECRET_KEY secrets to the repository (see README).")
        return

    old = {}
    if out_path.exists() and not args.demo:
        try:
            prev = json.loads(out_path.read_text())
            old = {x["id"]: x for x in prev.get("items", []) if parse(x["t"]) >= window_start}
        except (ValueError, KeyError):
            old = {}
    # refetch the last 6 hours too, in case a headline was tagged late; older ones are kept from the file
    since = window_start if not old else max(window_start, max(parse(x["t"]) for x in old.values()) - timedelta(hours=6))
    fetched = (fetch_demo if args.demo else fetch_alpaca)(tickers, since, now)
    print(f"{len(fetched)} headlines since {iso(since)} ({len(old)} already scored)")

    items = dict(old)
    new = [x for x in fetched if x["id"] not in old and x["nsym"] <= MAX_SYMBOLS]
    for x in fetched:   # keep tags fresh on ones we already scored
        if x["id"] in items:
            items[x["id"]].update(syms=x["syms"], nsym=x["nsym"])
    if new:
        scorer = KeywordScorer() if args.demo else FinBert()
        for x, s in zip(new, scorer([x["title"] for x in new])):
            x["f"] = round(s, 3)
            items[x["id"]] = x
    for x in items.values():   # "f" = FinBERT's own score, "s" = final tone (price targets from their numbers)
        x.setdefault("f", x.get("s", 0.0))
        x["s"] = adjust(x["title"], x["f"])
    keep = sorted((x for x in items.values() if parse(x["t"]) >= window_start), key=lambda x: x["t"], reverse=True)
    named_in(keep)
    base = baseline(keep, now)
    stocks = aggregate(keep, tickers, now, base)
    out = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M+00:00"), "demo": args.demo,
        "model": "Keyword stand-in (demo)" if args.demo else "FinBERT (ProsusAI/finbert)",
        "source": "Demo headlines" if args.demo else "Alpaca news (Benzinga)",
        "window_days": WINDOW_DAYS, "baseline": round(base, 3), "half_life_h": HALF_LIFE_H, "headline_cut": HEADLINE_CUT, "mood_cuts": list(MOOD_CUTS),
        "stocks": stocks,
        "items": [{k: x[k] for k in ("id", "t", "title", "url", "src", "syms", "nsym", "about", "f", "s")} for x in keep],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, separators=(",", ":")))
    moods = [v["mood"] for v in stocks.values()]
    print(f"Scored {len(new)} new headlines; {len(stocks)} stocks with news: "
          + ", ".join(f"{m} {moods.count(m)}" for m in ("bullish", "leaning_bullish", "neutral", "leaning_bearish", "bearish", "quiet")))


if __name__ == "__main__":
    sys.exit(main())
