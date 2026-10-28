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
  ["topdown.yml",      "2,32 13-21 * * 1-5", "Top-Down alerts: every 30 min while the US market is open"],
  ["scan.yml",         "15 22 * * 1-5",      "Daily scan (S&P 100)"],
  ["commodities.yml",  "40 22 * * 1-5",      "Daily commodities scan"],
  ["crypto.yml",       "20 0 * * *",         "Daily crypto scan"],
  ["news.yml",         "10 * * * *",         "News: every hour"],
  ["mentions.yml",     "20 */6 * * *",       "News mentions: every 6 hours"],
  ["liquidations.yml", "40 */4 * * *",       "BTC liquidation heatmap: every 4 hours"],
  ["calendar.yml",     "37 5 * * *",         "Market calendar (morning)"],
  ["calendar.yml",     "43 21 * * 1-5",      "Market calendar (evening)"],
  ["backtest.yml",     "0 6 * * 6",          "Weekly backtest (Saturday)"],
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
  return SCHEDULE.filter(([, cron]) => cronMatches(cron, d)).map(([wf]) => wf).filter((w, i, a) => a.indexOf(w) === i);
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
async function start(env, wf) {
  const r = await gh(env, `/actions/workflows/${wf}/dispatches`, { method: "POST", body: JSON.stringify({ ref: "main" }) });
  if (r.status !== 204) throw new Error(`${wf}: HTTP ${r.status} ${(await r.text()).slice(0, 200)}`);
}

export default {
  async scheduled(event, env, ctx) {
    if (!env.GITHUB_TOKEN) { console.error("GITHUB_TOKEN secret is missing"); return; }
    const list = due(event.scheduledTime);
    const results = await Promise.allSettled(list.map(wf => start(env, wf)));
    results.forEach((r, k) => r.status === "fulfilled" ? console.log(`started ${list[k]}`) : console.error(String(r.reason)));
  },

  async fetch(req, env) {
    const now = Date.now();
    let token = "missing: add the GITHUB_TOKEN secret";
    if (env.GITHUB_TOKEN) {
      const r = await gh(env, "/actions/workflows");
      const names = r.ok ? (await r.json()).workflows.map(w => w.path.split("/").pop()) : [];
      const missing = [...new Set(SCHEDULE.map(s => s[0]))].filter(w => !names.includes(w));
      token = !r.ok ? `not working (GitHub said HTTP ${r.status})` : missing.length ? `works, but these workflows weren't found: ${missing.join(", ")}` : "works";
    }
    return new Response(JSON.stringify({
      now: new Date(now).toISOString().slice(0, 16).replace("T", " ") + " UTC",
      token,
      schedule: SCHEDULE.map(([wf, cron, what]) => ({ workflow: wf, what, cron_utc: cron, next: nextRun(cron, now) })),
    }, null, 2), { headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
  },
};
