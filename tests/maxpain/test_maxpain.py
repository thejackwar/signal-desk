"""Max pain maths and the scorecard (engine/maxpain.py).
    python tests/maxpain/test_maxpain.py"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
import maxpain as M  # noqa: E402

# 1. Hand-checked chain. Calls at 100: 10, at 105: 5. Puts at 95: 10, at 100: 5.
#    P=95:  calls 0                          puts 5x5 = 25            -> 25
#    P=100: calls 0                          puts 0                   -> 0   <- max pain
#    P=105: calls 10x5 = 50                  puts 0                   -> 50
r = M.max_pain({95.0: (0, 10), 100.0: (10, 5), 105.0: (5, 0)})
assert r["max_pain"] == 100.0, r
assert [round(v) for _, v in r["curve"]] == [2500, 0, 5000], r["curve"]
assert r["call_oi"] == 15 and r["put_oi"] == 15

# 2. Heavy puts above the price pull max pain up, heavy calls below pull it down.
r = M.max_pain({90.0: (0, 0), 100.0: (0, 0), 110.0: (0, 1000)})
assert r["max_pain"] == 110.0
r = M.max_pain({90.0: (1000, 0), 100.0: (0, 0), 110.0: (0, 0)})
assert r["max_pain"] == 90.0

# 3. Monthly expiries: third Friday, or the Thursday before a holiday Friday.
assert M.is_monthly(date(2026, 11, 20)) and not M.is_monthly(date(2026, 11, 13))
assert M.is_monthly(date(2025, 4, 17))                     # Thursday before Good Friday (18 Apr 2025)

# 4. analyse() keeps the nearest expiries plus the monthly even when the chain has daily expiries.
today = date(2026, 11, 2)
chain = []
for d in range(0, 30):
    e = date.fromordinal(today.toordinal() + d)
    if e.weekday() < 5:
        for K in (95.0, 100.0, 105.0):
            chain += [(e, "C", K, 100), (e, "P", K, 100)]
exps = M.analyse(100.0, chain, today)
assert [e["expiry"] for e in exps if e["monthly"]] == ["2026-11-20"], exps
assert len(exps) == M.MAX_EXPIRIES + 1

# 5. Scorecard: price moved toward max pain in one case, away in the other.
hist = [
    {"date": "2026-10-12", "symbol": "AAA", "expiry": "2026-10-16", "dte": "4", "monthly": "1", "spot": "100", "max_pain": "95"},
    {"date": "2026-10-09", "symbol": "AAA", "expiry": "2026-10-16", "dte": "7", "monthly": "1", "spot": "100", "max_pain": "95"},
    {"date": "2026-10-16", "symbol": "AAA", "expiry": "2026-10-16", "dte": "0", "monthly": "1", "spot": "97", "max_pain": "95"},
    {"date": "2026-10-09", "symbol": "BBB", "expiry": "2026-10-16", "dte": "7", "monthly": "1", "spot": "50", "max_pain": "55"},
]
sc = M.score(hist, {("AAA", "2026-10-16"): 96.0, ("BBB", "2026-10-16"): 48.0}, date(2026, 11, 2))
wk = sc["groups"]["week_all"]
assert wk["n"] == 2 and wk["toward"] == 0.5, wk
day = sc["groups"]["day_all"]
assert day["n"] == 1 and day["toward"] == 1.0, day
assert sc["expiries_scored"] == 1
print("maxpain: all checks passed")
