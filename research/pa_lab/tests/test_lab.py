"""Price Action Lab tests: the seal, no look-ahead, and the engine's fill rules.

Run: PA_LAB_DEMO=1 PA_LAB_DATA=<dir> python -m pytest research/pa_lab/tests -q
(needs the demo data: PA_LAB_DEMO=1 python -m research.pa_lab.data)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.pa_lab import config as C
from research.pa_lab import guard
from research.pa_lab.charts import build_market, fvgs, get_structure, zones
from research.pa_lab.data import load_clean
from research.pa_lab.dials import SETUPS, write_rulebook
from research.pa_lab.engine import _sim
from research.pa_lab.setups import generate

DIALS = dict(lvl_tol=0.25, lvl_touches=2, lvl_shape="line", bo_margin=0.0, flip_window=10, pb_level=0.5, tl_tol=0.1,
             retest="yes", sweep_depth=0.1, reclaim_bars=2, zone_first="no", zone_age=200, gap_min=0.0, gap_first="no")


# ------------------------------------------------------------------ seal
def test_sealed_file_read_refused(monkeypatch):
    monkeypatch.delenv("PA_LAB_UNLOCK", raising=False)
    files = list(C.SEALED.glob("*.parquet"))
    assert files, "ingest must have written the sealed window"
    with pytest.raises(guard.SealError):
        guard.read_frame(files[0])


def test_final_sessions_in_frame_refused(monkeypatch):
    monkeypatch.delenv("PA_LAB_UNLOCK", raising=False)
    with pytest.raises(guard.SealError):
        guard.check_frame(pd.DataFrame({"session": ["2024-07-31", C.FINAL_FROM]}))


def test_unlock_needs_step_and_env(monkeypatch):
    monkeypatch.delenv("PA_LAB_UNLOCK", raising=False)
    with pytest.raises(guard.SealError):
        with guard.unlock("final_test"):
            pass
    monkeypatch.setenv("PA_LAB_UNLOCK", "final_test")
    with pytest.raises(guard.SealError):
        with guard.unlock("something_else"):
            pass
    before = C.ACCESS_LOG.read_text().count('"unlock"') if C.ACCESS_LOG.exists() else 0
    with guard.unlock("final_test", note="guard test"):
        assert guard.unlocked()
    assert not guard.unlocked()
    log = C.ACCESS_LOG.read_text()
    assert log.count('"unlock"') == before + 1 and '"relock"' in log


def test_clean_has_no_final_sessions():
    for sym in C.SYMBOLS:
        df = load_clean(sym)
        assert df["session"].max() < C.FINAL_FROM


def test_rulebook_lists_every_setup():
    spec = write_rulebook()
    text = (C.RESULTS / "rulebook.md").read_text()
    assert spec["valid_total"] > 10 ** 9
    for s in SETUPS:
        assert spec["names"][s] in text


# ------------------------------------------------------------------ no look-ahead
def _market(df):
    return build_market("QQQ", df)


@pytest.fixture(scope="module")
def sample():
    df = load_clean("QQQ")
    return df[(df["session"] >= "2022-03-01") & (df["session"] <= "2022-07-31")].reset_index(drop=True)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_detectors_and_signals_match_on_cut_data(sample, seed):
    rng = np.random.default_rng(C.MASTER_SEED + seed)
    cut = int(rng.integers(len(sample) // 3, len(sample) - 1000))
    full, part = _market(sample), _market(sample.iloc[:cut])
    for name in ["5m", "15m", "1h", "4h", "1d"]:
        F, P = full.charts[name], part.charts[name]
        closed = np.flatnonzero(F.end <= cut - 1)          # bars closed before the cut in the full run
        nb_ = len(closed)
        assert np.allclose(F.c[:nb_], P.c[:nb_]) and np.allclose(np.nan_to_num(F.atr[:nb_]), np.nan_to_num(P.atr[:nb_]))
        for k in (2, 3, 5):
            sf, sp = get_structure(F, k), get_structure(P, k)
            last_closed = nb_ - 1
            # trend and breaks as of every closed bar (the partial last bar in the cut run is not compared)
            assert np.array_equal(sf[0][:last_closed], sp[0][:last_closed])
            ef = sf[3][sf[3] < last_closed]; ep = sp[3][sp[3] < last_closed]
            assert np.array_equal(ef, ep)
        gf, gp = fvgs(F), fvgs(P)
        assert np.array_equal(gf[gf[:, 0] < nb_ - 1], gp[gp[:, 0] < nb_ - 1])
        zf, zp = zones(F), zones(P)
        assert np.array_equal(zf[zf[:, 0] < nb_ - 1], zp[zp[:, 0] < nb_ - 1])
        for s in SETUPS:
            for k in (2, 5):
                a = generate(F, s, k, DIALS)
                b = generate(P, s, k, DIALS)
                a = a[a[:, 0] < nb_ - 1]
                b = b[b[:, 0] < nb_ - 1]
                assert a.shape == b.shape and np.allclose(a, b, equal_nan=True), (name, s, k)


# ------------------------------------------------------------------ engine
def _bars(rows):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def _one(o, h, l, c, *, dec=0, d=1, etype=0, lvl=100.0, stop=99.0, tgt=102.0, texit=-1, close_stop=0, size=100.0):
    n = len(c)
    z = lambda x, t: np.array([x], dtype=t)
    return _sim(o, h, l, c, z(dec, np.int64), z(d, np.int64), z(etype, np.int64), z(lvl, np.float64), z(stop, np.float64),
                z(tgt, np.float64), z(size, np.float64), z(texit, np.int64), z(n - 1, np.int64), np.ones(n, np.bool_),
                np.zeros(0, np.int64), np.zeros(0, np.int64), False, close_stop, np.zeros(n, np.int64), 0.01, 1.0, 100.0, 1, n - 1)


def test_ambiguous_bar_is_a_loss():
    o, h, l, c = _bars([[100, 100, 100, 100], [100, 102.5, 98.5, 101]])
    tr, _ = _one(o, h, l, c)
    assert tr[0, 5] == 0 and tr[0, 4] == pytest.approx(98.99)


def test_market_entry_and_target_costs():
    o, h, l, c = _bars([[100, 100, 100, 100], [100, 102.02, 99.5, 102]])
    tr, daily = _one(o, h, l, c)
    assert tr[0, 3] == pytest.approx(100.01)               # 1 tick slippage on the market entry
    assert tr[0, 4] == pytest.approx(102.0) and tr[0, 5] == 1   # target is a limit: no slippage
    assert tr[0, 6] == pytest.approx((102 - 100.01) * 100 - 1.0)
    assert daily[0] == pytest.approx(tr[0, 6])


def test_target_needs_one_tick_through():
    o, h, l, c = _bars([[100, 100, 100, 100], [100, 102.0, 99.5, 101.9], [101.9, 101.9, 101.0, 101.0]])
    tr, _ = _one(o, h, l, c, texit=2)
    assert tr[0, 5] == 4                                    # touched 102 but never 1 tick through: time exit


def test_gap_through_stop_exits_worse_than_open():
    o, h, l, c = _bars([[100, 100, 100, 100], [98.0, 98.5, 97.5, 98.2]])
    tr, _ = _one(o, h, l, c)
    assert tr[0, 4] == pytest.approx(97.99)


def test_limit_fills_only_one_tick_through_never_better():
    o, h, l, c = _bars([[101, 101, 101, 101], [101, 101, 100.0, 100.5], [99.0, 99.5, 98.0, 99.2]])
    tr, _ = _one(o, h, l, c, etype=1, lvl=100.0, stop=97.0, tgt=110.0, texit=2)
    # bar 1 only touches 100.00 (no fill); bar 2 gaps through: fills at the limit, not at the better open 99.0
    assert tr[0, 0] == 2 and tr[0, 3] == pytest.approx(100.0)


def test_close_based_stop_waits_for_the_close():
    o, h, l, c = _bars([[100, 100, 100, 100], [100, 100.2, 98.0, 99.5], [99.5, 99.6, 98.5, 98.8]])
    tr, _ = _one(o, h, l, c, close_stop=1)
    assert tr[0, 1] == 2 and tr[0, 5] == 2 and tr[0, 4] == pytest.approx(98.79)
