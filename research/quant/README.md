# Research engine

A stricter backtest of every stock strategy, kept apart from the website. It answers one
question before any strategy goes on (or stays on) the site: **is the edge real, or did it
come from hindsight, free trading or luck?**

```
research/quant/
  universe.py   point-in-time universe: the top 100 S&P 500 members by trading value, each quarter
  data.py       daily prices from Yahoo Finance, cached (cache/ is not committed)
  engine.py     alerts -> daily positions -> account, with the universe check and trading costs
  metrics.py    CAGR, Sharpe, Sortino, worst drop, turnover; bootstrap and deflated Sharpe
  run.py        runs it all, writes results/report.json and results/REPORT.md
  data/sp500_membership.csv   S&P 500 membership history (github.com/fja05680/sp500, MIT)
```

Runs on GitHub Actions (`.github/workflows/research.yml`): on every push to this folder, on the
1st of each month, or by hand from the Actions tab. Offline test: `python research/quant/run.py --demo`.

## What it checks, per strategy

| Check | How | Why |
|---|---|---|
| Survivorship | point-in-time top 100 vs today's S&P 100 list | today's list was picked with hindsight |
| Costs | every buy and sell pays 10 bp; Sharpe also at 0 and 25 bp | high-turnover strategies can look good only for free |
| Hindsight in timing | buy at the next close instead of the alert's close | a signal shouldn't need the exact closing price |
| Beats holding | block bootstrap of Sharpe vs holding the same stocks | "beat SPY" can just mean "held different stocks" |
| Luck across many tries | deflated Sharpe with ~20 strategies tried | with 20 tries some win by chance |
| Out of sample | develop on 2011–2020, judge on 2021–now | the test years were never used to pick anything |
| Picking winners | the site's keep-rule applied on 2011–2020 only, then judged on 2021–now; yearly walk-forward | checks the line-up decision itself |

Sizing: "split" is the app's account (money split equally over open positions, so one lone
position is 100% of it); "capped" holds at most 5% per stock and keeps the rest in cash.

## Known limits

- Yahoo has no prices for most delisted stocks (mostly takeovers). The report shows the coverage.
- Trading value stands in for market cap when ranking the top 100.
- No Italian tax yet (the Strategy Lab has it); costs only.
