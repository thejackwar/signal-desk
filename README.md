# Investment Center Signals

A phone-friendly web app that explains twelve trading strategies and shows
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
  rating.js              technical rating (Strong sell … Strong buy) on each stock page; test: tests/rating/test_rating.py
  trendlines.html/.js    automatic trendlines + support/resistance, stocks and crypto (data from worker/)
  data/calendar.json     upcoming earnings, IPOs, economy (written by engine/events.py)
  data/topdown/          Top-Down alerts (written by engine/topdown.py)
  data/brokers.json      Brokers page: facts and links (edit by hand; affiliate links go here)
  data/ucits.json        EU-buyable versions of SPY/QQQ and an ETC per commodity (edit by hand)
  data/history.json      every alert ever sent, graded 5/20/60 sessions later (written by engine/scan.py;
                         the Track record page). Crypto and commodities keep their own in their folders.
worker/                  Cloudflare Workers: trendlines data, counted broker links (see worker/README.md)
engine/
  strategies.py          the 12 strategy detectors (shared by scanner and examples)
  scan.py                daily scanner
  backtest.py            15-year backtest of every strategy
  build_examples.py      builds the example charts
  strategies_content.json  the written explanations (edit text here)
  tickers.txt            the stock list
                         (Relative Strength compares with SPY for stocks, bitcoin for crypto and
                         the equal-weight average for commodities: see use_benchmark in scan.py)
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

## Crypto

The app has a Stocks / Crypto switch. Crypto uses the same twelve strategies on the coins
in `engine/crypto.txt` (names in `engine/crypto_names.json`) and writes everything to
`docs/data/crypto/`. The *Daily crypto scan* workflow runs every day at 00:20 UTC, just
after the daily candle closes, weekends included. The weekly backtest covers both markets.
Phone notification channels for crypto are `<NTFY_PREFIX>-crypto-<strategy>`.

## Commodities

The third switch, *Commodities*, runs the same twelve strategies on 19 front-month futures in
`engine/commodities.txt` (gold, silver, platinum, palladium, copper, WTI and Brent crude, natural gas,
gasoline, heating oil, corn, wheat, soybeans, coffee, cocoa, sugar, cotton, live cattle, lean hogs).
`engine/commodities_names.json` holds each one's name, sector, contract unit, exchange and a line on
what moves its price. Data goes to `docs/data/commodities/`; the *Daily commodities scan* workflow
runs Monday to Friday at 22:40 UTC, after the futures settle. Phone channels are
`<NTFY_PREFIX>-commodities-<strategy>`.

The page has its own look tied to what's traded: each commodity is drawn as the physical good
(ingots for metals, drums for energy, sacks for grains, beans for softs, ear tags for livestock),
a *pit* board groups them by sector, and a card tracks the gold/silver, copper/gold and Brent/WTI
ratios. Yahoo's front-month prices are stitched from successive contracts, so the charts and the
backtest include small jumps on roll days.

## BTC/USDT liquidation heatmap

`engine/liquidations.py` estimates where leveraged BTC futures positions would be
liquidated (the *BTC liquidation heatmap* workflow, every 4 hours). For every candle it
takes the rise in open interest as new positions, spreads them over 5×–100× leverage and
keeps them on the map until price reaches their liquidation level; when open interest
falls, that share is removed. Two maps: 24 hours from 15-minute candles (fine price
steps) and 30 days from hourly candles. Data, in order of preference: Binance BTCUSDT
(blocked from GitHub's US servers), OKX BTC-USDT perpetual, Yahoo BTC-USD (volume as a
stand-in when open interest isn't available). It's a model, like the well-known heatmap
sites, not exchange data.

## Trendlines

`docs/trendlines.html` (side menu → *Trendlines*, or the button on any stock page): type an
S&P 100 ticker or SPY and get W, D, 4H, 1H, 30m, 15m and 5m charts with the trendlines drawn
automatically, plus a table of the line levels and the bias per timeframe. Same rules as
`trendlines.py` in the sp500-bot repo (`docs/trendlines.js` is a line-for-line port;
`tests/trendlines/test_parity.py` checks both give identical lines). The candles come from Alpaca
through a small free Cloudflare Worker that keeps the keys off the website: setup in
`worker/README.md`.

## News

`engine/news.py` collects headlines every hour (the *News* workflow) from public RSS feeds:
CNBC, MarketWatch, Investing.com, Nasdaq, Seeking Alpha and Yahoo Finance for stocks;
CoinDesk, Cointelegraph, Decrypt, The Block, Bitcoin Magazine and CryptoSlate for crypto.
It keeps the headline, source, time, a short summary and a link to the full article, and
tags the stocks or coins a headline mentions. Edit `FEEDS` in the file to change sources.

## Calendar

`engine/events.py` (the *Market calendar* workflow, at 05:37 UTC every day and 21:43 UTC on weekdays)
builds `docs/data/calendar.json`: earnings dates from two weeks back to three months ahead for US companies
worth $2 billion or more, upcoming and recently priced IPOs on US exchanges, and the main US economic
releases (CPI, PCE, PPI, jobs report, GDP, retail sales, ISM, consumer sentiment, Fed decisions). Sources:
Yahoo Finance's earnings, IPO and economic calendars, each S&P 100 stock's own Yahoo page (for analysts'
EPS and revenue estimates), Nasdaq's IPO calendar, and Nasdaq's earnings calendar as a fallback. The FOMC
dates are typed into `FOMC` in the file: add next year's when the Fed publishes them. If every source
fails, the previous file is kept.

In the app: side menu → *Calendar* (on the phone, *News* → *Calendar*). A month view, Monday to Friday,
with each day's events, or a list of the next three weeks; filter by type and by which companies
(S&P 100, $10B+, all). Past earnings show the reported EPS and whether it beat or missed the estimate.
Each stock's page shows its next earnings date. `python engine/events.py --demo` writes made-up data.

## News mentions

`engine/mentions.py` (the *News mentions* workflow, daily) counts news articles that
mention each stock and coin, using Google News search. Google returns at most 100 results
per search, so for busy names the window narrows from 30 days to 7 days, 24 hours or 4
hours (scaled up and marked "~"). Every run stores that day's count, so the comparison
with a usual day builds up over time.

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

## Top-Down Analysis alerts (trendline breaks, every 30 minutes)

`engine/topdown.py` checks the Trendlines page's lines on all 7 timeframes for every S&P 100
stock and SPY, every 30 minutes while the US market is open (`.github/workflows/topdown.yml`),
and writes new breaks to `docs/data/topdown/alerts.json`. The app shows them under the
**Top-Down Analysis** strategy; each viewer picks the timeframes (W, D, 4H, 1H by default).
Phone notifications go to one ntfy channel per timeframe: `<NTFY_PREFIX>-topdown-1h` etc.

Switch it on once: repository *Settings → Secrets and variables → Actions → Secrets* → add
`ALPACA_KEY_ID` and `ALPACA_SECRET_KEY` (the same Alpaca keys as the Trendlines Worker). Until
then the workflow just skips. Try it without keys: `python engine/topdown.py --demo --force`.
Tests: `python tests/topdown/test_rules.py` (same lines as the Trendlines page, no line alerted twice).

## License

All rights reserved: the code is public to read, but not to reuse. See [LICENSE](LICENSE).

## Link previews and visitor stats

`docs/og-image.png` is the picture shown when the site's link is shared (WhatsApp, LinkedIn,
Reddit…); the tags at the top of `docs/index.html` and `docs/trendlines.html` point to it with
the full site address, so update those if the site moves to its own domain.
Visitor stats: in Cloudflare open *Analytics & Logs → Web Analytics → Add a site*, enter the
site's address, choose the JavaScript snippet and copy only its token into
`CF_ANALYTICS_TOKEN` near the top of both pages. Free, no cookies, no banner needed.
