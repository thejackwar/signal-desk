// Cloudflare Worker: an exact clock for the GitHub workflows.
// GitHub's own schedules often start 5-20 minutes late or get skipped. This Worker runs every
// minute (one Cron Trigger: "* * * * *"), and when a workflow is due it starts it straight away,
// exactly like pressing "Run workflow" in the Actions tab.
//
// Secret:   GITHUB_TOKEN  a fine-grained token for thejackwar/signal-desk with Actions: Read and write
// Optional: REPO          defaults to thejackwar/signal-desk
// GET /     shows the schedule, the next start of each workflow and whether the token works.
//
// Times are UTC, the same as the cron lines in .github/workflows (5 fields: minute hour
// day-of-month month day-of-week, Sunday = 0). Change a time here, then paste the Worker again.

const SCHEDULE = [
  // [workflow file, cron (UTC), what it is, optional inputs for "Run workflow"]
  // US market hours (wide enough for summer and winter time; the scripts exit when the market is closed)
  ["gex.yml",              "0 13 * * 1-5",         "Gamma levels (before the open)", { job: "daily" }],
  ["gex.yml",              "30 7 * * 1-5",         "Gamma levels (European morning: last session's open interest)", { job: "intraday" }],
  ["gex.yml",              "15,45 14-20 * * 1-5",  "Gamma levels at the live price: every 30 min while the US market is open", { job: "intraday" }],
  ["maxpain.yml",          "20 13 * * 1-5",        "Max pain (backup, in case the gamma levels didn't run)"],
  ["gamma-setup.yml",      "1-59/5 13-21 * * 1-5", "Gamma rejection setup: every 5 min while the US market is open", { job: "live" }],
  ["session-momentum.yml", "2-59/5 13-21 * * 1-5", "Session Momentum (QQQ first 5-min candle): every 5 min while the US market is open", { job: "live" }],
  ["premarket-live.yml",   "25 13,14 * * 1-5",     "Premarket Momentum: morning scan at 09:25 New York (phone alert with the picks)"],
  ["premarket-live.yml",   "29 14,15 * * 1-5",     "Premarket Momentum: 'sell at 10:30' reminder"],
  ["premarket-live.yml",   "50 14,15 * * 1-5",     "Premarket Momentum: settle the paper trades (10:50 New York)"],
  ["choch.yml",            "37 17,18 * * 1-5",     "4H CHoCH stocks: after the 09:30-13:30 candle", { job: "stocks" }],
  ["choch.yml",            "7 20,21 * * 1-5",      "4H CHoCH stocks: after the 13:30-16:00 candle", { job: "stocks" }],
  ["topdown.yml",          "2,32 13-21 * * 1-5",   "Top-Down alerts: every 30 min while the US market is open"],
  ["live-stocks.yml",      "5,35 13-20 * * 1-5",   "Live stock scan (provisional): every 30 min while the US market is open"],
  ["darkpool-blocks.yml",  "6,36 13-21 * * 1-5",   "Dark pool block prints: every 30 min while the US market is open"],
  ["live-patterns.yml",    "9,39 13-20 * * 1-5",   "Live chart patterns (stocks): every 30 min while the US market is open", { job: "live" }],
  ["live-patterns.yml",    "39 21 * * 1-5",        "Live chart patterns: after the close in winter time (daily candles)", { job: "live" }],
  ["correlation.yml",      "45 19 * * 1-5",        "Implied correlation (near the close)"],
  // after the close
  ["calendar.yml",         "43 21 * * 1-5",        "Market calendar (evening)"],
  ["scan.yml",             "15 22 * * 1-5",        "Daily scan (S&P 100)"],
  ["commodities.yml",      "40 22 * * 1-5",        "Daily commodities scan"],
  ["darkpool-daily.yml",   "15 23 * * 1-5",        "Dark pool daily pressure + weekly venues (after FINRA publishes)"],
  ["company.yml",          "50 22 * * 1-5",        "Company data for the Terminal (financials, analysts, holders, dividends)"],
  // commodities and crypto
  ["live-commodities.yml", "50 7-21 * * 1-5",      "Live commodities scan (provisional): every hour on weekdays"],
  ["crypto.yml",           "20 0 * * *",           "Daily crypto scan"],
  ["live-crypto.yml",      "25 3-23/4 * * *",      "Live crypto scan (provisional): every 4 hours"],
  ["topdown-crypto.yml",   "7,37 * * * *",         "Top-Down crypto alerts: every 30 min, every day"],
  ["choch.yml",            "7 */4 * * *",          "4H CHoCH bitcoin: after every 4H candle", { job: "btc" }],
  ["btc-dip-forward.yml",  "7 * * * *",            "BTC dip forward test: every hour"],
  ["liquidations.yml",     "40 */4 * * *",         "BTC liquidation heatmap: every 4 hours"],
  // news and calendar
  ["news.yml",             "10,30,50 * * * *",     "News: every 20 minutes"],
  ["mentions.yml",         "20 */6 * * *",         "News mentions: every 6 hours"],
  ["news-market.yml",      "17 5 * * *",           "Market news tone (morning)"],
  ["calendar.yml",         "37 5 * * *",           "Market calendar (morning)"],
  // Terminal market boards (indices, FX, yields, Fed odds, futures curves, COT)
  ["markets.yml",          "22 6-21 * * 1-5",      "Terminal market boards: every hour on weekdays"],
  ["markets.yml",          "22 9 * * 0,6",         "Terminal market boards (weekend)"],
  // weekly and monthly
  ["backtest.yml",         "0 6 * * 6",            "Weekly backtest (Saturday)"],
  ["options.yml",          "30 6 * * 6",           "Options backtests (Saturday, after the weekly backtest)"],
  ["pairs.yml",            "45 6 * * 6",           "Pairs trading (Saturday)"],
  ["gamma-setup.yml",      "15 7 * * 6",           "Gamma setup backtest (Saturday)", { job: "backtest" }],
  ["session-momentum.yml", "35 7 * * 6",           "Session Momentum backtest (Saturday)", { job: "backtest" }],
  ["choch.yml",            "55 7 * * 6",           "4H CHoCH backtest (Saturday)", { job: "backtest" }],
  ["live-patterns.yml",    "20 8 * * 6",           "Live chart patterns: win rates backtest (Saturday)", { job: "stats" }],
  ["sp500-list.yml",       "13 5 1 * *",           "S&P 500 list (first day of each month)"],
  ["research.yml",         "30 7 1 * *",           "Research engine (first day of each month)"],
];

// ------------------------------------------------------------- cron matching
const RANGES = [[0, 59], [0, 23], [1, 31], [1, 12], [0, 7]];
function field(spec, [lo, hi]) {
  const ok = new Set();
  for (const part of spec.split(",")) {
    const [range, stepTxt] = part.split("/");
    const step = stepTxt ? parseInt(stepTxt, 10) : 1;
    let a, b;
    if (range === "*") [a, b] = [lo, hi];
    else if (range.includes("-")) [a, b] = range.split("-").map(Number);
    else { a = Number(range); b = stepTxt ? hi : a; }
    if (![a, b, step].every(Number.isInteger) || step < 1 || a < lo || b > hi || a > b) throw new Error(`bad cron field "${spec}"`);
    for (let v = a; v <= b; v += step) ok.add(v);
  }
  return ok;
}
export function cronMatches(expr, d) {
  const f = expr.trim().split(/\s+/);
  if (f.length !== 5) throw new Error(`cron needs 5 fields: "${expr}"`);
  const [mi, ho, dom, mo, dow] = f.map((s, k) => field(s, RANGES[k]));
  if (dow.has(7)) dow.add(0);                                    // 7 = Sunday too
  if (!mi.has(d.getUTCMinutes()) || !ho.has(d.getUTCHours()) || !mo.has(d.getUTCMonth() + 1)) return false;
  const domAny = f[2] === "*", dowAny = f[4] === "*";
  const domOk = dom.has(d.getUTCDate()), dowOk = dow.has(d.getUTCDay());
  return domAny && dowAny ? true : domAny ? dowOk : dowAny ? domOk : domOk || dowOk;   // standard cron rule
}
export function due(date) {
  const d = new Date(date); d.setUTCSeconds(0, 0);
  const seen = new Set();
  return SCHEDULE.filter(([, cron]) => cronMatches(cron, d))
    .map(([wf, , , inputs]) => ({ wf, inputs }))
    .filter(r => { const k = r.wf + JSON.stringify(r.inputs || {}); return !seen.has(k) && seen.add(k); });
}
function nextRun(cron, from) {
  const d = new Date(from); d.setUTCSeconds(0, 0);
  for (let k = 1; k <= 8 * 24 * 60; k++) {                       // look up to 8 days ahead
    d.setUTCMinutes(d.getUTCMinutes() + 1);
    if (cronMatches(cron, d)) return d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
  }
  return null;
}

// ------------------------------------------------------------- GitHub
const gh = (env, path, init = {}) => fetch(`https://api.github.com/repos/${env.REPO || "thejackwar/signal-desk"}${path}`, {
  ...init,
  headers: { Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "signal-desk-scheduler", ...(init.headers || {}) },
});
async function start(env, { wf, inputs }) {
  const body = inputs ? { ref: "main", inputs } : { ref: "main" };
  const r = await gh(env, `/actions/workflows/${wf}/dispatches`, { method: "POST", body: JSON.stringify(body) });
  if (r.status !== 204) throw new Error(`${wf}: HTTP ${r.status} ${(await r.text()).slice(0, 200)}`);
}

export default {
  async scheduled(event, env, ctx) {
    if (!env.GITHUB_TOKEN) { console.error("GITHUB_TOKEN secret is missing"); return; }
    const list = due(event.scheduledTime);
    const results = await Promise.allSettled(list.map(wf => start(env, wf)));
    results.forEach((r, k) => r.status === "fulfilled"
      ? console.log(`started ${list[k].wf}${list[k].inputs ? " " + JSON.stringify(list[k].inputs) : ""}`)
      : console.error(String(r.reason)));
  },

  async fetch(req, env) {
    const now = Date.now();
    let token = "missing: add the GITHUB_TOKEN secret";
    if (env.GITHUB_TOKEN) {
      const r = await gh(env, "/actions/workflows?per_page=100");
      const names = r.ok ? (await r.json()).workflows.map(w => w.path.split("/").pop()) : [];
      const missing = [...new Set(SCHEDULE.map(s => s[0]))].filter(w => !names.includes(w));
      token = !r.ok ? `not working (GitHub said HTTP ${r.status})` : missing.length ? `works, but these workflows weren't found: ${missing.join(", ")}` : "works";
    }
    return new Response(JSON.stringify({
      now: new Date(now).toISOString().slice(0, 16).replace("T", " ") + " UTC",
      token,
      schedule: SCHEDULE.map(([wf, cron, what, inputs]) => ({ workflow: wf, what, cron_utc: cron, ...(inputs ? { inputs } : {}), next: nextRun(cron, now) })),
    }, null, 2), { headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
  },
};
