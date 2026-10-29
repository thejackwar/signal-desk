"""
News mood: is the latest news on each S&P 100 stock bullish or bearish?

  python engine/sentiment.py                       # Google News (what the website shows)
  ALPACA_KEY_ID=... ALPACA_SECRET_KEY=... python engine/sentiment.py --source alpaca --out FILE
  python engine/sentiment.py --demo                # made-up headlines and a simple stand-in scorer, no internet

1. Headlines: Google News searches per company (the same searches as the "Most mentioned" page),
   kept only from established news outlets (TRUSTED below), only when the headline names the
   company, and with the same story from several outlets merged into one. Each hourly run
   refreshes a third of the stocks, so every stock is searched every 3 hours.
   "--source alpaca" uses Alpaca's news feed (Benzinga) instead; Alpaca's terms don't allow
   republishing its data, so that is only for private comparisons, never the public site.
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
GOOGLE_PAUSE = 1.3       # seconds between Google News searches, to stay polite
SAME_STORY = 0.5         # headlines sharing this share of their words are the same story

# Established outlets kept from Google News (domain -> name shown). Left out on purpose: press-release
# wires (always upbeat), and sites built on "3 stocks to buy now" style articles.
TRUSTED = {
    "reuters.com": "Reuters", "apnews.com": "AP", "bloomberg.com": "Bloomberg", "wsj.com": "WSJ",
    "ft.com": "Financial Times", "cnbc.com": "CNBC", "marketwatch.com": "MarketWatch", "barrons.com": "Barron's",
    "finance.yahoo.com": "Yahoo Finance", "yahoo.com": "Yahoo Finance", "investors.com": "Investor's Business Daily",
    "morningstar.com": "Morningstar", "economist.com": "The Economist", "nytimes.com": "New York Times",
    "washingtonpost.com": "Washington Post", "theguardian.com": "The Guardian", "bbc.com": "BBC", "bbc.co.uk": "BBC",
    "cnn.com": "CNN", "foxbusiness.com": "Fox Business", "axios.com": "Axios", "fortune.com": "Fortune",
    "forbes.com": "Forbes", "businessinsider.com": "Business Insider", "latimes.com": "Los Angeles Times",
    "usatoday.com": "USA Today", "nbcnews.com": "NBC News", "cbsnews.com": "CBS News", "abcnews.go.com": "ABC News",
    "techcrunch.com": "TechCrunch", "theverge.com": "The Verge", "theinformation.com": "The Information",
    "statnews.com": "STAT", "fiercepharma.com": "Fierce Pharma", "fiercebiotech.com": "Fierce Biotech",
    "spglobal.com": "S&P Global", "americanbanker.com": "American Banker", "freightwaves.com": "FreightWaves",
    "nikkei.com": "Nikkei Asia", "asia.nikkei.com": "Nikkei Asia", "politico.com": "Politico",
}
# Opinion, comparison and listicle headlines (mostly syndicated on Yahoo Finance): not news, dropped.
FILLER = re.compile(r"key facts|here'?s why|here'?s what|should you|is it (time|too late)|what to (know|expect|watch)|"
                    r"buy or sell|buy,? sell,? or hold|stocks? to (buy|watch|own)|better (buy|stock)|reasons? to|top pick|"
                    r"\bvs\.?\b.*\bwhich\b|\bwhich\b.*\b(better|buy)\b|jim cramer|cramer (says|on)|(could|will) .* make you|"
                    r"millionaire|retire|dividend stocks?|\(\w+(-\w)?\) (rises|falls|dips|gains|outpaces|lags|trails|surpasses|"
                    r"stock (sinks|rises|falls|moves))( (higher|lower) than| as)? market|is .* (a buy|undervalued|overvalued)\??$", re.I)
SYNDICATOR = {"Yahoo Finance": 0.5}   # an aggregator's own copy counts half unless another outlet ran the story too
STOP = set("the a an and or of to in on for with at by from as is are its it this that after over into says said new than "
           "will be has have shares stock stocks".split())


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
def words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9$%.]+", title.lower()) if len(w) > 2 and w not in STOP}


def same_story(a: set[str], b: set[str]) -> bool:
    return bool(a and b) and len(a & b) / len(a | b) >= SAME_STORY


def trusted(url: str) -> str | None:
    host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    while host:
        if host in TRUSTED:
            return TRUSTED[host]
        host = host.partition(".")[2] if host.count(".") > 1 else ""
    return None


def fetch_google(tickers: list[str], window: str) -> list[dict]:
    """Google News RSS per company, trusted outlets only, headline must name the company."""
    import hashlib
    from email.utils import parsedate_to_datetime
    sys.path.insert(0, str(ENGINE))
    import news
    from mentions import query_for
    ms = news.matchers("stocks")
    out = []
    for k, t in enumerate(tickers):
        name = NAMES.get(t, [t])[0]
        q = query_for(t, name, "stocks")
        phrases = re.findall(r'"([^"]+)"', q)
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": f"{q} when:{window}", "hl": "en-US", "gl": "US", "ceid": "US:en"})
        xml = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (InvestmentCenterSignals)"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    xml = r.read().decode("utf-8", "replace")
                break
            except Exception as e:
                print(f"  {t}: google news retry ({e})")
                time.sleep(GOOGLE_PAUSE * 4)
        time.sleep(GOOGLE_PAUSE)
        if xml is None:
            continue
        kept = 0
        for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
            g = lambda tag: (re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", it, re.S) or [None, ""])[1]
            src_url = (re.search(r'<source url="([^"]+)"', it) or [None, ""])[1]
            src = trusted(src_url)
            if not src:
                continue
            title = html_unescape(g("title"))
            title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()        # Google adds " - Outlet"
            named = t in news.tag(title, ms) or any(p in title for p in phrases)
            if not title or not named or FILLER.search(title):
                continue
            try:
                when = parsedate_to_datetime(g("pubDate")).astimezone(timezone.utc)
            except Exception:
                continue
            out.append({"id": "g" + hashlib.sha1(title.lower().encode()).hexdigest()[:12], "t": iso(when), "title": title,
                        "url": html_unescape(g("link")).strip(), "src": src, "syms": [t], "nsym": 1, "about": [t], "k": "g"})
            kept += 1
        print(f"  {t}: {kept} headlines")
    return out


def html_unescape(s: str) -> str:
    import html
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.S)
    return html.unescape(html.unescape(s))


def merge_google(items: dict, fetched: list[dict]) -> list[dict]:
    """Add fetched Google headlines to items, merging the same story (from any outlet, found for any
    stock) into one headline that lists every stock it names. Returns the headlines that are new."""
    new = []
    sig = {i: words(x["title"]) for i, x in items.items()}
    for x in sorted(fetched, key=lambda x: x["t"]):
        w = words(x["title"])
        twin = x["id"] if x["id"] in items else next((i for i, ws in sig.items() if same_story(w, ws)), None)
        if twin:
            y = items[twin]
            y["syms"] = y["about"] = sorted(set(y["syms"]) | set(x["syms"]))
            y["nsym"] = len(y["syms"])
            if x["src"] not in y.get("also", []) and x["src"] != y["src"]:
                y.setdefault("also", []).append(x["src"])   # other outlets that ran the story
            continue
        items[x["id"]] = x
        sig[x["id"]] = w
        new.append(x)
    return new


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
    syn = 1.0 if item.get("also") else SYNDICATOR.get(item.get("src"), 1.0)
    return 0.5 ** (age_h / HALF_LIFE_H) * (1.0 if n <= 1 else 0.7 if n <= 3 else 0.4) * (1.0 if named else NOT_NAMED) * syn


def is_named(item: dict, t: str) -> bool:
    """Is the headline about this company? Named in it, or (Alpaca) the only company tagged."""
    return t in item.get("about", item["syms"]) or (item.get("k") != "g" and item.get("nsym", len(item["syms"])) == 1)


def named_in(items: list[dict]) -> None:
    """item["about"]: the stocks the headline names (same name matching as the Headlines page)."""
    sys.path.insert(0, str(ENGINE))
    import news
    ms = news.matchers("stocks")
    for x in items:
        if x.get("k") != "g":   # Google headlines were matched to their company when fetched
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
    ap.add_argument("--source", choices=["google", "alpaca"], default="google")
    ap.add_argument("--all", action="store_true", help="search every stock this run (default: a third, by the hour)")
    args = ap.parse_args()
    src = "demo" if args.demo else args.source
    out_path = Path(args.out)
    tickers = load_tickers()
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=WINDOW_DAYS)

    if src == "alpaca" and not (os.environ.get("ALPACA_KEY_ID") and os.environ.get("ALPACA_SECRET_KEY")):
        print("Skipped: add the ALPACA_KEY_ID and ALPACA_SECRET_KEY secrets to the repository (see README).")
        return

    old = {}
    if out_path.exists() and not args.demo:
        try:
            prev = json.loads(out_path.read_text())
            if prev.get("source_id", "alpaca") == src:   # a file from the other source starts over
                old = {x["id"]: x for x in prev.get("items", []) if parse(x["t"]) >= window_start}
        except (ValueError, KeyError):
            old = {}
    items = dict(old)
    if src == "google":
        # first run: every stock over the whole window; then a third of the stocks each hour, last day
        part = tickers if (args.all or not old) else [t for i, t in enumerate(tickers) if i % 3 == now.hour % 3]
        fetched = fetch_google(part, f"{WINDOW_DAYS}d" if not old else "1d")
        print(f"{len(fetched)} trusted headlines for {len(part)} stocks ({len(old)} already scored)")
        new = merge_google(items, [x for x in fetched if parse(x["t"]) >= window_start])
    else:
        # refetch the last 6 hours too, in case a headline was tagged late; older ones are kept from the file
        since = window_start if not old else max(window_start, max(parse(x["t"]) for x in old.values()) - timedelta(hours=6))
        fetched = (fetch_demo if args.demo else fetch_alpaca)(tickers, since, now)
        print(f"{len(fetched)} headlines since {iso(since)} ({len(old)} already scored)")
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
    keep = sorted((x for x in items.values() if parse(x["t"]) >= window_start
                   and not (x.get("k") == "g" and FILLER.search(x["title"]))), key=lambda x: x["t"], reverse=True)
    named_in(keep)
    base = baseline(keep, now)
    stocks = aggregate(keep, tickers, now, base)
    out = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M+00:00"), "demo": args.demo,
        "model": "Keyword stand-in (demo)" if args.demo else "FinBERT (ProsusAI/finbert)",
        "source": {"demo": "Demo headlines", "alpaca": "Alpaca news (Benzinga)", "google": "Google News, established outlets only"}[src],
        "source_id": src,
        "window_days": WINDOW_DAYS, "baseline": round(base, 3), "half_life_h": HALF_LIFE_H, "headline_cut": HEADLINE_CUT, "mood_cuts": list(MOOD_CUTS),
        "stocks": stocks,
        "items": [{k: x[k] for k in ("id", "t", "title", "url", "src", "syms", "nsym", "about", "f", "s", "k", "also") if k in x} for x in keep],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, separators=(",", ":")))
    moods = [v["mood"] for v in stocks.values()]
    print(f"Scored {len(new)} new headlines; {len(stocks)} stocks with news: "
          + ", ".join(f"{m} {moods.count(m)}" for m in ("bullish", "leaning_bullish", "neutral", "leaning_bearish", "bearish", "quiet")))


if __name__ == "__main__":
    sys.exit(main())
