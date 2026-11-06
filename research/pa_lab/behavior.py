"""Prompt 2: behaviour tests. Does price respect levels, flips, breaks, sweeps, gaps and zones more than chance?

Each test measures one effect on QQQ discovery data pooled over the 5m, 15m and 1h charts (swing strength 3),
with a 95% interval from 2,000 bootstrap resamples of whole sessions, then repeats it on SPY and on each era.
Verdict: "survived" when QQQ's pooled effect points the claimed way with an interval that excludes zero, SPY
has the same sign and both eras agree; "split" when only part of that holds; "didn't survive" otherwise.

Also two engine sanity checks: random entries (should lose about their costs) and "the cheat" (a strategy
that peeks one bar ahead; should look impossibly good, proving the engine would expose look-ahead).
"""
from __future__ import annotations

import json

import numpy as np

from . import config as C
from .charts import Market, fvgs, get_structure, get_swings, levels_at, zones
from .engine import sharpe, sess_range, window_idx, _sim, sess_end
from .lab import discovery_market

CHARTS = ["5m", "15m", "1h"]
K = 3
H_BARS = 20
MOVE = 0.5          # ATR needed to call a hold (move away) or a fail (close through)


# ------------------------------------------------------------------ primitives
def hold_outcome(ch, i, d, lv, horizon=H_BARS, move=MOVE):
    """+1 when price moves `move` ATR in direction d from lv before closing `move` ATR through it, 0 if it
    closes through first, nan if neither within horizon. Starts after bar i."""
    a = ch.atr[i]
    if not a > 0:
        return np.nan
    for j in range(i + 1, min(len(ch), i + 1 + horizon)):
        if d > 0:
            if ch.h[j] >= lv + move * a:
                return 1.0
            if ch.c[j] < lv - move * a:
                return 0.0
        else:
            if ch.l[j] <= lv - move * a:
                return 1.0
            if ch.c[j] > lv + move * a:
                return 0.0
    return np.nan


def start_ok(ch, i, d, lv):
    """Touches are compared from the same start: the touch bar closes back on the level's side, within 0.5 ATR."""
    a = ch.atr[i]
    return a > 0 and 0 <= d * (ch.c[i] - lv) <= 0.5 * a


def placebo(ch, rng, i, d, lv, lo_bar, hi_bar, tries=30):
    """A random line matched to a real touch: a random bar whose low (high) reaches a line placed the same
    distance in ATR below (above) its close as the real level was. Returns (bar, line) or None."""
    dist = d * (ch.c[i] - lv) / ch.atr[i]
    for _ in range(tries):
        j = int(rng.integers(lo_bar + 1, hi_bar))
        a = ch.atr[j]
        if not a > 0:
            continue
        lv_r = ch.c[j] - d * dist * a
        if (d > 0 and ch.l[j] <= lv_r and ch.c[j - 1] > lv_r) or (d < 0 and ch.h[j] >= lv_r and ch.c[j - 1] < lv_r):
            return j, lv_r
    return None


def matched(S, name, ch, rng, i, d, lv, se, lo_bar, hi_bar):
    """Real touch in group 0 and its matched random line in group 1 (only when both start the same way)."""
    if not start_ok(ch, i, d, lv):
        return
    pl = placebo(ch, rng, i, d, lv, lo_bar, hi_bar)
    if pl is None:
        return
    S[name].add(hold_outcome(ch, i, d, lv), se[i], 0)
    S[name].add(hold_outcome(ch, pl[0], d, pl[1]), se[pl[0]], 1)


def fwd(ch, i, d, n):
    """Signed close-to-close move over n bars, in ATR."""
    a = ch.atr[i]
    if not a > 0 or i + n >= len(ch):
        return np.nan
    return d * (ch.c[i + n] - ch.c[i]) / a


class Sample:
    """Values with their session and a group label; effect = mean(group A) - mean(group B) (or mean(A))."""

    def __init__(self):
        self.v, self.s, self.g = [], [], []

    def add(self, val, sess, grp):
        if val == val:
            self.v.append(val); self.s.append(sess); self.g.append(grp)

    def arrays(self):
        return np.array(self.v, float), np.array(self.s, int), np.array(self.g, int)


def effect_ci(sample: Sample, n_sess: int, rng, two_group: bool, boot=C.BOOT):
    v, s, g = sample.arrays()
    if two_group:
        if (g == 0).sum() < 10 or (g == 1).sum() < 10:
            return None
    elif len(v) < 10:
        return None
    def stat(w):
        if two_group:
            a = np.bincount(s[g == 0], weights=v[g == 0], minlength=n_sess)
            na = np.bincount(s[g == 0], minlength=n_sess)
            b = np.bincount(s[g == 1], weights=v[g == 1], minlength=n_sess)
            nb_ = np.bincount(s[g == 1], minlength=n_sess)
            return (w @ a) / np.maximum(w @ na, 1) - (w @ b) / np.maximum(w @ nb_, 1)
        a = np.bincount(s, weights=v, minlength=n_sess)
        na = np.bincount(s, minlength=n_sess)
        return (w @ a) / np.maximum(w @ na, 1)
    used = np.unique(s)
    point = float(stat(np.ones((1, n_sess)))[0])
    W = np.zeros((boot, n_sess))
    draws = rng.choice(used, size=(boot, len(used)))
    for r in range(boot):
        W[r] = np.bincount(draws[r], minlength=n_sess)
    bs = stat(W)
    lo, hi = np.percentile(bs, [2.5, 97.5])
    n = int((g == 0).sum()) if two_group else len(v)
    m = int((g == 1).sum()) if two_group else 0
    return {"effect": point, "lo": float(lo), "hi": float(hi), "n": n, "n_b": m}


# ------------------------------------------------------------------ events
def level_touches(ch, sess, rng, lo_bar, hi_bar):
    """Touches of real levels (swing clusters, 0.25 ATR, any touches) and of random lines."""
    pb, cb, pr, kd = get_swings(ch, K)
    order = np.argsort(cb, kind="stable")
    cb, pr = cb[order].astype(np.int64), pr[order]
    out = []
    last_j = -1
    lo = hi = mid = cnt = np.empty(0)
    for i in range(max(1, lo_bar), hi_bar):
        a = ch.atr[i]
        if not a > 0:
            continue
        j = np.searchsorted(cb, i)
        if j != last_j:
            lo, hi, mid, cnt = levels_at(i, a, cb, pr, 40, 0.25)
            last_j = j
        p = ch.c[i - 1]
        for q in range(len(mid)):
            if p > hi[q] and ch.l[i] <= hi[q]:
                out.append((i, 1, mid[q], hi[q], lo[q], int(cnt[q])))
            elif p < lo[q] and ch.h[i] >= lo[q]:
                out.append((i, -1, mid[q], hi[q], lo[q], int(cnt[q])))
    return np.array(out, float).reshape(-1, 6)


def run_tests(mk: Market, window: tuple[str, str], rng) -> dict:
    n_sess = len(mk.sessions)
    S = {name: Sample() for name in TESTS}
    a1, b1 = window_idx(mk, *window)
    for cname in CHARTS:
        ch = mk.charts[cname]
        lo_bar = int(np.searchsorted(ch.end, a1))
        hi_bar = int(np.searchsorted(ch.end, b1)) - 60
        se = ch.sess
        ev = level_touches(ch, se, rng, lo_bar, hi_bar)
        touch_hist: dict = {}
        for i, d, mid, hi, lo, cnt in ev:
            i = int(i); d = int(d)
            ho_line = hold_outcome(ch, i, d, mid)
            ho_box = hold_outcome(ch, i, d, hi if d > 0 else lo)
            matched(S, "levels_vs_random", ch, rng, i, d, mid, se, lo_bar, hi_bar)
            if start_ok(ch, i, d, mid):
                S["hold_by_touches"].add(ho_line, se[i], 0 if cnt >= 3 else (1 if cnt == 1 else 2))
                S["wall_or_target"].add(ho_line, se[i], 0 if cnt >= 4 else (1 if cnt in (2, 3) else 2))
            edge = hi if d > 0 else lo
            if start_ok(ch, i, d, edge) and start_ok(ch, i, d, mid):
                S["line_vs_box"].add(ho_box, se[i], 0)
                S["line_vs_box"].add(ho_line, se[i], 1)
            key = round(mid / max(ch.atr[i], 1e-9) * 4)
            prev = touch_hist.get(key)
            b = fwd(ch, i, d, 10)
            if prev is not None and i - prev[0] < 300:
                S["shrinking_bounces"].add(b, se[i], 0)
                S["shrinking_bounces"].add(prev[1], se[int(prev[0])], 1)
            touch_hist[key] = (i, b)
        # wick through vs close through a level
        for i, d, mid, hi, lo, cnt in ev:
            i = int(i); d = int(d)
            a = ch.atr[i]
            through = (ch.l[i] < mid - 0.1 * a) if d > 0 else (ch.h[i] > mid + 0.1 * a)
            if not through:
                continue
            closed_through = ch.c[i] < mid if d > 0 else ch.c[i] > mid
            # continuation through the level by 1 ATR within 20 bars (against d)
            S["wick_vs_close"].add(1.0 if fwd(ch, i, -d, 20) >= 1 else 0.0, se[i], 0 if closed_through else 1)
        tr, lhi, llo, eb, ed, ek, el = get_structure(ch, K)
        sw = get_swings(ch, K)
        for b, d, kk, lv in zip(eb, ed, ek, el):
            if not (lo_bar <= b < hi_bar):
                continue
            for n_ in (10, 20, 50):
                S[f"{'bos' if kk == 1 else 'choch'}_follow_{n_}"].add(fwd(ch, b, d, n_), se[b], 0)
            # flip: first retest within 20 bars from the new side
            for j in range(b + 1, min(len(ch), b + 21)):
                if (d > 0 and ch.l[j] <= lv) or (d < 0 and ch.h[j] >= lv):
                    matched(S, "flip_zones", ch, rng, j, d, lv, se, lo_bar, hi_bar)
                    break
            if kk == 2:
                # did a sweep of the opposite swing happen in the 10 bars before the change of character?
                swept = False
                for j in range(max(1, b - 10), b):
                    ref = llo[j - 1] if d > 0 else lhi[j - 1]
                    if ref == ref and ((d > 0 and ch.l[j] < ref and ch.c[j] > ref) or (d < 0 and ch.h[j] > ref and ch.c[j] < ref)):
                        swept = True
                        break
                S["trend_starts_with_sweep"].add(fwd(ch, b, d, 20), se[b], 0 if swept else 1)
        # sweeps (wick past the last swing, close back) vs closes through it
        for i in range(lo_bar + 1, hi_bar):
            L, Hh = llo[i - 1], lhi[i - 1]
            if L == L and ch.l[i] < L:
                S["sweep_vs_close"].add(fwd(ch, i, 1, 20), se[i], 0 if ch.c[i] > L else 1)
            if Hh == Hh and ch.h[i] > Hh:
                S["sweep_vs_close"].add(fwd(ch, i, -1, 20), se[i], 0 if ch.c[i] < Hh else 1)
        # trend lines vs random lines
        pbv, cbv, prv, kdv = sw
        for side in (1, -1):
            idx = np.flatnonzero(kdv == (-1 if side == 1 else 1))
            idx = idx[np.argsort(cbv[idx], kind="stable")]
            for q in range(1, len(idx)):
                b1_, b2_ = pbv[idx[q - 1]], pbv[idx[q]]
                p1, p2 = prv[idx[q - 1]], prv[idx[q]]
                if b2_ == b1_ or (side == 1 and p2 <= p1) or (side == -1 and p2 >= p1):
                    continue
                start = cbv[idx[q]] + 1
                stop_ = cbv[idx[q + 1]] if q + 1 < len(idx) else start + 100
                for i in range(max(start, lo_bar), min(stop_, hi_bar)):
                    line = p2 + (p2 - p1) / (b2_ - b1_) * (i - b2_)
                    if (side == 1 and ch.c[i - 1] > line and ch.l[i] <= line) or (side == -1 and ch.c[i - 1] < line and ch.h[i] >= line):
                        matched(S, "trend_lines", ch, rng, i, side, line, se, lo_bar, hi_bar)
                        break
        # fair value gaps: hold at first return vs random; fill within 50 bars
        for gb, gd, glo, ghi, midc in fvgs(ch):
            gb = int(gb); gd = int(gd)
            if not (lo_bar <= gb < hi_bar):
                continue
            edge = ghi if gd > 0 else glo
            far = glo if gd > 0 else ghi
            filled = 0.0
            for j in range(gb + 1, min(len(ch), gb + 51)):
                if (gd > 0 and ch.l[j] <= far) or (gd < 0 and ch.h[j] >= far):
                    filled = 1.0
                    break
            # matched random zone: same far-edge distance from the close (ATR), at a random bar
            a0 = ch.atr[gb]
            if a0 > 0:
                dist = gd * (ch.c[gb] - far) / a0
                r = int(rng.integers(lo_bar + 1, hi_bar))
                if ch.atr[r] > 0:
                    far_r = ch.c[r] - gd * dist * ch.atr[r]
                    fr = 0.0
                    for j in range(r + 1, min(len(ch), r + 51)):
                        if (gd > 0 and ch.l[j] <= far_r) or (gd < 0 and ch.h[j] >= far_r):
                            fr = 1.0
                            break
                    S["fvg_fill"].add(filled, se[gb], 0)
                    S["fvg_fill"].add(fr, se[r], 1)
            for j in range(gb + 1, min(len(ch), gb + 200)):
                if (gd > 0 and ch.l[j] <= edge) or (gd < 0 and ch.h[j] >= edge):
                    matched(S, "fvg_hold", ch, rng, j, gd, edge, se, lo_bar, hi_bar)
                    break
        # supply/demand zones: first return vs later returns
        for zb, zd, zlo, zhi, base in zones(ch):
            zb = int(zb); zd = int(zd)
            if not (lo_bar <= zb < hi_bar):
                continue
            edge = zhi if zd > 0 else zlo
            nret = 0
            for j in range(zb + 1, min(len(ch), zb + 200)):
                if (zd > 0 and ch.c[j] < zlo) or (zd < 0 and ch.c[j] > zhi):
                    break
                if (zd > 0 and ch.l[j] <= edge < ch.l[j - 1]) or (zd < 0 and ch.h[j] >= edge > ch.h[j - 1]):
                    if start_ok(ch, j, zd, edge):
                        S["zones_first_vs_later"].add(hold_outcome(ch, j, zd, edge), se[j], 0 if nret == 0 else 1)
                    nret += 1
        # pullback depth: trend continuation after a deep (>=0.5) vs shallow (<0.5) pullback
        for i in range(lo_bar + 1, hi_bar):
            t = tr[i - 1]
            Hh, L = lhi[i - 1], llo[i - 1]
            if t == 0 or not (Hh == Hh and L == L) or Hh <= L:
                continue
            if t == 1 and ch.l[i] < ch.l[i - 1] and ch.c[i] > ch.o[i]:
                depth = (Hh - ch.l[i]) / (Hh - L)
                if 0.2 <= depth <= 1.0:
                    S["pullback_depth"].add(fwd(ch, i, 1, 20), se[i], 0 if depth >= 0.5 else 1)
            if t == -1 and ch.h[i] > ch.h[i - 1] and ch.c[i] < ch.o[i]:
                depth = (ch.h[i] - L) / (Hh - L)
                if 0.2 <= depth <= 1.0:
                    S["pullback_depth"].add(fwd(ch, i, -1, 20), se[i], 0 if depth >= 0.5 else 1)
        # engulfing / rejection candles at levels vs the same candles away from levels
        near = np.zeros(len(ch), bool)
        near_dir = np.zeros(len(ch), int)
        for i, d, mid, hi, lo, cnt in ev:
            near[int(i)] = True; near_dir[int(i)] = int(d)
        for i in range(max(lo_bar, 1), hi_bar):
            o, h, l, c = ch.o[i], ch.h[i], ch.l[i], ch.c[i]
            rng_ = max(h - l, 1e-9)
            po, pc = ch.o[i - 1], ch.c[i - 1]
            bull = ((min(o, c) - l) >= 0.5 * rng_) or (pc < po and c > o and c >= po and o <= pc)
            bear = ((h - max(o, c)) >= 0.5 * rng_) or (pc > po and c < o and c <= po and o >= pc)
            for d, ok in ((1, bull), (-1, bear)):
                if ok:
                    at = near[i] and near_dir[i] == d
                    S["candles_at_levels"].add(fwd(ch, i, d, 10), se[i], 0 if at else 1)
    out = {}
    for name, (kind, *_rest) in TESTS.items():
        res = effect_ci(S[name], n_sess, rng, two_group=(kind == "diff"))
        out[name] = res
    return out


# name: (kind, claim sign, title, what is measured)
TESTS = {
    "levels_vs_random": ("diff", 1, "Real levels vs random lines", "hold rate at swing levels minus hold rate at random touched lines"),
    "hold_by_touches": ("diff", 1, "Hold rate by touch count", "hold rate at levels with 3+ touches minus levels with 1 touch"),
    "wall_or_target": ("diff", 1, "Many touches: wall or target", "hold rate at 4+ touch levels minus 2-3 touch levels (positive = wall)"),
    "shrinking_bounces": ("diff", -1, "Shrinking bounces", "bounce (10 bars, ATR) at a later touch minus the earlier touch (claim: shrinks)"),
    "line_vs_box": ("diff", 1, "A line against a box", "hold rate measured at the box edge minus at the line"),
    "wick_vs_close": ("diff", 1, "Wick through vs close through", "follow-through 1 ATR beyond after a close through minus after a wick through"),
    "flip_zones": ("diff", 1, "Flip zones", "hold rate on the first retest of a broken swing minus random lines"),
    "bos_follow_10": ("mean", 1, "Break of structure, 10 bars", "move in the break's direction after 10 bars (ATR)"),
    "bos_follow_20": ("mean", 1, "Break of structure, 20 bars", "move in the break's direction after 20 bars (ATR)"),
    "bos_follow_50": ("mean", 1, "Break of structure, 50 bars", "move in the break's direction after 50 bars (ATR)"),
    "choch_follow_10": ("mean", 1, "Change of character, 10 bars", "move in the new direction after 10 bars (ATR)"),
    "choch_follow_20": ("mean", 1, "Change of character, 20 bars", "move in the new direction after 20 bars (ATR)"),
    "choch_follow_50": ("mean", 1, "Change of character, 50 bars", "move in the new direction after 50 bars (ATR)"),
    "sweep_vs_close": ("diff", 1, "Sweeps vs closes through", "reversal move (20 bars) after a sweep minus after a close through the swing"),
    "trend_starts_with_sweep": ("diff", 1, "Trends that start with a sweep", "20-bar move after a change of character with a sweep minus without"),
    "trend_lines": ("diff", 1, "Trend lines", "hold rate at trend line touches minus random lines"),
    "fvg_hold": ("diff", 1, "Fair value gaps hold", "hold rate at a gap's first return minus random lines"),
    "fvg_fill": ("diff", 1, "Fair value gaps fill", "share of gaps filled within 50 bars minus random zones at the same distance"),
    "zones_first_vs_later": ("diff", 1, "Zones: first return vs later", "hold rate on the first return to a zone minus later returns"),
    "pullback_depth": ("diff", 1, "Pullback depth", "20-bar trend move after a deep (50%+) pullback minus a shallow one"),
    "candles_at_levels": ("diff", 1, "Engulfing and rejection at levels", "10-bar move after the candle at a level minus the same candle elsewhere"),
}


def verdict(main, check, era1, era2, sign):
    if not main:
        return "didn't survive"
    pts = main["effect"] * sign > 0
    sig = (main["lo"] > 0) if sign > 0 else (main["hi"] < 0)
    same_check = check is not None and check["effect"] * sign > 0
    eras = all(e is not None and e["effect"] * sign > 0 for e in (era1, era2))
    if pts and sig and same_check and eras:
        return "survived"
    if pts and (sig or same_check or eras):
        return "split"
    return "didn't survive"


# ------------------------------------------------------------------ engine sanity: random entries and the cheat
def engine_checks(mk: Market, rng) -> dict:
    a, b = window_idx(mk, *C.DISCOVERY)
    m1 = mk.charts["1m"]
    ch = mk.charts["15m"]
    sel = np.flatnonzero((ch.end >= a) & (ch.end < b - 100) & (ch.atr > 0))
    picks = np.sort(rng.choice(sel, size=min(1500, len(sel)), replace=False))
    dec = ch.end[picks]
    d = np.where(rng.random(len(picks)) < 0.5, 1, -1)
    atr = ch.atr[picks]
    res = {}
    for name, dirs in (("random_entries", d), ("cheat", np.sign(m1.c[np.minimum(dec + 15, b - 1)] - m1.c[dec]).astype(np.int64))):
        dirs = np.where(dirs == 0, 1, dirs).astype(np.int64)
        entry = m1.c[dec] + dirs * C.TICK
        stop = entry - dirs * 1.0 * atr
        tgt = entry + dirs * 1.0 * atr
        n = len(dec)
        tr, daily = _sim(m1.o, m1.h, m1.l, m1.c, dec.astype(np.int64), dirs, np.zeros(n, np.int64), entry, stop, tgt,
                         np.full(n, float(C.SHARES)), np.minimum(dec + 15, b - 1).astype(np.int64), np.zeros(n, np.int64),
                         np.ones(len(m1.c), np.bool_), np.zeros(0, np.int64), np.zeros(0, np.int64), False, 0, mk.sess1m,
                         C.TICK, C.COMMISSION, float(C.SHARES), len(mk.sessions), b - 1)
        cost = len(tr) * (C.COMMISSION + 2 * C.TICK * C.SHARES)
        res[name] = {"trades": int(len(tr)), "dollars": float(tr[:, 6].sum()), "win_rate": float((tr[:, 6] > 0).mean()),
                     "sharpe": sharpe(daily[sess_range(mk, *C.DISCOVERY)]), "costs_paid_approx": float(cost)}
    res["random_entries"]["behaves"] = bool(res["random_entries"]["dollars"] < 0.5 * res["random_entries"]["costs_paid_approx"] and abs(res["random_entries"]["sharpe"]) < 3)
    res["cheat"]["behaves"] = bool(res["cheat"]["win_rate"] > 0.75 and res["cheat"]["sharpe"] > 5)
    return res


def main() -> None:
    rng = np.random.default_rng(C.MASTER_SEED)
    mks = {s: discovery_market(s) for s in C.SYMBOLS}
    results = {}
    for sym, mk in mks.items():
        results[sym] = {"pooled": run_tests(mk, C.DISCOVERY, rng)}
        if sym == C.MAIN:
            for era, w in C.ERAS.items():
                results[sym][era] = run_tests(mk, w, rng)
    table = []
    for name, (kind, sign, title, what) in TESTS.items():
        m = results[C.MAIN]["pooled"][name]
        row = {"test": name, "title": title, "measure": what, "claim_sign": sign,
               "main": m, "check": results[C.CHECK]["pooled"][name],
               "era1": results[C.MAIN]["era1"][name], "era2": results[C.MAIN]["era2"][name]}
        row["verdict"] = verdict(row["main"], row["check"], row["era1"], row["era2"], sign)
        table.append(row)
    checks = engine_checks(mks[C.MAIN], rng)
    out = C.RESULTS / "behavior"
    out.mkdir(parents=True, exist_ok=True)
    (out / "behavior_tests.json").write_text(json.dumps({"tests": table, "engine_checks": checks}, indent=1))
    lines = ["| test | effect (QQQ) | 95% interval | SPY | 2022 | 2023-24 | verdict |", "|---|---|---|---|---|---|---|"]
    f = lambda r: "n/a" if r is None else f"{r['effect']:+.3f}"
    for r in table:
        m = r["main"]
        ci = "n/a" if m is None else f"[{m['lo']:+.3f}, {m['hi']:+.3f}]"
        lines.append(f"| {r['title']} | {f(m)} | {ci} | {f(r['check'])} | {f(r['era1'])} | {f(r['era2'])} | {r['verdict']} |")
    (out / "behavior_tests.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(json.dumps(checks, indent=1))


if __name__ == "__main__":
    main()
