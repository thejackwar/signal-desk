# News-only strategy study

Data to 2026-11-03. Headlines from Alpaca/Benzinga (S&P 100, top coins, gold/silver/oil ETFs) scored by FinBERT, plus GDELT's worldwide tone and article counts for oil, gold, silver, bitcoin and the stock market. Signals are only used from the close after the news. Costs included. Nothing tuned: fixed rules, so 2024-now is an honest second look.

**How to read it.** *Reaction* = rank correlation between the day's news and that same day's move (news explaining the move — not tradable). *IC* = rank correlation between the signal and the *next* h days' return (tradable edge; 0.02+ is meaningful in finance, |t| > 2 is unlikely to be luck). *Top/Bottom* = average next-h-day return after the strongest fifth of signals vs the weakest fifth.

## Stocks (S&P 100, Benzinga headlines)

| Signal | Obs | Reaction | IC 1d (t) | IC 3d (t) | IC 5d (t) | IC 10d (t) | 5d top / bottom / all |
|---|---|---|---|---|---|---|---|
| Mood | 93,971 | +0.077 | +0.002 (+0.5) | -0.004 (-0.6) | -0.004 (-0.4) | -0.004 (-0.3) | +0.38% / +0.42% / +0.41% |
| Mood vs usual | 91,899 | +0.077 | +0.002 (+0.5) | -0.003 (-0.4) | -0.002 (-0.2) | +0.001 (+0.1) | +0.38% / +0.42% / +0.41% |
| News burst | 91,886 | +0.076 | +0.001 (+0.4) | -0.002 (-0.4) | -0.001 (-0.2) | -0.000 (-0.0) | +0.40% / +0.39% / +0.41% |
| News volume (unsigned) | 188,166 | +0.014 | -0.001 (-0.4) | +0.001 (+0.1) | +0.003 (+0.6) | +0.002 (+0.3) | +0.40% / +0.36% / +0.36% |

Strategy (long only) vs buy & hold, yearly growth after costs:

| Signal | Hold | All years | 2019-23 | 2024-now | Sharpe (vs B&H) | Time invested | Same rules on the worst news |
|---|---|---|---|---|---|---|---|
| Mood | 1d | +3.2% vs +18.4% | +6.4% vs +17.9% | -1.7% vs +19.2% | 0.26 vs 0.98 | 100% | +9.9% |
| Mood | 3d | +16.3% vs +18.4% | +17.1% vs +17.9% | +14.9% vs +19.2% | 0.86 vs 0.98 | 100% | +17.4% |
| Mood | 5d | +19.0% vs +18.4% ✓ | +18.8% vs +17.9% ✓ | +19.4% vs +19.2% ✓ | 0.98 vs 0.98 | 100% | +18.7% |
| Mood | 10d | +19.9% vs +18.4% ✓ | +19.3% vs +17.9% ✓ | +21.0% vs +19.2% ✓ | 1.04 vs 0.98 | 100% | +20.0% |
| Mood vs usual | 1d | +1.2% vs +18.4% | +1.8% vs +17.9% | +0.2% vs +19.2% | 0.16 vs 0.98 | 100% | +8.6% |
| Mood vs usual | 3d | +14.3% vs +18.4% | +13.8% vs +17.9% | +15.2% vs +19.2% | 0.75 vs 0.98 | 100% | +18.4% |
| Mood vs usual | 5d | +17.3% vs +18.4% | +16.8% vs +17.9% | +18.1% vs +19.2% | 0.88 vs 0.98 | 100% | +18.8% |
| Mood vs usual | 10d | +19.6% vs +18.4% ✓ | +18.5% vs +17.9% ✓ | +21.3% vs +19.2% ✓ | 0.98 vs 0.98 | 99% | +20.2% |
| News burst | 1d | -23.7% vs +18.4% | -19.1% vs +17.9% | -30.5% vs +19.2% | -0.81 vs 0.98 | 92% | -1.9% |
| News burst | 3d | +5.5% vs +18.4% | +6.7% vs +17.9% | +3.6% vs +19.2% | 0.36 vs 0.98 | 92% | +12.4% |
| News burst | 5d | +5.7% vs +18.4% | +2.1% vs +17.9% | +11.7% vs +19.2% | 0.39 vs 0.98 | 92% | +13.2% |
| News burst | 10d | +11.4% vs +18.4% | +6.8% vs +17.9% | +19.1% vs +19.2% | 0.70 vs 0.98 | 92% | +16.4% |

## Crypto, 11 coins pooled (Benzinga)

| Signal | Obs | Reaction | IC 1d (t) | IC 3d (t) | IC 5d (t) | IC 10d (t) | 5d top / bottom / all |
|---|---|---|---|---|---|---|---|
| Mood | 8,298 | +0.344 | -0.021 (-1.9) | +0.008 (+0.4) | -0.027 (-1.1) | -0.011 (-0.3) | +0.54% / +0.46% / +0.30% |
| Mood vs usual | 7,686 | +0.314 | -0.025 (-2.2) | +0.002 (+0.1) | -0.026 (-1.0) | -0.014 (-0.4) | +0.57% / +0.39% / +0.31% |
| News burst | 7,686 | +0.296 | -0.015 (-1.3) | +0.007 (+0.4) | -0.009 (-0.4) | -0.011 (-0.3) | +0.57% / +0.42% / +0.31% |
| News volume (unsigned) | 28,486 | +0.005 | +0.014 (+2.3) | +0.018 (+1.8) | +0.027 (+2.0) | +0.023 (+1.2) | +0.48% / -0.05% / +0.95% |

## Bitcoin, Benzinga

| Signal | Obs | Reaction | IC 1d (t) | IC 3d (t) | IC 5d (t) | IC 10d (t) | 5d top / bottom / all |
|---|---|---|---|---|---|---|---|
| Mood | 1,698 | +0.175 | -0.024 (-1.0) | +0.020 (+0.5) | -0.007 (-0.1) | -0.008 (-0.1) | +0.87% / +0.55% / +0.26% |
| Mood vs usual | 1,672 | +0.175 | -0.024 (-1.0) | +0.023 (+0.5) | +0.003 (+0.1) | -0.004 (-0.1) | +0.77% / +0.27% / +0.32% |
| News burst | 1,672 | +0.177 | -0.010 (-0.4) | +0.030 (+0.7) | +0.023 (+0.4) | +0.013 (+0.2) | +0.27% / -0.18% / +0.32% |
| News volume (unsigned) | 2,713 | -0.015 | -0.004 (-0.2) | -0.028 (-0.8) | -0.026 (-0.6) | -0.047 (-0.8) | -0.15% / +0.47% / +0.66% |

Strategy (long only) vs buy & hold, yearly growth after costs:

| Signal | Hold | All years | 2019-23 | 2024-now | Sharpe (vs B&H) | Time invested | Same rules on the worst news |
|---|---|---|---|---|---|---|---|
| Mood | 1d | -8.4% vs +34.5% | -7.3% vs +41.6% | -10.2% vs +23.9% | -0.39 vs 0.80 | 13% | – |
| Mood | 3d | +4.2% vs +34.5% | +3.8% vs +41.6% | +4.9% vs +23.9% | 0.40 vs 0.80 | 13% | – |
| Mood | 5d | +3.7% vs +34.5% | +4.0% vs +41.6% | +3.2% vs +23.9% | 0.39 vs 0.80 | 13% | – |
| Mood | 10d | +3.5% vs +34.5% | +1.8% vs +41.6% | +6.4% vs +23.9% | 0.42 vs 0.80 | 13% | – |
| Mood vs usual | 1d | -2.9% vs +34.5% | +1.8% vs +41.6% | -10.2% vs +23.9% | -0.14 vs 0.80 | 7% | – |
| Mood vs usual | 3d | +4.9% vs +34.5% | +6.4% vs +41.6% | +2.7% vs +23.9% | 0.56 vs 0.80 | 7% | – |
| Mood vs usual | 5d | +2.4% vs +34.5% | +3.7% vs +41.6% | +0.3% vs +23.9% | 0.35 vs 0.80 | 7% | – |
| Mood vs usual | 10d | +2.7% vs +34.5% | +2.1% vs +41.6% | +3.6% vs +23.9% | 0.50 vs 0.80 | 7% | – |
| News burst | 1d | +3.8% vs +34.5% | +2.0% vs +41.6% | +6.6% vs +23.9% | 0.64 vs 0.80 | 1% | – |
| News burst | 3d | +1.1% vs +34.5% | +0.4% vs +41.6% | +2.1% vs +23.9% | 0.32 vs 0.80 | 1% | – |
| News burst | 5d | +0.5% vs +34.5% | +0.7% vs +41.6% | +0.1% vs +23.9% | 0.18 vs 0.80 | 1% | – |
| News burst | 10d | +1.1% vs +34.5% | +1.2% vs +41.6% | +0.9% vs +23.9% | 0.39 vs 0.80 | 1% | – |

## Ethereum, Benzinga

| Signal | Obs | Reaction | IC 1d (t) | IC 3d (t) | IC 5d (t) | IC 10d (t) | 5d top / bottom / all |
|---|---|---|---|---|---|---|---|
| Mood | 1,532 | +0.211 | -0.024 (-0.9) | +0.009 (+0.2) | -0.004 (-0.1) | +0.008 (+0.1) | +0.79% / +0.36% / +0.11% |
| Mood vs usual | 1,508 | +0.200 | -0.026 (-1.0) | +0.007 (+0.2) | +0.000 (+0.0) | +0.017 (+0.2) | +0.68% / +0.33% / +0.17% |
| News burst | 1,508 | +0.159 | -0.034 (-1.3) | +0.005 (+0.1) | +0.003 (+0.1) | -0.014 (-0.2) | +0.62% / +0.29% / +0.17% |
| News volume (unsigned) | 2,713 | -0.021 | -0.002 (-0.1) | -0.015 (-0.4) | -0.003 (-0.1) | -0.024 (-0.4) | +0.16% / +0.19% / +0.83% |

Strategy (long only) vs buy & hold, yearly growth after costs:

| Signal | Hold | All years | 2019-23 | 2024-now | Sharpe (vs B&H) | Time invested | Same rules on the worst news |
|---|---|---|---|---|---|---|---|
| Mood | 1d | -4.9% vs +33.4% | +2.5% vs +59.5% | -15.7% vs +0.0% | -0.12 vs 0.77 | 12% | – |
| Mood | 3d | +2.9% vs +33.4% | +4.0% vs +59.5% | +1.2% vs +0.0% ✓ | 0.27 vs 0.77 | 12% | – |
| Mood | 5d | +2.1% vs +33.4% | -0.3% vs +59.5% | +6.2% vs +0.0% ✓ | 0.23 vs 0.77 | 12% | – |
| Mood | 10d | +4.0% vs +33.4% | +1.6% vs +59.5% | +7.9% vs +0.0% ✓ | 0.40 vs 0.77 | 12% | – |
| Mood vs usual | 1d | -7.3% vs +33.4% | +0.1% vs +59.5% | -18.0% vs +0.0% | -0.36 vs 0.77 | 8% | – |
| Mood vs usual | 3d | +1.8% vs +33.4% | +2.5% vs +59.5% | +0.6% vs +0.0% ✓ | 0.21 vs 0.77 | 8% | – |
| Mood vs usual | 5d | +0.5% vs +33.4% | -1.8% vs +59.5% | +4.5% vs +0.0% ✓ | 0.10 vs 0.77 | 8% | – |
| Mood vs usual | 10d | +3.6% vs +33.4% | -0.4% vs +59.5% | +10.3% vs +0.0% ✓ | 0.44 vs 0.77 | 8% | – |
| News burst | 1d | +1.1% vs +33.4% | -0.9% vs +59.5% | +4.4% vs +0.0% ✓ | 0.16 vs 0.77 | 2% | – |
| News burst | 3d | +1.1% vs +33.4% | +0.5% vs +59.5% | +2.2% vs +0.0% ✓ | 0.20 vs 0.77 | 2% | – |
| News burst | 5d | +1.1% vs +33.4% | +1.9% vs +59.5% | -0.1% vs +0.0% | 0.24 vs 0.77 | 2% | – |
| News burst | 10d | +1.5% vs +33.4% | +1.7% vs +59.5% | +1.2% vs +0.0% ✓ | 0.34 vs 0.77 | 2% | – |

## Gold (GLD), Benzinga

| Signal | Obs | Reaction | IC 1d (t) | IC 3d (t) | IC 5d (t) | IC 10d (t) | 5d top / bottom / all |
|---|---|---|---|---|---|---|---|
| Mood | 654 | +0.031 | +0.042 (+1.1) | +0.008 (+0.1) | -0.032 (-0.4) | -0.035 (-0.3) | +0.08% / +0.44% / +0.38% |
| Mood vs usual | 601 | +0.032 | +0.025 (+0.6) | -0.014 (-0.2) | -0.053 (-0.6) | -0.046 (-0.4) | +0.11% / +0.79% / +0.45% |
| News burst | 601 | +0.010 | +0.050 (+1.2) | +0.016 (+0.2) | -0.006 (-0.1) | -0.001 (-0.0) | +0.65% / +0.64% / +0.45% |
| News volume (unsigned) | 1,867 | +0.040 | +0.022 (+0.9) | +0.047 (+1.2) | +0.037 (+0.7) | +0.027 (+0.4) | +0.51% / +0.27% / +0.32% |

Strategy (long only) vs buy & hold, yearly growth after costs:

| Signal | Hold | All years | 2019-23 | 2024-now | Sharpe (vs B&H) | Time invested | Same rules on the worst news |
|---|---|---|---|---|---|---|---|
| Mood | 1d | -1.1% vs +15.5% | +0.8% vs +10.0% | -4.1% vs +25.0% | -0.26 vs 0.88 | 4% | – |
| Mood | 3d | -0.6% vs +15.5% | +0.2% vs +10.0% | -2.0% vs +25.0% | -0.20 vs 0.88 | 4% | – |
| Mood | 5d | -0.2% vs +15.5% | +0.1% vs +10.0% | -0.7% vs +25.0% | -0.07 vs 0.88 | 4% | – |
| Mood | 10d | +0.2% vs +15.5% | +0.0% vs +10.0% | +0.6% vs +25.0% | 0.14 vs 0.88 | 4% | – |
| Mood vs usual | 1d | -0.5% vs +15.5% | +0.3% vs +10.0% | -1.8% vs +25.0% | -0.10 vs 0.88 | 5% | – |
| Mood vs usual | 3d | +0.2% vs +15.5% | +0.5% vs +10.0% | -0.5% vs +25.0% | 0.07 vs 0.88 | 5% | – |
| Mood vs usual | 5d | -0.0% vs +15.5% | +0.2% vs +10.0% | -0.5% vs +25.0% | -0.01 vs 0.88 | 5% | – |
| Mood vs usual | 10d | +0.3% vs +15.5% | +0.1% vs +10.0% | +0.6% vs +25.0% | 0.16 vs 0.88 | 5% | – |
| News burst | 1d | -0.7% vs +15.5% | +1.0% vs +10.0% | -3.4% vs +25.0% | -0.12 vs 0.88 | 3% | – |
| News burst | 3d | +0.8% vs +15.5% | +0.5% vs +10.0% | +1.3% vs +25.0% | 0.37 vs 0.88 | 3% | – |
| News burst | 5d | +0.5% vs +15.5% | -0.0% vs +10.0% | +1.4% vs +25.0% | 0.31 vs 0.88 | 3% | – |
| News burst | 10d | +0.3% vs +15.5% | -0.1% vs +10.0% | +1.0% vs +25.0% | 0.22 vs 0.88 | 3% | – |

## Silver (SLV), Benzinga

| Signal | Obs | Reaction | IC 1d (t) | IC 3d (t) | IC 5d (t) | IC 10d (t) | 5d top / bottom / all |
|---|---|---|---|---|---|---|---|
| Mood | 220 | +0.095 | -0.033 (-0.5) | -0.024 (-0.2) | -0.105 (-0.7) | -0.034 (-0.2) | -0.50% / +2.47% / +0.73% |
| Mood vs usual | 123 | +0.074 | -0.096 (-1.1) | -0.145 (-0.9) | -0.155 (-0.8) | -0.019 (-0.1) | -1.20% / +2.65% / +0.50% |
| News burst | 123 | +0.103 | -0.073 (-0.8) | -0.130 (-0.8) | -0.210 (-1.0) | -0.013 (-0.1) | -2.72% / +3.65% / +0.50% |
| News volume (unsigned) | 1,867 | +0.015 | +0.003 (+0.1) | +0.021 (+0.5) | +0.031 (+0.6) | +0.028 (+0.4) | +1.19% / +0.66% / +0.46% |

Strategy (long only) vs buy & hold, yearly growth after costs:

| Signal | Hold | All years | 2019-23 | 2024-now | Sharpe (vs B&H) | Time invested | Same rules on the worst news |
|---|---|---|---|---|---|---|---|
| Mood | 1d | +2.0% vs +17.4% | +0.0% vs +10.7% | +5.4% vs +29.1% | 0.61 vs 0.63 | 0% | – |
| Mood | 3d | -0.7% vs +17.4% | +0.0% vs +10.7% | -1.7% vs +29.1% | -0.15 vs 0.63 | 0% | – |
| Mood | 5d | +0.1% vs +17.4% | +0.0% vs +10.7% | +0.2% vs +29.1% | 0.05 vs 0.63 | 0% | – |
| Mood | 10d | -0.1% vs +17.4% | +0.0% vs +10.7% | -0.2% vs +29.1% | -0.03 vs 0.63 | 0% | – |
| Mood vs usual | 1d | +1.8% vs +17.4% | -0.2% vs +10.7% | +5.0% vs +29.1% | 0.45 vs 0.63 | 1% | – |
| Mood vs usual | 3d | -0.5% vs +17.4% | -0.3% vs +10.7% | -0.8% vs +29.1% | -0.10 vs 0.63 | 1% | – |
| Mood vs usual | 5d | +0.4% vs +17.4% | -0.3% vs +10.7% | +1.6% vs +29.1% | 0.17 vs 0.63 | 1% | – |
| Mood vs usual | 10d | +0.3% vs +17.4% | -0.2% vs +10.7% | +1.1% vs +29.1% | 0.12 vs 0.63 | 1% | – |
| News burst | 1d | +2.6% vs +17.4% | +0.5% vs +10.7% | +6.1% vs +29.1% | 0.59 vs 0.63 | 1% | – |
| News burst | 3d | -0.0% vs +17.4% | +0.1% vs +10.7% | -0.3% vs +29.1% | 0.02 vs 0.63 | 1% | – |
| News burst | 5d | -1.6% vs +17.4% | +0.0% vs +10.7% | -4.2% vs +29.1% | -0.21 vs 0.63 | 1% | – |
| News burst | 10d | -0.3% vs +17.4% | +0.0% vs +10.7% | -0.8% vs +29.1% | -0.05 vs 0.63 | 1% | – |

## Oil (USO), Benzinga

| Signal | Obs | Reaction | IC 1d (t) | IC 3d (t) | IC 5d (t) | IC 10d (t) | 5d top / bottom / all |
|---|---|---|---|---|---|---|---|
| Mood | 1,717 | +0.021 | +0.003 (+0.1) | -0.000 (-0.0) | +0.012 (+0.2) | +0.018 (+0.2) | +0.49% / +0.21% / +0.34% |
| Mood vs usual | 1,717 | +0.007 | -0.011 (-0.5) | -0.010 (-0.2) | -0.000 (-0.0) | +0.017 (+0.2) | +0.33% / +0.45% / +0.34% |
| News burst | 1,717 | -0.016 | -0.005 (-0.2) | +0.003 (+0.1) | +0.006 (+0.1) | -0.012 (-0.2) | +0.42% / +0.20% / +0.34% |
| News volume (unsigned) | 1,867 | +0.017 | +0.002 (+0.1) | -0.023 (-0.6) | -0.032 (-0.6) | -0.020 (-0.3) | +0.30% / +0.60% / +0.37% |

Strategy (long only) vs buy & hold, yearly growth after costs:

| Signal | Hold | All years | 2019-23 | 2024-now | Sharpe (vs B&H) | Time invested | Same rules on the worst news |
|---|---|---|---|---|---|---|---|
| Mood | 1d | -2.8% vs +8.1% | -4.7% vs -6.1% ✓ | +0.4% vs +35.7% | -0.08 vs 0.40 | 18% | – |
| Mood | 3d | +3.5% vs +8.1% | +4.4% vs -6.1% ✓ | +2.0% vs +35.7% | 0.31 vs 0.40 | 18% | – |
| Mood | 5d | +3.8% vs +8.1% | +2.0% vs -6.1% ✓ | +6.7% vs +35.7% | 0.39 vs 0.40 | 18% | – |
| Mood | 10d | +4.4% vs +8.1% | +4.5% vs -6.1% ✓ | +4.3% vs +35.7% | 0.53 vs 0.40 | 18% | – |
| Mood vs usual | 1d | -6.1% vs +8.1% | -9.4% vs -6.1% | -0.5% vs +35.7% | -0.41 vs 0.40 | 14% | – |
| Mood vs usual | 3d | +3.4% vs +8.1% | +5.3% vs -6.1% ✓ | +0.4% vs +35.7% | 0.41 vs 0.40 | 14% | – |
| Mood vs usual | 5d | +3.4% vs +8.1% | +2.4% vs -6.1% ✓ | +5.1% vs +35.7% | 0.48 vs 0.40 | 14% | – |
| Mood vs usual | 10d | +3.8% vs +8.1% | +4.9% vs -6.1% ✓ | +1.9% vs +35.7% | 0.63 vs 0.40 | 13% | – |
| News burst | 1d | +0.4% vs +8.1% | -0.7% vs -6.1% ✓ | +2.2% vs +35.7% | 0.09 vs 0.40 | 4% | – |
| News burst | 3d | +0.7% vs +8.1% | -1.3% vs -6.1% ✓ | +3.8% vs +35.7% | 0.13 vs 0.40 | 4% | – |
| News burst | 5d | +0.8% vs +8.1% | -0.7% vs -6.1% ✓ | +3.1% vs +35.7% | 0.18 vs 0.40 | 4% | – |
| News burst | 10d | +0.6% vs +8.1% | -1.6% vs -6.1% ✓ | +4.2% vs +35.7% | 0.16 vs 0.40 | 4% | – |

## Oil (USO), GDELT world news

| Signal | Obs | Reaction | IC 1d (t) | IC 3d (t) | IC 5d (t) | IC 10d (t) | 5d top / bottom / all |
|---|---|---|---|---|---|---|---|
| Mood | 1,840 | +0.006 | -0.011 (-0.5) | -0.016 (-0.4) | -0.002 (-0.0) | -0.029 (-0.4) | +0.47% / -0.18% / +0.38% |
| Mood vs usual | 1,840 | +0.009 | -0.007 (-0.3) | -0.009 (-0.2) | -0.001 (-0.0) | -0.035 (-0.5) | +0.52% / +0.59% / +0.38% |
| News burst | 1,840 | +0.021 | -0.016 (-0.7) | -0.011 (-0.3) | +0.024 (+0.5) | -0.004 (-0.1) | +0.83% / +0.21% / +0.38% |
| News volume (unsigned) | 1,867 | -0.010 | -0.024 (-1.0) | -0.013 (-0.3) | -0.018 (-0.3) | -0.029 (-0.4) | +0.38% / +0.89% / +0.37% |

Strategy (long only) vs buy & hold, yearly growth after costs:

| Signal | Hold | All years | 2019-23 | 2024-now | Sharpe (vs B&H) | Time invested | Same rules on the worst news |
|---|---|---|---|---|---|---|---|
| Mood | 1d | +2.2% vs +8.1% | +0.3% vs -6.1% ✓ | +5.5% vs +35.7% | 0.22 vs 0.40 | 22% | – |
| Mood | 3d | +1.9% vs +8.1% | +2.1% vs -6.1% ✓ | +1.7% vs +35.7% | 0.23 vs 0.40 | 22% | – |
| Mood | 5d | +2.5% vs +8.1% | +3.3% vs -6.1% ✓ | +1.2% vs +35.7% | 0.29 vs 0.40 | 22% | – |
| Mood | 10d | +3.6% vs +8.1% | +3.6% vs -6.1% ✓ | +3.5% vs +35.7% | 0.43 vs 0.40 | 22% | – |
| Mood vs usual | 1d | +7.1% vs +8.1% | +3.3% vs -6.1% ✓ | +13.5% vs +35.7% | 0.57 vs 0.40 | 17% | – |
| Mood vs usual | 3d | +4.0% vs +8.1% | +3.1% vs -6.1% ✓ | +5.6% vs +35.7% | 0.44 vs 0.40 | 17% | – |
| Mood vs usual | 5d | +4.7% vs +8.1% | +5.3% vs -6.1% ✓ | +3.8% vs +35.7% | 0.54 vs 0.40 | 17% | – |
| Mood vs usual | 10d | +3.4% vs +8.1% | +2.5% vs -6.1% ✓ | +4.8% vs +35.7% | 0.47 vs 0.40 | 17% | – |
| News burst | 1d | -0.7% vs +8.1% | +0.4% vs -6.1% ✓ | -2.4% vs +35.7% | -0.38 vs 0.40 | 1% | – |
| News burst | 3d | -0.9% vs +8.1% | -0.8% vs -6.1% ✓ | -1.2% vs +35.7% | -0.65 vs 0.40 | 1% | – |
| News burst | 5d | -0.3% vs +8.1% | -0.0% vs -6.1% ✓ | -0.9% vs +35.7% | -0.30 vs 0.40 | 1% | – |
| News burst | 10d | +0.1% vs +8.1% | +0.1% vs -6.1% ✓ | -0.1% vs +35.7% | 0.07 vs 0.40 | 1% | – |

## Gold (GLD), GDELT world news

| Signal | Obs | Reaction | IC 1d (t) | IC 3d (t) | IC 5d (t) | IC 10d (t) | 5d top / bottom / all |
|---|---|---|---|---|---|---|---|
| Mood | 1,838 | +0.001 | +0.022 (+0.9) | +0.030 (+0.7) | +0.023 (+0.5) | +0.051 (+0.7) | +0.52% / +0.36% / +0.33% |
| Mood vs usual | 1,838 | -0.017 | +0.021 (+0.9) | +0.015 (+0.4) | -0.001 (-0.0) | +0.007 (+0.1) | +0.28% / +0.31% / +0.33% |
| News burst | 1,838 | +0.017 | -0.002 (-0.1) | +0.012 (+0.3) | +0.015 (+0.3) | +0.008 (+0.1) | +0.45% / +0.30% / +0.33% |
| News volume (unsigned) | 1,867 | +0.004 | +0.012 (+0.5) | +0.031 (+0.8) | +0.044 (+0.8) | +0.065 (+0.9) | +0.50% / +0.15% / +0.32% |

Strategy (long only) vs buy & hold, yearly growth after costs:

| Signal | Hold | All years | 2019-23 | 2024-now | Sharpe (vs B&H) | Time invested | Same rules on the worst news |
|---|---|---|---|---|---|---|---|
| Mood | 1d | +2.6% vs +15.5% | +0.9% vs +10.0% | +5.4% vs +25.0% | 0.38 vs 0.88 | 24% | – |
| Mood | 3d | +3.3% vs +15.5% | +3.1% vs +10.0% | +3.7% vs +25.0% | 0.60 vs 0.88 | 24% | – |
| Mood | 5d | +2.7% vs +15.5% | +1.8% vs +10.0% | +4.2% vs +25.0% | 0.52 vs 0.88 | 24% | – |
| Mood | 10d | +3.2% vs +15.5% | +1.7% vs +10.0% | +5.6% vs +25.0% | 0.64 vs 0.88 | 24% | – |
| Mood vs usual | 1d | +1.8% vs +15.5% | +1.0% vs +10.0% | +3.1% vs +25.0% | 0.30 vs 0.88 | 16% | – |
| Mood vs usual | 3d | +1.8% vs +15.5% | +2.0% vs +10.0% | +1.6% vs +25.0% | 0.40 vs 0.88 | 16% | – |
| Mood vs usual | 5d | +1.8% vs +15.5% | +1.3% vs +10.0% | +2.6% vs +25.0% | 0.43 vs 0.88 | 16% | – |
| Mood vs usual | 10d | +2.6% vs +15.5% | +2.1% vs +10.0% | +3.6% vs +25.0% | 0.68 vs 0.88 | 16% | – |
| News burst | 1d | -0.9% vs +15.5% | +0.6% vs +10.0% | -3.4% vs +25.0% | -0.33 vs 0.88 | 1% | – |
| News burst | 3d | -0.1% vs +15.5% | +0.3% vs +10.0% | -0.9% vs +25.0% | -0.10 vs 0.88 | 1% | – |
| News burst | 5d | +0.1% vs +15.5% | +0.3% vs +10.0% | -0.2% vs +25.0% | 0.16 vs 0.88 | 1% | – |
| News burst | 10d | +0.2% vs +15.5% | +0.1% vs +10.0% | +0.2% vs +25.0% | 0.29 vs 0.88 | 1% | – |

## Silver (SLV), GDELT world news

| Signal | Obs | Reaction | IC 1d (t) | IC 3d (t) | IC 5d (t) | IC 10d (t) | 5d top / bottom / all |
|---|---|---|---|---|---|---|---|
| Mood | 1,837 | -0.018 | +0.015 (+0.6) | -0.014 (-0.3) | +0.013 (+0.2) | +0.046 (+0.6) | +0.41% / +0.41% / +0.48% |
| Mood vs usual | 1,837 | -0.026 | -0.004 (-0.1) | -0.047 (-1.2) | -0.030 (-0.6) | -0.013 (-0.2) | +0.30% / +0.56% / +0.48% |
| News burst | 1,837 | -0.029 | -0.023 (-1.0) | -0.013 (-0.3) | -0.007 (-0.1) | -0.005 (-0.1) | +0.55% / +0.54% / +0.48% |
| News volume (unsigned) | 1,867 | +0.036 | +0.013 (+0.6) | +0.029 (+0.7) | +0.047 (+0.9) | +0.038 (+0.5) | +0.59% / -0.02% / +0.46% |

Strategy (long only) vs buy & hold, yearly growth after costs:

| Signal | Hold | All years | 2019-23 | 2024-now | Sharpe (vs B&H) | Time invested | Same rules on the worst news |
|---|---|---|---|---|---|---|---|
| Mood | 1d | +3.8% vs +17.4% | +4.1% vs +10.7% | +3.2% vs +29.1% | 0.33 vs 0.63 | 22% | – |
| Mood | 3d | +2.2% vs +17.4% | +2.7% vs +10.7% | +1.4% vs +29.1% | 0.26 vs 0.63 | 22% | – |
| Mood | 5d | +1.7% vs +17.4% | +2.4% vs +10.7% | +0.6% vs +29.1% | 0.23 vs 0.63 | 22% | – |
| Mood | 10d | +2.9% vs +17.4% | +3.3% vs +10.7% | +2.1% vs +29.1% | 0.37 vs 0.63 | 22% | – |
| Mood vs usual | 1d | +0.7% vs +17.4% | +1.5% vs +10.7% | -0.7% vs +29.1% | 0.11 vs 0.63 | 16% | – |
| Mood vs usual | 3d | -1.4% vs +17.4% | -0.7% vs +10.7% | -2.4% vs +29.1% | -0.12 vs 0.63 | 16% | – |
| Mood vs usual | 5d | -0.6% vs +17.4% | +0.3% vs +10.7% | -2.1% vs +29.1% | -0.04 vs 0.63 | 16% | – |
| Mood vs usual | 10d | +1.4% vs +17.4% | +1.7% vs +10.7% | +1.0% vs +29.1% | 0.25 vs 0.63 | 16% | – |
| News burst | 1d | -1.4% vs +17.4% | -0.9% vs +10.7% | -2.1% vs +29.1% | -0.15 vs 0.63 | 2% | – |
| News burst | 3d | -0.1% vs +17.4% | +0.4% vs +10.7% | -0.9% vs +29.1% | 0.00 vs 0.63 | 2% | – |
| News burst | 5d | -0.1% vs +17.4% | -0.1% vs +10.7% | -0.1% vs +29.1% | -0.01 vs 0.63 | 2% | – |
| News burst | 10d | +0.8% vs +17.4% | +0.5% vs +10.7% | +1.4% vs +29.1% | 0.41 vs 0.63 | 2% | – |

## Bitcoin, GDELT world news

| Signal | Obs | Reaction | IC 1d (t) | IC 3d (t) | IC 5d (t) | IC 10d (t) | 5d top / bottom / all |
|---|---|---|---|---|---|---|---|
| Mood | 2,479 | +0.048 | +0.010 (+0.5) | -0.006 (-0.2) | -0.016 (-0.3) | -0.009 (-0.1) | +0.83% / +1.32% / +0.62% |
| Mood vs usual | 2,459 | +0.055 | +0.004 (+0.2) | -0.015 (-0.4) | -0.027 (-0.6) | -0.017 (-0.3) | +0.44% / +0.85% / +0.67% |
| News burst | 2,459 | +0.021 | +0.028 (+1.4) | +0.023 (+0.7) | +0.009 (+0.2) | +0.002 (+0.0) | +0.79% / +0.50% / +0.67% |
| News volume (unsigned) | 2,713 | -0.005 | -0.003 (-0.1) | -0.048 (-1.4) | -0.051 (-1.2) | -0.077 (-1.3) | +0.38% / +1.24% / +0.66% |

Strategy (long only) vs buy & hold, yearly growth after costs:

| Signal | Hold | All years | 2019-23 | 2024-now | Sharpe (vs B&H) | Time invested | Same rules on the worst news |
|---|---|---|---|---|---|---|---|
| Mood | 1d | -0.1% vs +34.5% | -6.8% vs +41.6% | +11.7% vs +23.9% | 0.10 vs 0.80 | 19% | – |
| Mood | 3d | +7.3% vs +34.5% | +6.3% vs +41.6% | +9.0% vs +23.9% | 0.50 vs 0.80 | 19% | – |
| Mood | 5d | +6.7% vs +34.5% | +6.1% vs +41.6% | +7.8% vs +23.9% | 0.50 vs 0.80 | 19% | – |
| Mood | 10d | +3.6% vs +34.5% | -0.0% vs +41.6% | +9.8% vs +23.9% | 0.32 vs 0.80 | 19% | – |
| Mood vs usual | 1d | -5.0% vs +34.5% | -8.3% vs +41.6% | +0.8% vs +23.9% | -0.20 vs 0.80 | 13% | – |
| Mood vs usual | 3d | +0.1% vs +34.5% | -2.0% vs +41.6% | +3.4% vs +23.9% | 0.07 vs 0.80 | 13% | – |
| Mood vs usual | 5d | +2.6% vs +34.5% | +0.9% vs +41.6% | +5.5% vs +23.9% | 0.27 vs 0.80 | 13% | – |
| Mood vs usual | 10d | +2.3% vs +34.5% | -0.9% vs +41.6% | +7.7% vs +23.9% | 0.26 vs 0.80 | 13% | – |
| News burst | 1d | +0.7% vs +34.5% | +4.8% vs +41.6% | -5.6% vs +23.9% | 0.12 vs 0.80 | 2% | – |
| News burst | 3d | +2.2% vs +34.5% | +5.5% vs +41.6% | -2.8% vs +23.9% | 0.36 vs 0.80 | 2% | – |
| News burst | 5d | +1.1% vs +34.5% | +3.9% vs +41.6% | -3.2% vs +23.9% | 0.24 vs 0.80 | 2% | – |
| News burst | 10d | +1.4% vs +34.5% | +3.4% vs +41.6% | -1.8% vs +23.9% | 0.30 vs 0.80 | 2% | – |

## S&P 500 (SPY), GDELT world news

| Signal | Obs | Reaction | IC 1d (t) | IC 3d (t) | IC 5d (t) | IC 10d (t) | 5d top / bottom / all |
|---|---|---|---|---|---|---|---|
| Mood | 1,840 | -0.014 | -0.033 (-1.4) | -0.086 (-2.1) | -0.109 (-2.1) | -0.151 (-2.0) | +0.05% / +0.72% / +0.32% |
| Mood vs usual | 1,840 | -0.001 | -0.021 (-0.9) | -0.069 (-1.7) | -0.081 (-1.6) | -0.124 (-1.7) | +0.05% / +0.57% / +0.32% |
| News burst | 1,840 | +0.041 | -0.019 (-0.8) | -0.027 (-0.7) | -0.003 (-0.1) | -0.069 (-0.9) | +0.32% / +0.27% / +0.32% |
| News volume (unsigned) | 1,867 | +0.019 | -0.034 (-1.5) | -0.038 (-0.9) | -0.017 (-0.3) | -0.013 (-0.2) | +0.18% / +0.49% / +0.33% |

Strategy (long only) vs buy & hold, yearly growth after costs:

| Signal | Hold | All years | 2019-23 | 2024-now | Sharpe (vs B&H) | Time invested | Same rules on the worst news |
|---|---|---|---|---|---|---|---|
| Mood | 1d | +0.1% vs +16.3% | +1.5% vs +14.6% | -2.2% vs +19.1% | 0.04 vs 0.87 | 25% | – |
| Mood | 3d | +0.8% vs +16.3% | +1.7% vs +14.6% | -0.8% vs +19.1% | 0.17 vs 0.87 | 25% | – |
| Mood | 5d | +1.1% vs +16.3% | +2.0% vs +14.6% | -0.5% vs +19.1% | 0.23 vs 0.87 | 25% | – |
| Mood | 10d | +2.7% vs +16.3% | +3.5% vs +14.6% | +1.5% vs +19.1% | 0.60 vs 0.87 | 25% | – |
| Mood vs usual | 1d | +0.3% vs +16.3% | +2.2% vs +14.6% | -2.6% vs +19.1% | 0.08 vs 0.87 | 19% | – |
| Mood vs usual | 3d | -0.3% vs +16.3% | +0.3% vs +14.6% | -1.4% vs +19.1% | -0.06 vs 0.87 | 19% | – |
| Mood vs usual | 5d | -0.0% vs +16.3% | +0.3% vs +14.6% | -0.5% vs +19.1% | 0.02 vs 0.87 | 19% | – |
| Mood vs usual | 10d | +1.6% vs +16.3% | +1.8% vs +14.6% | +1.2% vs +19.1% | 0.44 vs 0.87 | 19% | – |
| News burst | 1d | -0.8% vs +16.3% | +0.0% vs +14.6% | -2.1% vs +19.1% | -0.49 vs 0.87 | 2% | – |
| News burst | 3d | -0.2% vs +16.3% | +0.3% vs +14.6% | -1.1% vs +19.1% | -0.17 vs 0.87 | 2% | – |
| News burst | 5d | -0.2% vs +16.3% | +0.1% vs +14.6% | -0.8% vs +19.1% | -0.20 vs 0.87 | 2% | – |
| News burst | 10d | +0.3% vs +16.3% | +0.2% vs +14.6% | +0.3% vs +19.1% | 0.32 vs 0.87 | 2% | – |

## Verdict

- **Stocks (S&P 100, Benzinga headlines)**: same-day reaction up to +0.077; no signal predicts the next days reliably (all |t| < 2); beats buy & hold in both halves: none.
- **Crypto, 11 coins pooled (Benzinga)**: same-day reaction up to +0.344; predictive (|t| ≥ 2, same sign before and after 2024): Mood vs usual 1d (IC -0.025); beats buy & hold in both halves: none.
- **Bitcoin, Benzinga**: same-day reaction up to +0.177; no signal predicts the next days reliably (all |t| < 2); beats buy & hold in both halves: none.
- **Ethereum, Benzinga**: same-day reaction up to +0.211; no signal predicts the next days reliably (all |t| < 2); beats buy & hold in both halves: none.
- **Gold (GLD), Benzinga**: same-day reaction up to +0.040; no signal predicts the next days reliably (all |t| < 2); beats buy & hold in both halves: none.
- **Silver (SLV), Benzinga**: same-day reaction up to +0.103; no signal predicts the next days reliably (all |t| < 2); beats buy & hold in both halves: none.
- **Oil (USO), Benzinga**: same-day reaction up to +0.021; no signal predicts the next days reliably (all |t| < 2); beats buy & hold in both halves: none.
- **Oil (USO), GDELT world news**: same-day reaction up to +0.021; no signal predicts the next days reliably (all |t| < 2); beats buy & hold in both halves: none.
- **Gold (GLD), GDELT world news**: same-day reaction up to -0.017; no signal predicts the next days reliably (all |t| < 2); beats buy & hold in both halves: none.
- **Silver (SLV), GDELT world news**: same-day reaction up to +0.036; no signal predicts the next days reliably (all |t| < 2); beats buy & hold in both halves: none.
- **Bitcoin, GDELT world news**: same-day reaction up to +0.055; no signal predicts the next days reliably (all |t| < 2); beats buy & hold in both halves: none.
- **S&P 500 (SPY), GDELT world news**: same-day reaction up to +0.041; predictive (|t| ≥ 2, same sign before and after 2024): Mood 3d (IC -0.086), Mood 5d (IC -0.109), Mood 10d (IC -0.151); beats buy & hold in both halves: none.
