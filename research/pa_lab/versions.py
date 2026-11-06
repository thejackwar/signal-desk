"""Prompt 3: the version sets, all drawn with the master seed and saved (and hashed) before anything runs.

- spread set: SPREAD_PER_SETUP versions per setup, filled greedily so each dial value appears about as often
  as random valid draws would give it (draw 400 candidates, add the one that brings the shares closest to
  target); only versions with at least 40 signals on QQQ discovery are kept
- common set: each setup's usual settings crossed with its usual entries, two stops, four targets, no filter
  or exactly one, and its main variants
- people sample: per setup, 40% with no filter, 40% with one and 20% with two, every other dial at random
- six named versions, each a full system as usually taught, with lab defaults for what the description leaves out
"""
from __future__ import annotations

import gzip
import hashlib
import itertools
import json
import os
from collections import Counter

import numpy as np

from . import config as C
from .dials import BREAK_SETUPS, FILTERS, LEVEL_SETUPS, SETUPS, dial_space, valid

FILTER_KEYS = list(FILTERS)
FILTER_OFF = {"f_trend": "off", "f_choppy": "off", "f_run": "off", "f_fresh": "off", "f_news": "trade",
              "f_session": "all", "f_pullback": "off", "f_gap": "off"}


def scale() -> float:
    return float(os.environ.get("PA_LAB_SCALE", "1"))


def vid(setup: str, v: dict) -> str:
    return hashlib.sha1(json.dumps([setup, v], sort_keys=True, default=str).encode()).hexdigest()[:12]


def random_version(setup: str, rng, n_filters: int | None = None) -> dict:
    sp = dial_space(setup)
    while True:
        v = {k: vals[int(rng.integers(len(vals)))] for k, (vals, _) in sp.items()}
        if n_filters is not None:
            v.update(FILTER_OFF)
            for k in rng.permutation(FILTER_KEYS)[:n_filters]:
                vals = [x for x in sp[k][0] if x != FILTER_OFF[k]]
                v[k] = vals[int(rng.integers(len(vals)))]
        v = {k: (x.item() if hasattr(x, "item") else x) for k, x in v.items()}
        if valid(setup, v):
            return v


def target_shares(setup: str, rng, n: int = 20000) -> dict:
    cnt = Counter()
    for _ in range(n):
        for k, x in random_version(setup, rng).items():
            cnt[(k, str(x))] += 1
    return {k: c / n for k, c in cnt.items()}


def spread_set(setup: str, rng, signal_ok, per_setup: int) -> list[dict]:
    tgt = target_shares(setup, rng)
    keys = list(tgt)
    kidx = {k: i for i, k in enumerate(keys)}
    T = np.array([tgt[k] for k in keys])
    counts = np.zeros(len(keys))
    chosen, seen = [], set()
    n_draw = max(10, int(C.SPREAD_CANDIDATES * min(1.0, scale() * 10)))
    tries = 0
    while len(chosen) < per_setup and tries < per_setup * 20:
        tries += 1
        cands = [random_version(setup, rng) for _ in range(n_draw)]
        rows = []
        for v in cands:
            r = np.zeros(len(keys))
            for k, x in v.items():
                j = kidx.get((k, str(x)))
                if j is not None:
                    r[j] = 1
            rows.append(r)
        R = np.array(rows)
        m = len(chosen) + 1
        err = np.abs((counts + R) / m - T).sum(axis=1)
        for q in np.argsort(err):
            v = cands[q]
            h = vid(setup, v)
            if h in seen or not signal_ok(setup, v):
                continue
            chosen.append(v)
            seen.add(h)
            counts += R[q]
            break
    return chosen


def common_set(setup: str) -> list[dict]:
    if setup in BREAK_SETUPS:
        entries = [("close", "none"), ("limit", "none")]
    else:
        entries = [("limit", "none"), ("close", "close_back"), ("close", "either")]
    stops = ["level", "swing"] if setup != "fvg" else ["gap_mid", "swing"]
    targets = ["1R", "2R", "3R", "swing"]
    filters = [{}, {"f_trend": "setup"}, {"f_session": "open"}, {"f_news": "skip"}]
    variants = {
        "level_bounce": [{"lvl_tol": 0.25, "lvl_touches": t, "lvl_shape": s} for t in (2, 3) for s in ("line", "box")],
        "breakout": [{"lvl_tol": 0.25, "lvl_touches": t, "lvl_shape": s, "bo_margin": 0.0} for t in (2, 3) for s in ("line", "box")],
        "flip_zone": [{"lvl_tol": 0.25, "lvl_touches": 2, "lvl_shape": "line", "flip_window": w} for w in (5, 10, 20)],
        "trend_pullback": [{"pb_level": f} for f in (0.382, 0.5, 0.618)],
        "trend_line": [{"tl_tol": t} for t in (0.1, 0.25)],
        "bos_continuation": [{"retest": r} for r in ("no", "yes")],
        "choch_reversal": [{"retest": r} for r in ("no", "yes")],
        "sweep_reclaim": [{"sweep_depth": d, "reclaim_bars": b} for d in (0.1, 0.25) for b in (1, 3)],
        "sd_zone": [{"zone_first": f, "zone_age": 200} for f in ("yes", "no")],
        "fvg": [{"gap_min": g, "gap_first": f} for g in (0.0, 0.1) for f in ("yes", "no")],
    }[setup]
    out = []
    for ch, (entry, conf), stop, tgt, flt, var in itertools.product(["1m", "5m", "15m", "1h", "4h", "1d"], entries, stops, targets, filters, variants):
        v = {"chart": ch, "direction": "both", "swing": 3, "trigger": "same", "confirm": conf, "entry": entry,
             "stop_type": "hard", "stop_ref": stop, "stop_buf": 0.1, "target": tgt, "manage": "hold", "time_exit": "96"}
        v.update(FILTER_OFF)
        v.update(flt)
        v.update(var)
        if ch == "1d" and flt.get("f_session"):
            continue
        if valid(setup, v):
            out.append(v)
    return out


def people_sample(setup: str, rng, n: int, seen: set) -> list[dict]:
    out = []
    plan = [0] * int(n * 0.4) + [1] * int(n * 0.4) + [2] * (n - 2 * int(n * 0.4))
    for nf in plan:
        for _ in range(200):
            v = random_version(setup, rng, n_filters=nf)
            h = vid(setup, v)
            if h not in seen:
                seen.add(h)
                out.append(v)
                break
    return out


def _named_base(**kw) -> dict:
    v = {"direction": "both", "swing": 3, "trigger": "same", "confirm": "none", "entry": "close", "stop_type": "hard",
         "stop_ref": "level", "stop_buf": 0.1, "target": "2R", "manage": "hold", "time_exit": "96"}
    v.update(FILTER_OFF)
    v.update(kw)
    return v


NAMED = {
    "The Bare Minimum": ("level_bounce", _named_base(chart="15m", entry="limit", lvl_tol=0.25, lvl_touches=2, lvl_shape="line", time_exit="flat_1600")),
    "The Daily Version": ("breakout", _named_base(chart="1d", trigger="same", confirm="close_back", stop_ref="swing", f_trend="daily",
                                                 lvl_tol=0.25, lvl_touches=2, lvl_shape="box", bo_margin=0.0, time_exit="24")),
    "The Checklist": ("trend_pullback", _named_base(chart="1h", trigger="down1", confirm="either", stop_ref="swing", f_trend="up1",
                                                   f_news="skip", f_session="open", pb_level=0.5)),
    "The Sweep System": ("sweep_reclaim", _named_base(chart="15m", trigger="down1", confirm="lower_bos", stop_ref="wick", target="swing",
                                                     f_session="open", time_exit="flat_1600", sweep_depth=0.1, reclaim_bars=2)),
    "The Zone System": ("sd_zone", _named_base(chart="1h", trigger="down1", confirm="rejection", target="3R", f_trend="setup",
                                              zone_first="yes", zone_age=200)),
    "The Entry Model": ("fvg", _named_base(chart="5m", trigger="down1", entry="limit", stop_ref="gap_mid", f_session="open",
                                          f_news="skip", time_exit="flat_1600", gap_min=0.1, gap_first="yes")),
}


def plan(signal_count) -> dict:
    """Draw every set, save the configurations and their hash before running anything."""
    rng = np.random.default_rng(C.MASTER_SEED)
    per_spread = max(5, int(C.SPREAD_PER_SETUP * scale()))
    per_people = max(5, int(C.PEOPLE_PER_SETUP * scale()))
    rows = []
    for setup in SETUPS:
        ok = lambda s, v: signal_count(s, v) >= C.MIN_SIGNALS
        spread = spread_set(setup, rng, ok, per_spread)
        seen = {vid(setup, v) for v in spread}
        common = common_set(setup)
        if scale() < 1:
            common = [common[int(i)] for i in rng.choice(len(common), size=max(5, int(len(common) * scale())), replace=False)]
        for v in common:
            seen.add(vid(setup, v))
        people = people_sample(setup, rng, per_people, seen)
        for sset, vs in (("spread", spread), ("common", common), ("people", people)):
            for v in vs:
                rows.append({"id": vid(setup, v), "set": sset, "setup": setup, "dials": v})
        print(f"  {setup}: spread {len(spread)}, common {len(common)}, people {len(people)}")
    for name, (setup, v) in NAMED.items():
        assert valid(setup, v), name
        rows.append({"id": vid(setup, v), "set": "named", "name": name, "setup": setup, "dials": v})
    out = C.RESULTS / "funnel"
    out.mkdir(parents=True, exist_ok=True)
    p = out / "versions.jsonl.gz"
    body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows).encode()
    with open(p, "wb") as f:                      # mtime=0 so the same versions always hash the same
        f.write(gzip.compress(body, mtime=0))
    from .guard import sha256
    h = sha256(p)
    (out / "versions.sha256").write_text(h + "  versions.jsonl.gz\n")
    return {"versions": len(rows), "sha256": h}


def load_versions() -> list[dict]:
    with gzip.open(C.RESULTS / "funnel" / "versions.jsonl.gz", "rt") as f:
        return [json.loads(x) for x in f]
