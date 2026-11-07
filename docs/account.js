// Accounts: log in (Supabase, email + password, invite-only) and everything the site saves on the
// device follows you to every device: chart drawings and indicators, the Terminal layout, watchlists,
// screens, price alerts (their ntfy topic), the practice portfolio, page settings.
//
// How: the site already keeps all of that in localStorage. This file watches writes to those keys and
// copies them to the account (table public.user_data, one row per key, readable only by its owner:
// row-level security, see worker/README.md → Accounts). On every page load and when the tab comes back,
// it pulls the account's rows: anything newer from another device replaces the local copy.
// Without a login (or before SUPABASE_URL is set) nothing changes: the site works on the device alone.
//
// Loaded early on every page (before the page's own scripts), so it sees every save.
(() => {
  const SUPABASE_URL = "https://fcbgxiafnbtrtxxhegbu.supabase.co";          // e.g. https://abcdefgh.supabase.co (Project Settings → API)
  const SUPABASE_KEY = "sb_publishable_DfmNbJR3E-H5dW8mru2JUA_f6kNIQkh";          // the anon / publishable key (public by design; never the service_role key)
  const SITE = "https://thejackwar.github.io/signal-desk/";
  if (!SUPABASE_URL || !SUPABASE_KEY) return;

  // ------------------------------------------------------------------ what is synced
  const META = "signaldesk.sync";    // {key: last change (ms)} on this device; never synced itself
  const EXTRA = new Set(["board", "choch4h", "sessionMom"]);
  const SKIP = new Set([META, "signaldesk.seen"]);   // "seen" = which alerts this device has shown as new
  const synced = k => typeof k === "string" && !SKIP.has(k) && (k.startsWith("signaldesk.") || EXTRA.has(k));

  const LS = window.localStorage, P = Storage.prototype;
  const rawSet = P.setItem, rawRemove = P.removeItem;
  const meta = () => { try { return JSON.parse(LS.getItem(META)) || {}; } catch { return {}; } };
  const setMeta = m => { try { rawSet.call(LS, META, JSON.stringify(m)); } catch {} };
  const dirty = new Set();
  let user = null, sb = null, pushT = null, pulling = false;

  const touch = k => { const m = meta(); m[k] = Date.now(); setMeta(m); dirty.add(k); schedulePush(); };
  P.setItem = function (k, v) {
    const before = this === LS && synced(k) ? LS.getItem(k) : undefined;
    rawSet.call(this, k, v);
    if (this === LS && synced(k) && before !== String(v) && !pulling) touch(k);
  };
  P.removeItem = function (k) {
    const had = this === LS && synced(k) && LS.getItem(k) !== null;
    rawRemove.call(this, k);
    if (had && !pulling) touch(k);
  };

  function schedulePush() {
    if (!user) return;
    clearTimeout(pushT);
    pushT = setTimeout(push, 1500);
  }
  async function push() {
    if (!user || !dirty.size) return;
    const m = meta(), keys = [...dirty]; dirty.clear();
    const rows = keys.map(k => ({ user_id: user.id, key: k, value: LS.getItem(k), updated_at: new Date(m[k] || Date.now()).toISOString() }));
    const { error } = await sb.from("user_data").upsert(rows, { onConflict: "user_id,key" });
    if (error) { keys.forEach(k => dirty.add(k)); status(`Couldn't save to your account (${error.message}). Retrying…`, true); pushT = setTimeout(push, 15000); }
    else status("");
  }
  window.addEventListener("pagehide", () => { if (dirty.size) push(); });

  // newer rows from the account replace the local copy; local changes the account hasn't seen are sent up
  async function pull({ reloadIfChanged } = {}) {
    if (!user) return false;
    const { data, error } = await sb.from("user_data").select("key, value, updated_at").eq("user_id", user.id);
    if (error) { status(`Couldn't load your account (${error.message}).`, true); return false; }
    const m = meta(), seen = new Set();
    let changed = false;
    pulling = true;
    try {
      for (const r of data || []) {
        seen.add(r.key);
        if (!synced(r.key)) continue;
        const t = Date.parse(r.updated_at) || 0;
        if (t > (m[r.key] || 0)) {
          if (LS.getItem(r.key) !== r.value) { r.value === null ? rawRemove.call(LS, r.key) : rawSet.call(LS, r.key, r.value); changed = true; }
          m[r.key] = t;
        } else if ((m[r.key] || 0) > t) dirty.add(r.key);
      }
      // first login on this device: what's already here goes into the account
      for (let i = 0; i < LS.length; i++) { const k = LS.key(i); if (synced(k) && !seen.has(k)) { m[k] = m[k] || Date.now(); dirty.add(k); } }
    } finally { pulling = false; }
    setMeta(m);
    if (dirty.size) push();
    if (changed && reloadIfChanged) {
      // the page read its settings at start-up: reload once so it shows the account's versions
      try { if (!sessionStorage.getItem("ics.synced")) { sessionStorage.setItem("ics.synced", "1"); location.reload(); return true; } } catch {}
    }
    if (changed && !reloadIfChanged) banner();
    if (!changed) { try { sessionStorage.removeItem("ics.synced"); } catch {} }
    return changed;
  }

  // ------------------------------------------------------------------ the UI
  const css = `
.icsa-btn{display:inline-flex;align-items:center;gap:7px;appearance:none;border:1px solid var(--line,var(--hair,#2b3644));background:transparent;color:var(--fg,#e6edf3);border-radius:980px;padding:6px 12px;font:600 13px/1 var(--font,inherit);cursor:pointer;white-space:nowrap}
.icsa-btn:hover{border-color:var(--accent,#38bdf8)}
.icsa-btn i{width:20px;height:20px;border-radius:50%;display:grid;place-items:center;background:linear-gradient(135deg,#38bdf8,#4ade80);color:#04121a;font:700 11px/1 var(--font,inherit);font-style:normal}
.icsa-side{margin:0 0 10px}
.icsa-fixed{position:fixed;right:14px;bottom:calc(14px + env(safe-area-inset-bottom,0px));z-index:60;background:rgba(18,24,32,.94);box-shadow:0 6px 20px rgba(0,0,0,.35)}
.icsa-scrim{position:fixed;inset:0;z-index:1000;background:rgba(4,8,12,.6);backdrop-filter:blur(4px);display:grid;place-items:center;padding:16px}
.icsa-box{width:min(380px,100%);background:#121820;border:1px solid #2b3644;border-radius:18px;padding:22px;color:#e6edf3;font:15px/1.45 -apple-system,BlinkMacSystemFont,Inter,"Segoe UI",Roboto,sans-serif;box-shadow:0 20px 60px rgba(0,0,0,.5)}
.icsa-box h2{margin:0 0 4px;font-size:21px;letter-spacing:-.02em}
.icsa-box p{margin:0 0 14px;color:#a5b1bd;font-size:14px}
.icsa-box label{display:block;font-size:12px;color:#a5b1bd;margin:10px 0 4px}
.icsa-box input{width:100%;box-sizing:border-box;height:42px;border-radius:10px;border:1px solid #2b3644;background:#0b0f14;color:#e6edf3;padding:0 12px;font:500 15px inherit}
.icsa-box input:focus{outline:none;border-color:#38bdf8;box-shadow:0 0 0 3px rgba(56,189,248,.2)}
.icsa-go{margin-top:16px;width:100%;height:44px;border:0;border-radius:980px;background:linear-gradient(95deg,#38bdf8,#34d399 60%,#4ade80);color:#04121a;font:700 15px inherit;cursor:pointer}
.icsa-go:disabled{opacity:.6;cursor:default}
.icsa-row{display:flex;justify-content:space-between;gap:10px;margin-top:12px;font-size:13px}
.icsa-row button{appearance:none;border:0;background:none;color:#7dd3fc;cursor:pointer;font:inherit;padding:0}
.icsa-msg{min-height:1.3em;margin-top:10px;font-size:13px;color:#a5b1bd}
.icsa-msg.err{color:#f87171}
.icsa-who{background:#0b0f14;border-radius:12px;padding:12px;margin:6px 0 4px;font-size:14px;word-break:break-all}
.icsa-who small{display:block;color:#7d8a97;font-size:12px;margin-top:4px}
.icsa-out{margin-top:12px;width:100%;height:40px;border-radius:980px;border:1px solid #2b3644;background:none;color:#f87171;font:600 14px inherit;cursor:pointer}
.icsa-banner{position:fixed;left:50%;bottom:calc(18px + env(safe-area-inset-bottom,0px));transform:translateX(-50%);z-index:999;background:#121820;border:1px solid #38bdf8;color:#e6edf3;border-radius:12px;padding:10px 14px;font:14px -apple-system,Inter,sans-serif;display:flex;gap:12px;align-items:center;box-shadow:0 10px 30px rgba(0,0,0,.4)}
.icsa-banner button{appearance:none;border:0;border-radius:980px;padding:6px 12px;background:#38bdf8;color:#04121a;font:700 13px inherit;cursor:pointer}`;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  let lastStatus = "";
  function status(t) { lastStatus = t; const el = document.querySelector(".icsa-box .icsa-sync"); if (el) el.textContent = t; }
  function buttons() {
    const label = user ? `<i>${esc((user.email || "?")[0].toUpperCase())}</i>Account` : "Log in";
    const html = cls => `<button type="button" class="icsa-btn ${cls}" data-icsa>${label}</button>`;
    const side = document.getElementById("sidefoot");          // index.html: sidebar
    if (side) { let b = document.querySelector(".icsa-side"); if (!b) side.insertAdjacentHTML("beforebegin", html("icsa-side")); else b.outerHTML = html("icsa-side"); }
    const mfoot = document.querySelector(".sheet .mfoot");      // index.html: phone menu
    if (mfoot) { const b = mfoot.querySelector("[data-icsa]"); b ? (b.outerHTML = html("")) : mfoot.insertAdjacentHTML("afterbegin", html("")); }
    const bar = document.querySelector("nav.bar .bar-in > span:last-child");   // stand-alone pages: top bar, right side
    if (bar && !side) { bar.style.textAlign = "right"; bar.innerHTML = html(""); }
    if (!side && !bar && !document.querySelector(".icsa-fixed") && !document.documentElement.classList.contains("embed"))
      document.body.insertAdjacentHTML("beforeend", html("icsa-fixed"));
    else if (!side && !bar) { const f = document.querySelector(".icsa-fixed"); if (f) f.outerHTML = html("icsa-fixed"); }
  }
  function banner() {
    if (document.querySelector(".icsa-banner")) return;
    document.body.insertAdjacentHTML("beforeend", `<div class="icsa-banner" role="status">Changes from your other device<button type="button" data-icsa-reload>Reload</button></div>`);
  }

  function modal(view) {
    document.querySelector(".icsa-scrim")?.remove();
    const body = view === "account" ? `<h2>Your account</h2><div class="icsa-who">${esc(user.email)}<small>Drawings, layouts, watchlists, price alerts and settings are saved to this account and appear on every device you log in on.</small></div>
        <p class="icsa-msg icsa-sync">${esc(lastStatus)}</p>
        <div class="icsa-row"><button type="button" data-icsa-view="setpw">Change password</button><button type="button" data-icsa-sync>Sync now</button></div>
        <button type="button" class="icsa-out" data-icsa-out>Log out</button>`
      : view === "forgot" ? `<h2>Reset password</h2><p>We'll email you a link to set a new password.</p>
        <form data-icsa-form="forgot"><label>Email<input name="email" type="email" autocomplete="email" required></label><button class="icsa-go">Send the link</button></form>
        <p class="icsa-msg"></p><div class="icsa-row"><button type="button" data-icsa-view="login">Back to log in</button></div>`
      : view === "setpw" ? `<h2>${user ? "Set your password" : "New password"}</h2><p>At least 8 characters.</p>
        <form data-icsa-form="setpw"><label>New password<input name="pw" type="password" autocomplete="new-password" minlength="8" required></label><button class="icsa-go">Save password</button></form><p class="icsa-msg"></p>`
      : `<h2>Log in</h2><p>Your drawings, layouts, watchlists and alerts, on every device. Accounts are by invitation for now.</p>
        <form data-icsa-form="login"><label>Email<input name="email" type="email" autocomplete="email" required></label>
          <label>Password<input name="pw" type="password" autocomplete="current-password" required></label><button class="icsa-go">Log in</button></form>
        <p class="icsa-msg"></p><div class="icsa-row"><button type="button" data-icsa-view="forgot">Forgot password?</button></div>`;
    document.body.insertAdjacentHTML("beforeend", `<div class="icsa-scrim" data-icsa-close><div class="icsa-box" role="dialog" aria-modal="true" aria-label="Account">${body}</div></div>`);
    document.querySelector(".icsa-box input")?.focus();
  }
  const msg = (t, err) => { const el = document.querySelector(".icsa-box .icsa-msg"); if (el) { el.textContent = t; el.classList.toggle("err", !!err); } };

  document.addEventListener("click", async e => {
    const t = e.target;
    if (t.closest?.("[data-icsa]")) { e.preventDefault(); if (!sb) return; modal(user ? "account" : "login"); return; }
    if (t.matches?.("[data-icsa-close]")) { t.remove(); return; }
    const v = t.closest?.("[data-icsa-view]"); if (v) { modal(v.dataset.icsaView); return; }
    if (t.closest?.("[data-icsa-reload]")) { location.reload(); return; }
    if (t.closest?.("[data-icsa-sync]")) { status("Syncing…"); await push(); const ch = await pull(); status(ch ? "Updated from your account: reload to see it." : "Up to date."); return; }
    if (t.closest?.("[data-icsa-out]")) { await push(); await sb.auth.signOut(); document.querySelector(".icsa-scrim")?.remove(); return; }
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") document.querySelector(".icsa-scrim")?.remove(); });
  document.addEventListener("submit", async e => {
    const f = e.target.closest?.("[data-icsa-form]"); if (!f || !sb) return;
    e.preventDefault();
    const go = f.querySelector(".icsa-go"); go.disabled = true; msg("");
    try {
      if (f.dataset.icsaForm === "login") {
        const { error } = await sb.auth.signInWithPassword({ email: f.email.value.trim(), password: f.pw.value });
        if (error) throw error;
        document.querySelector(".icsa-scrim")?.remove();
      } else if (f.dataset.icsaForm === "forgot") {
        const { error } = await sb.auth.resetPasswordForEmail(f.email.value.trim(), { redirectTo: SITE });
        if (error) throw error;
        msg("If that email has an account, the link is on its way.");
      } else if (f.dataset.icsaForm === "setpw") {
        const { error } = await sb.auth.updateUser({ password: f.pw.value });
        if (error) throw error;
        msg("Password saved.");
        setTimeout(() => document.querySelector(".icsa-scrim")?.remove(), 900);
      }
    } catch (err) {
      msg(/Invalid login/i.test(err.message) ? "Wrong email or password." : err.message, true);
    } finally { go.disabled = false; }
  });

  // ------------------------------------------------------------------ start
  // an invite or password-reset link lands here with the session in the address (#access_token=…&type=…)
  const linkType = /[#&]type=(invite|recovery|signup)/.exec(location.hash)?.[1] || null;
  const s = document.createElement("script");
  s.src = "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/dist/umd/supabase.min.js";
  s.onload = async () => {
    sb = window.supabase.createClient(SUPABASE_URL, SUPABASE_KEY, { auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, storageKey: "ics.auth" } });
    sb.auth.onAuthStateChange(async (ev, session) => {
      const was = user?.id;
      user = session?.user || null;
      buttons();
      if (ev === "PASSWORD_RECOVERY") { modal("setpw"); return; }
      if (user && user.id !== was) {
        if (/access_token=/.test(location.hash)) history.replaceState(null, "", location.pathname + location.search + "#terminal");
        await pull({ reloadIfChanged: !linkType });
        if (linkType === "invite" || linkType === "recovery") modal("setpw");
      }
    });
    const { data } = await sb.auth.getSession();
    user = data.session?.user || null;
    buttons();
  };
  document.head.appendChild(s);
  const ready = () => buttons();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready); else ready();
  // back to the tab after using another device: fetch what changed there (at most every 30 s)
  let lastPull = 0;
  document.addEventListener("visibilitychange", () => { if (!document.hidden && user && Date.now() - lastPull > 30e3) { lastPull = Date.now(); pull(); } });
})();
