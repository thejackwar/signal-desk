# Strategy screen: 87 new stock strategies

Generated 2026-11-04 10:06 UTC. Universe: the point-in-time top 100 (234 different stocks). Costs 10 bp per side. Idle money earns T-bills. Nothing on the website changes.

**How it works (fixed before the run):** every strategy is screened on 2021-01-01 to now. Those with a higher Sharpe than holding the same stocks are ranked by how often they beat holding in a block bootstrap; the best 15 are then run on 2018-01-01 to 2020-12-31, years never used to pick them. Confirmed = beats holding in at least 70% of bootstraps there, with a higher Sharpe. Failed = under 40%.

| Benchmark | Screen CAGR | Screen Sharpe | Screen worst drop | 2018–2020 CAGR | 2018–2020 Sharpe |
|---|---|---|---|---|---|
| Hold the same stocks (equal weight) | 15.1% | 0.88 | -25.3% | 15.5% | 0.70 |
| SPY | 14.3% | 0.89 | -24.5% | 14.0% | 0.69 |

## Finalists (12) and the 2018–2020 check

| # | Strategy | Family | Screen CAGR | Screen Sharpe | Beats hold | 2018–2020 CAGR | 2018–2020 Sharpe | Beats hold | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `santa_rally` | Calendar & seasonality | 3.6% | 1.84 | 99% | 3.2% | 0.79 | 52% | **Mixed** |
| 2 | `mom_residual_12_1` | Cross-sectional momentum | 26.3% | 1.03 | 73% | 9.0% | 0.46 | 13% | **Failed** |
| 3 | `high_volume_premium` | Volume & liquidity | 18.8% | 1.05 | 70% | 22.7% | 1.17 | 85% | **Confirmed** |
| 4 | `market_absorption_ratio` | Market breadth & regime | 12.9% | 0.97 | 63% | -2.7% | -0.05 | 2% | **Failed** |
| 5 | `near_52wk_low_avoid` | 52-week highs/lows & index events | 15.1% | 0.89 | 60% | 16.7% | 0.75 | 83% | **Confirmed** |
| 6 | `inv_vol_universe` | Risk overlays | 13.8% | 0.93 | 58% | 15.0% | 0.71 | 63% | **Mixed** |
| 7 | `lottery_max_avoid` | Low volatility / defensive | 14.3% | 0.93 | 56% | 12.2% | 0.60 | 5% | **Failed** |
| 8 | `low_idio_vol` | Low volatility / defensive | 13.2% | 0.95 | 55% | 13.8% | 0.67 | 52% | **Mixed** |
| 9 | `site_lineup_vote` | Ensembles & composites | 13.7% | 0.90 | 53% | 12.5% | 0.63 | 30% | **Failed** |
| 10 | `breadth_scaled_exposure` | Risk overlays | 10.0% | 0.89 | 50% | 4.2% | 0.35 | 7% | **Failed** |
| 11 | `protective_breadth_mom` | Dual / absolute momentum | 9.9% | 0.89 | 49% | 6.4% | 0.62 | 41% | **Mixed** |
| 12 | `min_variance` | Low volatility / defensive | 10.2% | 0.91 | 45% | 9.1% | 0.55 | 38% | **Failed** |

Holding the same stocks: Sharpe 0.88 in the screen years, 0.70 in 2018–2020.

## Every strategy, screen years

Sorted by Sharpe minus holding's. Deflated = chance the edge over holding is real after 87 tries (0.95 is the usual bar). Exposure = average share of money in stocks.

| Strategy | Family | CAGR | Sharpe | vs hold | Worst drop | Beats hold | Sharpe @25bp | Buy 1 day later | Turnover/yr | Exposure | Deflated |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `santa_rally` | Calendar & seasonality | 3.6% | 1.84 | +0.95 | -2% | 99% | 1.70 | 1.43 | 2.0 | 3% | 0.00 |
| `high_volume_premium` | Volume & liquidity | 18.8% | 1.05 | +0.17 | -20% | 70% | 0.86 | 1.09 | 22.3 | 100% | 0.00 |
| `mom_residual_12_1` | Cross-sectional momentum | 26.3% | 1.03 | +0.14 | -25% | 73% | 0.97 | 1.04 | 9.5 | 100% | 0.00 |
| `market_absorption_ratio` | Market breadth & regime | 12.9% | 0.97 | +0.08 | -18% | 63% | 0.95 | 0.93 | 1.2 | 74% | 0.00 |
| `low_idio_vol` | Low volatility / defensive | 13.2% | 0.95 | +0.07 | -17% | 55% | 0.89 | 0.96 | 5.6 | 100% | 0.00 |
| `inv_vol_universe` | Risk overlays | 13.8% | 0.93 | +0.05 | -21% | 58% | 0.91 | 0.92 | 1.5 | 100% | 0.00 |
| `lottery_max_avoid` | Low volatility / defensive | 14.3% | 0.93 | +0.04 | -23% | 56% | 0.89 | 0.93 | 3.7 | 100% | 0.00 |
| `min_variance` | Low volatility / defensive | 10.2% | 0.91 | +0.03 | -15% | 45% | 0.88 | 0.91 | 2.5 | 100% | 0.00 |
| `site_lineup_vote` | Ensembles & composites | 13.7% | 0.90 | +0.01 | -27% | 53% | 0.78 | 0.87 | 12.2 | 100% | 0.00 |
| `protective_breadth_mom` | Dual / absolute momentum | 9.9% | 0.89 | +0.01 | -13% | 49% | 0.81 | 0.76 | 6.4 | 35% | 0.00 |
| `breadth_scaled_exposure` | Risk overlays | 10.0% | 0.89 | +0.01 | -15% | 50% | 0.84 | 0.90 | 3.9 | 75% | 0.00 |
| `near_52wk_low_avoid` | 52-week highs/lows & index events | 15.1% | 0.89 | +0.00 | -27% | 60% | 0.86 | 0.87 | 2.6 | 100% | 0.00 |
| `drawdown_brake` | Risk overlays | 12.1% | 0.88 | -0.00 | -19% | 51% | 0.87 | 0.85 | 1.3 | 87% | 0.00 |
| `lowvol_252` | Low volatility / defensive | 10.4% | 0.88 | -0.01 | -16% | 40% | 0.85 | 0.86 | 1.9 | 100% | 0.00 |
| `corr_spike_filter` | Correlation & dispersion | 13.1% | 0.88 | -0.01 | -21% | 49% | 0.86 | 0.82 | 1.8 | 92% | 0.00 |
| `mom_time_series_stock` | Dual / absolute momentum | 14.5% | 0.85 | -0.03 | -21% | 49% | 0.82 | 0.81 | 4.1 | 100% | 0.00 |
| `squeeze_breakout` | Volatility breakout | 17.8% | 0.85 | -0.03 | -34% | 50% | 0.55 | 0.62 | 43.6 | 84% | 0.00 |
| `composite_mom_lowvol_52wh` | Ensembles & composites | 11.1% | 0.84 | -0.04 | -16% | 37% | 0.78 | 0.86 | 5.2 | 100% | 0.00 |
| `trend_vote_5` | Ensembles & composites | 13.2% | 0.83 | -0.05 | -21% | 44% | 0.77 | 0.83 | 6.5 | 100% | 0.00 |
| `vol_target_mom` | Risk overlays | 9.4% | 0.83 | -0.06 | -14% | 36% | 0.75 | 0.86 | 5.7 | 89% | 0.00 |
| `mom_vol_managed` | Cross-sectional momentum | 12.9% | 0.82 | -0.06 | -18% | 43% | 0.79 | 0.82 | 4.0 | 51% | 0.00 |
| `breadth_washout_buy` | Market breadth & regime | 7.4% | 0.82 | -0.06 | -13% | 39% | 0.77 | 0.83 | 2.8 | 13% | 0.00 |
| `shock_drift_up` | Post-shock drift | 17.1% | 0.81 | -0.07 | -42% | 32% | 0.50 | 0.82 | 47.1 | 100% | 0.00 |
| `lowbeta_bab` | Low volatility / defensive | 10.0% | 0.81 | -0.07 | -16% | 33% | 0.79 | 0.83 | 1.5 | 100% | 0.00 |
| `mom_intermediate_12_7` | Cross-sectional momentum | 23.0% | 0.79 | -0.09 | -37% | 44% | 0.75 | 0.74 | 9.6 | 100% | 0.00 |
| `trend_lowvol` | Ensembles & composites | 9.3% | 0.79 | -0.09 | -17% | 31% | 0.72 | 0.80 | 6.1 | 99% | 0.00 |
| `breadth_200d_regime` | Market breadth & regime | 10.2% | 0.78 | -0.10 | -20% | 37% | 0.76 | 0.77 | 1.7 | 85% | 0.00 |
| `amihud_illiquid` | Volume & liquidity | 19.1% | 0.77 | -0.11 | -26% | 24% | 0.76 | 0.76 | 2.7 | 100% | 0.00 |
| `low_corr_stocks` | Correlation & dispersion | 10.8% | 0.77 | -0.11 | -13% | 28% | 0.74 | 0.73 | 2.6 | 100% | 0.00 |
| `chaikin_money_flow` | Volume & liquidity | 12.8% | 0.75 | -0.13 | -26% | 30% | 0.45 | 0.81 | 37.3 | 100% | 0.00 |
| `mom_risk_adjusted` | Cross-sectional momentum | 19.9% | 0.74 | -0.14 | -35% | 40% | 0.71 | 0.73 | 7.8 | 100% | 0.00 |
| `wide_range_followthrough` | Volatility breakout | 16.1% | 0.70 | -0.18 | -34% | 26% | 0.21 | 0.37 | 84.8 | 91% | 0.00 |
| `volume_dryup_pullback` | Volume & liquidity | 12.0% | 0.70 | -0.18 | -23% | 36% | 0.44 | 0.15 | 32.5 | 59% | 0.00 |
| `dual_momentum_top10` | Dual / absolute momentum | 20.0% | 0.69 | -0.19 | -42% | 36% | 0.66 | 0.65 | 6.8 | 100% | 0.00 |
| `winners_on_dips` | Trend + pullback | 19.3% | 0.68 | -0.20 | -31% | 30% | 0.49 | 0.70 | 45.2 | 103% | 0.00 |
| `halloween` | Calendar & seasonality | 8.3% | 0.68 | -0.20 | -19% | 19% | 0.65 | 0.69 | 2.3 | 48% | 0.00 |
| `mom_clenow_regression` | Cross-sectional momentum | 12.6% | 0.65 | -0.23 | -25% | 22% | 0.58 | 0.67 | 10.3 | 88% | 0.00 |
| `obv_accumulation` | Volume & liquidity | 10.6% | 0.65 | -0.23 | -20% | 15% | 0.11 | 0.55 | 64.4 | 99% | 0.00 |
| `rev_1w` | Short-term reversal | 16.0% | 0.65 | -0.23 | -36% | 17% | 0.19 | 0.60 | 90.2 | 100% | 0.00 |
| `mom_12_1` | Cross-sectional momentum | 18.0% | 0.64 | -0.24 | -40% | 28% | 0.61 | 0.64 | 6.9 | 100% | 0.00 |
| `shock_reversal_no_volume` | Post-shock drift | 11.9% | 0.63 | -0.25 | -34% | 21% | 0.35 | 0.89 | 40.2 | 58% | 0.00 |
| `mom_bear_switch` | Cross-sectional momentum | 17.0% | 0.62 | -0.27 | -40% | 26% | 0.59 | 0.63 | 7.4 | 100% | 0.00 |
| `mom_frog_in_pan` | Cross-sectional momentum | 13.9% | 0.60 | -0.28 | -38% | 24% | 0.56 | 0.59 | 8.1 | 100% | 0.00 |
| `market_turbulence` | Market breadth & regime | 8.1% | 0.59 | -0.29 | -31% | 10% | 0.54 | 0.56 | 5.3 | 80% | 0.00 |
| `breadth_thrust` | Market breadth & regime | 5.9% | 0.58 | -0.30 | -31% | 15% | 0.55 | 0.68 | 2.4 | 33% | 0.00 |
| `rev_1m` | Short-term reversal | 13.1% | 0.57 | -0.31 | -37% | 11% | 0.46 | 0.79 | 21.3 | 100% | 0.00 |
| `mom_acceleration` | Cross-sectional momentum | 12.7% | 0.57 | -0.31 | -34% | 21% | 0.51 | 0.56 | 11.6 | 100% | 0.00 |
| `low_realized_skew` | Low volatility / defensive | 8.8% | 0.56 | -0.32 | -27% | 4% | 0.48 | 0.64 | 9.6 | 100% | 0.00 |
| `residual_zscore_reversion` | Mean reversion | 11.5% | 0.56 | -0.32 | -39% | 20% | -0.09 | 0.47 | 108.3 | 82% | 0.00 |
| `ad_line_divergence` | Market breadth & regime | 7.9% | 0.56 | -0.32 | -25% | 1% | 0.42 | 0.55 | 14.3 | 72% | 0.00 |
| `mom_consistent` | Cross-sectional momentum | 12.9% | 0.55 | -0.33 | -38% | 18% | 0.50 | 0.56 | 9.6 | 93% | 0.00 |
| `canary_universe` | Dual / absolute momentum | 12.0% | 0.53 | -0.35 | -37% | 21% | 0.47 | 0.44 | 12.7 | 79% | 0.00 |
| `multi_horizon_trend_score` | Ensembles & composites | 7.1% | 0.53 | -0.35 | -24% | 9% | 0.26 | 0.49 | 27.2 | 89% | 0.00 |
| `dispersion_mom_switch` | Correlation & dispersion | 11.6% | 0.51 | -0.38 | -37% | 8% | 0.43 | 0.57 | 15.0 | 100% | 0.00 |
| `mom_top_trend_exit` | Trend + pullback | 10.4% | 0.49 | -0.39 | -35% | 14% | 0.45 | 0.52 | 7.9 | 83% | 0.00 |
| `universe_entry` | 52-week highs/lows & index events | 9.8% | 0.47 | -0.41 | -48% | 8% | 0.43 | 0.51 | 8.3 | 99% | 0.00 |
| `rev_residual_1w` | Short-term reversal | 9.8% | 0.47 | -0.41 | -39% | 4% | 0.01 | 0.59 | 89.1 | 100% | 0.00 |
| `month_seasonality_cs` | Calendar & seasonality | 9.5% | 0.46 | -0.42 | -51% | 4% | 0.36 | 0.47 | 20.2 | 100% | 0.00 |
| `nh_nl_regime` | Market breadth & regime | 5.5% | 0.44 | -0.44 | -36% | 2% | 0.40 | 0.46 | 4.5 | 84% | 0.00 |
| `dec_tax_loss_rebound` | Calendar & seasonality | 3.1% | 0.42 | -0.46 | -16% | 20% | 0.39 | 0.55 | 1.9 | 10% | 0.00 |
| `first_new_high_after_base` | 52-week highs/lows & index events | 5.8% | 0.42 | -0.47 | -33% | 7% | 0.19 | 0.42 | 25.7 | 95% | 0.00 |
| `beta_rotation_breadth` | Market breadth & regime | 8.0% | 0.40 | -0.48 | -41% | 10% | 0.38 | 0.39 | 5.5 | 100% | 0.00 |
| `market_crash_rebound` | Post-shock drift | 2.5% | 0.40 | -0.48 | -9% | 15% | 0.35 | 0.58 | 2.1 | 3% | 0.00 |
| `turn_of_month` | Calendar & seasonality | 2.8% | 0.40 | -0.49 | -10% | 15% | -0.07 | 0.25 | 24.5 | 19% | 0.00 |
| `keltner_trend` | Volatility breakout | 5.5% | 0.36 | -0.52 | -39% | 3% | -0.00 | 0.21 | 52.0 | 99% | 0.00 |
| `factor_rotation_mom` | Ensembles & composites | 6.0% | 0.34 | -0.54 | -37% | 7% | 0.29 | 0.25 | 11.7 | 100% | 0.00 |
| `rev_overnight_momentum` | Short-term reversal | 5.7% | 0.33 | -0.55 | -42% | 3% | 0.23 | 0.21 | 20.0 | 100% | 0.00 |
| `drawdown_turnaround` | Mean reversion | 4.4% | 0.30 | -0.59 | -66% | 6% | 0.17 | 0.22 | 29.0 | 45% | 0.00 |
| `gap_down_reversal` | Gaps | 4.2% | 0.29 | -0.59 | -48% | 11% | -0.04 | 0.30 | 71.5 | 48% | 0.00 |
| `mom_breadth_filter` | Ensembles & composites | 3.6% | 0.27 | -0.61 | -37% | 5% | 0.24 | 0.27 | 7.1 | 80% | 0.00 |
| `mom_52wk_high` | Cross-sectional momentum | 3.4% | 0.27 | -0.61 | -30% | 3% | 0.13 | 0.21 | 18.1 | 100% | 0.00 |
| `quarter_end_winners` | Calendar & seasonality | 1.5% | 0.23 | -0.65 | -15% | 6% | 0.07 | 0.31 | 8.3 | 8% | 0.00 |
| `high_near_52wh_pullback` | Trend + pullback | 2.5% | 0.22 | -0.66 | -39% | 1% | -0.42 | 0.34 | 91.9 | 99% | 0.00 |
| `trend_pullback_ema` | Trend + pullback | 2.3% | 0.21 | -0.67 | -30% | 0% | -0.61 | -0.00 | 103.2 | 100% | 0.00 |
| `rev_intraday_losers` | Short-term reversal | 0.0% | 0.15 | -0.73 | -48% | 0% | 0.05 | 0.21 | 19.8 | 100% | 0.00 |
| `gap_up_continuation` | Gaps | -0.7% | 0.12 | -0.76 | -52% | 3% | -0.11 | 0.24 | 45.5 | 74% | 0.00 |
| `nr7_breakout` | Volatility breakout | 0.1% | 0.10 | -0.79 | -41% | 0% | -0.80 | -0.11 | 107.3 | 99% | 0.00 |
| `weekday_skip_monday` | Calendar & seasonality | -0.6% | 0.05 | -0.84 | -23% | 0% | -0.92 | 0.22 | 105.1 | 79% | 0.00 |
| `ma_slope_trend_strength` | Trend + pullback | -1.6% | 0.04 | -0.85 | -47% | 1% | -0.39 | 0.03 | 61.7 | 94% | 0.00 |
| `variance_ratio_dip` | Mean reversion | -5.9% | -0.14 | -1.02 | -47% | 0% | -0.92 | 0.14 | 122.4 | 91% | 0.00 |
| `opex_week` | Calendar & seasonality | -1.6% | -0.15 | -1.04 | -25% | 0% | -0.60 | -0.11 | 24.1 | 23% | 0.00 |
| `ibs_reversion` | Mean reversion | -6.6% | -0.25 | -1.13 | -52% | 0% | -1.90 | -0.37 | 217.6 | 99% | 0.00 |
| `consec_down_days` | Mean reversion | -24.6% | -1.08 | -1.97 | -84% | 0% | -2.85 | -0.19 | 279.0 | 85% | 0.00 |
| `cs_ibs_daily` | Mean reversion | -25.9% | -1.17 | -2.05 | -88% | 0% | -4.11 | -1.42 | 457.4 | 100% | 0.00 |
| `gap_fill_intraday` ⓘ | Gaps | -42.8% | -1.72 | -2.60 | -96% | 0% | -3.15 | – | 290.8 | 58% | 0.00 |
| `overnight_only_hold` ⓘ | Short-term reversal | -33.9% | -3.76 | -4.65 | -91% | 0% | -10.75 | – | 504.0 | 100% | 0.00 |
| `vol_open_breakout` ⓘ | Volatility breakout | -55.2% | -6.96 | -7.85 | -99% | 0% | -13.56 | – | 502.6 | 100% | 0.00 |

ⓘ trades inside the day (open/close), so 'buy 1 day later' doesn't apply.

## Not in this run

19 more ideas need data the engine doesn't have yet (sectors, fundamentals, VIX). They come in a second batch.
