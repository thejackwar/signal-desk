# Signal Desk

A phone-friendly web app that explains nine trading strategies and shows
their alerts on the S&P 100. You pick the strategies you want to follow;
each one has a plain-English explanation, its exact rules, and worked chart
examples. **Information only: it doesn't trade and it isn't investment advice.**

```
docs/                    the website (served by GitHub Pages)
  index.html             the whole app
  data/strategies.json   explanations + example charts   (built by engine/build_examples.py)
  data/alerts.json       the alert feed                   (written by engine/scan.py)
  data/charts/           one chart file per alert
  data/backtest.json     backtest results                 (written by engine/backtest.py)
  data/stocks.json       stock list + market health       (written by engine/scan.py)
  data/stocks/           one-year chart per stock
engine/
  strategies.py          the 9 strategy detectors (shared by scanner and examples)
  scan.py                daily scanner
  backtest.py            15-year backtest of every strategy
  build_examples.py      builds the example charts
  strategies_content.json  the written explanations (edit text here)
  tickers.txt            the stock list
  names.json             company names and sectors
.github/workflows/scan.yml      runs the scan every weekday after the US close
.github/workflows/backtest.yml  reruns the backtest every Saturday
```

## Put it online (about 10 minutes, free)

1. **Create a repository** on GitHub (e.g. `signal-desk`) and upload everything in this folder,
   keeping the folder structure, including the hidden `.github` folder.
2. **Turn on the website:** repository *Settings → Pages* → Source: *Deploy from a branch*,
   branch `main`, folder `/docs` → Save. After a minute your app lives at
   `https://<your-username>.github.io/signal-desk/`.
3. **Run the first real scan:** *Actions* tab → *Daily scan* → *Run workflow*.
   This replaces the demo alerts with real S&P 100 alerts from the last 15 sessions.
   From then on it runs by itself every weekday at 22:15 UTC.
   Then run *Weekly backtest* the same way (takes a few minutes) to replace the demo
   backtest with 15 years of real S&P 100 results. It reruns every Saturday.
4. **On your iPhone:** open the address in Safari → Share → *Add to Home Screen*.

> If the Actions tab says workflows are disabled, click the button to enable them.
> If the push step fails with a permissions error: *Settings → Actions → General →
> Workflow permissions* → *Read and write permissions*.

## Phone notifications (optional)

Alerts always show in the app. To also get a push notification:

1. *Settings → Secrets and variables → Actions → Variables* → New variable
   `NTFY_PREFIX` with a hard-to-guess value, e.g. `giacomo-desk-7f3k9`.
   Anyone who knows the name can read the channel, so don't use something obvious.
2. Install **ntfy** from the App Store.
3. Open the app's *Phone alerts* tab: it lists one channel per strategy you follow.
   Subscribe to them in ntfy.

## Signal strength

Every alert gets a score from 0 to 4, using only what was known on the alert day:
+1 if it goes with the long-term trend (price above the 200-day average for bullish
alerts, below for bearish), +1 or +2 if other strategies fired the same way on the same
stock in the last 4 sessions, −1 if any fired the opposite way, and +1 if the strategy's
alerts beat a random day in the backtest. 0–1 = Weak, 2 = Moderate, 3–4 = Strong.

## How the backtest works

For every past alert it checks whether the price moved the expected way 5, 20 and 60
sessions later, and compares that with a random day in the same stocks. It also simulates
EUR 10,000 following the bullish alerts (trend strategies hold until the opposite alert,
the others for 20 sessions), split equally across open positions, against holding all the
stocks equally. No costs, taxes or slippage, and it uses today's S&P 100 list, so
companies that dropped out of the index are missing. Treat it as a rough guide.

## Change things

- **Stocks:** edit `engine/tickers.txt` (Yahoo Finance symbols).
- **Explanations:** edit `engine/strategies_content.json`, then run
  `python engine/build_examples.py` and upload the new `docs/data/strategies.json`.
- **Rules / parameters:** each strategy is one function in `engine/strategies.py`.
  Rebuild the examples afterwards so they match.
- **Test locally without internet:** `python engine/scan.py --demo` (and
  `python engine/backtest.py --demo`) write alerts from
  simulated prices on made-up tickers. Then `python -m http.server -d docs` and open
  http://localhost:8000.

## Moving to a native iPhone app later

Everything in `engine/` and `docs/data/` stays as it is. A SwiftUI app would read the
same `alerts.json`, `strategies.json` and chart files from the GitHub Pages address;
only `index.html` gets replaced by Swift screens.
