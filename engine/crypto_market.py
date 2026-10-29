"""
Crypto market overview data for the website (docs/data/crypto/market.json), and the long euro
price history used by the DCA calculator and the seasonality page (docs/data/crypto/long.json).

market.json (every 4 hours, with the liquidation heatmap):
  fng        Fear & Greed index, today and the last year            alternative.me
  global     total market cap, Bitcoin and Ethereum dominance        CoinGecko /global
  derivs     funding rate now and 7-day average, open interest,     OKX perpetual swaps
             long/short account ratio (BTC, ETH)
  ethbtc     ETH/BTC price ratio, daily, last 2 years                Yahoo Finance
  altseason  share of the app's coins that beat Bitcoin over 90 days, daily, last year
  corr       30- and 90-day correlation of Bitcoin's daily moves with the Nasdaq 100 (QQQ)
             and gold (GLD), last 2 years                            Yahoo Finance
  etf        US spot Bitcoin and Ethereum ETF net flows, US$ million  Farside Investors
  halving    next Bitcoin halving: block height, blocks left, estimated date  blockchain.info

long.json (daily, with the crypto scan): daily closes in euro for the biggest coins since they
were listed on Yahoo, plus Bitcoin in dollars. Kept small: 6 significant digits.

Every source is optional: when one fails, its part is left out (or the previous copy is kept)
and the rest is still written.

  python engine/crypto_market.py            # market.json
  python engine/crypto_market.py --long     # long.json
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "docs" / "data" / "crypto"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
DERIV_COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "LINK", "AVAX", "LTC"]
LONG_COINS = ["BTC", "ETH", "XRP", "BNB", "SOL", "DOGE", "ADA", "TRX", "LINK", "LTC", "DOT", "AVAX"]
HALVING_EVERY = 210_000


def get(url: str, raw: bool = False, tries: int = 3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
            return body if raw else json.loads(body)
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(2 * (k + 1))


def part(name: str, fn, out: dict, sources: list, label: str):
    try:
        v = fn()
        if v:
            out[name] = v
            sources.append(label)
            print(f"  ok   {name}")
    except Exception as e:
        print(f"  !    {name}: {e}")


# ------------------------------------------------------------------ sources
def fear_greed():
    d = get("https://api.alternative.me/fng/?limit=400")["data"]
    hist = sorted((datetime.fromtimestamp(int(x["timestamp"]), timezone.utc).strftime("%Y-%m-%d"), int(x["value"])) for x in d)
    now = d[0]
    return {"value": int(now["value"]), "label": now["value_classification"], "hist": [list(h) for h in hist[-366:]]}


def global_caps():
    g = get("https://api.coingecko.com/api/v3/global")["data"]
    p = g["market_cap_percentage"]
    return {"mcap_usd": round(g["total_market_cap"]["usd"]), "vol_usd": round(g["total_volume"]["usd"]),
            "chg24h": round(g.get("market_cap_change_percentage_24h_usd", 0), 2),
            "btc_dom": round(p.get("btc", 0), 2), "eth_dom": round(p.get("eth", 0), 2),
            "stable_dom": round(sum(p.get(k, 0) for k in ("usdt", "usdc", "dai", "usde", "fdusd")), 2)}


def okx(path: str):
    j = get("https://www.okx.com/api/v5/" + path)
    if j.get("code") != "0":
        raise RuntimeError(j.get("msg") or j.get("code"))
    return j["data"]


def derivatives():
    rows = []
    for c in DERIV_COINS:
        inst = f"{c}-USDT-SWAP"
        try:
            now = okx(f"public/funding-rate?instId={inst}")[0]
            hist = okx(f"public/funding-rate-history?instId={inst}&limit=21")      # 3 a day = 7 days
            oi = okx(f"public/open-interest?instType=SWAP&instId={inst}")[0]
            rates = [float(h["realizedRate"] or h["fundingRate"]) for h in hist]
            row = {"coin": c, "funding": round(float(now["fundingRate"]) * 100, 4),
                   "funding7d": round(float(np.mean(rates)) * 100, 4) if rates else None,
                   "oi_usd": round(float(oi["oiUsd"]))}
            try:     # open interest a week ago (all OKX contracts on this coin, US$)
                h = okx(f"rubik/stat/contracts/open-interest-volume?ccy={c}&period=1D")
                if len(h) > 7:
                    row["oi_chg7d"] = round((float(h[0][1]) / float(h[7][1]) - 1) * 100, 1)
            except Exception:
                pass
            if c in ("BTC", "ETH"):
                try:
                    ls = okx(f"rubik/stat/contracts/long-short-account-ratio?ccy={c}&period=1D")
                    row["long_short"] = float(ls[0][1])
                    row["long_short_hist"] = [[datetime.fromtimestamp(int(t) / 1000, timezone.utc).strftime("%Y-%m-%d"), float(v)] for t, v in ls[:60]][::-1]
                except Exception:
                    pass
            rows.append(row)
            time.sleep(0.25)
        except Exception as e:
            print(f"     {c}: {e}")
    return rows


def yahoo(tickers: list[str], period: str = "3y") -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(tickers, period=period, interval="1d", auto_adjust=True, progress=False, threads=True)
    close = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]].rename(columns={"Close": tickers[0]})
    close.index = pd.DatetimeIndex(close.index).tz_localize(None).normalize()
    return close


def coins_list() -> list[str]:
    raw = (ROOT / "engine" / "crypto.txt").read_text().splitlines()
    return [x.strip() for x in raw if x.strip() and not x.startswith("#")]


def from_yahoo(out: dict, sources: list):
    coins = coins_list()
    try:
        px = yahoo(coins + ["QQQ", "GLD"], "3y")
    except Exception as e:
        print(f"  !    yahoo: {e}")
        return
    day = lambda i: i.strftime("%Y-%m-%d")
    btc = px["BTC-USD"].dropna()
    # ETH/BTC
    try:
        r = (px["ETH-USD"] / px["BTC-USD"]).dropna().iloc[-730:]
        out["ethbtc"] = [[day(i), round(float(v), 6)] for i, v in r.items()]
        print("  ok   ethbtc")
    except Exception as e:
        print(f"  !    ethbtc: {e}")
    # altcoin season: share of the other coins with a better 90-day return than Bitcoin
    try:
        crypto = px[[c for c in coins if c in px]].dropna(how="all")
        r90 = crypto / crypto.shift(90) - 1
        alts = [c for c in crypto.columns if c != "BTC-USD"]
        beat = r90[alts].gt(r90["BTC-USD"], axis=0)
        known = r90[alts].notna()
        share = (beat.sum(axis=1) / known.sum(axis=1).replace(0, np.nan) * 100).dropna().iloc[-366:]
        last = r90.iloc[-1]
        out["altseason"] = {"value": round(float(share.iloc[-1])), "coins": int(known.iloc[-1].sum()),
                            "hist": [[day(i), round(float(v))] for i, v in share.items()],
                            "r90": {c.replace("-USD", ""): round(float(last[c]) * 100, 1) for c in crypto.columns if pd.notna(last[c])}}
        print("  ok   altseason")
    except Exception as e:
        print(f"  !    altseason: {e}")
    # correlation with the Nasdaq 100 and gold, on US trading days
    try:
        us = px[["BTC-USD", "QQQ", "GLD"]].dropna()
        ret = us.pct_change().dropna()
        corr = {}
        for k, t in (("nasdaq", "QQQ"), ("gold", "GLD")):
            c30 = ret["BTC-USD"].rolling(30).corr(ret[t])
            c90 = ret["BTC-USD"].rolling(90).corr(ret[t])
            both = pd.concat([c30, c90], axis=1).dropna().iloc[-504:]
            corr[k] = {"now30": round(float(both.iloc[-1, 0]), 2), "now90": round(float(both.iloc[-1, 1]), 2),
                       "hist": [[day(i), round(float(a), 2), round(float(b), 2)] for i, (a, b) in both.iterrows()]}
        out["corr"] = corr
        print("  ok   corr")
    except Exception as e:
        print(f"  !    corr: {e}")
    sources.append("Yahoo Finance")
    return btc


def etf_flows():
    out = {}
    for k in ("btc", "eth"):
        page = get(f"https://farside.co.uk/{k}/", raw=True)
        tables = re.findall(r"<table.*?</table>", page, re.S)
        rows = []
        for tb in tables:
            for tr in re.findall(r"<tr.*?</tr>", tb, re.S):
                cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).replace("\xa0", " ").strip() for c in re.findall(r"<t[hd].*?</t[hd]>", tr, re.S)]
                if not cells or not re.fullmatch(r"\d{1,2} \w{3} \d{4}", cells[0]):
                    continue
                v = cells[-1].replace(",", "")
                if v in ("-", ""):
                    continue
                neg = v.startswith("(")
                try:
                    num = float(v.strip("()"))
                except ValueError:
                    continue
                d = datetime.strptime(cells[0], "%d %b %Y").strftime("%Y-%m-%d")
                rows.append([d, round(-num if neg else num, 1)])
        rows = sorted({d: v for d, v in rows}.items())
        # the newest day often shows 0.0 until every fund has reported: drop a trailing exact zero
        if rows and rows[-1][1] == 0:
            rows = rows[:-1]
        if rows:
            out[k] = [list(r) for r in rows[-120:]]
    return out


def halving():
    s = get("https://api.blockchain.info/stats")
    h = int(s["n_blocks_total"])
    nxt = (h // HALVING_EVERY + 1) * HALVING_EVERY
    mins = float(s.get("minutes_between_blocks") or 10)
    eta = datetime.now(timezone.utc) + timedelta(minutes=(nxt - h) * min(max(mins, 9), 11))
    return {"height": h, "next": nxt, "left": nxt - h, "eta": eta.strftime("%Y-%m-%d"),
            "past": ["2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20"]}


# ------------------------------------------------------------------ outputs
def build_market():
    path = DIR / "market.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    out, sources = {}, []
    print("Crypto market data:")
    part("fng", fear_greed, out, sources, "alternative.me Fear & Greed")
    part("global", global_caps, out, sources, "CoinGecko")
    part("derivs", derivatives, out, sources, "OKX perpetual swaps")
    from_yahoo(out, sources)
    part("etf", etf_flows, out, sources, "Farside Investors")
    part("halving", halving, out, sources, "blockchain.info")
    for k, v in old.items():                     # a source that failed this time keeps its last copy
        if k not in out and k not in ("generated_at", "sources"):
            out[k] = v
    if len(out) < 2:
        sys.exit("Too few sources worked: market.json left as it was.")
    out = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M+00:00"), "sources": sources, **out}
    path.write_text(json.dumps(out, separators=(",", ":")))
    print(f"Wrote {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")


def sig(v: float) -> float:
    return float(f"{v:.6g}")


def build_long():
    tick = [f"{c}-EUR" for c in LONG_COINS] + ["BTC-USD"]
    px = yahoo(tick, "max")
    dates = pd.date_range(px.index.min(), px.index.max(), freq="D")
    px = px.reindex(dates)
    out = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M+00:00"), "start": dates[0].strftime("%Y-%m-%d"),
           "days": len(dates), "eur": {}, "usd": {}}
    for t in tick:
        if t not in px:
            continue
        s = px[t].ffill()
        first = s.first_valid_index()
        if first is None:
            continue
        i0 = dates.get_loc(first)
        vals = [sig(v) for v in s.iloc[i0:].values]
        (out["usd"] if t.endswith("-USD") else out["eur"])[t.split("-")[0]] = {"s": int(i0), "c": vals}
    path = DIR / "long.json"
    path.write_text(json.dumps(out, separators=(",", ":")))
    print(f"Wrote {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB, {len(out['eur'])} coins in euro)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--long", action="store_true", help="write long.json (daily euro history) instead")
    a = ap.parse_args()
    build_long() if a.long else build_market()
