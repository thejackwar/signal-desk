"""
Collects market news headlines from public RSS feeds.

  python engine/news.py             # both markets
  python engine/news.py --demo      # sample items, no internet

Writes docs/data/news.json (stocks), docs/data/crypto/news.json (crypto) and
docs/data/commodities/news.json (commodities):
headline, link, source, time, a short summary, and the tickers / coins the
headline mentions. Only what the publishers put in their public feeds is
used, and every item links to the full article on the publisher's site.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = Path(__file__).parent
KEEP_DAYS = 4
MAX_ITEMS = 200
SUMMARY_CHARS = 220

FEEDS = {
    "stocks": [
        ("CNBC", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),          # markets
        ("CNBC", "https://www.cnbc.com/id/10001147/device/rss/rss.html"),          # business
        ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
        ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
        ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
        ("Investing.com", "https://www.investing.com/rss/news_25.rss"),            # stock market news
        ("Nasdaq", "https://www.nasdaq.com/feed/rssoutbound?category=Markets"),
        ("Seeking Alpha", "https://seekingalpha.com/market_currents.xml"),
    ],
    "crypto": [
        ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("Cointelegraph", "https://cointelegraph.com/rss"),
        ("Decrypt", "https://decrypt.co/feed"),
        ("The Block", "https://www.theblock.co/rss.xml"),
        ("Bitcoin Magazine", "https://bitcoinmagazine.com/feed"),
        ("CryptoSlate", "https://cryptoslate.com/feed/"),
    ],
    "commodities": [
        ("Investing.com", "https://www.investing.com/rss/news_11.rss"),            # commodities & futures
        ("OilPrice.com", "https://oilprice.com/rss/main"),
        ("Mining.com", "https://www.mining.com/feed/"),
        ("CNBC", "https://www.cnbc.com/id/19836768/device/rss/rss.html"),          # energy
    ],
}
NAMES_FILE = {"stocks": "names.json", "crypto": "crypto_names.json", "commodities": "commodities_names.json"}


# Company names that are also everyday words: only tag them on the ticker itself
# or with a company word next to them.
AMBIGUOUS = {"Target", "Visa", "Southern Company", "Oracle", "Meta Platforms", "Linde", "Intuit", "Charter Communications",
             "Booking Holdings", "Simon Property Group", "Ford Motor", "Merck", "Danaher", "Altria", "RTX", "UPS", "IBM", "3M"}
EXTRA_ALIASES = {
    "GOOGL": ["Alphabet", "Google"], "META": ["Meta", "Facebook", "Instagram"], "BRK-B": ["Berkshire"],
    "JPM": ["JPMorgan", "JP Morgan"], "GS": ["Goldman"], "MS": ["Morgan Stanley"], "BAC": ["Bank of America", "BofA"],
    "WFC": ["Wells Fargo"], "C": ["Citigroup", "Citi"], "TSLA": ["Tesla", "Musk"], "NVDA": ["Nvidia"], "AMZN": ["Amazon"],
    "MSFT": ["Microsoft"], "AAPL": ["Apple", "iPhone"], "F": ["Ford"], "GM": ["General Motors"], "KO": ["Coca-Cola"],
    "PEP": ["PepsiCo", "Pepsi"], "MCD": ["McDonald's"], "WMT": ["Walmart"], "COST": ["Costco"], "DIS": ["Disney"],
    "NFLX": ["Netflix"], "INTC": ["Intel"], "AMD": ["AMD"], "AVGO": ["Broadcom"], "ORCL": ["Oracle Corp", "Oracle's"],
    "BA": ["Boeing"], "LLY": ["Eli Lilly", "Lilly"], "UNH": ["UnitedHealth"], "PFE": ["Pfizer"], "XOM": ["Exxon"],
    "CVX": ["Chevron"], "V": ["Visa Inc"], "MA": ["Mastercard"], "PYPL": ["PayPal"], "UBER": ["Uber"], "PLTR": ["Palantir"],
    "CRM": ["Salesforce"], "ADBE": ["Adobe"], "QCOM": ["Qualcomm"], "SBUX": ["Starbucks"], "NKE": ["Nike"], "HD": ["Home Depot"],
    "TGT": ["Target Corp", "Target's"], "IBM": ["IBM"], "CAT": ["Caterpillar"], "DE": ["John Deere", "Deere"],
    "BTC-USD": ["Bitcoin", "BTC"], "ETH-USD": ["Ethereum", "Ether", "ETH"], "SOL-USD": ["Solana", "SOL"], "XRP-USD": ["XRP", "Ripple"],
    "BNB-USD": ["BNB", "Binance Coin"], "DOGE-USD": ["Dogecoin", "DOGE"], "ADA-USD": ["Cardano", "ADA"], "TRX-USD": ["TRON", "TRX"],
    "LINK-USD": ["Chainlink"], "XLM-USD": ["Stellar", "XLM"], "BCH-USD": ["Bitcoin Cash", "BCH"], "AVAX-USD": ["Avalanche", "AVAX"],
    "LTC-USD": ["Litecoin", "LTC"], "HBAR-USD": ["Hedera", "HBAR"], "DOT-USD": ["Polkadot"], "XMR-USD": ["Monero", "XMR"],
    "ETC-USD": ["Ethereum Classic"], "ATOM-USD": ["Cosmos", "ATOM"], "ALGO-USD": ["Algorand", "ALGO"],
    "GC=F": ["gold", "Gold", "bullion", "XAU"], "SI=F": ["silver", "Silver", "XAG"], "PL=F": ["platinum", "Platinum"],
    "PA=F": ["palladium", "Palladium"], "HG=F": ["copper", "Copper"],
    "CL=F": ["WTI", "crude", "Crude", "oil", "Oil", "OPEC", "OPEC+"], "BZ=F": ["Brent"],
    "NG=F": ["natural gas", "Natural gas", "Natural Gas", "LNG", "nat-gas", "natgas", "gas", "Gas"], "RB=F": ["gasoline", "Gasoline", "RBOB"],
    "HO=F": ["heating oil", "Heating oil", "diesel", "Diesel"], "ZC=F": ["corn", "Corn"], "ZW=F": ["wheat", "Wheat"],
    "ZS=F": ["soybean", "Soybean", "soybeans", "Soybeans", "soy", "soymeal", "Soymeal"], "KC=F": ["coffee", "Coffee", "arabica", "Arabica"],
    "CC=F": ["cocoa", "Cocoa"], "SB=F": ["sugar", "Sugar"], "CT=F": ["cotton", "Cotton"],
    "LE=F": ["cattle", "Cattle", "beef", "Beef"], "HE=F": ["hogs", "Hogs", "pork", "Pork"],
}


def matchers(market: str) -> list[tuple[str, re.Pattern]]:
    names = json.loads((ENGINE / NAMES_FILE[market]).read_text())
    out = []
    for t, (name, *_) in names.items():
        aliases = set(EXTRA_ALIASES.get(t, []))
        if name not in AMBIGUOUS and market == "stocks":
            aliases.add(name)
        if market == "stocks":
            aliases.add("$" + t)                       # cashtag
            if len(t) >= 3 and t.isalpha() and t not in {"CAT", "LOW", "ALL", "NOW", "ABT", "SO", "DE"}:
                aliases.add(t)                         # e.g. "NVDA", "AAPL" written in a headline
        pats = [re.escape(a) for a in sorted(aliases, key=len, reverse=True)]
        if pats:
            out.append((t, re.compile(r"(?<![\w$])(" + "|".join(pats) + r")(?![\w])")))
    return out


def tag(text: str, ms) -> list[str]:
    found = [t for t, rx in ms if rx.search(text)]
    if "BCH-USD" in found and "BTC-USD" in found and not re.search(r"(?<!\w)Bitcoin(?! Cash)", text):
        found.remove("BTC-USD")
    if "ETC-USD" in found and "ETH-USD" in found and not re.search(r"Ethereum(?! Classic)|\bEther\b|\bETH\b", text):
        found.remove("ETH-USD")
    if "BZ=F" in found and "CL=F" in found and not re.search(r"\bWTI\b|U\.?S\.? (crude|oil)", text):
        found.remove("CL=F")                       # "Brent crude" is about Brent, not WTI
    return found[:5]


def clean(s: str) -> str:
    s = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", " ", s)   # leftover page CSS
    s = re.sub(r"[.#]?[\w-]+\s*\{[^{}]*\}", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"(The post .* appeared first on .*|Continue reading.*|Read more.*|Read the full story.*|Image source:.*)$", "", s).strip()
    return s


def short(s: str) -> str:
    if len(s) <= SUMMARY_CHARS:
        return s
    cut = s[:SUMMARY_CHARS].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"


def when(entry) -> datetime | None:
    for k in ("published_parsed", "updated_parsed"):
        v = entry.get(k)
        if v:
            return datetime(*v[:6], tzinfo=timezone.utc)
    for k in ("published", "updated"):
        v = entry.get(k)
        if v:
            try:
                d = parsedate_to_datetime(v)
                return d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def collect(market: str) -> tuple[list[dict], dict]:
    import feedparser
    ms = matchers(market)
    items, status = {}, {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    for source, url in FEEDS[market]:
        try:
            f = feedparser.parse(url, agent="Mozilla/5.0 (InvestmentCenterSignals news reader)")
            n = 0
            for e in f.entries:
                title = clean(e.get("title", ""))
                link = e.get("link", "")
                t = when(e)
                if not title or not link.startswith("http") or not t or t < cutoff:
                    continue
                summ = short(clean(e.get("summary", "") or e.get("description", "")))
                if summ.lower().startswith(title.lower()[:40]):
                    summ = ""
                key = re.sub(r"\W+", "", title.lower())[:80]
                if key in items:
                    continue
                items[key] = {
                    "id": hashlib.md5(link.encode()).hexdigest()[:10],
                    "title": title, "link": link, "source": source,
                    "time": t.isoformat(timespec="minutes").replace("+00:00", "Z"),
                    "summary": summ,
                    "tickers": tag(title + " " + summ, ms),
                }
                n += 1
            status[source] = status.get(source, 0) + n
            print(f"  {source}: {n} items ({url})")
        except Exception as ex:
            status.setdefault(source, 0)
            print(f"  ! {source} failed: {ex}")
    out = sorted(items.values(), key=lambda x: x["time"], reverse=True)[:MAX_ITEMS]
    return out, status


def demo(market: str):
    now = datetime.now(timezone.utc)
    ms = matchers(market)
    sample = {
        "stocks": [("CNBC", "Nvidia shares climb as chip demand outlook improves"), ("Yahoo Finance", "Apple and Microsoft lead tech rebound"),
                   ("MarketWatch", "Stocks drift as investors await Fed decision"), ("Nasdaq", "Tesla deliveries beat estimates")],
        "crypto": [("CoinDesk", "Bitcoin holds near $80,000 as ETF flows turn positive"), ("Cointelegraph", "Ethereum fees hit yearly low"),
                   ("Decrypt", "Solana network upgrade goes live"), ("The Block", "XRP jumps after court ruling")],
        "commodities": [("OilPrice.com", "Crude oil slips as US inventories build"), ("Kitco", "Gold holds near record as dollar softens"),
                        ("Investing.com", "Coffee jumps on Brazil frost risk"), ("Mining.com", "Copper rises on China stimulus hopes")],
    }[market]
    out = []
    for k, (src, title) in enumerate(sample * 3):
        out.append({"id": f"demo{k}", "title": title, "link": "https://example.com/", "source": src,
                    "time": (now - timedelta(hours=k * 3)).isoformat(timespec="minutes").replace("+00:00", "Z"),
                    "summary": "Demo headline for testing the layout.", "tickers": tag(title, ms)})
    return out, {s: 3 for s, _ in sample}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    for market, path in (("stocks", ROOT / "docs" / "data" / "news.json"), ("crypto", ROOT / "docs" / "data" / "crypto" / "news.json"),
                         ("commodities", ROOT / "docs" / "data" / "commodities" / "news.json")):
        print(market)
        items, status = demo(market) if args.demo else collect(market)
        if not items:
            print(f"  no items for {market}; keeping the previous file")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "market": market, "demo": args.demo, "sources": status, "items": items,
        }, separators=(",", ":")))
        print(f"  -> {path.relative_to(ROOT)}: {len(items)} items")


if __name__ == "__main__":
    main()
