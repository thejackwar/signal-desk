"""Fetch 1-minute bars (09:30-12:00 New York) for the recent Open Drive Snap-Back trades that took Leg 2,
so they can be drawn as candlestick charts. Reads docs/data/snapback/backtest.json (base_trades_recent)
-> docs/data/snapback/examples.json"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from open_breakout import fetch, ny  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "docs" / "data" / "snapback"
NY = ZoneInfo("America/New_York")
bt = json.loads((D / "backtest.json").read_text())
trades = [t for t in bt["base_trades_recent"] if t.get("l2")]
out = []
for t in trades:
    utc = lambda hm: datetime.fromisoformat(f"{t['d']}T{hm}").replace(tzinfo=NY).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bars = fetch([t["s"]], "1Min", utc("09:30"), utc("12:00")).get(t["s"], [])
    rows = [{"t": ny(b["t"]).strftime("%H:%M"), "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]}
            for b in bars if t["d"] == ny(b["t"]).strftime("%Y-%m-%d") and "09:30" <= ny(b["t"]).strftime("%H:%M") < "12:00"]
    out.append({**t, "bars": rows})
    print(t["s"], t["d"], len(rows), "bars", flush=True)
(D / "examples.json").write_text(json.dumps({"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                                             "trades": out}, separators=(",", ":")))
