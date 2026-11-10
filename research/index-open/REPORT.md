# Index Open Study (2026-11-10)

Does the pre-market direction (last pre-market price vs yesterday's close) tell you which way SPY / QQQ go in the first 30 or 60 minutes after the 09:30 open? 5-minute SIP bars since 2020-01-01. Follow trade = enter at the open in the gap's direction, exit at 10:00 / 10:30, 0.01% a side. Small move first = from the open, +X% in the gap's direction reached before -X% against it (a bar touching both counts as a loss). 50% is a coin flip.

## SPY

| Filter | Days | Match 30m | Match 60m | Follow 60m win / avg / PF | +0.1% first (60m) | +0.2% first | +0.3% first |
|---|---|---|---|---|---|---|---|
| none | 1721 | 47.9% | 49.4% | 46.4% / -0.023% / 0.86 | 40.5% vs 58.5% | 38.5% vs 46.8% | 31.1% vs 34.6% |
| with_market_trend | 894 | 49.3% | 51.3% | 49.0% / -0.026% / 0.85 | 41.6% vs 56.9% | 36.7% vs 46.1% | 28.5% vs 33.7% |
| late_agrees | 908 | 47.9% | 49.0% | 46.4% / -0.019% / 0.89 | 40.0% vs 59.1% | 36.7% vs 47.2% | 29.6% vs 35.8% |
| both | 487 | 48.7% | 51.7% | 49.7% / -0.021% / 0.88 | 41.1% vs 58.1% | 34.7% vs 46.0% | 26.9% vs 34.5% |

By gap size, no filter:

| Gap | Days | Match 30m | Match 60m | Follow 60m avg |
|---|---|---|---|---|
| under 0.25% | 678 | 46.9% | 47.6% | -0.026% |
| 0.25-0.5% | 474 | 43.5% | 47.7% | -0.039% |
| 0.5-1% | 365 | 52.3% | 52.6% | -0.010% |
| over 1% | 204 | 53.9% | 53.4% | -0.000% |

Dev vs test, match 60m:

- none: 2020-2023 48.7% (1003 days), 2024 on 50.4% (718 days)
- with_market_trend: 2020-2023 51.1% (513 days), 2024 on 51.7% (381 days)
- late_agrees: 2020-2023 48.2% (521 days), 2024 on 50.1% (387 days)
- both: 2020-2023 52.9% (278 days), 2024 on 50.2% (209 days)

## QQQ

| Filter | Days | Match 30m | Match 60m | Follow 60m win / avg / PF | +0.1% first (60m) | +0.2% first | +0.3% first |
|---|---|---|---|---|---|---|---|
| none | 1719 | 48.2% | 49.0% | 47.0% / -0.030% / 0.88 | 33.6% vs 66.3% | 45.2% vs 53.3% | 42.2% vs 48.4% |
| with_market_trend | 888 | 48.6% | 49.8% | 47.5% / -0.033% / 0.87 | 34.2% vs 65.7% | 44.3% vs 53.7% | 39.2% vs 49.1% |
| late_agrees | 870 | 48.3% | 49.2% | 47.7% / -0.017% / 0.93 | 34.0% vs 65.9% | 44.6% vs 53.7% | 42.4% vs 47.2% |
| both | 451 | 46.3% | 48.3% | 46.3% / -0.048% / 0.82 | 34.1% vs 65.6% | 43.5% vs 54.3% | 38.6% vs 48.1% |

By gap size, no filter:

| Gap | Days | Match 30m | Match 60m | Follow 60m avg |
|---|---|---|---|---|
| under 0.25% | 533 | 50.8% | 51.0% | +0.012% |
| 0.25-0.5% | 411 | 47.2% | 45.7% | -0.061% |
| 0.5-1% | 447 | 47.4% | 48.3% | -0.044% |
| over 1% | 328 | 46.3% | 50.6% | -0.042% |

Dev vs test, match 60m:

- none: 2020-2023 48.5% (1002 days), 2024 on 49.7% (717 days)
- with_market_trend: 2020-2023 50.0% (516 days), 2024 on 49.5% (372 days)
- late_agrees: 2020-2023 48.9% (507 days), 2024 on 49.6% (363 days)
- both: 2020-2023 49.4% (261 days), 2024 on 46.8% (190 days)
