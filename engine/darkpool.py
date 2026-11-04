"""Dark pools: what the off-exchange market is doing in the S&P 100. Three free sources, one script.

  daily     FINRA daily short sale volume (Reg SHO, consolidated "CNMS" file), published by 18:00 New York
            every trading day. It covers trades reported to FINRA's facilities, i.e. OFF-exchange trading:
            dark pools AND wholesalers filling retail orders. Per stock we take the short ratio
            (short volume / total off-exchange volume) and compare it with that stock's last 60 days.
            The popular reading (SqueezeMetrics' "DIX"): when buyers lift offers off-exchange, market makers
            fill them by selling short, so an unusually HIGH short ratio = unusual off-exchange BUYING.
            It's a theory, not a fact, which is why the backtest exists.
            Also an S&P 100 "dark index": the dollar-weighted short ratio of all the stocks together.
  weekly    FINRA ATS transparency data: shares traded in each named dark pool (UBS, Goldman, JPMorgan...)
            per stock per week. Published 2 weeks late for S&P 100 stocks (NMS Tier 1).
  blocks    Large off-exchange prints from Alpaca's SIP trade feed (exchange code "D" = reported to a FINRA
            facility), every 30 minutes while the market is open. Alpaca's free plan serves SIP trades
            15+ minutes old, so the window always ends 16 minutes ago. A print is a "block" at $1M+.
            Each block is compared with the last lit (exchange) trade before it: above = buyer paid up,
            below = seller hit down, at = no lean. Prints are also off-exchange retail fills, see the page.
  backfill  Downloads the FINRA daily files from --since (default 2019-01-02) for the backtest.
  backtest  Does the daily score predict returns? Event study (next open -> close 5/10/20 days later)
            vs the same stocks on every day, split in-sample (2019-2022) and out-of-sample (2023 on),
            plus two simple strategies vs buy & hold SPY. 0.10% cost per trade, no taxes.

Writes docs/data/darkpool/: daily.json, weekly.json, blocks.json, backtest.json (the site),
shortvol.csv (daily history, the backtest data), blocks_history.csv (one row per stock per day),
ats/<week>.json (raw weekly venue volumes).

    python engine/darkpool.py daily | weekly | blocks | backfill | backtest
    python engine/darkpool.py demo          # synthetic data for all four outputs, no keys or network
Blocks need ALPACA_KEY_ID / ALPACA_SECRET_KEY. Weekly works without keys; if FINRA ever asks for them,
set FINRA_API_CLIENT / FINRA_API_SECRET (free account at developer.finra.org).
Phone alerts for big blocks on the ntfy topic <NTFY_PREFIX>-darkpool.
"""
from __future__ import annotations

import base64
import csv
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
OUT = ROOT / "docs" / "data" / "darkpool"
NY = ZoneInfo("America/New_York")
ETFS = ["SPY", "QQQ", "IWM"]
UA = {"User-Agent": "signal-desk/1.0 (github.com/thejackwar/signal-desk)"}
CNMS = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{}.txt"
FINRA_API = "https://api.finra.org/data/group/otcMarket/name/weeklySummary"
ALPACA = "https://data.alpaca.markets/v2/stocks/trades"

Z_WIN = 60            # days of history the daily z-score compares with
Z_HOT = 1.5           # |z| at or above this = "unusual"
BLOCK_MIN = 1_000_000  # $ notional for a block
KEEP_PRINTS = 1500
# trade conditions that aren't a normal print at the current price (average-price, derivatively priced,
# prior reference, out of sequence, contingent / qualified contingent, opening/closing/cross prints)
ODD_CONDS = {"W", "B", "4", "P", "Z", "U", "T", "V", "7", "M", "Q", "O", "6", "5", "9", "H", "X"}


def key(sym: str) -> str:                           # BRK-B (Yahoo), BRK.B (Alpaca), BRK/B... -> BRKB
    return re.sub(r"[^A-Z0-9]", "", sym.upper())


def universe() -> list[str]:
    lines = (ENGINE / "tickers.txt").read_text().splitlines()
    return [x.strip() for x in lines if x.strip() and not x.startswith("#")] + ETFS


def names() -> dict:
    try:
        return {k: v[0] for k, v in json.loads((ENGINE / "names.json").read_text()).items()}
    except Exception:
        return {}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False))
    print(f"  wrote {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path} ({path.stat().st_size // 1024} KB)")


def r(x, n=2):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), n)


def http(url: str, data: bytes | None = None, headers: dict | None = None, timeout=40, tries=3):
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})},
                                         method="POST" if data is not None else "GET")
            return urllib.request.urlopen(req, timeout=timeout).read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):                 # FINRA's CDN answers 403 for a file that doesn't exist
                raise
            last = e
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After") or 10))
                continue
        except Exception as e:                       # timeouts, resets
            last = e
        time.sleep(2 + 3 * k)
    raise last


# ====================================================================== daily: FINRA short volume
def fetch_cnms(d: date) -> dict[str, tuple[int, int]] | None:
    """{KEY: (short volume, total volume)} for one day, None if FINRA has no file (holiday, not out yet)."""
    try:
        raw = http(CNMS.format(d.strftime("%Y%m%d")), timeout=60).decode("utf-8", "replace")
    except urllib.error.HTTPError:
        return None
    rows = raw.strip().splitlines()
    if not rows or "|" not in rows[0]:
        return None
    head = [h.strip() for h in rows[0].split("|")]
    try:
        i_s, i_sv, i_tv = head.index("Symbol"), head.index("ShortVolume"), head.index("TotalVolume")
    except ValueError:
        print(f"  ! unexpected FINRA header {head}")
        return None
    out = {}
    for line in rows[1:]:
        p = line.split("|")
        if len(p) <= max(i_s, i_sv, i_tv):
            continue                                  # the trailer line
        try:
            out[key(p[i_s])] = (int(float(p[i_sv])), int(float(p[i_tv])))
        except ValueError:
            continue
    return out or None


def weekdays(a: date, b: date):
    d = a
    while d <= b:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def load_hist() -> dict[str, dict[str, tuple[int, int]]]:
    """{date: {TICKER: (short, total)}} from shortvol.csv."""
    h: dict = {}
    f = OUT / "shortvol.csv"
    if f.exists():
        with f.open() as fh:
            for row in csv.DictReader(fh):
                h.setdefault(row["date"], {})[row["symbol"]] = (int(row["short"]), int(row["total"]))
    return h


def save_hist(h: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "shortvol.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "symbol", "short", "total"])
        for d in sorted(h):
            for s in sorted(h[d]):
                w.writerow([d, s, *h[d][s]])


def update_hist(h: dict, since: date, until: date, workers: int = 8) -> int:
    tick = universe()
    want = {key(t): t for t in tick}
    days = [d for d in weekdays(since, until) if d.isoformat() not in h]
    if not days:
        return 0
    print(f"  FINRA daily files: {len(days)} day(s) {days[0]} .. {days[-1]}")
    added = 0
    with ThreadPoolExecutor(workers) as ex:
        for d, got in zip(days, ex.map(fetch_cnms, days)):
            if not got:
                continue
            h[d.isoformat()] = {want[k]: got[k] for k in want if k in got and got[k][1] > 0}
            added += 1
    print(f"  added {added} day(s)")
    return added


def prices(tickers: list[str], start: str) -> "tuple":
    """Adjusted open/close/volume from Yahoo, the source the rest of the site uses."""
    import pandas as pd
    import yfinance as yf
    raw = yf.download(tickers, start=start, interval="1d", group_by="ticker", auto_adjust=True,
                      progress=False, threads=True)
    o, c, v = {}, {}, {}
    for t in tickers:
        try:
            df = raw[t].dropna()
        except KeyError:
            continue
        if len(df):
            o[t], c[t], v[t] = df["Open"], df["Close"], df["Volume"]
    idx = lambda s: pd.DataFrame(s).sort_index().rename(index=lambda x: x.strftime("%Y-%m-%d"))
    return idx(o), idx(c), idx(v)


def frames(h: dict):
    import pandas as pd
    dates = sorted(h)
    sv = pd.DataFrame({d: {s: v[0] for s, v in h[d].items()} for d in dates}).T.sort_index()
    tv = pd.DataFrame({d: {s: v[1] for s, v in h[d].items()} for d in dates}).T.sort_index()
    return sv, tv


def scores(sv, tv):
    """Short ratio, its z-score vs the previous Z_WIN days, and off-exchange volume vs usual."""
    ratio = sv / tv
    base = ratio.shift(1).rolling(Z_WIN, min_periods=40)
    z = (ratio - base.mean()) / base.std()
    volx = tv / tv.shift(1).rolling(Z_WIN, min_periods=40).median()
    return ratio, z, volx


def dark_index(sv, tv, close):
    """S&P 100 dark index: dollar-weighted short ratio of the stocks (ETFs left out), like DIX."""
    cols = [c for c in sv.columns if c not in ETFS]
    px = close.reindex(index=sv.index, columns=cols).ffill()
    dol_s = (sv[cols] * px).sum(axis=1, min_count=20)
    dol_t = (tv[cols] * px).sum(axis=1, min_count=20)
    idx = dol_s / dol_t
    base = idx.shift(1).rolling(Z_WIN, min_periods=40)
    return idx, (idx - base.mean()) / base.std()


def cmd_daily(args) -> None:
    import pandas as pd
    h = load_hist()
    today = now_utc().astimezone(NY).date()
    since = date.fromisoformat(max(h)) + timedelta(days=1) if h else date.fromisoformat(args.since)
    update_hist(h, max(since, today - timedelta(days=45)) if h else since, today)
    if not h:
        sys.exit("no FINRA data")
    save_hist(h)
    sv, tv = frames(h)
    start = (pd.Timestamp(sv.index[-1]) - pd.Timedelta(days=420)).strftime("%Y-%m-%d")
    _, close, vol = prices(list(sv.columns), start)
    build_daily(sv, tv, close, vol)


def build_daily(sv, tv, close, vol) -> None:
    ratio, z, volx = scores(sv, tv)
    idx, idx_z = dark_index(sv, tv, close)
    last = sv.index[-1]
    nm = names()
    stocks = []
    for t in sv.columns:
        rt, zt = ratio[t].get(last), z[t].get(last)
        if rt is None or not math.isfinite(rt):
            continue
        cons = vol[t].get(last) if t in vol else None
        px = close[t].get(last) if t in close else None
        adv = (vol[t].tail(20) * close[t].tail(20)).mean() if t in vol and t in close else None
        z5 = z[t].tail(5).mean()
        sig = None
        if zt is not None and math.isfinite(zt):
            sig = "buying" if zt >= Z_HOT else "selling" if zt <= -Z_HOT else None
        stocks.append({
            "t": t, "n": nm.get(t, t), "etf": t in ETFS,
            "ratio": r(rt * 100, 1), "avg": r(ratio[t].iloc[-Z_WIN - 1:-1].mean() * 100, 1),
            "z": r(zt), "z5": r(z5), "sig": sig,
            "vol": int(tv[t].get(last) or 0), "volx": r(volx[t].get(last)),
            "offex": r(100 * tv[t].get(last) / cons, 1) if cons else None,     # off-exchange % of all volume
            "px": r(px), "adv": r(adv / 1e6, 1) if adv and math.isfinite(adv) else None,   # $M a day
            "s": [r(x * 100, 1) for x in ratio[t].tail(60)],
            "zs": [r(x) for x in z[t].tail(60)],
        })
    stocks.sort(key=lambda s: -(s["z"] if s["z"] is not None else -9))
    tail = idx.dropna().tail(260)
    spy = close["SPY"].reindex(tail.index) if "SPY" in close else None
    pct = (tail.rank(pct=True).iloc[-1] * 100) if len(tail) else None
    write_json(OUT / "daily.json", {
        "generated_at": now_utc().strftime("%Y-%m-%dT%H:%MZ"), "date": last,
        "z_win": Z_WIN, "z_hot": Z_HOT,
        "index": {"value": r(tail.iloc[-1] * 100, 1), "z": r(idx_z.get(last)), "pct_1y": r(pct, 0),
                  "dates": list(tail.index), "v": [r(x * 100, 2) for x in tail],
                  "spy": [r(x) for x in spy] if spy is not None else None},
        "stocks": stocks,
        "ntfy_prefix": os.environ.get("NTFY_PREFIX", "").strip() or None,
    })


# ====================================================================== weekly: FINRA ATS by venue
_token = None


def venue_name(mpid: str, name: str) -> str:
    """FINRA prefixes names with the MPID: "UBSA UBS ATS" -> "UBS ATS", "BIDS BIDS ATS" -> "BIDS ATS"."""
    name = (name or mpid).strip()
    if name.upper().startswith(mpid.upper() + " "):
        rest = name[len(mpid) + 1:].strip()
        return rest if rest.upper() != "ATS" else f"{mpid} ATS"
    return name


def finra_post(body: dict) -> list:
    global _token
    hdr = {"Accept": "application/json", "Content-Type": "application/json"}
    cid, sec = os.environ.get("FINRA_API_CLIENT", ""), os.environ.get("FINRA_API_SECRET", "")
    if cid and sec and _token is None:
        tok = http("https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token?grant_type=client_credentials", data=b"",
                   headers={"Authorization": "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode()})
        _token = json.loads(tok)["access_token"]
    if _token:
        hdr["Authorization"] = f"Bearer {_token}"
    raw = http(FINRA_API, data=json.dumps(body).encode(), headers=hdr, timeout=90)
    return json.loads(raw) if raw.strip() else []


def fetch_week(wk: str, tickers: list[str]) -> list[dict]:
    rows, off = [], 0
    syms = sorted({t.replace("-", ".") for t in tickers} | {t.replace("-", " ") for t in tickers if "-" in t})
    while True:
        page = finra_post({
            "limit": 5000, "offset": off,
            "fields": ["issueSymbolIdentifier", "MPID", "marketParticipantName", "totalWeeklyShareQuantity",
                       "totalWeeklyTradeCount", "weekStartDate"],
            "compareFilters": [
                {"compareType": "EQUAL", "fieldName": "summaryTypeCode", "fieldValue": "ATS_W_SMBL_FIRM"},
                {"compareType": "EQUAL", "fieldName": "weekStartDate", "fieldValue": wk},
            ],
            "domainFilters": [{"fieldName": "issueSymbolIdentifier", "values": syms}],
        })
        rows += page
        if len(page) < 5000:
            return rows
        off += 5000


def cmd_weekly(args) -> None:
    tick = universe()
    want = {key(t): t for t in tick}
    adir = OUT / "ats"
    adir.mkdir(parents=True, exist_ok=True)
    today = now_utc().date()
    monday = today - timedelta(days=today.weekday())
    weeks = [(monday - timedelta(weeks=k)).isoformat() for k in range(2, 14)]      # newest published ~2 weeks back
    have = {p.stem for p in adir.glob("*.json")}
    fresh = 0
    err = None
    for wk in weeks:
        if wk in have and wk not in weeks[:3]:       # the latest weeks can still be updated by FINRA
            continue
        try:
            rows = fetch_week(wk, tick)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:200]
            print(f"  ! FINRA ATS {wk}: {err}")
            break
        if not rows:
            continue
        per: dict = {}
        venues: dict = {}
        for x in rows:
            t = want.get(key(x.get("issueSymbolIdentifier", "")))
            if not t:
                continue
            m = x.get("MPID") or "?"
            nm_ = (x.get("marketParticipantName") or m).strip()
            venues[m] = venue_name(m, nm_)
            a = per.setdefault(t, {})
            a[m] = a.get(m, 0) + int(x.get("totalWeeklyShareQuantity") or 0)
        write_json(adir / f"{wk}.json", {"week": wk, "venues": venues, "shares": per})
        fresh += 1
    build_weekly(err)
    print(f"  {fresh} week(s) fetched")


def build_weekly(err: str | None = None) -> None:
    files = sorted((OUT / "ats").glob("*.json"))[-12:]
    if not files:
        write_json(OUT / "weekly.json", {"generated_at": now_utc().strftime("%Y-%m-%dT%H:%MZ"), "weeks": [],
                                         "error": err or "no data yet"})
        return
    wkd = [json.loads(f.read_text()) for f in files]
    weeks = [w["week"] for w in wkd]
    vn: dict = {}
    for w in wkd:
        vn.update({m: venue_name(m, n) for m, n in w["venues"].items()})
    # consolidated weekly volume from Yahoo, to turn shares into "% of all trading"
    cons = {}
    try:
        _, _, vol = prices(universe(), weeks[0])
        import pandas as pd
        for w in weeks:
            a, b = w, (date.fromisoformat(w) + timedelta(days=4)).isoformat()
            cons[w] = vol.loc[(vol.index >= a) & (vol.index <= b)].sum()
    except Exception as e:
        print(f"  ! Yahoo volume: {e}")
    nm = names()
    last = wkd[-1]
    stocks = []
    for t in universe():
        row = last["shares"].get(t)
        if not row:
            continue
        tot = sum(row.values())
        top = sorted(row.items(), key=lambda kv: -kv[1])[:4]
        trend = []
        for w in wkd:
            s = sum(w["shares"].get(t, {}).values())
            c = cons.get(w["week"])
            cv = float(c[t]) if c is not None and t in c and c[t] > 0 else None
            trend.append(r(100 * s / cv, 2) if cv else None)
        prev = [x for x in trend[:-1] if x is not None]
        stocks.append({"t": t, "n": nm.get(t, t), "sh": tot, "pct": trend[-1],
                       "avg": r(sum(prev) / len(prev), 2) if prev else None, "trend": trend,
                       "top": [[vn.get(m, m), m, r(100 * s / tot, 1)] for m, s in top]})
    stocks.sort(key=lambda s: -s["sh"])
    allv: dict = {}
    for row in last["shares"].values():
        for m, s in row.items():
            allv[m] = allv.get(m, 0) + s
    tot = sum(allv.values()) or 1
    venues = [{"mpid": m, "name": vn.get(m, m), "sh": s, "pct": r(100 * s / tot, 1)}
              for m, s in sorted(allv.items(), key=lambda kv: -kv[1])[:15]]
    weekly_total = [sum(sum(v.values()) for v in w["shares"].values()) for w in wkd]
    write_json(OUT / "weekly.json", {
        "generated_at": now_utc().strftime("%Y-%m-%dT%H:%MZ"), "weeks": weeks, "week": weeks[-1],
        "total": weekly_total, "venues": venues, "stocks": stocks, "error": err})


# ====================================================================== blocks: Alpaca SIP off-exchange prints
def session_bounds(d: date) -> tuple[datetime, datetime]:
    o = datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY)
    return o.astimezone(timezone.utc), (o + timedelta(hours=6, minutes=30)).astimezone(timezone.utc)


def alpaca_pages(symbols: list[str], start: datetime, end: datetime):
    hdr = {"APCA-API-KEY-ID": os.environ["ALPACA_KEY_ID"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]}
    tok = None
    while True:
        q = {"symbols": ",".join(symbols), "start": start.isoformat().replace("+00:00", "Z"),
             "end": end.isoformat().replace("+00:00", "Z"), "limit": 10000, "feed": "sip", "sort": "asc"}
        if tok:
            q["page_token"] = tok
        page = json.loads(http(ALPACA + "?" + urllib.parse.urlencode(q), headers=hdr, timeout=90))
        yield page.get("trades") or {}
        tok = page.get("next_page_token")
        if not tok:
            return


def load_today(d: date) -> dict:
    f = OUT / "blocks.json"
    if f.exists():
        b = json.loads(f.read_text())
        if b.get("date") == d.isoformat():
            return b
        roll_history(b)                               # yesterday's totals into the history file
    return {"date": d.isoformat(), "until": None, "stocks": {}, "prints": [], "alerted": []}


def roll_history(b: dict) -> None:
    if not b.get("stocks") or not b.get("final"):
        return
    f = OUT / "blocks_history.csv"
    new = not f.exists()
    seen = set()
    if not new:
        with f.open() as fh:
            seen = {row["date"] for row in csv.DictReader(fh)}
    if b["date"] in seen:
        return
    with f.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["date", "symbol", "total", "offex", "blocks", "notional", "above", "below"])
        for t, s in sorted(b["stocks"].items()):
            w.writerow([b["date"], t, s["tot"], s["dark"], s["bn"], s["bv"], s["up"], s["dn"]])


def cmd_blocks(args) -> None:
    global OUT
    now = now_utc()
    d = now.astimezone(NY).date()
    if args.day:                                     # test on a past session, written to --out, nothing published
        d = date.fromisoformat(args.day)
        OUT = Path(args.out)
        o, c = session_bounds(d)
        now = c + timedelta(minutes=20)
    if d.weekday() >= 5:
        print("weekend: nothing to do"); return
    o, c = session_bounds(d)
    b = load_today(d)
    if b.get("final"):
        print("today already finished"); return
    start = datetime.fromisoformat(b["until"]) if b.get("until") else o
    end = min(now - timedelta(minutes=16), c)
    if end <= start + timedelta(minutes=1):
        print(f"nothing new yet ({start:%H:%M}..{end:%H:%M} UTC)")
        if now >= c + timedelta(minutes=16) and b.get("until"):
            b["final"] = True; write_json(OUT / "blocks.json", b)
        return
    # typical size of each stock, for the "unusual" alert: 1% of the average daily dollar volume, at least $10M
    adv = {}
    try:
        for s in json.loads((OUT / "daily.json").read_text())["stocks"]:
            if s.get("adv"):
                adv[s["t"]] = s["adv"] * 1e6
    except Exception:
        pass
    alp = {t.replace("-", "."): t for t in (args.symbols.split(",") if args.symbols else universe())}
    syms = list(alp)
    print(f"  window {start:%H:%M}..{end:%H:%M} UTC, {len(syms)} symbols")
    st = b["stocks"]
    last_lit: dict = {}
    n_trades = 0
    new_prints = []
    for i in range(0, len(syms), 25):
        for page in alpaca_pages(syms[i:i + 25], start, end):
            for sym, trades in page.items():
                t = alp.get(sym, sym)
                s = st.setdefault(t, {"tot": 0, "dark": 0, "bn": 0, "bv": 0, "up": 0, "dn": 0})
                for x in trades:
                    n_trades += 1
                    sz, px, ex = int(x.get("s") or 0), float(x.get("p") or 0), x.get("x")
                    conds = set(x.get("c") or [])
                    s["tot"] += sz
                    odd = bool(conds & ODD_CONDS)
                    if ex != "D":
                        if not odd and sz >= 100:
                            last_lit[t] = px
                        continue
                    s["dark"] += sz
                    val = sz * px
                    if odd or val < BLOCK_MIN:
                        continue
                    lit = last_lit.get(t)
                    lean = None if lit is None else 1 if px > lit * 1.00005 else -1 if px < lit * 0.99995 else 0
                    s["bn"] += 1; s["bv"] += int(val)
                    if lean == 1: s["up"] += int(val)
                    if lean == -1: s["dn"] += int(val)
                    new_prints.append({"t": t, "ts": x["t"][:19] + "Z", "px": r(px, 4 if px < 10 else 2), "sz": sz,
                                       "v": int(val), "lit": r(lit, 4 if (lit or 0) < 10 else 2), "lean": lean})
    print(f"  {n_trades:,} trades read, {len(new_prints)} block(s)")
    # alerts: big for this stock
    big = [p for p in new_prints if p["v"] >= max(10e6, 0.01 * adv.get(p["t"], 0))]
    for p in big:
        p["big"] = True
    notify(big)
    prints = b["prints"] + new_prints
    if len(prints) > KEEP_PRINTS:
        keep = set(sorted(range(len(prints)), key=lambda k: -prints[k]["v"])[:KEEP_PRINTS])
        prints = [p for k, p in enumerate(prints) if k in keep]
    b.update({"until": end.isoformat(), "prints": prints, "updated": now.strftime("%Y-%m-%dT%H:%MZ"),
              "ntfy_prefix": os.environ.get("NTFY_PREFIX", "").strip() or None, "min": BLOCK_MIN})
    if end >= c:
        b["final"] = True
    write_json(OUT / "blocks.json", b)


def notify(big: list[dict]) -> None:
    prefix = os.environ.get("NTFY_PREFIX", "").strip()
    if not prefix or not big:
        return
    big = sorted(big, key=lambda p: -p["v"])
    lean = {1: "above the lit price", -1: "below the lit price", 0: "at the lit price", None: ""}
    lines = [f"{p['t']}: ${p['v'] / 1e6:.1f}M ({p['sz']:,} sh @ {p['px']}) {lean[p['lean']]}".strip() for p in big[:8]]
    if len(big) > 8:
        lines.append(f"and {len(big) - 8} more")
    req = urllib.request.Request(f"https://ntfy.sh/{prefix}-darkpool", data="\n".join(lines).encode(), method="POST",
                                 headers={"Title": f"Dark pool blocks: {', '.join(dict.fromkeys(p['t'] for p in big[:5]))}",
                                          "Tags": "waves"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"  ! notification failed: {e}")


# ====================================================================== backtest
def stats(eq, rf=0.0):
    import numpy as np
    ret = eq.pct_change().dropna()
    yrs = max(len(ret) / 252, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    sh = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else None
    return {"cagr": r(cagr * 100, 1), "dd": r(dd * 100, 1), "sharpe": r(sh), "total": r((eq.iloc[-1] / eq.iloc[0] - 1) * 100, 1)}


def run_backtest(sv, tv, opn, close, split: str) -> dict:
    import numpy as np
    import pandas as pd
    ratio, z, _ = scores(sv, tv)
    idx, idx_z = dark_index(sv, tv, close)
    days = [d for d in sv.index if d in close.index]
    z, opn, close = z.loc[days], opn.reindex(index=days), close.reindex(index=days)
    stocks = [c for c in z.columns if c not in ETFS and c in close.columns]
    # --- event study: signal on day t (FINRA publishes after the close), buy the next open, sell the close h days later
    events = []
    for h in (5, 10, 20):
        fwd = close[stocks].shift(-h) / opn[stocks].shift(-1) - 1
        for period, mask in (("in", np.array(days) < split), ("out", np.array(days) >= split)):
            f = fwd[mask]
            zz = z[stocks][mask]
            base = f.stack().dropna()
            for name, sel in (("buying", zz >= Z_HOT), ("selling", zz <= -Z_HOT)):
                ev = f[sel].stack().dropna()
                if len(ev) < 20 or len(base) < 20:
                    continue
                events.append({"sig": name, "h": h, "period": period, "n": int(len(ev)),
                               "avg": r(ev.mean() * 100, 2), "win": r((ev > 0).mean() * 100, 1),
                               "base": r(base.mean() * 100, 2), "base_win": r((base > 0).mean() * 100, 1),
                               "edge": r((ev.mean() - base.mean()) * 100, 2)})
    # --- strategy 1: hold (equal weight) every stock with a dark-buying signal in the last 10 days
    cost = 0.001
    sig = (z[stocks] >= Z_HOT).astype(float)
    hold = sig.rolling(10, min_periods=1).max().shift(1).fillna(0)          # enter the day after the signal
    rets = close[stocks].pct_change().fillna(0)
    w = hold.div(hold.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    turn = w.diff().abs().sum(axis=1).fillna(0)
    s1 = (w * rets).sum(axis=1) - turn * cost
    # --- strategy 2: SPY when the 5-day dark index is above its 1-year median, else cash
    spy = close["SPY"].pct_change().fillna(0)
    di = idx.reindex(days).rolling(5).mean()
    on = (di > di.rolling(252, min_periods=120).median()).astype(float).shift(1).fillna(0)
    s2 = on * spy - on.diff().abs().fillna(0) * cost
    ew = rets.mean(axis=1)
    curves = {}
    out = {}
    for period, sl in (("all", slice(None)), ("in", [d < split for d in days]), ("out", [d >= split for d in days])):
        res = {}
        for k, s in (("dark_buys", s1), ("dark_index_spy", s2), ("spy", spy), ("equal_weight", ew)):
            sub = s[sl] if period != "all" else s
            if len(sub) < 60:
                continue
            res[k] = stats((1 + sub).cumprod())
        out[period] = res
    eq = {k: (1 + s).cumprod() for k, s in (("dark_buys", s1), ("dark_index_spy", s2), ("spy", spy), ("equal_weight", ew))}
    step = max(1, len(days) // 400)
    pick = list(range(0, len(days), step))
    if pick[-1] != len(days) - 1:
        pick.append(len(days) - 1)
    curves = {"dates": [days[i] for i in pick], **{k: [r(e.iloc[i] * 100000, 0) for i in pick] for k, e in eq.items()}}
    # verdict in plain words
    oo = [e for e in events if e["period"] == "out" and e["sig"] == "buying"]
    os_ = [e for e in events if e["period"] == "out" and e["sig"] == "selling"]
    good = [e for e in oo if (e["edge"] or 0) > 0.15]
    o = out.get("out", {})
    if not oo:
        verdict = "Not enough data yet."
    else:
        eb = sum(e["edge"] for e in oo) / len(oo)
        es = sum(e["edge"] for e in os_) / len(os_) if os_ else 0
        beat = (o.get("dark_buys", {}).get("cagr") or -99) > (o.get("equal_weight", {}).get("cagr") or 99)
        strength = "a small edge" if eb > 0.05 else "no real edge" if eb > -0.05 else "the wrong way round"
        verdict = (f"Out of sample, stocks after a dark buying signal did {eb:+.2f}% better on average than the same stocks "
                   f"on any day ({strength}), and after a dark selling signal {es:+.2f}%. "
                   + ("As a strategy it beat holding all the stocks equally." if beat else
                      "As a strategy it did not beat simply holding all the stocks equally."))
    return {"generated_at": now_utc().strftime("%Y-%m-%dT%H:%MZ"), "start": days[0], "end": days[-1], "split": split,
            "z_hot": Z_HOT, "z_win": Z_WIN, "cost": cost, "events": events, "results": out, "curves": curves,
            "verdict": verdict, "good": len(good), "of": len(oo)}


def cmd_backtest(args) -> None:
    h = load_hist()
    if len(h) < 300:
        update_hist(h, date.fromisoformat(args.since), now_utc().date()); save_hist(h)
    sv, tv = frames(h)
    opn, close, _ = prices(list(sv.columns), sv.index[0])
    write_json(OUT / "backtest.json", run_backtest(sv, tv, opn, close, args.split))


def cmd_backfill(args) -> None:
    h = load_hist()
    update_hist(h, date.fromisoformat(args.since), now_utc().date())
    save_hist(h)


# ====================================================================== demo
def cmd_demo(args) -> None:
    import numpy as np
    import pandas as pd
    global OUT
    OUT = Path(args.out)
    rng = np.random.default_rng(3)
    tick = universe()
    days = [d.isoformat() for d in weekdays(date(2021, 1, 4), date(2026, 11, 3))]
    n = len(days)
    drift = rng.normal(0.0004, 0.0002, len(tick))
    ret = rng.normal(drift, 0.017, (n, len(tick)))
    # a weak real effect so the demo shows something: high ratio before up days
    ratio = np.clip(0.48 + 0.04 * rng.standard_normal((n, len(tick))), 0.2, 0.8)
    ret[1:] += 0.02 * (ratio[:-1] - 0.48)
    close = pd.DataFrame(100 * np.exp(np.cumsum(ret, axis=0)), index=days, columns=tick)
    opn = close.shift(1).fillna(close) * (1 + rng.normal(0, 0.003, (n, len(tick))))
    tv = pd.DataFrame(rng.lognormal(15, 0.4, (n, len(tick))), index=days, columns=tick).round()
    sv = (tv * ratio).round()
    vol = tv / 0.45
    build_daily(sv, tv, close, vol)
    write_json(OUT / "backtest.json", run_backtest(sv, tv, opn, close, "2023-01-01"))
    # weekly
    (OUT / "ats").mkdir(parents=True, exist_ok=True)
    ven = {"UBSA": "UBS ATS", "SGMT": "SIGMA X2", "JPMX": "JPM-X", "MSPL": "MS POOL (ATS-4)", "LQNT": "LIQUIDNET",
           "INCR": "INTELLIGENT CROSS LLC", "EBXL": "LEVEL ATS", "CROS": "CROSSFINDER", "IATS": "IBKR ATS"}
    for k in range(12, 0, -1):
        wk = (date(2026, 10, 19) - timedelta(weeks=k - 1)).isoformat()
        shares = {t: {m: int(rng.lognormal(13, 0.8)) for m in ven} for t in tick}
        write_json(OUT / "ats" / f"{wk}.json", {"week": wk, "venues": ven, "shares": shares})
    files = sorted((OUT / "ats").glob("*.json"))
    wkd = [json.loads(f.read_text()) for f in files]
    nm = names()
    stocks = []
    for t in tick:
        row = wkd[-1]["shares"][t]; tot = sum(row.values())
        trend = [r(rng.uniform(5, 14), 2) for _ in wkd]
        top = sorted(row.items(), key=lambda kv: -kv[1])[:4]
        stocks.append({"t": t, "n": nm.get(t, t), "sh": tot, "pct": trend[-1], "avg": r(sum(trend[:-1]) / (len(trend) - 1), 2),
                       "trend": trend, "top": [[ven[m], m, r(100 * s / tot, 1)] for m, s in top]})
    stocks.sort(key=lambda s: -s["sh"])
    allv = {m: sum(wkd[-1]["shares"][t][m] for t in tick) for m in ven}
    tot = sum(allv.values())
    write_json(OUT / "weekly.json", {"generated_at": now_utc().strftime("%Y-%m-%dT%H:%MZ"), "weeks": [w["week"] for w in wkd],
               "week": wkd[-1]["week"], "total": [sum(sum(v.values()) for v in w["shares"].values()) for w in wkd],
               "venues": [{"mpid": m, "name": ven[m], "sh": s, "pct": r(100 * s / tot, 1)} for m, s in sorted(allv.items(), key=lambda kv: -kv[1])],
               "stocks": stocks, "error": None})
    # blocks
    d0 = datetime(2026, 11, 3, 14, 30, tzinfo=timezone.utc)
    prints, st = [], {}
    for k in range(260):
        t = tick[int(rng.integers(0, 40))]
        px = float(close[t].iloc[-1]) * (1 + rng.normal(0, 0.003))
        v = float(rng.lognormal(14.6, 0.9))
        sz = int(v / px)
        lean = int(rng.choice([1, -1, 0, 0]))
        p = {"t": t, "ts": (d0 + timedelta(seconds=int(rng.integers(0, 23000)))).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "px": r(px), "sz": sz, "v": int(sz * px), "lit": r(px * (1 - lean * 0.0002)), "lean": lean}
        if p["v"] > 20e6: p["big"] = True
        prints.append(p)
        s = st.setdefault(t, {"tot": 0, "dark": 0, "bn": 0, "bv": 0, "up": 0, "dn": 0})
        s["bn"] += 1; s["bv"] += p["v"]; s["up"] += p["v"] if lean == 1 else 0; s["dn"] += p["v"] if lean == -1 else 0
    for t in tick:
        s = st.setdefault(t, {"tot": 0, "dark": 0, "bn": 0, "bv": 0, "up": 0, "dn": 0})
        s["tot"] = int(rng.lognormal(16, 0.5)); s["dark"] = int(s["tot"] * rng.uniform(0.35, 0.55))
    write_json(OUT / "blocks.json", {"date": "2026-11-03", "until": "2026-11-03T21:00:00+00:00", "final": True,
               "updated": "2026-11-03T21:20Z", "stocks": st, "prints": sorted(prints, key=lambda p: p["ts"]),
               "min": BLOCK_MIN, "ntfy_prefix": "demo"})


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["daily", "weekly", "blocks", "backfill", "backtest", "demo"])
    ap.add_argument("--since", default="2019-01-02", help="first day of the FINRA history (backfill/backtest)")
    ap.add_argument("--split", default="2023-01-01", help="out-of-sample starts here")
    ap.add_argument("--out", default="/tmp/darkpool-demo", help="demo / test output folder")
    ap.add_argument("--day", help="blocks: test on this past session (YYYY-MM-DD), written to --out")
    ap.add_argument("--symbols", help="blocks: only these symbols (comma separated)")
    a = ap.parse_args()
    {"daily": cmd_daily, "weekly": cmd_weekly, "blocks": cmd_blocks, "backfill": cmd_backfill,
     "backtest": cmd_backtest, "demo": cmd_demo}[a.cmd](a)


if __name__ == "__main__":
    main()
