"""Prompt 1: ingest, clean, audit and seal.

python -m research.pa_lab.data            fetch from Alpaca (needs ALPACA_KEY_ID / ALPACA_SECRET_KEY), then build
python -m research.pa_lab.data --build    rebuild from data/raw only
PA_LAB_DEMO=1 ...                         synthetic bars instead of Alpaca (for offline tests)

Raw files are copied unchanged into data/raw/ and their sha256 recorded in results/raw_manifest.json.
The final window is written once to data/sealed/ with a sha256 manifest; the ingest refuses to rewrite it.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import config as C
from . import guard

NY = "America/New_York"
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"


# ------------------------------------------------------------------ fetch
def _get(params: dict) -> dict:
    url = BARS_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"APCA-API-KEY-ID": os.environ["ALPACA_KEY_ID"],
                                               "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
                                               "User-Agent": "ics-pa-lab"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # rate limits and blips
            if attempt == 5:
                raise
            print("  retry", e)
            time.sleep(3 * (attempt + 1))
    return {}


def fetch_year(sym: str, year: int, adjustment: str, end: date) -> pd.DataFrame:
    start = max(date(year, 1, 1), date.fromisoformat(C.FETCH_FROM))
    stop = min(date(year + 1, 1, 1), end)
    params = {"symbols": sym, "timeframe": "1Min", "start": f"{start}T00:00:00Z", "end": f"{stop}T00:00:00Z",
              "limit": "10000", "adjustment": adjustment, "feed": "sip"}
    rows = []
    while True:
        js = _get(params)
        for b in (js.get("bars") or {}).get(sym, []):
            rows.append((b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]))
        tok = js.get("next_page_token")
        if not tok:
            break
        params["page_token"] = tok
    return pd.DataFrame(rows, columns=["ts_event", "open", "high", "low", "close", "volume"])


def fetch_all() -> None:
    C.RAW.mkdir(parents=True, exist_ok=True)
    end = date.today()
    for sym in C.SYMBOLS:
        for adj in ("all", "raw"):
            for year in range(int(C.FETCH_FROM[:4]), end.year + 1):
                p = C.RAW / f"{sym}_{'adj' if adj == 'all' else 'raw'}_{year}.csv.gz"
                if p.exists() and year < end.year:
                    continue
                df = fetch_year(sym, year, adj, end)
                with gzip.open(p, "wt") as f:
                    df.to_csv(f, index=False)
                print(f"  {p.name}: {len(df)} bars")


def demo_raw(days: int = 1300) -> None:
    """Synthetic 1-minute RTH bars with mild mean reversion around round levels."""
    C.RAW.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(C.MASTER_SEED)
    d0 = date.fromisoformat(C.FETCH_FROM)
    days_list, d = [], d0
    while len(days_list) < days:
        if d.weekday() < 5:
            days_list.append(d)
        d += timedelta(days=1)
    common = rng.normal(0, 1, (len(days_list), 390))
    for k, sym in enumerate(C.SYMBOLS):
        px = 360.0 if sym == "QQQ" else 450.0
        rows = []
        own = rng.normal(0, 1, (len(days_list), 390))
        for i, dd in enumerate(days_list):
            vol = 0.0006 * (1 + 0.5 * np.sin(i / 60))
            px *= 1 + rng.normal(0, 0.006)                        # overnight gap
            for m in range(390):
                ret = vol * (0.8 * common[i, m] + 0.6 * own[i, m])
                o = px
                c = o * (1 + ret)
                h = max(o, c) * (1 + abs(rng.normal(0, vol / 3)))
                l = min(o, c) * (1 - abs(rng.normal(0, vol / 3)))
                t = datetime(dd.year, dd.month, dd.day, 9, 30, tzinfo=ZoneInfo(NY)) + timedelta(minutes=m)
                rows.append((t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), round(o, 2), round(h, 2), round(l, 2), round(c, 2), int(rng.integers(500, 50000))))
                px = c
        df = pd.DataFrame(rows, columns=["ts_event", "open", "high", "low", "close", "volume"])
        for year, part in df.groupby(df.ts_event.str[:4]):
            for tag in ("adj", "raw"):
                with gzip.open(C.RAW / f"{sym}_{tag}_{year}.csv.gz", "wt") as f:
                    part.to_csv(f, index=False)


# ------------------------------------------------------------------ clean + audit
def load_raw(sym: str, tag: str) -> pd.DataFrame:
    parts = []
    for p in sorted(C.RAW.glob(f"{sym}_{tag}_*.csv.gz")):
        parts.append(pd.read_csv(p))
    df = pd.concat(parts, ignore_index=True)
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True)
    return df


def to_sessions(df: pd.DataFrame) -> pd.DataFrame:
    ny = df["ts_event"].dt.tz_convert(NY)
    mins = ny.dt.hour * 60 + ny.dt.minute
    keep = (mins >= C.OPEN_MIN) & (mins < C.CLOSE_MIN)
    out = df.loc[keep].copy()
    out["ts_ny"] = ny[keep]
    out["minute"] = mins[keep].astype(np.int16)
    out["session"] = out["ts_ny"].dt.strftime("%Y-%m-%d")
    return out


def audit(sym: str, adj: pd.DataFrame, raw: pd.DataFrame) -> dict:
    a = {"symbol": sym, "bars": int(len(adj))}
    a["duplicate_timestamps"] = int(adj["ts_event"].duplicated().sum())
    a["out_of_order"] = int((adj["ts_event"].diff().dt.total_seconds() <= 0).sum())
    gaps = adj.groupby("session")["minute"].diff().dropna() - 1
    a["gaps_over_1m"] = int((gaps >= 1).sum())
    a["gaps_over_5m"] = int((gaps >= 5).sum())
    a["gaps_over_60m"] = int((gaps >= 60).sum())
    a["missing_minutes_total"] = int(gaps[gaps > 0].sum())
    hi_bad = adj["high"] < adj[["open", "close"]].max(axis=1) - 1e-9
    lo_bad = adj["low"] > adj[["open", "close"]].min(axis=1) + 1e-9
    a["high_low_inconsistent"] = int((hi_bad | lo_bad).sum())
    a["zero_volume"] = int((adj["volume"] <= 0).sum())
    m = adj[["ts_event", "close"]].merge(raw[["ts_event", "close"]], on="ts_event", suffixes=("_adj", "_raw"))
    ratio = (m["close_adj"] / m["close_raw"]).round(5)
    steps = m.loc[ratio.diff().abs() > 1e-5, "ts_event"].dt.tz_convert(NY).dt.strftime("%Y-%m-%d")
    a["adjustment_step_days"] = sorted(set(steps))   # should be dividend/split days only
    a["sessions"] = int(adj["session"].nunique())
    return a


def complete_sessions(df: pd.DataFrame) -> set[str]:
    """A session is complete when it has a bar opening at 15:59 or later; known early closes (13:00) count
    as complete when they have a bar opening at 12:59 or later and nothing after 13:00."""
    last = df.groupby("session")["minute"].max()
    first = df.groupby("session")["minute"].min()
    ok = (last >= C.LAST_FULL_BAR_MIN) | ((last >= 12 * 60 + 59) & (last < 13 * 60 + 1))
    ok &= first <= C.OPEN_MIN + 5
    return set(last.index[ok])


def build() -> dict:
    C.CLEAN.mkdir(parents=True, exist_ok=True)
    C.RESULTS.mkdir(parents=True, exist_ok=True)
    raw_manifest = {p.name: guard.sha256(p) for p in sorted(C.RAW.glob("*.csv.gz"))}
    (C.RESULTS / "raw_manifest.json").write_text(json.dumps(raw_manifest, indent=1))

    sealed_manifest_p = C.RESULTS / "sealed_manifest.json"
    old = json.loads(sealed_manifest_p.read_text()) if sealed_manifest_p.exists() else None
    # once a manifest exists it is never rewritten; missing sealed files (lost cache) are rebuilt up to its
    # last session and must hash the same, or the final test stops
    already_sealed = old is not None and all((C.SEALED / n).exists() for n in old.get("files", {}))
    audits, counts, sealed = {}, {}, {}
    for sym in C.SYMBOLS:
        adj, raw = to_sessions(load_raw(sym, "adj")), to_sessions(load_raw(sym, "raw"))
        adj = adj.drop_duplicates("ts_event").sort_values("ts_event")
        audits[sym] = audit(sym, adj, raw)
        comp = complete_sessions(adj)
        audits[sym]["incomplete_sessions"] = sorted(set(adj["session"]) - comp)
        adj = adj[adj["session"].isin(comp)]
        cols = ["ts_event", "session", "minute", "open", "high", "low", "close", "volume"]
        adj = adj[cols].reset_index(drop=True)
        pre = adj[adj["session"] < C.FINAL_FROM]
        fin = adj[adj["session"] >= C.FINAL_FROM]
        if old is not None:
            fin = fin[fin["session"] <= old["last_session"]]
        pre.to_parquet(C.CLEAN / f"{sym}.parquet", index=False)
        if not already_sealed:
            sealed[f"{sym}.parquet"] = guard.write_sealed(fin, f"{sym}.parquet")
        sess = pre.groupby("session").size().index
        counts[sym] = {"warmup": int(((sess >= C.WARMUP[0]) & (sess <= C.WARMUP[1])).sum()),
                       "discovery": int(((sess >= C.DISCOVERY[0]) & (sess <= C.DISCOVERY[1])).sum()),
                       "era1": int(((sess >= C.ERAS["era1"][0]) & (sess <= C.ERAS["era1"][1])).sum()),
                       "era2": int(((sess >= C.ERAS["era2"][0]) & (sess <= C.ERAS["era2"][1])).sum()),
                       "final": int(fin["session"].nunique()),
                       "final_last_session": str(fin["session"].max()) if len(fin) else None}
    if old is None:
        last = max(c["final_last_session"] or "" for c in counts.values())
        sealed_manifest_p.write_text(json.dumps({"written": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                 "from": C.FINAL_FROM, "last_session": last, "files": sealed}, indent=1))
    elif not already_sealed:
        same = all(sealed.get(n) == h for n, h in old["files"].items())
        print("  sealed files rebuilt from raw:", "hashes match" if same else "HASHES DIFFER (final test will stop)")
    else:
        print("  sealed window already written; left unchanged")
    (C.RESULTS / "audit.json").write_text(json.dumps(audits, indent=1))
    (C.RESULTS / "session_counts.json").write_text(json.dumps(counts, indent=1))
    return {"audit": audits, "counts": counts}


def verify_seal() -> bool:
    man = json.loads((C.RESULTS / "sealed_manifest.json").read_text())
    return all(guard.sha256(C.SEALED / n) == h for n, h in man["files"].items())


def load_clean(sym: str) -> pd.DataFrame:
    return guard.read_frame(C.CLEAN / f"{sym}.parquet")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="skip the download")
    args = ap.parse_args()
    if C.demo():
        demo_raw()
    elif not args.build:
        fetch_all()
    out = build()
    print(json.dumps(out["counts"], indent=1))


if __name__ == "__main__":
    main()
