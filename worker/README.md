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

# Broker link counter (second Worker, optional)

`go-worker.js` sits between the **Brokers** page and the brokers' sites: a click on
"Visit site" opens `https://go.<your-subdomain>.workers.dev/scalable`, the Worker adds one to
that broker's monthly count and sends the visitor on to the link in `docs/data/brokers.json`.
Without it the page links straight to the brokers, so this step can wait.

## Set it up (about 10 minutes, in the browser)

1. **Workers & Pages → Create → Create Worker**, name it `go` → **Deploy** → **Edit code**:
   paste the whole of `worker/go-worker.js` → **Deploy**.
2. **Storage & Databases → KV → Create**, name it `broker-clicks`. Then in the `go` Worker:
   **Settings → Bindings → Add → KV namespace**, variable name `CLICKS`, pick `broker-clicks`.
3. Still in **Settings → Variables and Secrets**, add a **Secret** `STATS_KEY` with any long
   random password (it protects the stats page).
4. If your site isn't at `https://thejackwar.github.io/signal-desk/`, also add a **Text**
   variable `SITE_URL` with its address.
5. Test: open `https://go.<your-subdomain>.workers.dev/trading212`. You should land on
   Trading 212's site.
6. In `docs/index.html`, set `const GO_BASE = "https://go.<your-subdomain>.workers.dev";`
   (search for `GO_BASE`) and push.

Clicks per broker and month: `https://go.<your-subdomain>.workers.dev/stats?key=<STATS_KEY>`.
Counts are approximate (two clicks in the same second can count as one); the affiliate
programs' own dashboards are the real record of sign-ups.

## Adding an affiliate link

Edit only `docs/data/brokers.json`: replace the broker's `url` with your affiliate link and
set `"affiliate": true` (that shows the **Affiliate** tag next to it). The Worker reads the
file from the site, so nothing else needs changing; new links work within about 5 minutes.

# Exact run times (third Worker: the scheduler)

GitHub starts scheduled workflows when it has capacity, often 5-20 minutes late, and now and
then not at all. `scheduler-worker.js` runs every minute on Cloudflare (which is punctual) and
starts each workflow at its time, like pressing **Run workflow** in the Actions tab. The times
are the same as the `cron:` lines in `.github/workflows` (UTC).

## Set it up (about 15 minutes, in the browser)

1. **A GitHub token that can only start workflows in this repository.** On GitHub: your
   profile picture → **Settings → Developer settings → Personal access tokens → Fine-grained
   tokens → Generate new token**.
   - Name: `signal-desk scheduler`. Expiration: the longest offered (note the date).
   - Repository access: **Only select repositories** → `signal-desk`.
   - Permissions → Repository permissions → **Actions: Read and write**. Nothing else.
   - **Generate token** and copy it (GitHub shows it once).
2. **The Worker.** Cloudflare: **Workers & Pages → Create → Create Worker**, name it
   `scheduler` → **Deploy** → **Edit code**: paste the whole of `worker/scheduler-worker.js`
   → **Deploy**.
3. In the Worker: **Settings → Variables and Secrets → Add**, type **Secret**, name
   `GITHUB_TOKEN`, value: the token from step 1.
4. In the Worker: **Settings → Trigger events → Add → Cron Triggers**, expression `* * * * *`
   (every minute) → Add.
5. Check it: open `https://scheduler.<your-subdomain>.workers.dev`. `"token": "works"` and a
   `next` time for every workflow means it's ready.
6. Switch off GitHub's own timer, so nothing runs twice: repository **Settings → Secrets and
   variables → Actions → Variables → New repository variable**, name `EXTERNAL_SCHEDULER`,
   value `on`. GitHub's late runs still appear in the Actions tab, but as *skipped* (grey).

Back to GitHub's timer at any moment: set `EXTERNAL_SCHEDULER` to `off` (or delete it).

To change a time, edit it in `SCHEDULE` near the top of `scheduler-worker.js`, paste the
Worker again, and change the matching `cron:` line in the workflow too (that's the backup).
When the token expires, the page in step 5 says "not working": make a new token (step 1) and
replace the secret (step 3). Each start shows up in the Worker's **Logs** as `started <file>`.
