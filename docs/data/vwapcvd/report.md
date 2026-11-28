# VWAP + CVD morning setups - backtest (2026-11-28T02:58+00:00)

S&P 100 from 2026-06-29; CVD pilot AAPL, MSFT, NVDA, AMZN, META, TSLA, GOOGL, AMD, AVGO, NFLX from 2026-06-01. Default: 2R target, exit 1200 NY. Costs 0.03% a side.

| Setup | Scope | Filter | Trades | Win % | Avg % | Avg R | PF | t |
|---|---|---|---|---|---|---|---|---|
| reclaim | full | all | 5547 | 43.7 | -0.04 | 0.018 | 0.85 | -4.61 |
| reclaim | pilot | all | 523 | 43.6 | -0.007 | 0.056 | 0.98 | -0.22 |
| reclaim | pilot | cvd | 38 | 36.8 | -0.127 | -0.043 | 0.62 | -1.2 |
| reclaim | pilot | anti | 485 | 44.1 | 0.002 | 0.064 | 1.01 | 0.07 |
| rejection | full | all | 5357 | 35.7 | -0.062 | -0.008 | 0.69 | -10.64 |
| rejection | pilot | all | 499 | 33.3 | -0.065 | -0.044 | 0.71 | -2.87 |
| rejection | pilot | cvd | 474 | 33.3 | -0.06 | -0.042 | 0.73 | -2.55 |
| rejection | pilot | anti | 25 | 32.0 | -0.157 | -0.091 | 0.36 | -2.23 |
| divergence | pilot | all | 395 | 34.2 | -0.07 | -0.002 | 0.6 | -4.08 |
| drive | full | all | 5679 | 46.9 | -0.051 | 0.015 | 0.89 | -3.29 |
| drive | pilot | all | 627 | 47.7 | -0.053 | 0.025 | 0.91 | -0.96 |
| drive | pilot | cvd | 327 | 49.8 | 0.014 | 0.05 | 1.03 | 0.19 |
| drive | pilot | anti | 300 | 45.3 | -0.126 | -0.002 | 0.79 | -1.63 |

Full grids (every target/exit) and per-year results are in backtest.json.
