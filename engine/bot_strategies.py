"""
Strategies from the trading-bot project (thejackwar/sp500-bot, handoff of 29 Oct 2026): the ones
that beat buy and hold in that project's backtests (stocks vs SPY, Bitcoin vs BTC).

Unlike the indicator strategies in strategies.py, each of these runs its own trades: it buys on
an alert, then sells on its own exit (a stop, a close below the 20-day low, a failed high...),
not on an opposite alert. So every function here replays the trades bar by bar and returns them
in StrategyResult.trades, which the backtest uses directly:

    signals  +1 = buy alert (decided on that day's close), -1 = sell alert
             (the 50-day rule long + short also uses -1 to open a short and +1 to cover it)
    exits    the bars whose alert closes a trade instead of opening one ("Sell", not "short")
    trades   [{side, sig, in, in_px, out, out_px, why}]: bought at the NEXT day's open after the
             alert, like the trading bot; sold at the stop price, at a close, or at the next open,
             depending on the exit. out = None while the trade is still open.

Rules are copied from the bot (detectors in deck_patterns.py, pattern_finder.py and rule50.py,
ported unchanged). Not copied: the bot's money rules (1% risk per trade, max 10% per stock, max
25 positions...). On this site every strategy is run the same way: the money is split equally
across the open trades, so results differ from the bot's reports.

Informational only. Nothing here is investment advice.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import deck_patterns as dp
import pattern_finder as pf
import rule50
from strategies import P, StrategyResult, _vals, sma

TRAIL = 20          # the bots' trend exit: a close below the lowest low of the 20 days before
WINDOW = 290        # the deck detector sees the last 290 daily candles each day (as in the bot)
WEAK = {"Double bottom", "Cup and handle", "Pipe bottom", "Hanging man"}   # dropped by the live rules
FAM = {"multi": 0, "short": 1, "candle": 2}                               # tie-break: multi-bar first


def _caps(df: pd.DataFrame) -> pd.DataFrame:
    """The bot's detectors use capitalised column names."""
    return df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder's ATR, as in the bot (ewm alpha 1/n, no warm-up gap)."""
    c = df["close"]
    tr = pd.concat([df["high"] - df["low"], (df["high"] - c.shift()).abs(), (df["low"] - c.shift()).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _key(df: pd.DataFrame, *extra):
    return (len(df), df.index[0], df.index[-1], float(df["close"].iat[-1]), float(df["close"].iat[0])) + extra


def _result(df, trades, notes, stops, overlays=(), shapes=()) -> StrategyResult:
    """Alerts from the trades: +1 / -1 on each trade's decision day (sig) and on the day its exit was decided."""
    sig = pd.Series(0, index=df.index, dtype=int)
    exits = set()
    for t in trades:
        sig.iat[t["sig"]] = t["side"]
        if t.get("exit_sig") is not None:
            sig.iat[t["exit_sig"]] = -t["side"]
            exits.add(t["exit_sig"])
    for t in trades:                       # a flip (C7): the same close sells one side and opens the other
        if t["sig"] in exits:
            exits.discard(t["sig"])
    return StrategyResult(sig, notes, list(overlays), None, list(shapes), stops, trades=trades, exits=exits)


# ----------------------------------------------------------- deck patterns (S1-S3, S5)

_DECK = {}


def deck_breakouts(df: pd.DataFrame) -> dict:
    """Up breakouts of the deck patterns, by bar: {i: [(family rank, name, stop, pattern)]}.
    Each day the detector sees the last WINDOW candles; a pattern counts on the day its breakout
    close happens (each pattern once). NR4, inside bar and doji are off, like in the bot."""
    k = _key(df)
    if k in _DECK:
        return _DECK[k]
    d = dp.add_atr(_caps(df)[["Open", "High", "Low", "Close"]])
    out, seen = {}, set()
    for i in range(150, len(d)):
        s = max(0, i - WINDOW + 1)
        w = d.iloc[s:i + 1]
        try:
            pats = dp.detect(w, families=("multi", "short", "candle"))
        except Exception:
            continue
        for p in pats:
            b = p.get("brk")
            if (p["stage"] != "breakout" or not b or b["bar"] != len(w) - 1 or b["dir"] != "LONG"
                    or p["name"] in ("Narrow range (NR4)", "Inside bar", "Doji")):
                continue
            key = (p["name"], w.index[p["start"]])
            if key in seen:
                continue
            seen.add(key)
            out.setdefault(i, []).append((FAM.get(p["family"], 1), p["name"].split(" (")[0], float(b["stop"]), p, s))
    _DECK[k] = out
    return out


def _pattern_shape(p, s, i, name):
    """The detector's swing points as a drawing, shifted from window to chart positions."""
    pts = sorted((int(x) + s, float(y)) for x, y, *_ in p.get("points") or [])
    if len(pts) < 2:
        return None
    return {"at": i, "name": name, "points": [[x, y] for x, y in pts] + [[i, float(p["brk"]["level"])]]}


def _stop_trades(df, entries: dict, exit_rule="trail", floor_stop=False, no_chase=None):
    """Long trades with a stop, the bots' common engine.
    entries: {i: (stop, name, target or None)} decided on day i's close -> bought at day i+1's open.
    exits (checked every day, the entry day included):
        a day opening at or below the stop sells at the open (not on the entry day itself),
        a low at or below the stop sells at the stop,
        exit_rule "trail": a close below the lowest low of the 20 days before sells at that close,
        exit_rule "target": a high at or above the target sells there (at the open if it opens above).
    floor_stop (deck v8): every close moves the stop to close - starting risk (up or down); once it
        has reached the entry price it never goes below it again.
    A stop given as ("below_open", d) is set d under the entry's open price.
    no_chase: skip the trade if the open is more than this fraction above the alert's close."""
    o, h, l, c = (df[k].values.astype(float) for k in ("open", "high", "low", "close"))
    low20 = df["low"].rolling(TRAIL, min_periods=TRAIL).min().shift(1).values
    n = len(c)
    trades, pos, pend = [], None, None
    for i in range(n):
        if pend is not None:                                  # yesterday's alert: buy at today's open
            a, stop, name, target = pend
            pend = None
            if isinstance(stop, tuple):                       # ("below_open", distance): set from the open
                stop = o[i] - stop[1]
            skip = (no_chase is not None and o[i] > c[a] * (1 + no_chase)) or o[i] <= stop \
                or (target is not None and o[i] >= target)
            if not skip:
                pos = {"side": 1, "sig": a, "in": i, "in_px": o[i], "stop": stop, "stop0": stop, "risk": o[i] - stop,
                       "target": target, "name": name, "out": None, "out_px": None, "exit_sig": None, "be": False}
                trades.append(pos)
        exited = False
        if pos is not None:
            x = why = None
            if o[i] <= pos["stop"] and i > pos["in"]:
                x, why = o[i], "opened below the stop"
            elif l[i] <= pos["stop"]:
                x, why = pos["stop"], ("break-even stop" if pos["be"] else "stop")
            elif exit_rule == "target" and h[i] >= pos["target"]:
                x, why = (o[i] if (o[i] >= pos["target"] and i > pos["in"]) else pos["target"]), "target"
            elif exit_rule == "trail" and np.isfinite(low20[i]) and c[i] < low20[i]:
                x, why = c[i], "close below the 20-day low"
            if x is not None:
                pos.update(out=i, out_px=float(x), exit_sig=i, why=why)
                pos, exited = None, True
            elif floor_stop:
                new = c[i] - pos["risk"]
                pos["be"] = pos["be"] or new >= pos["in_px"]
                pos["stop"] = max(new, pos["in_px"]) if pos["be"] else new
        if pos is None and pend is None and not exited and i in entries and i + 1 <= n:
            stop, name, target = entries[i]
            if isinstance(stop, tuple) or stop < c[i]:
                pend = (i, stop, name, target)
                if i == n - 1:                                # alert on the last close: bought tomorrow
                    trades.append({"side": 1, "sig": i, "in": None, "in_px": None, "out": None, "out_px": None,
                                   "exit_sig": None, "name": name, "pending": True})
    return trades


def _deck(df: pd.DataFrame, live=False, ma50=False, floor_stop=False) -> StrategyResult:
    c = df["close"]
    m50 = sma(c, 50)
    brk = deck_breakouts(df)
    entries, picked = {}, {}
    for i, found in brk.items():
        ok = [f for f in found if not (live and f[1] in WEAK)]
        if (live or ma50) and not (np.isfinite(m50.iat[i]) and c.iat[i] > m50.iat[i]):
            ok = []
        if ok:
            f = min(ok, key=lambda f: (f[0], f[1]))
            entries[i] = (f[2], f[1], None)
            picked[i] = f
    trades = _stop_trades(df, entries, "trail", floor_stop=floor_stop, no_chase=0.02 if live else None)
    for t in trades:
        t["rank"] = picked[t["sig"]][0]     # bot money rules: multi-bar first, then short-term, then candles
    notes, stops, shapes = {}, {}, []
    low20 = df["low"].rolling(TRAIL, min_periods=TRAIL).min().shift(1)
    for t in trades:
        i = t["sig"]
        _, name, stop, p, s = picked[i]
        notes[i] = (f"{name} breakout: closed above {P(p['brk']['level'])}. Buy at the next open"
                    + (" unless it opens more than 2% higher" if live else "") + f"; stop {P(stop)}")
        stops[i] = (stop, f"below the pattern's protective stop ({P(stop)}), just under the breakout candle")
        shp = _pattern_shape(p, s, i, name)
        if shp:
            shapes.append(shp)
        if t.get("exit_sig") is not None:
            j = t["exit_sig"]
            notes[j] = f"Sell: {t['why']}" + (f" ({P(low20.iat[j])})" if t["why"].startswith("close below") else
                                              f" ({P(t['out_px'])})")
            stops[j] = (None, "a sell alert closes the trade; it doesn't open a short")
    ov = [{"name": "20-day low (sell line)", "values": _vals(low20), "style": "band"}]
    if live or ma50:
        ov.append({"name": "SMA 50", "values": _vals(m50), "style": "accent2"})
    return _result(df, trades, notes, stops, ov, shapes)


def deck_live(df):
    """S1: deck v6 + 50-day MA filter + no-chase 2% + weak patterns removed (the bot's live rules)."""
    return _deck(df, live=True)


def deck_v6(df):
    """S2: deck v6, plain."""
    return _deck(df)


def deck_ma50(df):
    """S3: deck v6 + the 50-day MA filter only."""
    return _deck(df, ma50=True)


def deck_v8(df):
    """S5: deck v6 with the break-even floor stop."""
    return _deck(df, floor_stop=True)


# ----------------------------------------------------------- old pattern trader (S6)

S6_PATTERNS = ("ABCD", "Falling wedge", "Inverse head & shoulders", "Triple bottom", "Gartley")
_S6 = {}


def _s6_breakouts(df: pd.DataFrame) -> dict:
    """Selected up patterns that broke out at each day's close (each pattern once): {i: pattern}."""
    k = _key(df)
    if k in _S6:
        return _S6[k]
    d = _caps(df)[["Open", "High", "Low", "Close", "Volume"]].copy()
    d["ATR"] = _atr(df).values
    vavg = df["volume"].rolling(20).mean().values
    v = df["volume"].values
    look = pf.SETTINGS["daily"]["lookback"]
    out, seen = {}, set()
    c = df["close"].values
    for i in range(look - 1, len(d)):
        w = d.iloc[i - look + 1:i + 1]
        vr = float(v[i] / vavg[i]) if np.isfinite(vavg[i]) and vavg[i] > 0 else None
        try:
            pats, _ = pf.find_patterns(w, "daily", float(c[i]), float(c[i - 1]), vr)
        except Exception:
            continue
        for p in pats:
            if p["stage"] != "breakout" or p["dir"] != "LONG" or p["id"] in seen:
                continue
            if not pf.is_selected(p["name"], "LONG", "stocks"):
                continue
            seen.add(p["id"])
            out[i] = (p, i - look + 1)
            break
    _S6[k] = out
    return out


def pattern_trader(df: pd.DataFrame) -> StrategyResult:
    """S6: the old pattern trader. ABCD sells at its target; the others at a close below the 20-day low.
    Both use the pattern's stop."""
    brk = _s6_breakouts(df)
    low20 = df["low"].rolling(TRAIL, min_periods=TRAIL).min().shift(1)
    trades = []
    # ABCD and the rest have different exits: run them as two books, then keep one trade at a time
    for rule, names in (("target", {"ABCD"}), ("trail", set(S6_PATTERNS) - {"ABCD"})):
        ent = {i: (p["stop"], p["name"], p["target"] if rule == "target" else None)
               for i, (p, s) in brk.items() if p["name"] in names}
        trades += _stop_trades(df, ent, rule)
    trades = _one_at_a_time(trades, len(df))
    for t in trades:
        t["rank"] = 0 if t.get("name") == "ABCD" else 1   # the bot placed ABCD (best record) first
    notes, stops, shapes = {}, {}, []
    for t in trades:
        i = t["sig"]
        p, s = brk[i]
        tgt = f", sell at the target {P(p['target'])}" if p["name"] == "ABCD" else ""
        notes[i] = f"{p['name']} breakout: buy at the next open, stop {P(p['stop'])}{tgt}"
        stops[i] = (float(p["stop"]), f"below the pattern's stop ({P(p['stop'])})")
        pts = sorted((int(x) + s, float(y)) for x, y, *_ in p.get("points") or [])
        if len(pts) >= 2:
            shapes.append({"at": i, "name": p["name"], "points": [[x, y] for x, y in pts]})
        if t.get("exit_sig") is not None:
            j = t["exit_sig"]
            notes[j] = f"Sell: {t['why']} ({P(t['out_px'])})"
            stops[j] = (None, "a sell alert closes the trade; it doesn't open a short")
    return _result(df, trades, notes, stops, [{"name": "20-day low (sell line)", "values": _vals(low20), "style": "band"}],
                   shapes)


def _one_at_a_time(trades: list, n: int) -> list:
    """Keep trades in order of entry, skipping any that starts while an earlier one is still open."""
    keep, busy_until = [], -1
    for t in sorted(trades, key=lambda t: (t["sig"], t.get("in") or n)):
        if t["sig"] <= busy_until:
            continue
        keep.append(t)
        busy_until = n if t.get("out") is None else t["out"]
    return keep


# ----------------------------------------------------------- breakout bot (S7)

def breakout_bot(df: pd.DataFrame) -> StrategyResult:
    """S7, daily chart only: close above the 200-day SMA and above the 20-day Donchian high, confirmed by
    Bollinger (close above the upper band, or a squeeze in the last 10 candles). Stop 2 ATR under the
    entry; sell on a close below the 20-day low."""
    c = df["close"]
    m200 = sma(c, 200)
    mid = sma(c, 20)
    sd = c.rolling(20).std(ddof=0)
    up = mid + 2 * sd
    width = (4 * sd) / mid
    squeeze = width <= width.rolling(125).min().shift(1)
    confirms = (c > up) | squeeze.rolling(11, min_periods=1).max().fillna(0).astype(bool)
    dch = df["high"].rolling(20).max().shift(1)
    cross = (c > dch) & (c.shift(1) <= dch.shift(1))
    buy = ((c > m200) & cross & confirms).fillna(False).values
    a = _atr(df).values
    # the stop is 2 ATR (as of the alert day) under the actual entry, the next day's open
    entries = {int(i): (("below_open", 2 * a[i]), "Breakout", None) for i in np.flatnonzero(buy)}
    trades = _stop_trades(df, entries, "trail")
    low20 = df["low"].rolling(TRAIL, min_periods=TRAIL).min().shift(1)
    notes, stops = {}, {}
    for t in trades:
        i = t["sig"]
        why = "above the upper Bollinger band" if c.iat[i] > up.iat[i] else "after a Bollinger squeeze"
        notes[i] = (f"New 20-day high ({P(dch.iat[i])}) above the 200-day average, {why}. "
                    f"Buy at the next open, stop 2 ATR below it")
        stops[i] = (float(c.iat[i] - 2 * a[i]), "about 2 ATR below the entry (the bot's stop)")
        if t.get("exit_sig") is not None:
            j = t["exit_sig"]
            notes[j] = f"Sell: {t['why']} ({P(t['out_px'])})"
            stops[j] = (None, "a sell alert closes the trade; it doesn't open a short")
    return _result(df, trades, notes, stops, [
        {"name": "SMA 200", "values": _vals(m200), "style": "accent"},
        {"name": "Upper band", "values": _vals(up), "style": "band"},
        {"name": "20-day low (sell line)", "values": _vals(low20), "style": "muted"},
    ])


# ----------------------------------------------------------- 50-day rule and BTC trend (C1-C9)

_R50 = {}


def _rule(df: pd.DataFrame, side="long", **kw) -> pd.DataFrame:
    k = _key(df, side, tuple(sorted(kw.items())))
    if k not in _R50:
        _R50[k] = rule50.run(_caps(df)[["Open", "High", "Low", "Close"]], side=side, **kw)
    return _R50[k]


def _flag_trades(df: pd.DataFrame, entry, exit_, side=1, reason=None) -> list:
    """Trades from entry / exit flags decided on closes, carried out at the next day's open."""
    o = df["open"].values.astype(float)
    entry, exit_ = np.asarray(entry, bool), np.asarray(exit_, bool)
    n = len(o)
    trades, pos, pend_in, pend_out = [], None, None, None
    for i in range(n):
        if pend_out is not None and pos is not None:
            pos.update(out=i, out_px=float(o[i]))
            pos, pend_out = None, None
        if pend_in is not None:
            pos = {"side": side, "sig": pend_in, "in": i, "in_px": float(o[i]), "out": None, "out_px": None,
                   "exit_sig": None, "why": ""}
            trades.append(pos)
            pend_in = None
        if pos is not None and pend_out is None and exit_[i]:
            pos["exit_sig"] = i
            pos["why"] = (reason[i] if reason is not None else "") or "exit"
            pend_out = i
            if i == n - 1:
                pass                                  # sold at tomorrow's open
        elif pos is None and pend_in is None and entry[i]:
            pend_in = i
            if i == n - 1:
                trades.append({"side": side, "sig": i, "in": None, "in_px": None, "out": None, "out_px": None,
                               "exit_sig": None, "pending": True})
    return trades


R50_NOTES = {"lower high": "a lower high (the bounce failed below the highest high since the buy)",
             "close below the 50-day": "a close below the 50-day average",
             "came back to the 50-day": "the price came back down to the 50-day average",
             "starting stop": "a close below the starting stop",
             "higher low": "a higher low (the drop failed above the lowest low since the short)",
             "close above the 50-day": "a close above the 50-day average",
             "came back up to the 50-day": "the price came back up to the 50-day average"}


def _r50_strategy(df: pd.DataFrame, both=False, **kw) -> StrategyResult:
    c = df["close"]
    m50 = sma(c, 50)
    sides = (("long", 1), ("short", -1)) if both else (("long", 1),)
    trades = []
    runs = {}
    for name, s in sides:
        r = _rule(df, name, **kw)
        runs[s] = r
        trades += _flag_trades(df, r["entry"].values, r["exit"].values, s, r["reason"].values)
    if both:
        trades = _one_at_a_time_flip(trades, len(df))
    notes, stops = {}, {}
    for t in trades:
        i, s = t["sig"], t["side"]
        r = runs[s]
        st = r["stop"].values
        stop = float(st[i + 1]) if i + 1 < len(df) and np.isfinite(st[i + 1]) else None
        if s > 0:
            notes[i] = f"Closed above a rising 50-day average ({P(m50.iat[i])}). Buy at the next open"
            stops[i] = (stop, "a close below the starting stop (the last swing low, kept 1-3 ATR under the entry)") \
                if stop is not None and stop < c.iat[i] else (None, "")
        else:
            notes[i] = f"Closed below a falling 50-day average ({P(m50.iat[i])}). Sell short at the next open"
            stops[i] = (stop, "a close above the starting stop (the last swing high, kept 1-3 ATR above the entry)") \
                if stop is not None and stop > c.iat[i] else (None, "")
        if t.get("exit_sig") is not None:
            j = t["exit_sig"]
            what = "Sell" if s > 0 else "Cover the short"
            why = "a close above the starting stop" if s < 0 and t["why"] == "starting stop" else \
                R50_NOTES.get(t["why"], t["why"])
            notes.setdefault(j, f"{what} at the next open: {why}")
            stops.setdefault(j, (None, "a sell alert closes the trade; it doesn't open a short" if s > 0 else
                                 "a cover alert closes the short; it doesn't open a long"))
    return _result(df, trades, notes, stops, [{"name": "SMA 50", "values": _vals(m50), "style": "accent2"}])


def _one_at_a_time_flip(trades: list, n: int) -> list:
    """Long + short: one position at a time; a trade may start at the same open another one ends (a flip)."""
    keep, busy_until = [], -1
    for t in sorted(trades, key=lambda t: (t["sig"], t.get("in") or n)):
        start = t.get("in") if t.get("in") is not None else t["sig"] + 1
        if start < busy_until:
            continue
        keep.append(t)
        busy_until = n + 1 if t.get("out") is None else t["out"]
    return keep


R50_BASE = dict(exit_mode="lh", trend="rising50", reentry=False)


def btc50_f1(df):       # C1, the live bot
    return _r50_strategy(df, **R50_BASE, ma_exit="close", lh_right=1)


def btc50_touch_f1(df):  # C2
    return _r50_strategy(df, **R50_BASE, ma_exit="touch", lh_right=1)


def btc50_touch_f2(df):  # C3
    return _r50_strategy(df, **R50_BASE, ma_exit="touch", lh_right=2)


def btc50_touch(df):     # C4
    return _r50_strategy(df, **R50_BASE, ma_exit="touch")


def btc50_ma(df):        # C5
    return _r50_strategy(df, **R50_BASE, ma_exit="close")


def btc50_both(df):      # C7: long + short
    return _r50_strategy(df, both=True, **R50_BASE, ma_exit="close", lh_right=1)


def btc50_maonly(df):    # C8
    return _r50_strategy(df, exit_mode="none", trend="rising50", reentry=False, ma_exit="close")


def btc50_lhonly(df):    # C9
    return _r50_strategy(df, **R50_BASE, lh_right=1)


def btc_trend_50_200(df: pd.DataFrame) -> StrategyResult:
    """C6: hold while the close is above both the 50- and the 200-day average, else cash."""
    c = df["close"]
    m50, m200 = sma(c, 50), sma(c, 200)
    hold = ((c > m50) & (c > m200)).values
    valid = m200.notna().values
    trades = _flag_trades(df, hold & valid, ~hold & valid)
    notes, stops = {}, {}
    for t in trades:
        i = t["sig"]
        notes[i] = f"Closed above both the 50-day ({P(m50.iat[i])}) and the 200-day ({P(m200.iat[i])}). Buy at the next open"
        stops[i] = (None, "no stop: it sells at the next open after a close below either average")
        if t.get("exit_sig") is not None:
            j = t["exit_sig"]
            which = "50-day" if c.iat[j] <= m50.iat[j] else "200-day"
            notes[j] = f"Sell at the next open: closed below the {which} average"
            stops[j] = (None, "a sell alert closes the trade; it doesn't open a short")
    return _result(df, trades, notes, stops, [{"name": "SMA 50", "values": _vals(m50), "style": "accent2"},
                                              {"name": "SMA 200", "values": _vals(m200), "style": "accent"}])


BOT_REGISTRY = {
    "deck_live": deck_live, "deck_v6": deck_v6, "deck_ma50": deck_ma50, "deck_v8": deck_v8,
    "pattern_trader": pattern_trader, "breakout_bot": breakout_bot,
    "btc50_f1": btc50_f1, "btc50_touch_f1": btc50_touch_f1, "btc50_touch_f2": btc50_touch_f2,
    "btc50_touch": btc50_touch, "btc50_ma": btc50_ma, "btc_trend_50_200": btc_trend_50_200,
    "btc50_both": btc50_both, "btc50_maonly": btc50_maonly, "btc50_lhonly": btc50_lhonly,
}
STOCK_ONLY = {"deck_live", "deck_v6", "deck_ma50", "deck_v8", "pattern_trader", "breakout_bot"}
BOT_MARKETS = {sid: ("stocks",) if sid in STOCK_ONLY else ("crypto",) for sid in BOT_REGISTRY}
# the exit each strategy's backtest uses, shown on its strategy page
BOT_RULES = {
    "deck_live": "Buy at the next open after a breakout, sell at the stop or on a close below the 20-day low",
    "deck_v6": "Buy at the next open after a breakout, sell at the stop or on a close below the 20-day low",
    "deck_ma50": "Buy at the next open after a breakout, sell at the stop or on a close below the 20-day low",
    "deck_v8": "Buy at the next open after a breakout, sell at the moving stop or on a close below the 20-day low",
    "pattern_trader": "Buy at the next open after a breakout, sell at the stop, the target (ABCD) or a close below the 20-day low",
    "breakout_bot": "Buy at the next open, sell at a stop 2 ATR below or on a close below the 20-day low",
    "btc_trend_50_200": "Buy at the next open after a close above both averages, sell at the next open after a close below either",
    "btc50_both": "Long or short at the next open after the signal, out at the next open after the rule's exit",
}
for _sid in BOT_REGISTRY:
    BOT_RULES.setdefault(_sid, "Buy at the next open after the signal, sell at the next open after the rule's exit")

# The trading bot's own money rules, for the second account on each strategy page (backtest.py
# bot_account). Stocks: risk 1% of equity between entry and stop, at most `cap` of equity per stock,
# at most 100% invested, `max_pos` positions and `max_new` new trades a day. Crypto: the whole
# account in one trade at a time (the bot traded Bitcoin only, SIZE = 1.0).
DECK_MONEY = {"risk": 0.01, "cap": 0.10, "max_pos": 25, "max_new": 8,
              "text": "risk 1% per trade, max 10% per stock, max 25 positions, 8 new a day"}
BOT_MONEY = {sid: DECK_MONEY for sid in ("deck_live", "deck_v6", "deck_ma50", "deck_v8")}
BOT_MONEY["pattern_trader"] = {"risk": 0.01, "cap": 0.20, "max_pos": 10, "max_new": 3,
                               "text": "risk 1% per trade, max 20% per stock, max 10 positions, 3 new a day"}
BOT_MONEY["breakout_bot"] = {"risk": 0.01, "cap": 0.20, "max_pos": 10, "max_new": 3,
                             "text": "risk 1% per trade (stop 2 ATR), max 20% per stock, max 10 positions, 3 new a day"}
for _sid in BOT_REGISTRY:
    BOT_MONEY.setdefault(_sid, {"risk": None, "cap": 1.0, "max_pos": 1, "max_new": 1,
                                "text": "the whole account in one trade at a time"})

