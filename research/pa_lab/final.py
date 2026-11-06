"""Prompt 4: the final test, the one test on data no rule has seen.

    python -m research.pa_lab.final rehearse   last month of discovery, shifted in price, through the exact final code
    PA_LAB_UNLOCK=final_test python -m research.pa_lab.final run     the one real run (refuses if results exist)

Nothing chosen in discovery changes: the configuration and selection hashes are checked before unlocking and
the run stops if any differ. The sealed bars are read once, through the guard, which logs the unlock and relock.
Every version's metrics are saved and committed; the full daily P&L matrices go to a GitHub release asset,
since they cannot be rebuilt without opening the seal again.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

from . import config as C
from . import guard
from .charts import build_market
from .data import load_clean, verify_seal
from .dials import SETUP_NAMES, SETUPS
from .engine import run_version, sess_range, sharpe, twin_test, window_idx
from .versions import load_versions

OUT = C.RESULTS / "final"
FUNNEL = C.RESULTS / "funnel"


def check_hashes() -> None:
    for name in ("versions", "selection"):
        want = (FUNNEL / f"{name}.sha256").read_text().split()[0]
        got = guard.sha256(FUNNEL / ("versions.jsonl.gz" if name == "versions" else "selection.json"))
        if want != got:
            sys.exit(f"STOP: {name} hash differs ({got[:12]} vs {want[:12]}); nothing chosen in discovery may change")
    if not verify_seal():
        sys.exit("STOP: the sealed files do not match their manifest")


def final_frame(sym: str, sealed: pd.DataFrame | None, start: str, warm_from: str, shift: float = 1.0) -> pd.DataFrame:
    disc = load_clean(sym)
    disc = disc[disc["session"] >= warm_from]
    df = pd.concat([disc, sealed], ignore_index=True) if sealed is not None else disc
    if shift != 1.0:
        for k in ("open", "high", "low", "close"):
            df[k] = df[k] * shift
    return df.sort_values("ts_event").reset_index(drop=True)


def evaluate(frames: dict, window: tuple[str, str], out_dir, label: str) -> pd.DataFrame:
    vers = load_versions()
    sel = json.loads((FUNNEL / "selection.json").read_text())
    rows = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for sym, df in frames.items():
        mk = build_market(sym, df)
        w = window_idx(mk, *window)
        wm = sess_range(mk, *window)
        sessions = mk.sessions[wm]
        years = sorted({s[:4] for s in sessions})
        mats = {}
        for setup in SETUPS:
            vs = [r for r in vers if r["setup"] == setup]
            mat = np.zeros((len(vs), int(wm.sum())), np.float32)
            for q, r in enumerate(vs):
                res = run_version(mk, setup, r["dials"], w)
                dd = res.daily[wm]
                mat[q] = dd
                tr = res.trades
                row = {"id": r["id"], "set": r["set"], "setup": setup, "name": r.get("name", ""), "chart": r["dials"]["chart"],
                       "market": sym, "trades": int(len(tr)), "dollars": float(dd.sum()),
                       "per_year": float(dd.sum() / max(len(dd) / 252, 1e-9)), "sharpe": sharpe(dd)}
                for y in years:
                    row[f"y{y}"] = float(dd[np.char.startswith(sessions.astype(str), y)].sum())
                rows.append(row)
            mats[setup] = mat
        np.savez_compressed(out_dir / f"daily_{sym}.npz", sessions=sessions.astype(str), **mats)
        # twins for the candidates and for the best usual-settings version of each working setup
        if label == "final":
            twin_rows = []
            disc_ids = np.flatnonzero(wm)
            picks = {c["id"] for c in sel["candidates"]}
            best = best_usual()
            picks |= set(best["id"])
            for r in vers:
                if r["id"] in picks:
                    res = run_version(mk, r["setup"], r["dials"], w)
                    p = twin_test(mk, r["setup"], r["dials"], res, w, disc_ids, C.TWIN_SETS, C.MASTER_SEED + 7) if len(res.trades) else np.nan
                    twin_rows.append({"id": r["id"], "market": sym, "twin_p": p})
                    picks.discard(r["id"])
            pd.DataFrame(twin_rows).to_csv(out_dir / f"twins_{sym}.csv", index=False)
    df = pd.DataFrame(rows)
    df["held"] = (df["dollars"] > 0) & (df["sharpe"] >= C.HELD_SHARPE)
    df.to_csv(out_dir / "all_versions.csv.gz", index=False)
    return df


def best_usual() -> pd.DataFrame:
    """The best usual-settings (common set) version of each setup, picked by its discovery Sharpe on QQQ."""
    a = pd.read_csv(FUNNEL / "all_versions.csv.gz")
    a = a[(a["set"] == "common") & a["q_graded"].astype(bool)]
    return a.sort_values("q_sharpe", ascending=False).groupby("setup").head(1)[["setup", "id", "q_sharpe", "q_per_year"]]


def report(df: pd.DataFrame, out_dir) -> None:
    sel = json.loads((FUNNEL / "selection.json").read_text())
    cand = {c["id"] for c in sel["candidates"]}
    rep = {}
    for sym in C.SYMBOLS:
        d = df[df["market"] == sym]
        c = d[d["id"].isin(cand)]
        rep[sym] = {"versions": int(len(d)), "held_share": float(d["held"].mean()) if len(d) else 0.0,
                    "candidates": int(len(c)), "candidates_held": int(c["held"].sum()),
                    "candidates_held_share": float(c["held"].mean()) if len(c) else None,
                    "profitable_by_chart": d.groupby("chart")["dollars"].apply(lambda x: float((x > 0).mean())).to_dict()}
    # every setup on every chart: equal-weight average daily P&L of its versions
    whole = []
    for sym in C.SYMBOLS:
        z = np.load(out_dir / f"daily_{sym}.npz")
        sessions = z["sessions"]
        vers = load_versions()
        for setup in SETUPS:
            mat = z[setup]
            charts = np.array([r["dials"]["chart"] for r in vers if r["setup"] == setup])
            for ch in ["all", "1m", "5m", "15m", "1h", "4h", "1d"]:
                selm = np.ones(len(charts), bool) if ch == "all" else charts == ch
                if not selm.any():
                    continue
                avg = mat[selm].mean(axis=0).astype(np.float64)
                row = {"setup": setup, "name": SETUP_NAMES[setup], "market": sym, "chart": ch, "versions": int(selm.sum()),
                       "per_year": float(avg.sum() / (len(avg) / 252)), "sharpe": sharpe(avg), "total": float(avg.sum())}
                for y in sorted({s[:4] for s in sessions}):
                    row[f"y{y}"] = float(avg[np.char.startswith(sessions, y)].sum())
                row["held"] = bool(row["per_year"] > 0 and row["sharpe"] >= C.HELD_SHARPE)
                whole.append(row)
                if ch == "all":
                    np.save(out_dir / f"setup_avg_{setup}_{sym}.npy", avg.astype(np.float32))
    pd.DataFrame(whole).to_csv(out_dir / "setups_whole.csv", index=False)
    best = best_usual()
    bu = df[df["id"].isin(best["id"])].merge(best[["id", "q_sharpe", "q_per_year"]], on="id")
    bu.to_csv(out_dir / "best_usual.csv", index=False)
    df[df["set"] == "named"].to_csv(out_dir / "named_versions.csv", index=False)
    # dials the versions that held up have in common (share among held vs among all, QQQ)
    vers = {r["id"]: r for r in load_versions()}
    q = df[df["market"] == C.MAIN]
    held_ids = set(q.loc[q["held"], "id"])
    from collections import Counter
    allc, heldc = Counter(), Counter()
    for vid_, r in ((i, vers[i]) for i in q["id"]):
        for k, x in r["dials"].items():
            allc[(k, str(x))] += 1
            if vid_ in held_ids:
                heldc[(k, str(x))] += 1
    rows = [{"dial": k, "value": x, "share_all": allc[(k, x)] / len(q), "share_held": heldc[(k, x)] / max(len(held_ids), 1),
             "lift": (heldc[(k, x)] / max(len(held_ids), 1)) / (allc[(k, x)] / len(q))} for (k, x) in allc]
    pd.DataFrame(rows).sort_values("lift", ascending=False).to_csv(out_dir / "held_dials.csv", index=False)
    (out_dir / "summary.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))


def rehearse() -> None:
    """The last month of discovery, shifted 10% in price, through the exact final-test code; the seal stays closed."""
    frames = {s: final_frame(s, None, "2024-07-01", "2024-04-01", shift=1.10) for s in C.SYMBOLS}
    out = C.DATA / "rehearsal"
    df = evaluate(frames, ("2024-07-01", C.DISCOVERY[1]), out, "rehearsal")
    report(df, out)
    print("rehearsal ok:", len(df), "version runs")


def run() -> None:
    if (OUT / "all_versions.csv.gz").exists():
        sys.exit("STOP: final results already exist; the final test runs once")
    check_hashes()
    with guard.unlock("final_test", note="final test run"):
        frames = {}
        for sym in C.SYMBOLS:
            sealed = guard.read_frame(C.SEALED / f"{sym}.parquet")
            # keep only complete sessions (the ingest already dropped incomplete ones; checked again here)
            last = sealed.groupby("session")["minute"].max()
            ok = last.index[(last >= C.LAST_FULL_BAR_MIN) | ((last >= 12 * 60 + 59) & (last < 13 * 60 + 1))]
            sealed = sealed[sealed["session"].isin(ok)]
            frames[sym] = final_frame(sym, sealed, C.FINAL_FROM, C.FINAL_WARMUP_FROM)
        last_session = max(f["session"].max() for f in frames.values())
        df = evaluate(frames, (C.FINAL_FROM, last_session), OUT, "final")
    report(df, OUT)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["rehearse", "run", "report"])
    a = ap.parse_args()
    if a.cmd == "rehearse":
        rehearse()
    elif a.cmd == "run":
        run()
    else:
        report(pd.read_csv(OUT / "all_versions.csv.gz"), OUT)


if __name__ == "__main__":
    main()
