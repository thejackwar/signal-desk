# Trend Pullback (mean reversion) — first test, 6 Nov 2026

Rules (defaults): buy when RSI(2) closes below 10 and the close is above the 200-day average; sell on the
first close above the 5-day average, RSI(2) above 70, or 10 sessions after the buy. Buys only.
Code: `trend_pullback()` in `engine/strategies.py`, `pullback` in `docs/lab.js` (parity-tested).

Run through `docs/lab.js` on `docs/data/lab.json` (today's S&P 100, so survivorship flatters it),
€100k account, entries at the alert close, no costs unless stated. Equal split = the site's default
account (money split across every open position).

| From 2021 | CAGR | Worst drop | Sharpe | After IT tax |
|---|---|---|---|---|
| SPY buy & hold | +14.6% | −24.5% | 0.91 | +10.7% |
| Hold the same stocks | +15.7% | −23.5% | 1.03 | |
| Default, equal split | +16.6% | −27.1% | 0.87 | +12.3% |
| Default, 0.05% cost a side | +9.5% | −30.1% | 0.56 | +7.0% |
| Max 5 positions | +20.4% | −13.1% | 1.15 | +15.2% |
| Max 5 positions, 0.05% cost | +15.0% | −13.9% | 0.89 | +11.2% |
| Max 5, next open, 0.05% cost | +13.1% | −16.6% | 0.83 | +9.7% |

| From 2018 | CAGR | Worst drop | Sharpe |
|---|---|---|---|
| SPY | +14.1% | −33.7% | 0.79 |
| Default, equal split | +13.2% | −36.8% | 0.68 |
| Max 5 positions | +13.7% | −26.3% | 0.82 |
| Max 5 positions, 0.05% cost | +8.8% | −31.9% | 0.57 |

Trades: ~700 a year (equal split), ~225 a year with 5 slots; win rate 66–69%, average hold ~4 days,
average trade +0.3–0.5% before costs.

Variants (from 2021, equal split, no costs): no 200-day filter +21.0% (deeper worst trade, −40%);
RSI(2) < 5 +12.4%; < 20 +11.0%; 5-day limit +16.5%; 8% stop +14.2%; VIX filter 2 (skip elevated VIX)
+16.2% with a −23.1% worst drop; buying only while the VIX is elevated: +0.78% a trade but invested
22% of the time, +4.0% a year.

Takeaways: the per-trade edge is real but thin (~0.4%), so costs and the fill decide it. With 5 slots it
matched SPY since 2021 at about half the drawdown after 0.05% costs, but lagged clearly from 2018.
The research engine (point-in-time universe, costs, out-of-sample) gives the official grade.
