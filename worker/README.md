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
`[time, open, high, low, close, volume]` rows with New York times (volume added for the Chart page; older pages ignore it). The page builds 1H and 4H from the
30-min bars and draws the lines itself (`docs/trendlines.js`). Only S&P 100 stocks and SPY are
accepted (SPX → SPY, BRK.B → BRK-B). Answers are cached for a minute.

**Crypto:** `GET /?symbol=BTC-USD` (or `BTC`, `BTCUSD`) works for the 19 coins in
`engine/crypto.txt`. Crypto trades around the clock, so the Worker fetches all seven timeframes
directly (weekly to 5-minute, times in UTC) from Binance's public market data (coin/USDT, no keys).
If Binance doesn't list a coin (Monero) or can't be reached, it uses Kraken (coin/USD, last 720
candles per timeframe). No extra secrets are needed.

**Options:** `GET /?options=AAPL` returns the next four monthly option chains (strikes within 35% of
the price) from Alpaca's free option snapshots, for `docs/options.html`. Same keys, nothing to add:
just paste the updated code (step 3) once.

**Volume profile:** `GET /?vp=AAPL&range=5&hours=rth` returns intraday bars *with volume* for
`docs/volumeprofile.html`: the last 1, 5, 20, 60 or 250 sessions (1-, 1-, 5-, 15- and 30-minute bars), from Alpaca's
full-market SIP feed (`feed=sip`, end = now − 16 min, so the free plan works), regular hours (`hours=rth`) or 4:00–20:00
(`hours=all`). Coins come from Binance with their dollar (USDT) volume, Kraken as a fallback. Same keys, nothing to add:
just paste the updated code (step 3) once. Test: `node tests/volumeprofile/test_vp.mjs`.

**Live quotes and price alerts (Terminal):** `GET /?quotes=stocks` (Alpaca snapshots, free IEX real-time feed) and
`GET /?quotes=crypto` (Binance, Kraken for XMR) give the Terminal live prices, cached for 15 seconds. Price alerts need
two extra things on this same Worker, once:

1. **Storage:** Workers & Pages → **KV** → **Create namespace**, name it `px`. Then the Worker's **Settings → Bindings →
   Add → KV namespace**: variable name `PX`, namespace `px`.
2. **Clock:** the Worker's **Settings → Triggers → Cron Triggers → Add**: `*/5 * * * *` (every 5 minutes).
3. Paste the updated code (step 3) and deploy.

Each viewer gets their own random ntfy channel on the Terminal (shown in the price alerts panel); the Worker checks
every alert every 5 minutes and sends a notification to that channel when the price crosses the level, then removes
the alert. Free plan limits (KV: 1,000 writes and 100,000 reads a day) are far above what this uses.

If you change the code, paste it again in step 3. Tests: `python tests/trendlines/test_parity.py`,
`node tests/trendlines/test_crypto.mjs`, `node tests/trendlines/test_sr.mjs`.

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

To change a time, edit it in `SCHEDULE` near the top of `scheduler-worker.js` and change the matching
`cron:` line in the workflow too (that's the backup). `.github/workflows/deploy-scheduler.yml` deploys the
Worker on every change (same Cloudflare secrets as the data Worker below), so there's nothing to paste.
A new scheduled workflow needs a line in `SCHEDULE` too (and the `EXTERNAL_SCHEDULER` line
under its job), otherwise only GitHub's late timer starts it.
When the token expires, the page in step 5 says "not working": make a new token (step 1) and
replace the secret (step 3). Each start shows up in the Worker's **Logs** as `started <file>`.

## Automatic deploys of the data Worker

`.github/workflows/deploy-worker.yml` deploys `trendlines-worker.js` to the `trendlines` Worker on every
change, so it never has to be pasted by hand. One-time setup:

1. Cloudflare: **My Profile → API Tokens → Create Token → "Edit Cloudflare Workers" template**
   (account: yours, zone: all zones) → Create, and copy the token.
2. Cloudflare: copy your **Account ID** (Workers & Pages overview, right-hand side).
3. GitHub: repository **Settings → Secrets and variables → Actions → New repository secret**, twice:
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.
4. GitHub: **Actions → Deploy data Worker → Run workflow** for the first deploy.

The Alpaca secrets, the `PX` storage and the 5-minute timer set up above are kept.

## Accounts (docs/account.js, Supabase)

Log-in is email + password, invite-only; what the site saves on a device (drawings, layouts, watchlists,
price-alert topic, practice portfolio, page settings) is copied to the account and shows on every device.

1. supabase.com → new project (region Central EU / Frankfurt, Free plan).
2. Authentication → Sign In / Providers: Email on; **Allow new users to sign up: off**.
3. Authentication → URL Configuration: Site URL `https://thejackwar.github.io/signal-desk/`.
4. SQL Editor → run:

```sql
create table public.user_data (
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  key text not null,
  value text,
  updated_at timestamptz not null default now(),
  primary key (user_id, key)
);
alter table public.user_data enable row level security;
create policy "own rows only" on public.user_data for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
```

5. Project Settings → API: put the Project URL and the anon/publishable key in `docs/account.js`
   (both public by design; the service_role key never goes on the site).
6. Invite people: Authentication → Users → Add user → Send invitation. The link lets them set a password.
