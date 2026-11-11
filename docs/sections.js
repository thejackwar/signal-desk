// The site's sections: every page belongs to one, and each section shows a row of tabs to move between its pages.
// Used by index.html (phone menu, sidebar groups and the tab row on the app's own pages) and by every
// stand-alone page (chart.html, options.html …), which call ICS.mountSub() to draw the tab row under their top bar.
(() => {
  // [label, href inside index.html or a page file, regex on the app route (index.html only)]
  const SECTIONS = [
    { id: "home", name: "Home", items: [["Home", "#home", /^home$/]] },
    { id: "terminal", name: "Terminal", items: [["Terminal", "#terminal", /^terminal/]] },
    { id: "signals", name: "Signals", items: [
      ["Alerts", "#alerts", /^(alerts|alert-.*)$/], ["Best trades", "#winners", /^winners$/], ["Track record", "#record", /^record$/],
      ["Live trades", "#trades", /^trades$/], ["Phone alerts", "#notify", /^notify$/]] },
    { id: "markets", name: "Markets", items: [
      ["Markets", "#stocks", /^(stocks|stock-.*)$/], ["Heatmap", "#heatmap", /^heatmap$/],
      ["News & calendar", "#news", /^(news|buzz|mood|earnings.*|calendar.*)$/], ["Crypto dashboard", "#crypto", /^crypto/, "crypto-only"]] },
    { id: "charts", name: "Charts", items: [
      ["Chart", "chart.html"], ["Trendlines", "trendlines.html"], ["Volume profile", "volumeprofile.html"], ["Bar replay", "replay.html"]] },
    { id: "options", name: "Options & flow", items: [
      ["Options", "options.html"], ["Max pain", "maxpain.html"], ["Dark pools", "darkpool.html"], ["Implied correlation", "correlation.html"]] },
    { id: "strategies", name: "Strategies", items: [
      ["Strategy Lab", "#strategylab", /^strategylab/], ["Strategies", "#strategies", /^(strategies|strategy-)/], ["Research", "research.html"],
      ["Strategy Board", "board.html"], ["Strategy trades", "trades.html"], ["Chart patterns", "patterns.html"], ["Price Action Lab", "palab.html"], ["Session Momentum", "session.html"], ["Premarket Momentum", "premarket.html"], ["Gap-Down Short", "gapdown.html"], ["Opening Reversal", "reversal.html"], ["Fibonacci", "fib.html"], ["4H CHoCH", "choch.html"], ["Pairs trading", "pairs.html"],
      ["Smaller Crashes", "#crashes", /^crashes$/], ["VIX seasonality", "#vix", /^vix$/]] },
    { id: "practice", name: "Practice", items: [["Practice portfolio", "#practice", /^practice$/]] },
    { id: "learn", name: "Learn & more", items: [
      ["Learn", "#learn", /^learn/], ["Brokers", "#brokers", /^brokers/], ["About us", "#about", /^about$/]] },
  ];

  const page = () => (location.pathname.split("/").pop() || "index.html").toLowerCase();
  const inApp = () => page() === "index.html" || page() === "";
  const link = href => href.startsWith("#") && !inApp() ? `index.html${href}` : href;

  // which section and item the current address belongs to
  function where(route) {
    for (const s of SECTIONS) for (const it of s.items) {
      if (it[2] ? inApp() && it[2].test(route ?? location.hash.slice(1)) : !inApp() && page() === it[1]) return { s, it };
    }
    return null;
  }

  const esc = t => String(t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  // the tab row for a section (nothing for one-page sections)
  function subHtml(route) {
    const w = where(route);
    if (!w || w.s.items.length < 2) return "";
    return `<nav class="ics-sub" aria-label="${esc(w.s.name)}"><div class="ics-sub-in"><b>${esc(w.s.name)}</b>` +
      w.s.items.map(it => `<a href="${link(it[1])}"${it[3] ? ` class="${it[3]}"` : ""}${it === w.it ? ' aria-current="page"' : ""}>${esc(it[0])}</a>`).join("") +
      `</div></nav>`;
  }

  const CSS = `.ics-sub{position:relative;z-index:5;border-bottom:1px solid var(--hair-2,var(--line,#1f2833))}
.ics-sub-in{display:flex;gap:6px;align-items:center;overflow-x:auto;scrollbar-width:none;padding:10px 16px;max-width:1280px;margin:0 auto;-webkit-overflow-scrolling:touch}
.ics-sub-in::-webkit-scrollbar{display:none}
.ics-sub b{flex:none;font-size:12px;font-weight:600;color:var(--faint,#7d8a97);text-transform:uppercase;letter-spacing:.05em;margin-right:4px}
.ics-sub a{flex:none;font-size:13.5px;font-weight:500;color:var(--muted,#a5b1bd);text-decoration:none;padding:6px 12px;border-radius:980px;background:var(--fill,#1a222c);white-space:nowrap}
.ics-sub a:hover{color:var(--fg,#e6edf3);text-decoration:none}
.ics-sub a[aria-current="page"]{background:linear-gradient(95deg,#38bdf8 0%,#34d399 60%,#4ade80 100%);color:#04121a;font-weight:600}
#app > .ics-sub{margin:0 -16px 6px;border-bottom:0}
#app > .ics-sub .ics-sub-in{padding:4px 16px 8px}
#app:has(> .ics-sub) .seg.nsw[aria-label="Alerts view"]{display:none}
html:not([data-market="crypto"]) .ics-sub a.crypto-only{display:none}
.ics-uni{display:inline-flex;gap:2px;padding:2px;border:1px solid var(--line,#1e2834);border-radius:8px;background:var(--fill,#0a1017)}
.ics-uni button{font:600 12px/1 var(--font,inherit);color:var(--muted,#9aa7b4);background:none;border:0;border-radius:6px;padding:6px 10px;cursor:pointer}
.ics-uni button[aria-pressed="true"]{background:rgba(108,180,255,.14);color:var(--fg,#e8eef4);box-shadow:inset 0 0 0 1px rgba(108,180,255,.35)}`;
  function addCss() {
    if (document.getElementById("ics-sub-css")) return;
    const st = document.createElement("style"); st.id = "ics-sub-css"; st.textContent = CSS; document.head.appendChild(st);
  }

  // stand-alone pages: tab row right under the top bar, and the active tab scrolled into view
  function mountSub() {
    addCss();
    const html = subHtml();
    const bar = document.querySelector("nav.bar");
    if (!html || !bar) return;
    bar.insertAdjacentHTML("afterend", html);
    const cur = document.querySelector('.ics-sub a[aria-current="page"]');
    if (cur) cur.parentElement.scrollLeft = cur.offsetLeft - 60;
  }

  // S&P 100 / S&P 500: the app's stock-list choice (saved in its prefs), shared with the stand-alone pages.
  // udata("x.json") is data/x.json for the S&P 100 and data/sp500/x.json for the S&P 500 (engine/universe.py).
  const PREFS = "signaldesk.prefs";
  const uni = () => { try { return JSON.parse(localStorage.getItem(PREFS) || "{}").universe === "sp500" ? "sp500" : "sp100"; } catch (e) { return "sp100"; } };
  const setUni = u => { try { const p = JSON.parse(localStorage.getItem(PREFS) || "{}"); p.universe = u; localStorage.setItem(PREFS, JSON.stringify(p)); } catch (e) {} };
  const udata = path => (uni() === "sp500" ? "data/sp500/" : "data/") + path;
  const uniName = () => uni() === "sp500" ? "S&P 500" : "S&P 100";
  // a small two-button switch; picking one saves it and reloads the page with that list's files
  const uniSwitch = (cls = "") => `<span class="ics-uni ${cls}" role="group" aria-label="Stock list">${[["sp100", "S&amp;P 100"], ["sp500", "S&amp;P 500"]]
    .map(([u, t]) => `<button type="button" data-ics-uni="${u}" aria-pressed="${uni() === u}">${t}</button>`).join("")}</span>`;
  document.addEventListener("click", e => {
    const b = e.target.closest("button[data-ics-uni]");
    if (!b || inApp()) return;
    e.preventDefault();
    if (b.dataset.icsUni !== uni()) { setUni(b.dataset.icsUni); location.reload(); }
  });

  window.ICS = { SECTIONS, where, subHtml, addCss, mountSub, uni, setUni, udata, uniName, uniSwitch };
  addCss();
  if (!inApp()) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mountSub); else mountSub();
  }
})();
