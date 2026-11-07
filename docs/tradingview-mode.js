/* Official TradingView analysis mode. Loaded on demand; never replaces site data/overlays. */
(() => {
  "use strict";
  const futures = {
    "GC=F":"COMEX:GC1!", "SI=F":"COMEX:SI1!", "HG=F":"COMEX:HG1!",
    "PL=F":"NYMEX:PL1!", "PA=F":"NYMEX:PA1!", "CL=F":"NYMEX:CL1!",
    "BZ=F":"ICEEUR:BRN1!", "NG=F":"NYMEX:NG1!", "RB=F":"NYMEX:RB1!", "HO=F":"NYMEX:HO1!",
    "ZC=F":"CBOT:ZC1!", "ZW=F":"CBOT:ZW1!", "ZS=F":"CBOT:ZS1!",
    "KC=F":"ICEUS:KC1!", "CC=F":"ICEUS:CC1!", "SB=F":"ICEUS:SB1!", "CT=F":"ICEUS:CT1!",
    "LE=F":"CME:LE1!", "HE=F":"CME:HE1!"
  };
  function symbol(raw) {
    const s = String(raw || "").trim().toUpperCase();
    if (!s || !/^[A-Z0-9^:._!=/\-]+$/.test(s)) return null;
    if (futures[s]) return futures[s];
    if (s.includes(":")) return s;
    if (s === "^VIX") return "CBOE:VIX";
    if (s === "^GSPC" || s === "^SPX") return "SP:SPX";
    if (s.endsWith("-USD")) return "BINANCE:" + s.slice(0,-4) + "USDT";
    if (s.endsWith("USDT")) return "BINANCE:" + s.replace("/", "");
    return s.replace("-", ".");
  }
  function interval(tf) {
    return ({"1m":"1","5m":"5","15m":"15","30m":"30","1H":"60","4H":"240","D":"D","W":"W","M":"M"})[tf] || "D";
  }
  function config(opts) {
    return {autosize:true, symbol:symbol(opts.symbol), interval:interval(opts.tf), timezone:"Europe/Rome",
      theme:document.documentElement.dataset.theme === "light" ? "light" : "dark", style:"1", locale:"en",
      allow_symbol_change:true, hide_side_toolbar:false, hide_top_toolbar:false, hide_legend:false,
      hide_volume:false, withdateranges:true, save_image:true, calendar:false, details:false, hotlist:false,
      support_host:"https://www.tradingview.com"};
  }
  let dialog, widget, returnFocus, scrollBefore;
  const mounted = new WeakMap();
  function init() {
    if (dialog) return;
    const css = document.createElement("style");
    css.textContent = `
      .tv-mode-launch{display:block;margin:6px 0 10px;padding:7px 12px;border:1px solid #64748b;border-radius:7px;background:transparent;color:inherit;font:600 12px system-ui;cursor:pointer;min-height:34px}
      .tv-mode-launch:focus-visible,.tv-mode-dialog a:focus-visible,.tv-mode-dialog button:focus-visible{outline:2px solid #38bdf8;outline-offset:3px}
      .tv-mode-dialog{box-sizing:border-box;width:100vw;max-width:100vw;height:100dvh;max-height:100dvh;margin:0;padding:0;border:0;background:#10141c;color:#e2e8f0;font:13px/1.4 system-ui}
      .tv-mode-dialog[open]{display:flex;flex-direction:column}.tv-mode-dialog::backdrop{background:#000b}
      .tv-mode-head{display:flex;align-items:center;gap:14px;padding:10px 14px;border-bottom:1px solid #334155;flex-wrap:wrap}
      .tv-mode-head h2{font:600 15px system-ui;margin:0;flex:1}.tv-mode-head button{background:#1e293b;border:1px solid #64748b;border-radius:6px;color:#e2e8f0;padding:8px 12px;cursor:pointer}
      .tv-mode-dialog a{color:#7dd3fc}.tv-mode-note{margin:0;padding:8px 14px;color:#b3bfd1;font-size:12px}
      .tv-mode-widget{flex:1;min-height:280px;width:100%;position:relative}.tv-mode-widget .tradingview-widget-container{height:100%;width:100%;position:absolute;inset:0}
      .tv-mode-credit{padding:4px 14px;font-size:11px}.tv-mode-error{padding:30px}
      @media(max-width:600px){.tv-mode-head{gap:8px;padding:8px}.tv-mode-note{padding:6px 8px}.tv-mode-head h2{font-size:13px}.tv-mode-head a{font-size:11px}}
    `;
    document.head.appendChild(css);
    dialog = document.createElement("dialog"); dialog.className = "tv-mode-dialog";
    dialog.setAttribute("aria-labelledby", "tv-mode-title");
    dialog.innerHTML = `<header class="tv-mode-head"><h2 id="tv-mode-title">TradingView mode</h2><a class="tv-mode-external" target="_blank" rel="noopener">Open on TradingView ↗</a><button type="button">Return to site chart</button></header><p class="tv-mode-note">Use the left toolbar arrows for drawing-tool menus. TradingView market data and drawings are separate from this site's strategy overlays. Drawings reset when you close this mode. Some tradingview.com controls are unavailable in embeds.</p><div class="tv-mode-widget"></div><div class="tv-mode-credit"><a href="https://www.tradingview.com/widget-docs/widgets/charts/advanced-chart/" target="_blank" rel="noopener">Chart by TradingView</a></div>`;
    document.body.appendChild(dialog); widget = dialog.querySelector(".tv-mode-widget");
    dialog.querySelector("button").onclick = () => dialog.close();
    dialog.addEventListener("close", () => {
      widget.replaceChildren(); document.documentElement.style.overflow = scrollBefore;
      if (returnFocus?.isConnected) returnFocus.focus({preventScroll:true});
    });
  }
  function open(opts) {
    if (!symbol(opts.symbol)) return;
    // Embedded Terminal charts use the parent's viewport, not a small nested modal.
    if (window.parent !== window) {
      try { if (window.parent.ICTradingView) return window.parent.ICTradingView.open(opts); } catch {}
    }
    init();
    const cfg = config(opts);
    if (!dialog.open) {
      returnFocus = document.activeElement; scrollBefore = document.documentElement.style.overflow;
      document.documentElement.style.overflow = "hidden"; dialog.showModal();
    }
    dialog.querySelector("h2").textContent = `${opts.symbol} · TradingView mode`;
    dialog.querySelector(".tv-mode-external").href = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(cfg.symbol)}&interval=${cfg.interval}`;
    widget.replaceChildren();
    const container = document.createElement("div"); container.className = "tradingview-widget-container";
    const target = document.createElement("div"); target.className = "tradingview-widget-container__widget";
    target.style.cssText = "height:100%;width:100%"; container.appendChild(target);
    const script = document.createElement("script"); script.async = true;
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.textContent = JSON.stringify(cfg);
    script.onerror = () => { target.textContent = "TradingView could not load. Use Open on TradingView above, or return to the site chart."; target.className = "tv-mode-error"; };
    container.appendChild(script); widget.appendChild(container);
  }
  function mount(host, opts) {
    if (!host || !symbol(opts.symbol)) return;
    init();
    let item = mounted.get(host);
    if (item && host.contains(item.button)) { item.opts = opts; return; }
    const button = document.createElement("button"); button.type = "button";
    button.className = "tv-mode-launch"; button.textContent = "TradingView mode · drawing tools";
    button.title = "Open this market with TradingView's drawing toolbar and dropdown menus";
    item = {button, opts}; mounted.set(host, item);
    button.onclick = e => { e.preventDefault(); e.stopPropagation(); open(item.opts); };
    host.appendChild(button);
  }
  window.ICTradingView = {mount, open, symbol, interval, config};
})();
