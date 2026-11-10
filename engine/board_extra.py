"""Strategy Board rows for the strategies the Strategy Lab doesn't run (docs/board.html).

engine/day_board.mjs fills the board with every Strategy Lab strategy. This adds everything else that
is backtested on its own page or by its own script, one number per day on the same date axis:

  stocks   Premarket Momentum (long and short), Gap-Down Short, Opening Reversal, Open breakout,
           Open Drive & Snap-Back, 4H CHoCH, gamma level rejection, pairs trading, Smaller Crashes,
           VIX seasonality (seasonal and calendar), dark pool strategies, Top-Down (live paper trades),
           the options strategies on SPY, and every strategy of the research engine (Research page)
  crypto   4H CHoCH on bitcoin, Top-Down on bitcoin (live paper trades), the BTC dip buy

Each row is the strategy's own account, the way its page reports it:
  daily    the account's change that day (intraday strategies, Smaller Crashes, VIX, pairs, dark pools)
  realized trades counted on the day they close (4H CHoCH, gamma, Top-Down, BTC dip)
  monthly  options cycles and the research engine only keep month-end results: res "M", one tile a month

    python engine/board_extra.py stocks|crypto      (UNIVERSE=sp500 for the S&P 500 list)
 -> docs/data/board_extra.json, docs/data/sp500/board_extra.json, docs/data/crypto/board_extra.json
"""
from __future__ import annotations

import json
import os
import sys
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
FROM = "2021-01-01"
TOPDOWN_SIZE = 0.05      # Top-Down live trades: each closed trade counted as 5% of the account


def load(rel: str):
    p = DATA / rel
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def dig(obj, *keys):
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


class Axis:
    """The board's dates. A strategy day that isn't on it (a weekend, a holiday) goes to the next board day."""

    def __init__(self, dates: list[str]):
        self.dates = dates

    def place(self, pairs) -> dict[int, float]:
        out: dict[int, float] = {}
        for d, r in pairs:
            d = str(d)[:10]
            if d < FROM or r is None:
                continue
            i = bisect_left(self.dates, d)
            if i >= len(self.dates):
                continue                              # newer than the board's last day: it shows up tomorrow
            out[i] = (1 + out.get(i, 0.0)) * (1 + r) - 1
        return out


def from_board(b) -> list:
    """A saved "board" block (engine/board_series.py) -> [[date, change], ...]."""
    if not b or not b.get("dates"):
        return []
    return [[d, v / 1e5] for d, v in zip(b["dates"], b["days"])]


def from_points(points, start=None) -> list:
    """Equity points [[date, equity], ...] -> realized change on each point's day."""
    out, prev = [], start
    for d, e in points:
        if prev and prev > 0 and e is not None:
            out.append([d, e / prev - 1])
        prev = e
    return out


def month_ends(months: list[str], curve: list[float], axis: Axis) -> list:
    """Research engine: one equity value per month (YYYY-MM) -> that month's change on its last board day."""
    out = []
    for k in range(1, min(len(months), len(curve))):
        m = months[k]
        nxt = f"{int(m[:4]) + (m[5:] == '12')}-{(int(m[5:]) % 12) + 1:02d}"
        j = bisect_left(axis.dates, nxt + "-01") - 1   # last board day of month m
        if j < 0 or not axis.dates[j].startswith(m) or not curve[k - 1]:
            continue
        out.append([axis.dates[j], curve[k] / curve[k - 1] - 1])
    return out


def topdown(rel: str, only: str | None = None) -> list:
    led = load(rel) or {}
    out = []
    for t in led.get("closed") or []:
        if only and t.get("t") != only:
            continue
        when = t.get("exit_t") or t.get("entry_t")
        if when and t.get("ret") is not None:
            out.append([when[:10], t["ret"] / 100 * TOPDOWN_SIZE])
    return out


def btc_dip() -> list:
    """research/btc-dip: buy 5% under the 24-hour high, sell 24 hours later, no leverage (its default)."""
    try:
        import pandas as pd
        sys.path.insert(0, str(ROOT / "research" / "btc-dip"))
        import backtest as bd  # noqa: E402
        px, _ = bd.load()
        fw = ROOT / "research" / "btc-dip" / "forward" / "candles.csv"
        if fw.exists():
            f = pd.read_csv(fw, index_col=0)
            f.index = pd.to_datetime(f.index, utc=True, format="mixed")
            px = pd.concat([px, f[["open", "high", "low", "close"] + (["volume"] if "volume" in f else [])]])
            px = px[~px.index.duplicated(keep="last")].sort_index()
        t = bd.run(px, dip=0.05, hold=24, start="2020-12-01")
        return [[str(r.exit_time)[:10], float(r.ret)] for r in t.itertuples()]
    except Exception as e:  # noqa: BLE001
        print(f"  ! BTC dip: {e}")
        return []


OPTIONS = {"covered_call": "Covered call", "cash_secured_put": "Cash-secured put", "wheel": "The wheel",
           "protective_put": "Protective put", "collar": "Collar", "bull_call_spread": "Bull call spread",
           "bear_put_spread": "Bear put spread", "iron_condor": "Iron condor", "long_straddle": "Long straddle",
           "long_strangle": "Long strangle", "calendar": "Calendar spread"}


def sources(market: str, big: bool, axis: Axis) -> list[dict]:
    """Every row: id, name, tag, href, group (main | more | research), res, pairs."""
    u = "sp500/" if big else ""
    rows = []
    add = lambda **r: rows.append(r)
    if market == "stocks":
        intraday = [("premarket_long", "Premarket Momentum", "premarket/backtest.json", "premarket.html"),
                    ("premarket_short", "Premarket Momentum, short", "premarket/short/backtest.json", "premarket.html"),
                    ("gap_down_short", "Gap-Down Short", "gapdown/backtest.json", "gapdown.html"),
                    ("opening_reversal", "Opening Reversal", "reversal/backtest.json", "reversal.html"),
                    ("open_breakout", "Open breakout", "breakout/backtest.json", None),
                    ("open_snapback", "Open Drive & Snap-Back", "snapback/backtest.json", None)]
        for i, n, f, h in intraday:
            add(id=i, name=n, tag="intraday", href=h, group="main", pairs=from_board(dig(load(f), "board")))
        add(id="choch", name="4H CHoCH", tag="", href="choch.html", group="main",
            pairs=from_board(dig(load(u + "choch/backtest.json"), "markets", "stocks", "board")))
        add(id="gamma", name="Gamma level rejection", tag="SPY, QQQ, IWM", href="trendlines.html", group="main",
            pairs=from_board(dig(load("gamma/backtest.json"), "board")))
        add(id="pairs", name="Pairs trading", tag="", href="pairs.html", group="main",
            pairs=from_board(dig(load(u + "pairs/pairs.json"), "walk_forward", "board")))
        add(id="crash_filter", name="Smaller Crashes", tag="S&P 500", href="index.html#crashes", group="main",
            pairs=from_board(dig(load("crashfilter.json"), "board")))
        V = load("vix.json")
        add(id="vix_seasonal", name="VIX seasonality", tag="S&P 500", href="index.html#vix", group="main",
            pairs=from_board(dig(V, "board", "seasonal")))
        add(id="vix_calendar", name="VIX calendar months", tag="S&P 500", href="index.html#vix", group="more",
            pairs=from_board(dig(V, "board", "calendar")))
        DP = load(u + "darkpool/backtest.json")
        add(id="dark_buys", name="Dark pool buying", tag="", href="darkpool.html", group="more",
            pairs=from_board(dig(DP, "board", "dark_buys")))
        add(id="dark_index_spy", name="Dark pool index → SPY", tag="", href="darkpool.html", group="more",
            pairs=from_board(dig(DP, "board", "dark_index_spy")))
        add(id="topdown", name="Top-Down trendlines", tag="live", href="trendlines.html", group="main",
            pairs=topdown("topdown/trades.json"))
        O = load(u + "options/backtest.json") or load("options/backtest.json")
        dates = dig(O, "spy", "dates") or []
        for k, n in OPTIONS.items():
            eq = dig(O, "spy", "strat", k, "hold", "eq")
            if eq:
                add(id="opt_" + k, name=n, tag="options · SPY", href="options.html", group="more", res="M",
                    pairs=from_points(list(zip(dates, eq))))
        R = load("research.json") or {}
        for s in R.get("strategies") or []:
            if s.get("curve"):
                add(id="rs_" + s["id"], name=s["name"], tag="research", href="research.html", group="research", res="M",
                    pairs=month_ends(R.get("months") or [], s["curve"], axis))
    elif market == "crypto":
        add(id="choch_btc", name="4H CHoCH (Bitcoin)", tag="", href="choch.html", group="main",
            pairs=from_board(dig(load("choch/backtest.json"), "markets", "btc", "board")))
        add(id="topdown_btc", name="Top-Down trendlines (Bitcoin)", tag="live", href="trendlines.html", group="main",
            pairs=topdown("crypto/topdown_trades.json", None))
        add(id="btc_dip", name="BTC dip buy (5%, 24h)", tag="Bitcoin", href=None, group="main", pairs=btc_dip())
    return rows


def main() -> None:
    market = sys.argv[1] if len(sys.argv) > 1 else "stocks"
    big = market == "stocks" and os.environ.get("UNIVERSE") == "sp500"
    folder = DATA / ("sp500" if big else "") if market == "stocks" else DATA / market
    board = json.loads((folder / "board.json").read_text()) if (folder / "board.json").exists() else None
    if not board:
        print(f"no board.json for {market}; nothing to add")
        return
    axis = Axis(board["dates"])
    rows = []
    for r in sources(market, big, axis):
        pairs = r.pop("pairs")
        placed = axis.place(pairs)
        if not placed:
            print(f"  - {r['name']}: no data yet")
            continue
        at = sorted(placed)
        v = [round(placed[i] * 1e5) for i in at]
        tot = 1.0
        for x in v:
            tot *= 1 + x / 1e5
        first = min(str(d)[:10] for d, _ in pairs)
        rows.append({**{k: x for k, x in r.items() if x is not None}, "res": r.get("res", "D"),
                     "since": max(first, FROM), "total": round((tot - 1) * 100, 1), "at": at, "v": v})
    out = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "market": market,
           "unit": "0.1bp", "note": "at = indexes into board.json dates; days not listed = no position or no result",
           "strategies": rows}
    (folder / "board_extra.json").write_text(json.dumps(out, separators=(",", ":")))
    print(f"-> {(folder / 'board_extra.json').relative_to(ROOT)}: {len(rows)} strategies")
    for r in rows:
        print(f"   {r['total']:>8}%  {r['name']}  ({r['group']}, {len(r['at'])} days)")


if __name__ == "__main__":
    main()
