"""The ten setups. Each turns a chart and its dials into raw signals on the setup chart:

    bar     setup-chart bar whose close creates the signal (usable from end[bar])
    dir     +1 long, -1 short
    level   the price the setup is about (level, swing, line, zone edge, gap edge)
    wick    the touch bar's extreme (low for longs)
    swing   last confirmed opposite swing (swing low for longs) at the signal
    mid     the gap's middle candle low/high (fair value gaps), else the level
    fresh   1 when the broken level was never broken before (break setups)
    pb      pullback of the last swing leg at the signal (0 = at the extreme, 1 = full retrace)
    gap     1 when an unfilled fair value gap on the setup chart overlaps the level
    target  next swing beyond entry in the trade's direction (for the "next swing" target)

Only information known at the close of `bar` is used (tested in tests/test_lookahead.py).
"""
from __future__ import annotations

import numba as nb
import numpy as np

from .charts import Chart, fvgs, get_structure, get_swings, levels_at, zones

FIELDS = ["bar", "dir", "level", "wick", "swing", "mid", "fresh", "pb", "gap", "target"]
LOOK = 40   # swings remembered for levels


def _empty() -> np.ndarray:
    return np.zeros((0, len(FIELDS)))


@nb.njit(cache=True)
def _level_kernel(kind, o, h, l, c, atr, sw_cb, sw_pr, tol, touches, box, margin, window, last_hi, last_lo):
    """kind 0 bounce, 1 breakout, 2 flip. One signal per bar at most, on the nearest qualifying level."""
    n = len(c)
    out = np.empty((n, 10))
    m = 0
    broken = np.empty(4096)          # levels already broken (mid prices), for 'fresh'
    nb_ = 0
    pend_lvl = np.empty(64); pend_dir = np.empty(64, np.int64); pend_bar = np.empty(64, np.int64); npend = 0
    last_j = -1
    lo = np.empty(0); hi = np.empty(0); mid = np.empty(0); cnt = np.empty(0, np.int64)
    for i in range(1, n):
        a = atr[i]
        if np.isnan(a) or a <= 0:
            continue
        # levels are rebuilt when a new swing is confirmed (with the ATR of that bar), decisions.md D-06
        j = np.searchsorted(sw_cb, i, side="left")
        if j != last_j:
            lo, hi, mid, cnt = levels_at(i, a, sw_cb, sw_pr, LOOK, tol)
            last_j = j
        best = -1
        bestd = 1e18
        sig = 0
        p = c[i - 1]
        for q in range(len(mid)):
            if cnt[q] < touches:
                continue
            top = hi[q] if box else mid[q]
            bot = lo[q] if box else mid[q]
            if kind == 0:
                if p > top and l[i] <= top:          # support touched from above
                    d = p - top
                    if d < bestd:
                        bestd = d; best = q; sig = 1
                elif p < bot and h[i] >= bot:        # resistance touched from below
                    d = bot - p
                    if d < bestd:
                        bestd = d; best = q; sig = -1
            else:
                if p <= top and c[i] > top + margin * a:
                    d = abs(top - p)
                    if d < bestd:
                        bestd = d; best = q; sig = 1
                elif p >= bot and c[i] < bot - margin * a:
                    d = abs(p - bot)
                    if d < bestd:
                        bestd = d; best = q; sig = -1
        if kind == 2:
            # retests of earlier breaks: price comes back to the broken level from the new side
            k2 = 0
            for z in range(npend):
                lv = pend_lvl[z]; dz = pend_dir[z]; bz = pend_bar[z]
                done = False
                if i - bz > window:
                    done = True
                elif dz == 1 and l[i] <= lv and c[i - 1] > lv:
                    if m < n:
                        out[m, 0] = i; out[m, 1] = 1; out[m, 2] = lv; out[m, 3] = l[i]; out[m, 4] = last_lo[i]
                        out[m, 5] = lv; out[m, 6] = 1.0; out[m, 7] = 0.0; out[m, 8] = 0.0; out[m, 9] = last_hi[i]; m += 1
                    done = True
                elif dz == -1 and h[i] >= lv and c[i - 1] < lv:
                    if m < n:
                        out[m, 0] = i; out[m, 1] = -1; out[m, 2] = lv; out[m, 3] = h[i]; out[m, 4] = last_hi[i]
                        out[m, 5] = lv; out[m, 6] = 1.0; out[m, 7] = 0.0; out[m, 8] = 0.0; out[m, 9] = last_lo[i]; m += 1
                    done = True
                if not done:
                    pend_lvl[k2] = lv; pend_dir[k2] = dz; pend_bar[k2] = bz; k2 += 1
            npend = k2
        if best < 0:
            continue
        lv = (hi[best] if sig == 1 else lo[best]) if box else mid[best]
        if kind == 0:
            lv = (hi[best] if sig == 1 else lo[best]) if box else mid[best]
        fresh = 1.0
        for z in range(nb_):
            if abs(broken[z] - mid[best]) <= tol * a:
                fresh = 0.0
                break
        if kind >= 1 and nb_ < 4096:
            broken[nb_] = mid[best]; nb_ += 1
        if kind == 2:
            if npend < 64:
                pend_lvl[npend] = lv; pend_dir[npend] = sig; pend_bar[npend] = i; npend += 1
            continue
        out[m, 0] = i; out[m, 1] = sig; out[m, 2] = lv
        out[m, 3] = l[i] if sig == 1 else h[i]
        out[m, 4] = last_lo[i] if sig == 1 else last_hi[i]
        out[m, 5] = lv; out[m, 6] = fresh; out[m, 7] = 0.0; out[m, 8] = 0.0
        out[m, 9] = last_hi[i] if sig == 1 else last_lo[i]
        m += 1
    return out[:m]


def _pullback(sig: np.ndarray, ch: Chart, last_hi, last_lo) -> None:
    """Fill pb: how far price has pulled back into the last swing leg at the signal bar."""
    if not len(sig):
        return
    b = sig[:, 0].astype(int)
    H, L, c = last_hi[b], last_lo[b], ch.c[b]
    rng = H - L
    with np.errstate(invalid="ignore", divide="ignore"):
        pbl = np.where(sig[:, 1] > 0, (H - c) / rng, (c - L) / rng)
    sig[:, 7] = np.nan_to_num(np.clip(pbl, 0, 1.5))


@nb.njit(cache=True)
def _gap_kernel(sb, lv, gb, glo, ghi, out):
    for r in range(len(sb)):
        a = np.searchsorted(gb, sb[r] - 50, side="left")
        b = np.searchsorted(gb, sb[r], side="left")
        for q in range(a, b):
            if glo[q] <= lv[r] <= ghi[q]:
                out[r] = 1.0
                break


def _gap_confluence(sig: np.ndarray, ch: Chart) -> None:
    if not len(sig):
        return
    g = fvgs(ch)
    if not len(g):
        return
    col = np.zeros(len(sig))
    _gap_kernel(sig[:, 0], sig[:, 2], g[:, 0], g[:, 2], g[:, 3], col)
    sig[:, 8] = col


def generate(ch: Chart, setup: str, k: int, d: dict) -> np.ndarray:
    """Raw signals for one setup on one chart. Cached on the chart by its dials."""
    own = tuple(sorted((x, d[x]) for x in d if x.startswith(("lvl_", "bo_", "flip_", "pb_", "tl_", "retest", "sweep_", "reclaim_", "zone_", "gap_"))))
    key = ("sig", setup, k, own)
    if key in ch.cache:
        return ch.cache[key]
    trend, last_hi, last_lo, ev_bar, ev_dir, ev_kind, ev_lvl = get_structure(ch, k)
    o, h, l, c, atr = ch.o, ch.h, ch.l, ch.c, ch.atr
    n = len(ch)
    rows = []

    if setup in ("level_bounce", "breakout", "flip_zone"):
        sw = get_swings(ch, k)
        order = np.argsort(sw[1], kind="stable")
        kind = {"level_bounce": 0, "breakout": 1, "flip_zone": 2}[setup]
        sig = _level_kernel(kind, o, h, l, c, atr, sw[1][order].astype(np.int64), sw[2][order],
                            float(d["lvl_tol"]), int(d["lvl_touches"]), d["lvl_shape"] == "box",
                            float(d.get("bo_margin", 0.0)), int(d.get("flip_window", 10)), last_hi, last_lo)
    elif setup in ("bos_continuation", "choch_reversal"):
        want = 1 if setup == "bos_continuation" else 2
        sel = ev_kind == want
        for b, dr, lv in zip(ev_bar[sel], ev_dir[sel], ev_lvl[sel]):
            if d["retest"] == "no":
                rows.append([b, dr, lv, l[b] if dr > 0 else h[b], last_lo[b] if dr > 0 else last_hi[b], lv, 1, 0, 0,
                             last_hi[b] if dr > 0 else last_lo[b]])
            else:
                for j in range(b + 1, min(n, b + 11)):
                    if (dr > 0 and l[j] <= lv) or (dr < 0 and h[j] >= lv):
                        rows.append([j, dr, lv, l[j] if dr > 0 else h[j], last_lo[j] if dr > 0 else last_hi[j], lv, 1, 0, 0,
                                     last_hi[j] if dr > 0 else last_lo[j]])
                        break
        sig = np.array(rows, float).reshape(-1, 10)
        if len(sig):
            # fresh: the broken swing level had not been broken before (within 0.1 ATR)
            for r in range(len(sig)):
                a = atr[int(sig[r, 0])]
                prev = sig[max(0, r - 200):r, 2]           # the last 200 broken swings
                sig[r, 6] = 0.0 if len(prev) and np.any(np.abs(prev - sig[r, 2]) <= 0.1 * (a if a == a else 0)) else 1.0
    elif setup == "trend_pullback":
        f = float(d["pb_level"])
        used_h = np.nan
        for i in range(1, n):
            t = trend[i - 1]
            H, L = last_hi[i - 1], last_lo[i - 1]
            if np.isnan(H) or np.isnan(L) or H <= L:
                continue
            if t == 1:
                r = H - f * (H - L)
                if l[i] <= r < l[i - 1] and l[i] > L and H != used_h:
                    rows.append([i, 1, r, l[i], L, r, 0, 0, 0, H]); used_h = H
            elif t == -1:
                r = L + f * (H - L)
                if h[i] >= r > h[i - 1] and h[i] < H and L != used_h:
                    rows.append([i, -1, r, h[i], H, r, 0, 0, 0, L]); used_h = L
        sig = np.array(rows, float).reshape(-1, 10)
    elif setup == "trend_line":
        sw = get_swings(ch, k)
        pb, cb, pr, kd = sw
        tol = float(d["tl_tol"])
        lows = [(cb[q], pb[q], pr[q]) for q in np.argsort(cb, kind="stable") if kd[q] == -1]
        highs = [(cb[q], pb[q], pr[q]) for q in np.argsort(cb, kind="stable") if kd[q] == 1]
        for side, pts in ((1, lows), (-1, highs)):
            j = 0
            last2: list = []
            used = None
            for i in range(1, n):
                while j < len(pts) and pts[j][0] < i:
                    last2 = (last2 + [pts[j]])[-2:]
                    j += 1
                if len(last2) < 2:
                    continue
                (_, b1, p1), (_, b2, p2) = last2
                if b2 == b1 or (side == 1 and p2 <= p1) or (side == -1 and p2 >= p1):
                    continue
                a = atr[i]
                if np.isnan(a):
                    continue
                line = p2 + (p2 - p1) / (b2 - b1) * (i - b2)
                prev = p2 + (p2 - p1) / (b2 - b1) * (i - 1 - b2)
                key_line = (b1, b2)
                if key_line == used:
                    continue
                if side == 1 and c[i - 1] > prev and l[i] <= line + tol * a and c[i] > line - a:
                    rows.append([i, 1, line, l[i], last_lo[i], line, 0, 0, 0, last_hi[i]]); used = key_line
                elif side == -1 and c[i - 1] < prev and h[i] >= line - tol * a and c[i] < line + a:
                    rows.append([i, -1, line, h[i], last_hi[i], line, 0, 0, 0, last_lo[i]]); used = key_line
        sig = np.array(sorted(rows), float).reshape(-1, 10)
    elif setup == "sweep_reclaim":
        dep, rb = float(d["sweep_depth"]), int(d["reclaim_bars"])
        used_lo = used_hi = np.nan
        for i in range(1, n):
            a = atr[i]
            L, H = last_lo[i - 1], last_hi[i - 1]
            if np.isnan(a):
                continue
            if not np.isnan(L) and L != used_lo and l[i] < L - dep * a:
                for j in range(i, min(n, i + rb)):
                    if c[j] > L:
                        rows.append([j, 1, L, l[i:j + 1].min(), l[i:j + 1].min(), L, 0, 0, 0, H]); break
                used_lo = L
            if not np.isnan(H) and H != used_hi and h[i] > H + dep * a:
                for j in range(i, min(n, i + rb)):
                    if c[j] < H:
                        rows.append([j, -1, H, h[i:j + 1].max(), h[i:j + 1].max(), H, 0, 0, 0, L]); break
                used_hi = H
        sig = np.array(sorted(rows), float).reshape(-1, 10)
    elif setup == "sd_zone":
        z = zones(ch)
        first, age = d["zone_first"] == "yes", int(d["zone_age"])
        for zb, zd, zlo, zhi, base in z:
            zb = int(zb)
            for i in range(zb + 1, min(n, zb + age + 1)):
                if (zd > 0 and c[i] < zlo) or (zd < 0 and c[i] > zhi):
                    break                                              # zone broken
                if zd > 0 and l[i] <= zhi and l[i - 1] > zhi:
                    rows.append([i, 1, zhi, l[i], min(zlo, last_lo[i]) if not np.isnan(last_lo[i]) else zlo, zlo, 0, 0, 0, last_hi[i]])
                    if first:
                        break
                if zd < 0 and h[i] >= zlo and h[i - 1] < zlo:
                    rows.append([i, -1, zlo, h[i], max(zhi, last_hi[i]) if not np.isnan(last_hi[i]) else zhi, zhi, 0, 0, 0, last_lo[i]])
                    if first:
                        break
        sig = np.array(sorted(rows), float).reshape(-1, 10)
    elif setup == "fvg":
        g = fvgs(ch)
        gmin, first = float(d["gap_min"]), d["gap_first"] == "yes"
        for gb, gd, glo, ghi, midc in g:
            gb = int(gb)
            a = atr[gb]
            if np.isnan(a) or ghi - glo < gmin * a:
                continue
            for i in range(gb + 1, min(n, gb + 200)):
                if (gd > 0 and c[i] < glo) or (gd < 0 and c[i] > ghi):
                    break                                              # gap filled through
                if gd > 0 and l[i] <= ghi and l[i - 1] > ghi:
                    rows.append([i, 1, ghi, l[i], last_lo[i], midc, 0, 0, 0, last_hi[i]])
                    if first:
                        break
                if gd < 0 and h[i] >= glo and h[i - 1] < glo:
                    rows.append([i, -1, glo, h[i], last_hi[i], midc, 0, 0, 0, last_lo[i]])
                    if first:
                        break
        sig = np.array(sorted(rows), float).reshape(-1, 10)
    else:
        raise ValueError(setup)

    if len(sig):
        sig = sig[np.argsort(sig[:, 0], kind="stable")]
        _pullback(sig, ch, last_hi, last_lo)
        if setup != "fvg":
            _gap_confluence(sig, ch)
    ch.cache[key] = sig
    return sig
