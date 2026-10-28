# Trendlines data server (Cloudflare Worker)

`docs/trendlines.html` needs Alpaca candles, but the site is static (GitHub Pages) and the
Alpaca keys must never be in the browser. This tiny Worker holds the keys and hands the page
the candles. Free plan: 100,000 requests a day; one ticker = one request.

## Set it up (about 10 minutes, in the browser, no installs)

1. Sign up at **dash.cloudflare.com** (free).
2. **Workers & Pages → Create → Create Worker** (the "Hello World" starter). Name it
   `trendlines` → **Deploy**.
3. **Edit code**: delete everything, paste the whole of `worker/trendlines-worker.js` → **Deploy**.
4. The Worker's **Settings → Variables and Secrets → Add**, type **Secret**, twice:
   - `ALPACA_KEY_ID` = your Alpaca key ID
   - `ALPACA_SECRET_KEY` = your Alpaca secret key

   (The same keys as the `sp500-bot` repository's Actions secrets.) Save / deploy.
5. Test it: open `https://trendlines.<your-subdomain>.workers.dev/?symbol=SPY`.
   You should see a long JSON text starting with `{"symbol":"SPY"`.
   An `"error"` there tells you what's wrong (e.g. keys not set).
6. Open the site's **Trendlines** page → **Settings → Data server** → paste
   `https://trendlines.<your-subdomain>.workers.dev`. It's remembered on that device.
   To make it the default on every device, put the address in `DEFAULT_API` near the top of the
   script in `docs/trendlines.html`.

## What it does

`GET /?symbol=AAPL` fetches 5 Alpaca series in parallel (`1Week` 8 years, `1Day` 460 days,
`30Min` 330 days, `15Min` 95 days, `5Min` 35 days; `feed=sip`, `adjustment=all`,
end = now − 16 min), keeps regular hours (9:30–16:00 New York) for intraday bars, and returns
`[time, open, high, low, close]` rows with New York times. The page builds 1H and 4H from the
30-min bars and draws the lines itself (`docs/trendlines.js`). Only S&P 100 stocks and SPY are
accepted (SPX → SPY, BRK.B → BRK-B). Answers are cached for a minute.

If you change the code, paste it again in step 3. Tests: `python tests/trendlines/test_parity.py`.
