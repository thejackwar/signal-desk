"""
Live (paper) trades: every strategy in the Strategy Lab, run with its default settings on
fresh daily prices from the go-live day on, with every trade it takes kept in a ledger:

  docs/data/live_trades.json              stocks
  docs/data/crypto/live_trades.json       crypto
  docs/data/commodities/live_trades.json  commodities

The trades come from docs/lab.js itself (engine/live_trades.mjs), so they follow exactly the
same rules as the Strategy Lab and its calendar. Nothing is backfilled: only alerts from the
go-live day on count. A trade, once closed and written down, is never changed or removed
(a later data revision or a repainting pattern can't rewrite the record). Open trades are
refreshed on every run. Paper only: no orders are sent anywhere.

  python engine/live_trades.py --market stocks          # real prices (GitHub Actions)
  python engine/live_trades.py --market stocks --demo   # simulated prices, for testing
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import backtest  # noqa: E402
import scan  # noqa: E402
from scan import MARKETS, fetch_demo, fetch_real, load_tickers  # noqa: E402
from strategies import REGISTRY, for_market  # noqa: E402
from universe import U  # noqa: E402

ENGINE = Path(__file__).parent
START = "2026-11-02"      # go-live day: the first alerts that count are from this session's close
START_500 = "2026-11-09"  # the S&P 500 (UNIVERSE=sp500) starts its own record a week later
PERIOD = "2y"             # enough history for the 200-day averages and the chart patterns


def pre_trades(data) -> dict:
    """What the Lab can't recompute itself (chart-pattern alerts, trading-bot trades), the same way
    the weekly backtest saves them for lab.json (see LAB_PRE in backtest.py)."""
    pre: dict[str, dict[str, list[int]]] = {}
    for sid, fn in REGISTRY.items():
        if not for_market(sid, scan.MARKET):
            continue
        for t, df in data.items():
            try:
                res = fn(df)
            except Exception as e:
                print(f"    ! {sid} {t}: {e}")
                continue
            if res.trades is not None:
                pre.setdefault(sid, {})[t] = backtest.lab_trades(res.trades, df["close"].values)
            elif sid in backtest.LAB_SIGNALS:
                sig = res.signals.values
                pre.setdefault(sid, {})[t] = [(int(i) + 1) * int(sig[i]) for i in np.flatnonzero(sig)]
    return pre


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=list(MARKETS), default="stocks")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--start", default=None, help="go-live day (YYYY-MM-DD)")
    ap.add_argument("--out", help="ledger file (default: the market's data folder)")
    args = ap.parse_args()
    scan.configure(args.market)
    if args.start is None:
        args.start = START_500 if U.is500 and args.market == "stocks" else START
    out = Path(args.out) if args.out else scan.DATA / "live_trades.json"

    data = fetch_demo(bars=520, seed=11) if args.demo else fetch_real(load_tickers(), period=PERIOD)
    if not data:
        sys.exit("No price data downloaded; leaving the live trades untouched.")
    print(f"Live trades: {len(data)} tickers, {args.market}")
    scan.use_benchmark(data, args.demo, period=PERIOD)
    backtest.LAB_PRE.clear()
    backtest.LAB_PRE.update(pre_trades(data))
    bench = backtest.BENCH_BY_MARKET.get(args.market, backtest.BENCH)
    spy_c = None
    if not args.demo:
        try:
            spy_c = data[bench]["close"] if bench in data else fetch_real([bench], period=PERIOD)[bench]["close"]
        except Exception as e:   # only the Lab's market filter uses it
            print(f"  ! {bench}: {e}")
    lab = backtest.lab_prices(data, spy_c)

    with tempfile.TemporaryDirectory() as tmp:
        lab_file = Path(tmp) / "lab.json"
        lab_file.write_text(json.dumps(lab, separators=(",", ":")))
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["node", str(ENGINE / "live_trades.mjs"), str(lab_file), args.market, str(out), args.start,
                        "demo" if args.demo else "real"], check=True)
    # The same fresh prices for the Strategy Lab's followed strategies (your own setups, checked on
    # your device each time you open the app): lab.json itself only updates once a week.
    recent = out.parent / "lab_recent.json"
    recent.write_text(json.dumps(lab, separators=(",", ":")))
    print(f"-> {recent} ({recent.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
