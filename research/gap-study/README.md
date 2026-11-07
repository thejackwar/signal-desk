# Gap study — regular-session gaps, S&P 100 + S&P 500, Jan 2021 → 6 Nov 2026

Gap = today's open vs yesterday's regular-session close, every gap ≥ 0.5%. Costs 0.10% round trip.
Script: `gaps.py` · workflow: `.github/workflows/gap-study.yml` · raw results: `results/`.

## Findings

| | S&P 100 | S&P 500 |
|---|---|---|
| Gaps (≥0.5%) | 65,341 | 333,361 |
| Per stock per year | ~110 | ~114 |
| Filled same day | 49% | 53% |
| Filled within 5 days | 77% | 79% |
| Filled within 20 days | 87% | 89% |
| Fade at open (target yesterday's close, else exit at close) — win rate | 60% | 62% |
| Fade — average per trade after costs | −0.09% | −0.07% |
| Follow (open → close) — average after costs | −0.13% | −0.14% |

* **Gaps fill often, but fading them doesn't pay.** About 60% of fades win, but the winners are small
  and the losers bigger. Before costs the edge is +0.03% per trade; after 0.10% it's negative in
  every year 2021–2026.
* **Small gaps fill most** (0.5–1%: ~63% same day, ~93% within 20 days). Gaps of 8%+ almost never
  fill the same day (~5%) and only ~35% within 20 days.
* **After big gaps (2%+) the stock beats the average stock over the next 20 days either way** —
  gap ups keep rising (~+1%) and gap downs bounce (~+1%), in this hindsight universe. Most of that
  is survivorship bias: counting only gaps that happened while the stock was actually in the
  S&P 500 (`research/quant/data/sp500_membership.csv`), it shrinks to about +0.5% either way
  (marginal, and gap-down bounces are still flattered by delisted stocks being missing). Stocks
  that later joined the index (winners by construction) drove the rest (~+4% after gaps).

Verdict: no tradable gap strategy on daily data. Possible follow-ups: earnings gaps only
(post-earnings drift), intraday fade with a stop on 1-minute data, or gap + volume filter.
