// Cloudflare Worker: counted redirects for the broker links on the Brokers page.
// GET /<broker id>            -> 302 to that broker's "url" in docs/data/brokers.json, +1 click
// GET /stats?key=<STATS_KEY>  -> click counts per broker and month, as JSON
//
// The links are read from the live site's brokers.json, so that file stays the only place to
// edit them. Only URLs listed there are ever redirected to (no open redirect).
// Optional bindings: KV namespace CLICKS (without it, links still redirect, just uncounted),
// secret STATS_KEY (without it, /stats is off), variable SITE_URL (defaults to the site below).

const DEFAULT_SITE = "https://thejackwar.github.io/signal-desk/";

async function loadBrokers(env) {
  const site = (env.SITE_URL || DEFAULT_SITE).replace(/\/?$/, "/");
  const r = await fetch(site + "data/brokers.json", { cf: { cacheTtl: 300, cacheEverything: true } });
  if (!r.ok) throw new Error(`brokers.json: HTTP ${r.status}`);
  const js = await r.json();
  // brokers.json has sections (stocks, crypto), each with its own list; older files had one flat list
  const brokers = Array.isArray(js.sections) ? js.sections.flatMap(x => x.brokers || []) : Array.isArray(js.brokers) ? js.brokers : [];
  return { site, brokers };
}

async function count(env, id) {
  const key = `${id}:${new Date().toISOString().slice(0, 7)}`;          // e.g. scalable:2026-11
  const n = parseInt(await env.CLICKS.get(key), 10) || 0;
  await env.CLICKS.put(key, String(n + 1));
}

async function stats(env) {
  const out = {};
  let cursor;
  do {
    const page = await env.CLICKS.list({ cursor });
    for (const { name } of page.keys) {
      const [id, month] = name.split(":");
      (out[id] ||= {})[month] = parseInt(await env.CLICKS.get(name), 10) || 0;
    }
    cursor = page.list_complete ? null : page.cursor;
  } while (cursor);
  return out;
}

const json = (body, status = 200) => new Response(JSON.stringify(body, null, 2),
  { status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const path = url.pathname.replace(/^\/+|\/+$/g, "").toLowerCase();

    if (path === "stats") {
      if (!env.STATS_KEY || url.searchParams.get("key") !== env.STATS_KEY || !env.CLICKS) return json({ error: "not found" }, 404);
      return json(await stats(env));
    }

    if (!/^[a-z0-9-]{1,40}$/.test(path)) return json({ error: "not found" }, 404);
    let site, brokers;
    try { ({ site, brokers } = await loadBrokers(env)); }
    catch (e) { return json({ error: String(e.message || e) }, 502); }

    const b = brokers.find(x => String(x.id).toLowerCase() === path);
    if (!b || !/^https:\/\/[^\s]+$/.test(String(b.url || ""))) return Response.redirect(site + "#brokers", 302);

    if (env.CLICKS && req.method === "GET") ctx.waitUntil(count(env, path).catch(() => {}));
    return new Response(null, { status: 302, headers: { Location: b.url, "Cache-Control": "no-store", "X-Robots-Tag": "noindex" } });
  },
};
