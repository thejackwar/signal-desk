"""Price Action Lab: every fixed number in one place.

Adapted from a futures research spec (NQ/ES, 18:00-17:00 sessions) to what the site can get for free:
QQQ and SPY 1-minute bars from Alpaca, regular hours only (09:30-16:00 New York). QQQ plays NQ's role
(the market rules are found on), SPY plays ES's role (the check market). Every adaptation is logged in
decisions.md. Nothing here may change after a result has been seen, except through a numbered amendment
in decisions.md written before looking.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("PA_LAB_DATA", ROOT / "data"))   # not committed (cached on Actions)
RAW = DATA / "raw"
CLEAN = DATA / "clean"           # warm-up + discovery only
SEALED = DATA / "sealed"         # final window, written once by the ingest
_DEMO = os.environ.get("PA_LAB_DEMO") == "1"
RESULTS = (DATA / "demo_results") if _DEMO else (ROOT / "results")   # committed (real runs only)
SITE = (DATA / "demo_site") if _DEMO else (ROOT.parents[1] / "docs" / "data" / "palab")
ACCESS_LOG = RESULTS / "access_log.jsonl"

MASTER_SEED = 20261106

MAIN, CHECK = "QQQ", "SPY"       # NQ -> QQQ, ES -> SPY
SYMBOLS = [MAIN, CHECK]

# windows (session dates, New York)
FETCH_FROM = "2021-10-01"
WARMUP = ("2021-10-01", "2021-12-31")
DISCOVERY = ("2022-01-03", "2024-07-31")
ERAS = {"era1": ("2022-01-03", "2022-12-31"), "era2": ("2023-01-01", "2024-07-31")}
FINAL_FROM = "2024-08-01"
FINAL_WARMUP_FROM = "2024-05-01"   # discovery bars used only to warm the detectors in the final run

# session (regular hours)
OPEN_MIN, CLOSE_MIN = 9 * 60 + 30, 16 * 60
LAST_FULL_BAR_MIN = 15 * 60 + 59        # a complete session has a bar opening at 15:59 or later
FLAT_TIME_MIN = 15 * 60 + 59            # "flat at 16:00": exit at the close of the 15:59 bar

# charts: minutes per bar; 4h bars are 09:30-13:30 and 13:30-16:00; daily is the whole session
CHARTS = ["1m", "5m", "15m", "1h", "4h", "1d"]
CHART_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 390}
ATR_N = 14

# costs per round trip, for a fixed 100 shares (stands in for "one contract")
SHARES = 100
TICK = 0.01
COMMISSION = 1.00               # $ per round trip (~$0.005/share each way)
SLIP_TICKS = 1                  # against the trade on market entries, hard stops, close-based and structure exits

# behaviour tests and funnel
BOOT = 2000
TWIN_SETS = 10000
MIN_SIGNALS = 40
FUNNEL = {"min_trades": 30, "min_sharpe": 0.5, "twin_p": 0.05, "check_sharpe": 0.3}
HELD_SHARPE = 0.5

# version budget (spec: 4,000 per setup in the spread set)
SPREAD_PER_SETUP = 4000
SPREAD_CANDIDATES = 400
PEOPLE_PER_SETUP = 4800


def demo() -> bool:
    return os.environ.get("PA_LAB_DEMO") == "1"
