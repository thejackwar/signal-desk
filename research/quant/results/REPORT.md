# Research engine report (2026-11-06)

Universe: point-in-time top 100 of the S&P 500 by trading value, quarterly. 231 different stocks since 2011; we have prices for 85% of S&P 500 members over the period (the rest were delisted and Yahoo has no data). Of today's S&P 100, 72 were in the 2011 top 100.

All strategy numbers are **after 10 bp per buy and per sell**, long only, money split equally across open positions (the app's account). Development years 2011–2020, test years 2021–now (never used to pick anything).

## Benchmarks

| | CAGR | Sharpe | Worst drop | Sharpe dev | Sharpe test |
|---|---|---|---|---|---|
| Hold every stock in the universe (point in time) | 14.3% | 0.81 | -35% | 0.80 | 0.83 |
| Hold today's S&P 100 since 2011 (hindsight) | 17.6% | 1.04 | -35% | 1.06 | 1.01 |
| SPY | 13.8% | 0.85 | -34% | 0.84 | 0.86 |

## Strategies

Beats hold = probability its Sharpe is above holding the same stocks (bootstrap). Deflated = chance the edge over holding is real after ~20 strategies tried (0.95 is the usual bar).

| Strategy | On site | CAGR | Sharpe | Dev | Test | Beats hold (full / test) | Deflated | Today's list CAGR | Buy 1 day later | Sharpe @0 / 10 / 25 bp | Capped 5% CAGR |
|---|---|---|---|---|---|---|---|---|---|---|---|
| golden_cross | yes | 14.1% | 0.87 | 0.84 | 0.93 | 75% / 74% | 0.06 | 16.1% | 0.87 | 0.88 / 0.87 / 0.84 | 13.9% |
| ma200 | yes | 13.7% | 0.85 | 0.83 | 0.91 | 69% / 68% | 0.04 | 15.3% | 0.85 | 0.94 / 0.85 / 0.72 | 11.5% |
| relative_strength | yes | 14.0% | 0.82 | 0.82 | 0.83 | 49% / 42% | 0.07 | 16.0% | 0.82 | 0.85 / 0.82 / 0.78 | 14.0% |
| deck_live | yes | 11.9% | 0.76 | 0.71 | 0.86 | 26% / 43% | 0.01 | 13.0% | 0.76 | 0.90 / 0.76 / 0.55 | 10.7% |
| donchian |  | 11.4% | 0.73 | 0.79 | 0.63 | 8% / 2% | 0.00 | 14.4% | 0.74 | 0.81 / 0.73 / 0.62 | 10.1% |
| buy_the_dip |  | 12.9% | 0.72 | 0.72 | 0.73 | 2% / 10% | 0.01 | 17.2% | 0.75 | 0.79 / 0.72 / 0.62 | 13.0% |
| value_breakout |  | 10.7% | 0.67 | 0.67 | 0.67 | 4% / 7% | 0.00 | 14.8% | 0.71 | 0.80 / 0.67 / 0.48 | 9.4% |
| pattern_trader | yes | 12.5% | 0.67 | 0.52 | 0.90 | 15% / 57% | 0.06 | 19.0% | 0.67 | 0.77 / 0.67 / 0.52 | 3.9% |
| deck_v6 |  | 10.1% | 0.66 | 0.68 | 0.61 | 2% / 2% | 0.00 | 13.7% | 0.66 | 0.79 / 0.66 / 0.46 | 9.8% |
| back_to_value |  | 11.6% | 0.65 | 0.72 | 0.54 | 2% / 1% | 0.00 | 16.6% | 0.62 | 0.79 / 0.65 / 0.45 | 11.3% |
| deck_ma50 | yes | 9.7% | 0.65 | 0.52 | 0.87 | 4% / 46% | 0.00 | 11.4% | 0.65 | 0.79 / 0.65 / 0.44 | 9.7% |
| volume_breakout |  | 10.9% | 0.65 | 0.71 | 0.55 | 8% / 12% | 0.01 | 13.0% | 0.63 | 0.79 / 0.65 / 0.45 | 7.2% |
| bollinger |  | 11.5% | 0.64 | 0.66 | 0.60 | 3% / 8% | 0.01 | 15.4% | 0.61 | 0.77 / 0.64 / 0.45 | 11.7% |
| double_top_bottom |  | 10.9% | 0.62 | 0.77 | 0.34 | 7% / 3% | 0.01 | 11.3% | 0.63 | 0.75 / 0.62 / 0.42 | 5.2% |
| breakout_bot |  | 9.0% | 0.62 | 0.63 | 0.60 | 5% / 13% | 0.00 | 11.6% | 0.62 | 0.72 / 0.62 / 0.46 | 9.4% |
| supertrend |  | 9.2% | 0.59 | 0.56 | 0.66 | 0% / 6% | 0.00 | 12.4% | 0.65 | 0.69 / 0.59 / 0.45 | 8.9% |
| deck_v8 |  | 8.4% | 0.57 | 0.62 | 0.46 | 0% / 0% | 0.00 | 11.5% | 0.57 | 0.76 / 0.57 / 0.28 | 8.4% |
| triangle |  | 8.1% | 0.51 | 0.89 | -0.04 | 5% / 0% | 0.00 | 12.5% | 0.49 | 0.65 / 0.51 / 0.30 | 3.5% |
| news_contrarian |  | 4.9% | 0.47 | 0.18 | 0.90 | 6% / 54% | 0.00 | 5.9% | 0.47 | 0.54 / 0.47 / 0.35 | 4.6% |
| head_shoulders |  | 7.6% | 0.44 | 0.48 | 0.39 | 4% / 9% | 0.01 | 15.7% | 0.50 | 0.53 / 0.44 / 0.30 | 1.2% |
| rsi |  | 5.3% | 0.34 | 0.20 | 0.59 | 0% / 10% | 0.00 | 8.7% | 0.39 | 0.44 / 0.34 / 0.19 | 6.3% |
| macd |  | 3.9% | 0.30 | 0.19 | 0.50 | 0% / 1% | 0.00 | 6.6% | 0.38 | 0.60 / 0.30 / -0.15 | 5.2% |
| trend_pullback |  | 3.4% | 0.26 | 0.23 | 0.31 | 0% / 8% | 0.00 | 5.7% | 0.21 | 0.84 / 0.26 / -0.61 | 4.3% |

## Does picking the winners work?

Rank correlation of Sharpe, dev years vs test years: **0.12** (1 = the best on 2011–2020 stayed best after; 0 = no link).

| Portfolio of strategies (equal mix) | Test CAGR | Test Sharpe | Beats hold in test |
|---|---|---|---|
| Picked by the site's rule on 2011–2020 only (4) | 9.8% | 0.67 | 11% |
| Today's site line-up (picked with hindsight) (6) | 15.2% | 0.95 | 74% |
| All strategies (23) | 10.5% | 0.72 | 6% |

Walk-forward (each January keep the top 5 of the previous 5 years): CAGR 9.2%, Sharpe 0.63 vs holding the stocks 15.5%, Sharpe 0.85; beats hold in 1% of bootstraps.

## Phase 3: trend-filter portfolios

Weekly rebalance, 10 bp on every change, idle money in 3-month T-bills. Compared with holding every stock equally under the same rules.

| Portfolio | CAGR | Sharpe | Worst drop | Covid fall | 2022 | Invested | Dev | Test | Beats hold (full / test) | Deflated |
|---|---|---|---|---|---|---|---|---|---|---|
| hold_all | 14.3% | 0.82 | -35% | -35% | -14% | 100% | 0.81 | 0.83 | – / – | – |
| market_trend | 9.2% | 0.74 | -24% | -13% | -10% | 85% | 0.67 | 0.86 | 33% / 60% | 0.00 |
| market_cross | 8.7% | 0.61 | -35% | -35% | -3% | 84% | 0.49 | 0.86 | 6% / 58% | 0.00 |
| stock_trend_ivol | 11.0% | 0.77 | -29% | -29% | -13% | 99% | 0.75 | 0.81 | 26% / 40% | 0.00 |
| vol_target | 11.0% | 0.80 | -23% | -23% | -11% | 89% | 0.79 | 0.82 | 36% / 53% | 0.00 |
| trend_vol_target | 10.1% | 0.79 | -22% | -20% | -12% | 94% | 0.79 | 0.79 | 35% / 36% | 0.00 |

