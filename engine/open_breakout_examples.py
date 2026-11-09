"""Fetch 5-minute bars (pre-market included) for the example trades in docs/data/breakout/examples_pick.json:
the day before (regular session, where the line comes from) and the trade day 04:00-16:00 New York.
-> docs/data/breakout/examples.json"""
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from open_breakout import fetch, ny  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "docs" / "data" / "breakout"
NY = ZoneInfo("America/New_York")
pick = json.loads((D / "examples_pick.json").read_text())
out = {"win": [], "lose": []}
for side in ("win", "lose"):
    for t in pick[side]:
        d = date.fromisoformat(t["d"])
        start = datetime.combine(d - timedelta(days=5), datetime.min.time()).replace(tzinfo=NY)
        end = datetime.fromisoformat(t["d"] + "T16:00").replace(tzinfo=NY)
        bars = fetch([t["s"]], "5Min", start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")).get(t["s"], [])
        rows = []
        for b in bars:
            x = ny(b["t"]); m = x.hour * 60 + x.minute
            rows.append({"t": x.isoformat(timespec="minutes"), "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]})
        days = sorted({r["t"][:10] for r in rows if r["t"][:10] < t["d"]})
        prev = days[-1] if days else None
        keep = [r for r in rows if (r["t"][:10] == prev and "09:30" <= r["t"][11:16] < "16:00") or r["t"][:10] == t["d"]]
        out[side].append({**t, "prev": prev, "bars": keep})
        print(t["s"], t["d"], len(keep), "bars")
(D / "examples.json").write_text(json.dumps(out, separators=(",", ":")))
