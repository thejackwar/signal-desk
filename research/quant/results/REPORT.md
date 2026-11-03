# Research engine report (2026-11-03)

Universe: point-in-time top 100 of the S&P 500 by trading value, quarterly. 231 different stocks since 2011; we have prices for 85% of S&P 500 members over the period (the rest were delisted and Yahoo has no data). Of today's S&P 100, 72 were in the 2011 top 100.

All strategy numbers are **after 10 bp per buy and per sell**, long only, money split equally across open positions (the app's account). Development years 2011–2020, test years 2021–now (never used to pick anything).

## Benchmarks

| | CAGR | Sharpe | Worst drop | Sharpe dev | Sharpe test |
|---|---|---|---|---|---|
| Hold every stock in the universe (point in time) | 14.7% | 0.83 | -35% | 0.80 | 0.88 |
| Hold today's S&P 100 since 2011 (hindsight) | 17.7% | 1.04 | -35% | 1.06 | 1.02 |
| SPY | 14.0% | 0.86 | -34% | 0.84 | 0.89 |

## Strategies

Beats hold = probability its Sharpe is above holding the same stocks (bootstrap). Deflated = chance the edge over holding is real after ~20 strategies tried (0.95 is the usual bar).

| Strategy | On site | CAGR | Sharpe | Dev | Test | Beats hold (full / test) | Deflated | Today's list CAGR | Buy 1 day later | Sharpe @0 / 10 / 25 bp | Capped 5% CAGR |
|---|---|---|---|---|---|---|---|---|---|---|---|
| golden_cross |  | 14.4% | 0.88 | 0.84 | 0.97 | 76% / 73% | 0.07 | 16.2% | 0.88 | 0.90 / 0.88 / 0.86 | 14.2% |
| ma200 |  | 14.0% | 0.87 | 0.83 | 0.96 | 70% / 68% | 0.04 | 15.4% | 0.86 | 0.96 / 0.87 / 0.74 | 11.8% |
| relative_strength |  | 14.2% | 0.83 | 0.82 | 0.85 | 49% / 41% | 0.07 | 16.1% | 0.83 | 0.86 / 0.83 / 0.79 | 14.2% |
| deck_live |  | 11.9% | 0.76 | 0.69 | 0.89 | 22% / 43% | 0.01 | 13.0% | 0.76 | 0.90 / 0.76 / 0.55 | 10.7% |
| buy_the_dip | yes | 13.4% | 0.74 | 0.72 | 0.79 | 2% / 11% | 0.01 | 17.3% | 0.77 | 0.81 / 0.74 / 0.64 | 13.4% |
| donchian |  | 11.5% | 0.74 | 0.79 | 0.64 | 8% / 2% | 0.00 | 14.5% | 0.75 | 0.81 / 0.74 / 0.62 | 10.1% |
| pattern_trader | yes | 12.6% | 0.67 | 0.51 | 0.93 | 14% / 56% | 0.06 | 19.0% | 0.67 | 0.78 / 0.67 / 0.52 | 3.8% |
| bollinger | yes | 11.9% | 0.66 | 0.66 | 0.65 | 3% / 8% | 0.01 | 15.4% | 0.64 | 0.79 / 0.66 / 0.47 | 12.1% |
| volume_breakout |  | 11.0% | 0.66 | 0.72 | 0.56 | 8% / 11% | 0.01 | 13.2% | 0.64 | 0.79 / 0.66 / 0.45 | 7.4% |
| deck_v6 | yes | 10.0% | 0.66 | 0.67 | 0.63 | 1% / 2% | 0.00 | 13.6% | 0.66 | 0.79 / 0.66 / 0.45 | 10.0% |
| deck_ma50 |  | 9.7% | 0.65 | 0.51 | 0.89 | 4% / 45% | 0.00 | 11.5% | 0.65 | 0.79 / 0.65 / 0.44 | 9.8% |
| double_top_bottom |  | 11.2% | 0.63 | 0.78 | 0.37 | 7% / 3% | 0.02 | 11.6% | 0.64 | 0.77 / 0.63 / 0.43 | 5.3% |
| breakout_bot |  | 9.1% | 0.62 | 0.62 | 0.63 | 4% / 13% | 0.00 | 11.7% | 0.62 | 0.73 / 0.62 / 0.47 | 9.5% |
| supertrend |  | 9.3% | 0.60 | 0.56 | 0.68 | 0% / 5% | 0.00 | 12.5% | 0.66 | 0.70 / 0.60 / 0.46 | 9.1% |
| deck_v8 |  | 8.5% | 0.57 | 0.61 | 0.50 | 0% / 0% | 0.00 | 11.4% | 0.57 | 0.76 / 0.57 / 0.28 | 8.5% |
| triangle |  | 8.0% | 0.50 | 0.93 | -0.09 | 3% / 0% | 0.00 | 12.5% | 0.49 | 0.64 / 0.50 / 0.30 | 3.5% |
| head_shoulders | yes | 8.5% | 0.47 | 0.52 | 0.41 | 5% / 8% | 0.02 | 16.3% | 0.55 | 0.56 / 0.47 / 0.33 | 1.2% |
| rsi | yes | 5.6% | 0.35 | 0.20 | 0.64 | 0% / 9% | 0.00 | 8.7% | 0.41 | 0.46 / 0.35 / 0.20 | 6.7% |
| macd | yes | 4.0% | 0.30 | 0.19 | 0.51 | 0% / 1% | 0.00 | 6.6% | 0.39 | 0.60 / 0.30 / -0.15 | 5.3% |

## Does picking the winners work?

Rank correlation of Sharpe, dev years vs test years: **0.11** (1 = the best on 2011–2020 stayed best after; 0 = no link).

| Portfolio of strategies (equal mix) | Test CAGR | Test Sharpe | Beats hold in test |
|---|---|---|---|
| Picked by the site's rule on 2011–2020 only (4) | 10.0% | 0.68 | 7% |
| Today's site line-up (picked with hindsight) (7) | 12.5% | 0.78 | 12% |
| All strategies (19) | 11.1% | 0.75 | 6% |

Walk-forward (each January keep the top 5 of the previous 5 years): CAGR 9.9%, Sharpe 0.66 vs holding the stocks 16.0%, Sharpe 0.87; beats hold in 2% of bootstraps.

## Phase 3: trend-filter portfolios

Weekly rebalance, 10 bp on every change, idle money in 3-month T-bills. Compared with holding every stock equally under the same rules.

| Portfolio | CAGR | Sharpe | Worst drop | Covid fall | 2022 | Invested | Dev | Test | Beats hold (full / test) | Deflated |
|---|---|---|---|---|---|---|---|---|---|---|
| hold_all | 14.7% | 0.83 | -35% | -35% | -14% | 100% | 0.81 | 0.88 | – / – | – |
| market_trend | 9.5% | 0.77 | -24% | -13% | -10% | 85% | 0.67 | 0.94 | 32% / 60% | 0.00 |
| market_cross | 9.1% | 0.63 | -35% | -35% | -3% | 84% | 0.49 | 0.92 | 7% / 59% | 0.00 |
| stock_trend_ivol | 11.2% | 0.78 | -29% | -29% | -13% | 99% | 0.75 | 0.85 | 24% / 39% | 0.00 |
| vol_target | 11.3% | 0.82 | -23% | -23% | -11% | 89% | 0.79 | 0.88 | 38% / 54% | 0.00 |
| trend_vol_target | 10.3% | 0.80 | -22% | -20% | -12% | 94% | 0.79 | 0.83 | 33% / 34% | 0.00 |

