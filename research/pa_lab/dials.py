"""The dials: every price action rule turned into a fixed list of values. Writes results/rulebook.md and
results/rulebook_spec.json and counts every valid version.

Adaptation to regular-hours ETFs (decisions.md, D-03): the session filter values "all", "London and New
York" and "New York 08:00-16:00" are the same thing when only 09:30-16:00 exists, so the session dial keeps
"all" and "New York open 09:30-11:00", and adds "afternoon 13:30-16:00" as its third value.
"""
from __future__ import annotations

import itertools
import json
from math import prod

from . import config as C

SETUPS = ["level_bounce", "breakout", "flip_zone", "trend_pullback", "trend_line", "bos_continuation",
          "choch_reversal", "sweep_reclaim", "sd_zone", "fvg"]
SETUP_NAMES = {"level_bounce": "Level bounce", "breakout": "Breakout", "flip_zone": "Flip zone",
               "trend_pullback": "Trend pullback", "trend_line": "Trend line", "bos_continuation": "Break of structure",
               "choch_reversal": "Change of character", "sweep_reclaim": "Sweep and reclaim",
               "sd_zone": "Supply and demand zone", "fvg": "Fair value gap"}
LEVEL_SETUPS = {"level_bounce", "breakout", "flip_zone"}
BREAK_SETUPS = {"breakout", "flip_zone", "bos_continuation", "choch_reversal"}

# trigger charts: the setup chart or one or two steps lower; daily setups trigger on 1h
TRIGGERS = {"1m": ["1m"], "5m": ["5m", "1m"], "15m": ["15m", "5m", "1m"], "1h": ["1h", "15m", "5m"],
            "4h": ["4h", "1h", "15m"], "1d": ["1h"]}

SHARED = {
    "direction": (["long", "short", "both"], "which side the version trades"),
    "swing": ([2, 3, 5, 8], "swing strength: bars on each side that a swing high/low must beat"),
    "trigger": (["same", "down1", "down2"], "trigger chart: the setup chart or one or two steps lower (daily: 1h)"),
    "confirm": (["none", "close_back", "rejection", "engulfing", "either", "lower_bos"],
                "confirmation candle on the trigger chart before entering"),
    "entry": (["close", "limit"], "enter at the trigger bar's close, or a limit back at the level"),
    "stop_type": (["hard", "close"], "hard stop (intrabar) or close-based stop (trigger-chart close beyond it)"),
    "stop_ref": (["level", "wick", "swing", "gap_mid"], "what the stop sits beyond: the level, the touch wick, the last swing, or the gap's middle candle"),
    "stop_buf": ([0.0, 0.1, 0.25], "extra distance beyond the stop reference, in setup-chart ATR"),
    "target": (["1R", "1.5R", "2R", "3R", "4R", "5R", "swing"], "profit target in multiples of risk, or the next swing"),
    "manage": (["hold", "choch_exit"], "hold to stop/target, or exit on a change of character against the trade"),
    "time_exit": (["none", "flat_1600", "24", "96"], "time exit: none, flat at 16:00, or after 24 or 96 setup-chart bars"),
}
FILTERS = {
    "f_trend": (["off", "setup", "up1", "daily"], "trend must agree: on the setup chart, one chart up, or the daily"),
    "f_choppy": (["off", "on"], "skip days after a choppy session (body under 25% of the range)"),
    "f_run": (["off", "3", "5"], "skip trades in the direction of a 3 or 5 day run"),
    "f_fresh": (["off", "on"], "breaks only: the level was never broken before"),
    "f_news": (["trade", "skip", "half"], "CPI, jobs and FOMC days: trade, skip, or half size"),
    "f_session": (["all", "open", "afternoon"], "time of day: all, New York open 09:30-11:00, afternoon 13:30-16:00"),
    "f_pullback": (["off", "0.382", "0.5"], "minimum pullback of the last swing leg before entering"),
    "f_gap": (["off", "on"], "an unfilled fair value gap must overlap the level"),
}
LEVEL = {
    "lvl_tol": ([0.1, 0.25, 0.5], "swings within this many ATR form one level"),
    "lvl_touches": ([1, 2, 3, 4], "minimum touches (swings) in the level"),
    "lvl_shape": (["line", "box"], "level drawn as a line (average) or a box (lowest to highest swing)"),
}
OWN = {
    "level_bounce": {},
    "breakout": {"bo_margin": ([0.0, 0.1], "close must clear the level by this many ATR")},
    "flip_zone": {"flip_window": ([5, 10, 20], "bars after the break in which the retest must come")},
    "trend_pullback": {"pb_level": ([0.382, 0.5, 0.618], "retracement of the last leg that triggers the setup")},
    "trend_line": {"tl_tol": ([0.1, 0.25], "how close (ATR) a touch must come to the line")},
    "bos_continuation": {"retest": (["no", "yes"], "enter on the break, or wait up to 10 bars for a retest of the broken swing")},
    "choch_reversal": {"retest": (["no", "yes"], "enter on the change of character, or wait for a retest")},
    "sweep_reclaim": {"sweep_depth": ([0.05, 0.1, 0.25], "how far past the swing (ATR) the sweep must reach"),
                      "reclaim_bars": ([1, 2, 3], "bars allowed to close back inside")},
    "sd_zone": {"zone_first": (["yes", "no"], "only the first return to a zone, or any return"),
                "zone_age": ([50, 200], "zones older than this many bars are dropped")},
    "fvg": {"gap_min": ([0.0, 0.1, 0.25], "minimum gap size in ATR"),
            "gap_first": (["yes", "no"], "only the first return into the gap, or any")},
}
CHART_ORDER = ["1m", "5m", "15m", "1h", "4h", "1d"]


def trigger_chart(setup_chart: str, trig: str) -> str | None:
    opts = TRIGGERS[setup_chart]
    i = {"same": 0, "down1": 1, "down2": 2}[trig]
    if setup_chart == "1d":
        return "1h" if trig == "same" else None
    return opts[i] if i < len(opts) else None


def dial_space(setup: str) -> dict:
    sp = {"chart": (CHART_ORDER, "setup chart")}
    sp.update(SHARED)
    sp.update(FILTERS)
    if setup in LEVEL_SETUPS:
        sp.update(LEVEL)
    sp.update(OWN[setup])
    return sp


def valid(setup: str, v: dict) -> bool:
    """Combinations that make no sense are removed."""
    ch = v["chart"]
    if trigger_chart(ch, v["trigger"]) is None:
        return False
    if v["stop_ref"] == "gap_mid" and setup != "fvg":
        return False
    if v["time_exit"] == "flat_1600" and ch in ("4h", "1d"):
        return False
    if v["confirm"] == "lower_bos" and trigger_chart(ch, v["trigger"]) == ch:
        return False                                  # a lower-chart break needs a lower chart
    if v["f_fresh"] == "on" and setup not in BREAK_SETUPS:
        return False
    if v["f_trend"] == "up1" and ch == "1d":
        return False
    if v["f_session"] != "all" and ch == "1d":
        return False                                  # a daily bar has no time of day
    if v["f_gap"] == "on" and setup == "fvg":
        return False
    if v["stop_ref"] == "wick" and setup in ("bos_continuation", "choch_reversal") and v.get("retest") == "no":
        return False                                  # no touch wick without a retest
    return True


def count_valid(setup: str) -> int:
    """Exact count, by enumerating the dials the validity rules depend on and multiplying the rest."""
    sp = dial_space(setup)
    dep = ["chart", "trigger", "stop_ref", "time_exit", "confirm", "f_fresh", "f_trend", "f_session", "f_gap"]
    if "retest" in sp:
        dep.append("retest")
    free = prod(len(sp[k][0]) for k in sp if k not in dep)
    n = 0
    for combo in itertools.product(*(sp[k][0] for k in dep)):
        if valid(setup, dict(zip(dep, combo))):
            n += 1
    return n * free


def write_rulebook() -> dict:
    C.RESULTS.mkdir(parents=True, exist_ok=True)
    counts = {s: count_valid(s) for s in SETUPS}
    spec = {"setups": SETUPS, "names": SETUP_NAMES, "charts": CHART_ORDER, "triggers": TRIGGERS,
            "shared": {k: v[0] for k, v in SHARED.items()}, "filters": {k: v[0] for k, v in FILTERS.items()},
            "level": {k: v[0] for k, v in LEVEL.items()}, "own": {s: {k: v[0] for k, v in d.items()} for s, d in OWN.items()},
            "valid_versions": counts, "valid_total": sum(counts.values())}
    (C.RESULTS / "rulebook_spec.json").write_text(json.dumps(spec, indent=1))
    lines = ["# Price Action Lab rulebook", "",
             "Every price action rule as dials with fixed values. Markets: QQQ (main, in place of NQ) and SPY (check, in place of ES),",
             "1-minute bars, regular hours 09:30-16:00 New York. Nothing here changes after a result; changes go in numbered amendments in decisions.md.", "",
             "## Detectors", "",
             "- **Swing** of strength k: a high (low) above (below) the k bars on each side; known only k bars later.",
             "- **Trend**: up when the last two swing highs and lows both rise, down when both fall.",
             "- **Break of structure / change of character**: the first close past the last confirmed swing; with the trend it is a break of structure, against it a change of character.",
             "- **Level**: a cluster of swings within 0.1, 0.25 or 0.5 ATR, needing 1-4 touches, drawn as a line or a box.",
             "- **Trend line**: through the last two rising swing lows (or falling swing highs).",
             "- **Supply/demand zone**: the base candle before a run of 2-5 same-colour candles covering 1.5-3 ATR.",
             "- **Fair value gap**: three candles where the third's low is above the first's high (or the mirror).",
             "- **ATR**: Wilder's 14-bar ATR from bars before the current one. A higher-chart bar is usable only from its close.", "",
             "## Shared dials", ""]
    for name, block in (("Shared", SHARED), ("Filters", FILTERS), ("Levels (bounce, breakout, flip zone)", LEVEL)):
        if name != "Shared":
            lines += ["", f"## {name}", ""]
        for k, (vals, meaning) in block.items():
            lines.append(f"- `{k}` = {', '.join(map(str, vals))}: {meaning}")
    lines += ["", "## Each setup's own dials", ""]
    for s in SETUPS:
        own = OWN[s]
        lines.append(f"- **{SETUP_NAMES[s]}**" + ("" if own else ": none beyond the shared and level dials"))
        for k, (vals, meaning) in own.items():
            lines.append(f"  - `{k}` = {', '.join(map(str, vals))}: {meaning}")
    lines += ["", "## Removed combinations", "",
              "- a trigger chart that does not exist (1m has nothing lower; daily triggers on 1h only)",
              "- the gap's middle candle as a stop outside the fair value gap setup",
              "- flat at 16:00 on a 4h or daily setup",
              "- a lower-chart break of structure as confirmation when the trigger is the setup chart",
              "- 'fresh breakouts only' outside the break setups; 'one chart up' trend and time-of-day filters on a daily setup",
              "- gap confluence on the gap setup itself; a touch-wick stop on a break entered without a retest", "",
              "## Valid versions", ""]
    for s in SETUPS:
        lines.append(f"- {SETUP_NAMES[s]}: {counts[s]:,}")
    lines.append(f"- **Total: {sum(counts.values()):,}**")
    (C.RESULTS / "rulebook.md").write_text("\n".join(lines) + "\n")
    return spec


if __name__ == "__main__":
    s = write_rulebook()
    print(f"{s['valid_total']:,} valid versions")
