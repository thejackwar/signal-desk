# Core vs experimental strategies (2 Nov 2026)

The app now shows a short list of **core** strategies per market and folds the rest away
under **Experimental**. Experimental strategies still send alerts to anyone who follows them
and can all be tested in the Strategy Lab. The list is `CORE` in `docs/index.html`.

## How they were picked

Two sources, both already on the site:

- **15-year backtest** (`docs/data/backtest.json`, 2011–2026, today's S&P 100):
  yearly growth (CAGR) of the strategy's account vs buying and holding the benchmark,
  and the 20-day "edge" of buy alerts over a random day (hit rate and average move).
- **Live track record** (`docs/data/history.json`, alerts since 11 Sep 2026, checked
  20 sessions later). Small sample (7 weeks), so it can only veto, not promote.

A strategy is core when it beats or roughly matches buy-and-hold in the backtest *and*
its buy alerts beat a random day there, without a clearly bad live record.

## Stocks (benchmark SPY: 18.4% a year)

| Strategy | Backtest CAGR | Buy-alert edge, 20d (hit / avg) | Live buy alerts (n / right / avg) | Verdict |
|---|---|---|---|---|
| Pattern Trader | 22.0% | +3.9 pts / +0.83% | 6 / 67% / +2.66% | core |
| Buy the Dip | 20.3% | +1.6 / +0.30% | 68 / 62% / +3.40% | core |
| Bollinger Band Reversion | 19.9% | +3.4 / +0.45% | 81 / 57% / +1.90% | core |
| Head & Shoulders | 18.2% | +2.9 / +0.48% | 2 / 100% / +13.2% | core |
| MACD Crossover | 16.1% | +1.6 / +0.05% | 46 / 80% / +3.01% | core |
| RSI | 16.0% | +4.0 / +0.43% | 40 / 50% / +0.84% | core |
| Deck Patterns | 15.9% | +2.9 / +0.40% | 21 / 57% / +1.68% | core (one deck variant only) |
| 200-Day Trend Filter | 17.3% | +1.4 / −0.08% | 22 / 55% / −0.67% | experimental |
| Donchian Breakout | 16.9% | +0.2 / −0.34% | 39 / 56% / −2.86% | experimental |
| Golden / Death Cross | 17.3% | −0.3 / −0.59% | 4 / 50% / −3.08% | experimental |
| Volume Breakout | 17.4% | +0.1 / −0.05% | 8 / 50% / −3.87% | experimental |
| Relative Strength | 16.6% | +0.8 / −0.20% | 2 / 0% / −8.87% | experimental |
| Supertrend | 15.3% | +1.5 / +0.01% | 7 / 71% / +2.45% | experimental |
| Double Top / Bottom | 14.6% | +1.2 / −0.23% | 1 | experimental |
| Triangle Breakout | 16.1% | +1.7 / −0.16% | 1 | experimental |
| Deck (Live Rules / +50-Day / Break-Even) | 14–16% | +1.4 to +2.3 | mixed | experimental (duplicates) |
| Breakout Bot | 12.7% | +0.9 / −0.10% | 5 / 40% / −5.12% | experimental |

Top-Down Analysis (trendlines) stays core: it's a different kind of tool with no comparable numbers yet.

Live result of the core list: buy alerts 62% right, +2.41% on average after 20 sessions
(264 alerts), vs 49% / −0.69% for every alert of every strategy on both sides.

## The bigger finding: bearish stock alerts

Live, bullish stock alerts were right 60% of the time (+1.23% average, 396 alerts) while
bearish ones were right 40% (−2.26%, 482 alerts). In the backtest, short trades on stocks
have a profit factor of 0.28 against 2.83 for longs. Bearish alerts are most of what
pulls the overall record below a random day. Worth considering next: show stock alerts
long-only by default, like the Strategy Lab and Best trades already do.

## Crypto (benchmark: holding the coins, 74.5% a year) and commodities (6.1% a year)

Crypto core: MACD (92%), Volume Breakout (85%), Supertrend (78%), Donchian (73%),
Above the 50 & 200-Day (54%, profit factor 3.6). Most others trail buy-and-hold by a lot.
Live crypto samples are too small to judge.

Commodities core: RSI (11.8%), Bollinger (9.8%), Buy the Dip (6.2%). The rest trail the benchmark.

## Caveat

The backtest runs on today's index members, so companies that dropped out are missing
(survivorship bias): every number above is flattered, the long side most. The home page
says so next to the backtest figure. Recheck this list after a few more months of live record.
