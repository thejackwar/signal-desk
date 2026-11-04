# Improving residual momentum and the high-volume premium

Generated 2026-11-04 10:06 UTC. Same universe and 10 bp costs as the screen. The ten variants and the rule for picking were fixed before the run (see screen_improve.py).

**Step 1, pick on 2018-01-01 to now** (years already seen): the best Sharpe minus holding's per strategy.

| Strategy | Variant | CAGR | Sharpe | vs hold | Worst drop | Beats hold | Turnover/yr | Sharpe @25bp |
|---|---|---|---|---|---|---|---|---|
| Residual momentum | `mom_residual_12_1` (base) | 20.1% | 0.84 | 0.04 | -35% | 62% | 9.7 | 0.78 |
| Residual momentum | `mrv_top20` | 16.7% | 0.78 | -0.02 | -33% | 51% | 7.6 | 0.73 |
| Residual momentum | `mrv_buffer` ★ | 21.6% | 0.87 | 0.07 | -33% | 70% | 3.9 | 0.85 |
| Residual momentum | `mrv_mkt_trend` | 9.0% | 0.51 | -0.29 | -22% | 19% | 9.3 | 0.44 |
| Residual momentum | `mrv_vol_scaled` | 11.9% | 0.71 | -0.09 | -35% | 31% | 7.2 | 0.65 |
| Residual momentum | `mrv_stock_trend` | 18.4% | 0.78 | -0.02 | -37% | 53% | 9.5 | 0.73 |
| High-volume premium | `high_volume_premium` (base) | 20.1% | 1.09 | 0.30 | -20% | 88% | 22.3 | 0.91 |
| High-volume premium | `hvp_top20` | 16.7% | 0.99 | 0.19 | -20% | 75% | 19.4 | 0.82 |
| High-volume premium | `hvp_stock_trend` | 14.1% | 0.86 | 0.06 | -21% | 60% | 20.7 | 0.67 |
| High-volume premium | `hvp_up_volume` ★ | 21.3% | 1.18 | 0.38 | -19% | 93% | 21.5 | 1.00 |
| High-volume premium | `hvp_two_month` | 18.9% | 1.05 | 0.26 | -26% | 93% | 11.3 | 0.96 |
| High-volume premium | `hvp_mkt_trend` | 8.4% | 0.63 | -0.17 | -23% | 23% | 18.3 | 0.44 |

Holding the same stocks: Sharpe 0.80. ★ = picked.

## Step 2, the one-shot check on 2011-01-01 to 2017-12-31 (never looked at before)

| Strategy | Picked | CAGR | Sharpe | Holding's Sharpe | Beats hold | Base, same years (Sharpe) | Verdict |
|---|---|---|---|---|---|---|---|
| Residual momentum | `mrv_buffer` | 13.9% | 0.97 | 0.90 | 62% | 1.05 | **Mixed** |
| High-volume premium | `hvp_up_volume` | 11.6% | 0.79 | 0.90 | 27% | 0.67 | **Fails** |

Holds up = beats holding in at least 70% of bootstraps on these years, with a higher Sharpe. Residual momentum needs 3 years of history, so its check starts in 2012.

Deflated Sharpe over 2011–now, counting all 97 strategies tried: `mrv_buffer` 0.00, `hvp_up_volume` 0.00 (0.95 is the usual bar).
