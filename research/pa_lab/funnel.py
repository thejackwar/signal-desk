"""Prompt 3: run every version through the engine on discovery data, the funnel, and each setup as a whole.

    python -m research.pa_lab.funnel plan              draw and save every version (hashed) before any run
    python -m research.pa_lab.funnel run --setup X     run one setup's versions on QQQ and SPY discovery
    python -m research.pa_lab.funnel merge             stage tables, candidates, working setups (hashed)

Stages, on QQQ (fixed before the first run, config.FUNNEL):
  1 enough trades (30+)  2 profitable after all costs  3 Sharpe 0.5+  4 beats 10,000 sets of matched random
  trades (fewer than 5% make as much)  5 holds on SPY (profitable, Sharpe 0.3+)
A version is a final-test candidate when it passes 1-4, makes money in both eras, and holds on SPY.
Only versions with at least 40 QQQ discovery signals are graded.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict

import numpy as np
import pandas as pd

from . import config as C
from .dials import SETUP_NAMES, SETUPS
from .engine import run_version, sess_range, sharpe, twin_test, window_idx
from .guard import sha256
from .lab import discovery_market
from .setups import generate
from .versions import load_versions, plan

OUT = C.RESULTS / "funnel"
DAILY = C.DATA / "funnel_daily"         # per-version daily P&L (large; cached, not committed)


def signal_counter(mk, window):
    a, b = window

    def count(setup, v):
        sc = mk.charts[v["chart"]]
        sig = generate(sc, setup, int(v["swing"]), v)
        if not len(sig):
            return 0
        e = sc.end[sig[:, 0].astype(int)]
        m = (e >= a) & (e < b)
        if v["direction"] != "both":
            m &= sig[:, 1] == (1 if v["direction"] == "long" else -1)
        return int(m.sum())
    return count


def metrics(mk, res, disc_mask, era_masks) -> dict:
    tr, daily = res.trades, res.daily
    dd = daily[disc_mask]
    years = disc_mask.sum() / 252
    out = {"signals": res.n_signals, "trades": int(len(tr)), "dollars": float(tr[:, 6].sum()) if len(tr) else 0.0,
           "per_year": float(dd.sum() / years) if years else 0.0, "sharpe": sharpe(dd),
           "win_rate": float((tr[:, 6] > 0).mean()) if len(tr) else 0.0,
           "avg_R": float(tr[:, 7].mean()) if len(tr) else 0.0}
    for k, m in era_masks.items():
        out[k] = float(daily[m].sum())
    return out


def run_setup(setup: str) -> None:
    rows = [r for r in load_versions() if r["setup"] == setup]
    disc = (C.DISCOVERY[0], C.DISCOVERY[1])
    DAILY.mkdir(parents=True, exist_ok=True)
    out_rows = []
    for sym in C.SYMBOLS:
        mk = discovery_market(sym)
        w = window_idx(mk, *disc)
        dm = sess_range(mk, *disc)
        em = {k: sess_range(mk, *v) for k, v in C.ERAS.items()}
        sess_ids = np.flatnonzero(dm)
        mat = np.zeros((len(rows), int(dm.sum())), np.float32)
        t0 = time.time()
        for q, r in enumerate(rows):
            res = run_version(mk, setup, r["dials"], w)
            m = metrics(mk, res, dm, em)
            m.update(id=r["id"], market=sym)
            if sym == C.MAIN:
                graded = m["signals"] >= C.MIN_SIGNALS or r["set"] == "named"
                s1 = m["trades"] >= C.FUNNEL["min_trades"]
                s2 = s1 and m["dollars"] > 0
                s3 = s2 and m["sharpe"] >= C.FUNNEL["min_sharpe"]
                p = np.nan
                if graded and s3:
                    seed = C.MASTER_SEED + int(r["id"][:6], 16)
                    p = twin_test(mk, setup, r["dials"], res, w, sess_ids, C.TWIN_SETS, seed)
                m.update(graded=graded, s1=s1, s2=s2, s3=s3, twin_p=p, s4=bool(s3 and p < C.FUNNEL["twin_p"]))
            mat[q] = res.daily[dm]
            out_rows.append(m)
            if q % 500 == 0:
                print(f"  {setup} {sym} {q}/{len(rows)}  {time.time() - t0:.0f}s", flush=True)
        np.save(DAILY / f"{setup}_{sym}.npy", mat)
    df = pd.DataFrame(out_rows)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / f"metrics_{setup}.csv.gz", index=False)


def merge() -> None:
    vers = load_versions()
    meta = pd.DataFrame([{"id": r["id"], "set": r["set"], "setup": r["setup"], "name": r.get("name", ""),
                          **{f"d_{k}": str(x) for k, x in r["dials"].items()}} for r in vers])
    frames = [pd.read_csv(OUT / f"metrics_{s}.csv.gz") for s in SETUPS if (OUT / f"metrics_{s}.csv.gz").exists()]
    allm = pd.concat(frames, ignore_index=True)
    q = allm[allm.market == C.MAIN].drop(columns="market").add_prefix("q_").rename(columns={"q_id": "id"})
    e = allm[allm.market == C.CHECK].drop(columns="market").add_prefix("s_").rename(columns={"s_id": "id"})
    # one row per (version, set): a version drawn in two sets keeps both rows
    df = meta.merge(q.drop_duplicates("id"), on="id", how="left").merge(e.drop_duplicates("id"), on="id", how="left")
    df["s5"] = (df["s_dollars"] > 0) & (df["s_sharpe"] >= C.FUNNEL["check_sharpe"])
    df["both_eras"] = (df["q_era1"] > 0) & (df["q_era2"] > 0)
    df["candidate"] = df["q_graded"].astype(bool) & df["q_s4"].astype(bool) & df["both_eras"] & df["s5"]
    df.to_csv(OUT / "all_versions.csv.gz", index=False)

    g = df[df["q_graded"].astype(bool)]
    stage_cols = ["q_s1", "q_s2", "q_s3", "q_s4", "s5", "candidate"]
    def shares(by):
        t = g.groupby(by)[stage_cols].mean().reset_index()
        t.insert(1, "versions", g.groupby(by).size().values)
        return t
    funnel_total = {"graded": int(len(g)), **{c: int(g[c].astype(bool).sum()) for c in stage_cols},
                    "by_set": {s: {"graded": int((g["set"] == s).sum()), **{c: int(g.loc[g["set"] == s, c].astype(bool).sum()) for c in stage_cols}}
                               for s in ["spread", "common", "people", "named"]}}
    shares("setup").to_csv(OUT / "stage_by_setup.csv", index=False)
    shares("d_chart").to_csv(OUT / "stage_by_chart.csv", index=False)
    dial_rows = []
    for col in [c for c in g.columns if c.startswith("d_")]:
        for val, sub in g.groupby(col):
            dial_rows.append({"dial": col[2:], "value": val, "versions": len(sub),
                              **{c: float(sub[c].astype(bool).mean()) for c in stage_cols}, "median_sharpe": float(sub["q_sharpe"].median())})
    pd.DataFrame(dial_rows).to_csv(OUT / "stage_by_dial.csv", index=False)

    # win rate against profit (a sample of graded versions, for the scatter)
    g.sample(min(3000, len(g)), random_state=C.MASTER_SEED)[["setup", "d_chart", "q_win_rate", "q_per_year", "q_sharpe"]] \
        .to_csv(OUT / "winrate_vs_profit.csv", index=False)

    # effect of each rule with everything else equal: pairs in the common set differing in one dial
    com = df[df["set"] == "common"]
    dcols = [c for c in com.columns if c.startswith("d_")]
    eff = []
    for col in ["d_entry", "d_confirm", "d_stop_ref", "d_target", "d_f_trend", "d_f_session", "d_f_news", "d_chart"]:
        others = ["setup"] + [c for c in dcols if c != col]
        base = com.assign(_k=com[others].fillna("").astype(str).agg("|".join, axis=1))
        piv = base.pivot_table(index="_k", columns=col, values="q_sharpe", aggfunc="first")
        ref = piv.columns[0]
        for val in piv.columns[1:]:
            dlt = (piv[val] - piv[ref]).dropna()
            if len(dlt):
                eff.append({"dial": col[2:], "value": val, "vs": ref, "pairs": int(len(dlt)),
                            "mean_sharpe_change": float(dlt.mean()), "share_better": float((dlt > 0).mean())})
    pd.DataFrame(eff).to_csv(OUT / "rule_effects.csv", index=False)

    named = df[df["set"] == "named"][["name", "setup", "id", "q_trades", "q_dollars", "q_per_year", "q_sharpe", "q_win_rate",
                                      "q_era1", "q_era2", "q_twin_p", "s_dollars", "s_sharpe", "candidate"]]
    named.to_csv(OUT / "named_versions.csv", index=False)

    # each setup as a whole: equal-weight average daily P&L of all its versions, per chart and market
    whole = []
    for setup in SETUPS:
        for sym in C.SYMBOLS:
            p = DAILY / f"{setup}_{sym}.npy"
            if not p.exists():
                continue
            mat = np.load(p)
            ids = [r for r in vers if r["setup"] == setup]
            charts = np.array([r["dials"]["chart"] for r in ids])
            mk_sess = sess_sessions(sym)
            era1 = (mk_sess >= C.ERAS["era1"][0]) & (mk_sess <= C.ERAS["era1"][1])
            era2 = (mk_sess >= C.ERAS["era2"][0]) & (mk_sess <= C.ERAS["era2"][1])
            for ch in ["all", "1m", "5m", "15m", "1h", "4h", "1d"]:
                sel = np.ones(len(ids), bool) if ch == "all" else charts == ch
                if not sel.any():
                    continue
                avg = mat[sel].mean(axis=0).astype(np.float64)
                yrs = len(avg) / 252
                row = {"setup": setup, "name": SETUP_NAMES[setup], "market": sym, "chart": ch, "versions": int(sel.sum()),
                       "per_year": float(avg.sum() / yrs), "sharpe": sharpe(avg), "era1": float(avg[era1].sum()), "era2": float(avg[era2].sum())}
                row["works"] = bool(row["per_year"] > 0 and row["sharpe"] >= C.FUNNEL["min_sharpe"] and row["era1"] > 0 and row["era2"] > 0)
                whole.append(row)
    wdf = pd.DataFrame(whole)
    wdf.to_csv(OUT / "setups_whole.csv", index=False)

    cands = df[df["candidate"]][["id", "set", "setup", "name"]]
    working = wdf[(wdf["works"]) & (wdf["market"] == C.MAIN)][["setup", "chart"]] if len(wdf) else pd.DataFrame(columns=["setup", "chart"])
    sel = {"candidates": cands.to_dict("records"), "working_setups": working.to_dict("records"),
           "versions_sha256": (OUT / "versions.sha256").read_text().split()[0]}
    (OUT / "selection.json").write_text(json.dumps(sel, indent=1, sort_keys=True))
    (OUT / "selection.sha256").write_text(sha256(OUT / "selection.json") + "  selection.json\n")
    (OUT / "funnel_summary.json").write_text(json.dumps(funnel_total, indent=1))
    print(json.dumps(funnel_total, indent=1))
    print(f"  candidates: {len(cands)}, working setups (QQQ): {len(working)}")


_SESS: dict = {}


def sess_sessions(sym: str) -> np.ndarray:
    if sym not in _SESS:
        mk = discovery_market(sym)
        _SESS[sym] = mk.sessions[sess_range(mk, *C.DISCOVERY)]
    return _SESS[sym]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "run", "merge"])
    ap.add_argument("--setup")
    a = ap.parse_args()
    if a.cmd == "plan":
        mk = discovery_market(C.MAIN)
        print(plan(signal_counter(mk, window_idx(mk, *C.DISCOVERY))))
    elif a.cmd == "run":
        for s in ([a.setup] if a.setup else SETUPS):
            run_setup(s)
    else:
        merge()


if __name__ == "__main__":
    main()
