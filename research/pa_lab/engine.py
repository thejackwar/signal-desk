"""Prompt 2: the trade engine. A version (setup + dials) -> trades -> daily P&L with real costs.

Fills are always checked on 1-minute bars whatever the strategy's charts. One position at a time.
- costs: $1 commission per round trip for 100 shares, plus 1 tick ($0.01) of slippage against the trade on
  market entries, hard stops, close-based stops and structure exits; none on limit entries, targets and time exits
- a limit fills only when a 1-minute bar trades 1 tick through it, at the limit price (never better on a gap);
  targets are limits filled the same way
- a hard stop exits 1 tick worse than the stop, or 1 tick worse than the open when price gaps through it;
  a close-based stop exits at the first trigger-chart close beyond it
- if the stop and the target are both inside the same 1-minute bar, the stop counts: an ambiguous candle is a loss
"""
from __future__ import annotations

from dataclasses import dataclass

import numba as nb
import numpy as np

from . import config as C
from .charts import Market, get_structure
from .dials import trigger_chart
from .setups import generate

CONF_WINDOW = 12      # trigger bars allowed for the confirmation, and for a limit to fill
REASONS = ["stop", "target", "close_stop", "choch", "time", "flat", "end"]


@dataclass
class Result:
    trades: np.ndarray            # rows: entry_i, exit_i, dir, entry, exit, reason, dollars, R, size, candidate
    daily: np.ndarray             # P&L per session number (all sessions of the market)
    n_signals: int


# ------------------------------------------------------------------ per-market caches
def trig_end_mask(mk: Market, chart: str) -> np.ndarray:
    key = ("endmask", chart)
    ch = mk.charts[chart]
    if key not in ch.cache:
        m = np.zeros(len(mk.minute), np.bool_)
        m[ch.end] = True
        ch.cache[key] = m
    return ch.cache[key]


def choch_1m(mk: Market, chart: str, k: int):
    """1-minute indices (sorted) of bullish and bearish changes of character on a chart."""
    ch = mk.charts[chart]
    key = ("choch1m", k)
    if key not in ch.cache:
        _, _, _, eb, ed, ek, _ = get_structure(ch, k)
        sel = ek == 2
        ch.cache[key] = (np.sort(ch.end[eb[sel & (ed > 0)]]), np.sort(ch.end[eb[sel & (ed < 0)]]))
    return ch.cache[key]


def sess_end(mk: Market) -> np.ndarray:
    if not hasattr(mk, "_sess_end"):
        s = mk.sess1m
        last = np.r_[np.flatnonzero(s[1:] != s[:-1]), len(s) - 1]
        mk._sess_end = last
    return mk._sess_end


def daily_context(mk: Market) -> dict:
    """Per session (known before it opens): choppy previous session, 3/5 day runs, daily trend."""
    if hasattr(mk, "_ctx"):
        return mk._ctx
    d = mk.charts["1d"]
    body = np.abs(d.c - d.o) / np.maximum(d.h - d.l, 1e-9)
    choppy = np.r_[False, body[:-1] < 0.25]
    up = np.r_[0, np.sign(np.diff(d.c))]
    def run(nd):
        r = np.zeros(len(d), np.int64)
        for i in range(nd + 1, len(d)):
            last = up[i - nd:i]
            if np.all(last > 0):
                r[i] = 1
            elif np.all(last < 0):
                r[i] = -1
        return r
    mk._ctx = {"choppy": choppy, "run3": run(3), "run5": run(5)}
    return mk._ctx


# ------------------------------------------------------------------ confirmation
@nb.njit(cache=True)
def _confirm(sig_end, sig_dir, sig_lvl, t_bar_of, t_o, t_h, t_l, t_c, t_end, t_atr, mode, bos_bull, bos_bear, window):
    """For each signal: the trigger bar that confirms it (or -1). mode 0 none, 1 close back, 2 rejection,
    3 engulfing, 4 either, 5 lower-chart break of structure."""
    n = len(sig_end)
    out = np.full(n, -1, np.int64)
    for s in range(n):
        tb0 = t_bar_of[sig_end[s]]
        d = sig_dir[s]
        lv = sig_lvl[s]
        if mode == 0:
            out[s] = tb0
            continue
        for tb in range(tb0, min(len(t_c), tb0 + window)):
            o = t_o[tb]; h = t_h[tb]; l = t_l[tb]; c = t_c[tb]
            rng = max(h - l, 1e-9)
            a = t_atr[tb] if t_atr[tb] == t_atr[tb] else rng
            ok = False
            if mode == 1:
                ok = (c > lv) if d > 0 else (c < lv)
            rej = False
            eng = False
            if mode == 2 or mode == 4:
                if d > 0:
                    rej = (min(o, c) - l) >= 0.5 * rng and c > lv
                else:
                    rej = (h - max(o, c)) >= 0.5 * rng and c < lv
            if (mode == 3 or mode == 4) and tb >= 1:
                po = t_o[tb - 1]; pc = t_c[tb - 1]
                if d > 0:
                    eng = pc < po and c > o and c >= po and o <= pc and l <= lv + 0.25 * a
                else:
                    eng = pc > po and c < o and c <= po and o >= pc and h >= lv - 0.25 * a
            if mode == 2:
                ok = rej
            elif mode == 3:
                ok = eng
            elif mode == 4:
                ok = rej or eng
            elif mode == 5:
                ev = bos_bull if d > 0 else bos_bear
                k = np.searchsorted(ev, t_end[tb])
                ok = k < len(ev) and ev[k] == t_end[tb]
            if ok:
                out[s] = tb
                break
    return out


def lower_bos_1m(mk: Market, chart: str, k: int):
    ch = mk.charts[chart]
    key = ("bos1m", k)
    if key not in ch.cache:
        _, _, _, eb, ed, ek, _ = get_structure(ch, k)
        ch.cache[key] = (np.sort(ch.end[eb[ed > 0]]), np.sort(ch.end[eb[ed < 0]]))
    return ch.cache[key]


# ------------------------------------------------------------------ simulation
@nb.njit(cache=True)
def _sim(o, h, l, c, dec, dirs, etype, lvl, stop, tgt, size, texit, lim_exp, trig_end, ch_bull, ch_bear, use_choch,
         close_stop, sess_of, tick, commission, shares, n_sess, end_i):
    nt = len(dec)
    tr = np.zeros((nt, 10))
    m = 0
    daily = np.zeros(n_sess)
    free_from = 0
    for s in range(nt):
        t = dec[s]
        if t < free_from or t >= end_i:
            continue
        d = dirs[s]
        # ---- entry
        if etype[s] == 0:
            ei = t
            ep = c[t] + d * tick
            first = t + 1
        else:
            ei = -1
            lim = lvl[s]
            for j in range(t + 1, min(lim_exp[s] + 1, end_i + 1)):
                if (d > 0 and l[j] <= lim - tick) or (d < 0 and h[j] >= lim + tick):
                    ei = j
                    break
            if ei < 0:
                continue
            ep = lim
            first = ei
        st = stop[s]
        if d * (ep - st) <= tick:
            continue                                  # stop on the wrong side of entry: no trade
        risk = d * (ep - st)
        tg = tgt[s]
        if tg == tg and d * (tg - ep) <= 0:
            continue
        te = texit[s]
        if te >= 0 and te < first:
            continue
        # next change of character against the trade
        nxt_ch = -1
        if use_choch:
            ev = ch_bear if d > 0 else ch_bull
            k = np.searchsorted(ev, ei + 1)
            if k < len(ev):
                nxt_ch = ev[k]
        xi = -1
        xp = 0.0
        reason = 6
        for j in range(first, end_i + 1):
            # stop (hard): gap through at the open, then intrabar
            if close_stop == 0:
                if j > ei and ((d > 0 and o[j] <= st) or (d < 0 and o[j] >= st)):
                    xi = j; xp = o[j] - d * tick; reason = 0; break
                hit_stop = (d > 0 and l[j] <= st) or (d < 0 and h[j] >= st)
            else:
                hit_stop = False
            hit_tgt = j > ei and tg == tg and ((d > 0 and h[j] >= tg + tick) or (d < 0 and l[j] <= tg - tick))
            if hit_stop:
                xi = j; xp = st - d * tick; reason = 0; break          # includes ambiguous bars: stop counts
            if hit_tgt:
                xi = j; xp = tg; reason = 1; break
            if close_stop == 1 and trig_end[j] and ((d > 0 and c[j] < st) or (d < 0 and c[j] > st)):
                xi = j; xp = c[j] - d * tick; reason = 2; break
            if nxt_ch >= 0 and j == nxt_ch:
                xi = j; xp = c[j] - d * tick; reason = 3; break
            if te >= 0 and j >= te:
                xi = j; xp = c[j]; reason = 4; break
        if xi < 0:
            xi = end_i; xp = c[end_i]; reason = 6
        sz = size[s]
        dollars = d * (xp - ep) * sz - commission * sz / shares
        tr[m, 0] = ei; tr[m, 1] = xi; tr[m, 2] = d; tr[m, 3] = ep; tr[m, 4] = xp; tr[m, 5] = reason
        tr[m, 6] = dollars; tr[m, 7] = dollars / (risk * sz); tr[m, 8] = sz; tr[m, 9] = s
        m += 1
        daily[sess_of[xi]] += dollars
        free_from = xi + 1
    return tr[:m], daily


# ------------------------------------------------------------------ a version -> candidates -> result
CONF_MODE = {"none": 0, "close_back": 1, "rejection": 2, "engulfing": 3, "either": 4, "lower_bos": 5}
SESSION_WIN = {"all": (C.OPEN_MIN, C.CLOSE_MIN), "open": (9 * 60 + 30, 11 * 60), "afternoon": (13 * 60 + 30, 16 * 60)}
CHART_UP = {"1m": "5m", "5m": "15m", "15m": "1h", "1h": "4h", "4h": "1d", "1d": "1d"}


def candidates(mk: Market, setup: str, v: dict, window: tuple[int, int]):
    """Signals -> confirmed, filtered trade candidates (decision index, entry rules) inside window
    (1-minute index range [a, b))."""
    sc = mk.charts[v["chart"]]
    k = int(v["swing"])
    sig = generate(sc, setup, k, v)
    n_sig_all = len(sig)
    if not n_sig_all:
        return None, 0
    sig = sig[(sc.end[sig[:, 0].astype(int)] >= window[0]) & (sc.end[sig[:, 0].astype(int)] < window[1])]
    if v["direction"] != "both":
        sig = sig[sig[:, 1] == (1 if v["direction"] == "long" else -1)]
    n_sig = len(sig)
    if not n_sig:
        return None, 0
    tname = trigger_chart(v["chart"], v["trigger"])
    tc = mk.charts[tname]
    bull, bear = lower_bos_1m(mk, tname, k) if v["confirm"] == "lower_bos" else (np.zeros(0, np.int64), np.zeros(0, np.int64))
    sb = sig[:, 0].astype(np.int64)
    tb = _confirm(sc.end[sb], sig[:, 1].astype(np.int64), sig[:, 2], tc.bar_of_1m, tc.o, tc.h, tc.l, tc.c, tc.end, tc.atr,
                  CONF_MODE[v["confirm"]], bull, bear, CONF_WINDOW)
    ok = tb >= 0
    sig, sb, tb = sig[ok], sb[ok], tb[ok]
    if not len(sig):
        return None, n_sig
    dec = tc.end[tb]
    d = sig[:, 1]
    atr = sc.atr[sb]
    keep = ~np.isnan(atr) & (dec < window[1])
    # ---- filters (all known at the decision)
    s_of = mk.sess1m[dec]
    if v["f_trend"] != "off":
        name = {"setup": v["chart"], "up1": CHART_UP[v["chart"]], "daily": "1d"}[v["f_trend"]]
        fc = mk.charts[name]
        tr = get_structure(fc, k)[0]
        # trend of the last closed bar of that chart at the decision
        fb = fc.bar_of_1m[dec]
        closed = np.where(fc.end[fb] <= dec, fb, fb - 1)
        tval = np.where(closed >= 0, tr[np.maximum(closed, 0)], 0)
        keep &= tval == d
    ctx = daily_context(mk)
    if v["f_choppy"] == "on":
        keep &= ~ctx["choppy"][s_of]
    if v["f_run"] != "off":
        keep &= ctx["run" + v["f_run"]][s_of] != d
    if v["f_fresh"] == "on":
        keep &= sig[:, 6] > 0
    news = mk.news[s_of] > 0
    if v["f_news"] == "skip":
        keep &= ~news
    if v["f_session"] != "all":
        a, b = SESSION_WIN[v["f_session"]]
        mn = mk.minute[dec]
        keep &= (mn >= a) & (mn < b)
    if v["f_pullback"] != "off":
        keep &= sig[:, 7] >= float(v["f_pullback"])
    if v["f_gap"] == "on":
        keep &= sig[:, 8] > 0
    sig, sb, tb, dec, d, atr, s_of, news = sig[keep], sb[keep], tb[keep], dec[keep], d[keep], atr[keep], s_of[keep], news[keep]
    if not len(sig):
        return None, n_sig
    # ---- stop, target, size, exits
    ref = {"level": sig[:, 2], "wick": sig[:, 3], "swing": sig[:, 4], "gap_mid": sig[:, 5]}[v["stop_ref"]]
    stop = ref - d * float(v["stop_buf"]) * atr
    entry_px = np.where(v["entry"] == "close", mk.charts["1m"].c[dec] + d * C.TICK, sig[:, 2])
    risk = d * (entry_px - stop)
    if v["target"] == "swing":
        tgt = sig[:, 9].copy()
        tgt = np.where(np.isnan(tgt), entry_px, tgt)          # no swing to aim at: the trade is skipped (D-09)
    else:
        tgt = entry_px + d * float(v["target"].rstrip("R")) * risk
    size = np.where((v["f_news"] == "half") & news, C.SHARES / 2, C.SHARES).astype(np.float64)
    se = sess_end(mk)
    if v["time_exit"] == "flat_1600":
        texit = se[s_of]
    elif v["time_exit"] in ("24", "96"):
        nb_ = int(v["time_exit"])
        texit = sc.end[np.minimum(sb + nb_, len(sc) - 1)]
    else:
        texit = np.full(len(dec), -1, np.int64)
    lim_exp = tc.end[np.minimum(tb + CONF_WINDOW, len(tc) - 1)]
    lim_exp = np.minimum(lim_exp, se[s_of]) if v["time_exit"] == "flat_1600" else lim_exp
    cand = dict(dec=dec.astype(np.int64), dirs=d.astype(np.int64), etype=np.full(len(dec), 0 if v["entry"] == "close" else 1, np.int64),
                lvl=sig[:, 2].astype(np.float64), stop=stop.astype(np.float64), tgt=tgt.astype(np.float64), size=size,
                texit=texit.astype(np.int64), lim_exp=lim_exp.astype(np.int64), atr=atr, tname=tname, sbar=sb)
    return cand, n_sig


def run_version(mk: Market, setup: str, v: dict, window: tuple[int, int]) -> Result:
    cand, n_sig = candidates(mk, setup, v, window)
    n_sess = len(mk.sessions)
    if cand is None:
        return Result(np.zeros((0, 10)), np.zeros(n_sess), n_sig)
    m1 = mk.charts["1m"]
    use_ch = v["manage"] == "choch_exit"
    cb, cr = choch_1m(mk, cand["tname"], int(v["swing"])) if use_ch else (np.zeros(0, np.int64), np.zeros(0, np.int64))
    tr, daily = _sim(m1.o, m1.h, m1.l, m1.c, cand["dec"], cand["dirs"], cand["etype"], cand["lvl"], cand["stop"],
                     cand["tgt"], cand["size"], cand["texit"], cand["lim_exp"], trig_end_mask(mk, cand["tname"]),
                     cb, cr, use_ch, 1 if v["stop_type"] == "close" else 0, mk.sess1m, C.TICK, C.COMMISSION,
                     float(C.SHARES), n_sess, window[1] - 1)
    return Result(tr, daily, n_sig)


# ------------------------------------------------------------------ metrics
def sharpe(daily: np.ndarray) -> float:
    sd = daily.std(ddof=1) if len(daily) > 1 else 0.0
    return float(daily.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0


def window_idx(mk: Market, a: str, b: str) -> tuple[int, int]:
    s = mk.m1["session"].to_numpy()
    lo = int(np.searchsorted(s, a, side="left"))
    hi = int(np.searchsorted(s, b, side="right"))
    return lo, hi


def sess_range(mk: Market, a: str, b: str) -> np.ndarray:
    return (mk.sessions >= a) & (mk.sessions <= b)


# ------------------------------------------------------------------ matched random twins
@nb.njit(parallel=True, cache=True)
def _twins(o, h, l, c, slot_start, slot_len, disc_sess, slots, dirs, stop_atr, tgt_atr, hold, atr1m, sizes, tick,
           commission, shares, n_sets, seed, end_i):
    """Total P&L of n_sets sets of twins. Twin of trade t: same direction, a random minute in the same
    30-minute slot of the day on a random discovery session, the same stop and target distance in ATR (setup
    chart), the same holding time and the same costs (market entry with slippage, hard stop, limit target)."""
    nt = len(dirs)
    ns = len(disc_sess)
    totals = np.zeros(n_sets)
    for s in nb.prange(n_sets):
        np.random.seed(seed + s)
        tot = 0.0
        for t in range(nt):
            e = -1
            for _ in range(20):
                q = disc_sess[np.random.randint(0, ns)]
                ln = slot_len[q, slots[t]]
                if ln > 0:
                    e = slot_start[q, slots[t]] + np.random.randint(0, ln)
                    break
            if e < 0:
                continue
            d = dirs[t]
            a = atr1m[e]
            if not (a > 0):
                continue
            ep = c[e] + d * tick
            st = ep - d * stop_atr[t] * a
            tg = ep + d * tgt_atr[t] * a
            last = min(e + hold[t], end_i)
            xp = c[last]
            for j in range(e + 1, last + 1):
                if (d > 0 and o[j] <= st) or (d < 0 and o[j] >= st):
                    xp = o[j] - d * tick
                    break
                if (d > 0 and l[j] <= st) or (d < 0 and h[j] >= st):
                    xp = st - d * tick
                    break
                if (d > 0 and h[j] >= tg + tick) or (d < 0 and l[j] <= tg - tick):
                    xp = tg
                    break
            tot += d * (xp - ep) * sizes[t] - commission * sizes[t] / shares
        totals[s] = tot
    return totals


SLOTS = 13   # 30-minute slots, 09:30-16:00


def slot_tables(mk: Market):
    if hasattr(mk, "_slots"):
        return mk._slots
    n_sess = len(mk.sessions)
    start = np.zeros((n_sess, SLOTS), np.int64)
    length = np.zeros((n_sess, SLOTS), np.int64)
    slot = ((mk.minute - C.OPEN_MIN) // 30).clip(0, SLOTS - 1)
    key = mk.sess1m * SLOTS + slot
    chg = np.r_[True, key[1:] != key[:-1]]
    st = np.flatnonzero(chg)
    en = np.r_[st[1:], len(key)]
    for a, b in zip(st, en):
        start[mk.sess1m[a], slot[a]] = a
        length[mk.sess1m[a], slot[a]] = b - a
    mk._slots = (start, length, slot)
    return mk._slots


def twin_test(mk: Market, setup: str, v: dict, res: "Result", window: tuple[int, int], sess_ids: np.ndarray,
              n_sets: int, seed: int) -> float:
    """Share of twin sets that made at least as much as the version did (the version passes below 5%)."""
    tr = res.trades
    if not len(tr):
        return 1.0
    cand, _ = candidates(mk, setup, v, window)
    sc = mk.charts[v["chart"]]
    atr1m = sc.atr[sc.bar_of_1m]
    # previous closed bar's ATR is the same thing: atr[i] uses bars before i
    ei = tr[:, 0].astype(np.int64)
    m1 = mk.charts["1m"]
    # risk and target distance, in setup-chart ATR at entry
    a_e = atr1m[ei]
    d = tr[:, 2].astype(np.int64)
    ci = tr[:, 9].astype(np.int64)
    with np.errstate(divide="ignore", invalid="ignore"):
        stop_atr = np.where(a_e > 0, np.abs(tr[:, 3] - cand["stop"][ci]) / a_e, 1.0)
        tgt_atr = np.where(a_e > 0, np.abs(cand["tgt"][ci] - tr[:, 3]) / a_e, 1.0)
    hold = np.maximum(1, (tr[:, 1] - tr[:, 0]).astype(np.int64))
    start, length, slot = slot_tables(mk)
    totals = _twins(m1.o, m1.h, m1.l, m1.c, start, length, sess_ids.astype(np.int64), slot[ei].astype(np.int64), d,
                    stop_atr, tgt_atr, hold, atr1m, tr[:, 8].copy(), C.TICK, C.COMMISSION, float(C.SHARES), n_sets, seed,
                    window[1] - 1)
    return float((totals >= tr[:, 6].sum()).mean())
